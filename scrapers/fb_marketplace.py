"""
Facebook Marketplace -- listings activos por categoria (proxy de oferta y
demanda local), via scraping de la pagina de busqueda publica (sin login).

HONESTIDAD (ver README): a diferencia de eBay (bloqueado con CAPTCHA en la
mayoria de intentos), Facebook Marketplace SI respondio en el primer test
(2026-09-03): 76 precios extraidos de una sola pagina, sin login, sin
CAPTCHA. La pagina de resultados incluye cada listing como un atributo
aria-label con formato estable: "<titulo>, US$<precio>, <ciudad>,
publicacion <id>" -- mas robusto que depender de clases CSS (que cambian
seguido) porque aria-label es accesibilidad, no styling.

Esto NO mide "sold" (Facebook Marketplace no expone historial de vendidos
publicamente) -- mide LISTINGS ACTIVOS por categoria como proxy de oferta
+ densidad de mercado local. Complementa, no reemplaza, la senal de sold
real de eBay cuando esa funcione.

Uso:
    python -m scrapers.fb_marketplace
"""
from __future__ import annotations

import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import sqlite_utils
from playwright.sync_api import sync_playwright

from scrapers.schema import channel_type_for

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "masterstock_resale.sqlite"

SEARCH_URL = "https://www.facebook.com/marketplace/search/"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
]

# Terminos de busqueda ampliados 2026-09-03: antes 1 termino generico por
# categoria (5 filas/corrida total). Ahora por marca/SKU real validado en
# 35_Formula_del_Pallet_Ganador (categorias SOURCE del vault) -- multiples
# queries por categoria para tener una muestra real, no un solo punto de
# dato. category se repite (varias queries -> misma categoria); cada query
# se guarda como fila propia via naics_label, agregable despues por
# categoria o por marca especifica.
QUERIES = {
    "apparel_footwear": [
        "champion hoodie",
        "hanes t shirt",
        "wrangler jeans",
        "adidas hoodie",
        "nike shorts",
        "nike shoes",
        "adidas shoes new",
        "skechers shoes new",
        "new balance shoes new",
        "levis jeans",
    ],
    # Corregido 2026-09-03: LEGO/Hot Wheels/Barbie/Squishmallow/Nerf son
    # juguetes de coleccion, no deportes -- movidos a "toys" (mismo fix
    # que craigslist.py, con evidencia real de que la seccion deportiva
    # de Craigslist devolvia 0 para estos terminos).
    "sporting_hobby": [
        "golf clubs",
        "weight bench",
        "bicycle new",
        "big 5 sporting goods",
    ],
    "electronics_appliance": [
        "jbl speaker",
        "bose speaker",
        "beats headphones",
        "ge appliance new",
        "whirlpool appliance new",
        "electrolux appliance new",
    ],
    "furniture_home": [
        "sectional couch",
        "recliner chair",
        "ashley furniture new",
    ],
    "toys": [
        "hasbro board game",
        "mattel toy",
        "lego set",
        "hot wheels case",
        "barbie doll",
        "squishmallow",
        "nerf gun",
    ],
    # Ampliado 2026-09-03 -- categorias adicionales de
    # 20_Censo_Vendedores_B-Stock_Filtrado del vault (hogar/decoracion,
    # mascotas, bano/cocina -- categorias que sobreviven el filtro real
    # de sourcing de MasterStock).
    "home_decor": [
        "world market decor",
        "home decor set",
    ],
    "pet_supplies": [
        "petco pet supplies",
        "dog crate new",
    ],
    "bath_kitchen": [
        "kohler faucet",
        "kitchen fixture new",
    ],
}

# El HTML de Facebook varia entre corridas (A/B de su frontend, confirmado
# 2026-09-03): a veces sirve aria-label plano, a veces JSON embebido con
# mas estructura y mejor precision de precio. Se prueban ambos, JSON primero
# porque da amount numerico limpio (aria-label a veces trunca miles con
# formato "1.499" ambiguo entre punto decimal y separador de miles).
JSON_LISTING_RE = re.compile(
    r'"listing_price":\{"formatted_amount":"US\$[\d.,]+","amount_with_offset_in_currency":"\d+","amount":"([\d.]+)"\}'
    r'.{0,400}?"city":"([^"]*)".{0,600}?"marketplace_listing_title":"([^"]*)"'
)
ARIA_LISTING_RE = re.compile(r'aria-label="([^"$]+), US\$([\d.,]+), ([^,]+), publicaci[oó]n (\d+)"')

MIN_DELAY_SECONDS = 5
MAX_DELAY_SECONDS = 10


def _looks_blocked(html: str) -> bool:
    # OJO: "checkpoint" y "captcha" aparecen como SUBSTRINGS en JS interno de
    # Facebook incluso cuando la pagina no esta bloqueada (ej. rutas internas
    # "/checkpoint/block/", o el flag "is_checkpointed":false) -- probado con
    # falso positivo real en test 2026-09-03. Usar frases completas de UI de
    # bloqueo, y el flag JSON explicito "is_checkpointed":true si aparece.
    lowered = html.lower()
    if '"is_checkpointed":true' in lowered:
        return True
    markers = (
        "log into facebook to continue",
        "you must log in to continue",
        "confirm your identity",
        "you'll need to verify",
    )
    return any(m in lowered for m in markers)


def _parse_listings(html: str) -> list[dict]:
    # Metodo 1: JSON embebido, amount ya viene numerico limpio ("15.00").
    json_matches = JSON_LISTING_RE.findall(html)
    if json_matches:
        return [
            {"title": title.strip(), "price": float(price), "city": city.strip(), "listing_id": None}
            for price, city, title in json_matches
        ]

    # Metodo 2 (fallback): aria-label plano, precio con formato ambiguo
    # ("1,499" o "1.499" segun localizacion) -- se asume "," o "." como
    # separador de miles si hay 3 digitos despues, nunca decimal (Marketplace
    # no lista centavos en el resumen de busqueda).
    out = []
    for title, price_raw, city, listing_id in ARIA_LISTING_RE.findall(html):
        try:
            price_val = float(price_raw.replace(",", "").replace(".", ""))
        except ValueError:
            price_val = None
        out.append({"title": title.strip(), "price": price_val, "city": city.strip(), "listing_id": listing_id})
    return out


def run() -> int:
    db = sqlite_utils.Database(DB_PATH)
    table = db["comps_rotation"]

    inserted = 0
    blocked = 0
    fetched_at = datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for category, queries in QUERIES.items():
            for query in queries:
                context = browser.new_context(user_agent=random.choice(USER_AGENTS))
                page = context.new_page()

                qs = urlencode({"query": query, "sortBy": "creation_time_descend"})
                url = f"{SEARCH_URL}?{qs}"

                try:
                    page.goto(url, timeout=20000, wait_until="domcontentloaded")
                    page.wait_for_timeout(2000)
                    html = page.content()
                except Exception as exc:  # noqa: BLE001
                    print(f"[fb_marketplace] ERROR {category} ({query}): {exc}")
                    html = ""
                finally:
                    context.close()

                # pk incluye naics_label (el query/marca) -- sin esto, varias
                # marcas de la misma categoria en el mismo dia se pisarian
                # entre si via upsert (bug real corregido 2026-09-03, antes
                # solo 1 query por categoria asi que no se notaba).
                if not html or _looks_blocked(html):
                    blocked += 1
                    print(f"[fb_marketplace] BLOCKED {category} ({query})")
                    table.upsert(
                        {
                            "category": category,
                            "period": today,
                            "source": "fb_marketplace",
                            "naics_label": query,
                            "channel_type": channel_type_for("fb_marketplace"),
                            "fetched_at": fetched_at,
                            "naics_code": None,
                            "metric": "active_listing_count",
                            "value": None,
                            "geo": "US",
                            "confidence": "blocked",
                            "notes": "FB Marketplace blocked/login-wall/checkpoint",
                        },
                        pk=("category", "period", "source", "naics_label"),
                        alter=True,
                    )
                    time.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))
                    continue

                listings = _parse_listings(html)
                prices = [x["price"] for x in listings if x["price"] is not None]
                median_price = sorted(prices)[len(prices) // 2] if prices else None

                table.upsert(
                    {
                        "category": category,
                        "period": today,
                        "source": "fb_marketplace",
                        "naics_label": query,
                        "channel_type": channel_type_for("fb_marketplace"),
                        "fetched_at": fetched_at,
                        "naics_code": None,
                        "metric": "active_listing_count",
                        "value": len(listings),
                        "geo": "US",
                        "confidence": "med" if listings else "low",
                        "notes": f"query='{query}', median_price_usd={median_price}, listings parseados de aria-label en 1a pagina de resultados",
                    },
                    pk=("category", "period", "source", "naics_label"),
                    alter=True,
                )
                inserted += 1
                print(f"[fb_marketplace] OK {category} ({query}): {len(listings)} listings, median=${median_price}")
                time.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))

        browser.close()

    print(f"[fb_marketplace] inserted {inserted} rows, {blocked} blocked, into {DB_PATH}")
    return inserted


if __name__ == "__main__":
    run()
