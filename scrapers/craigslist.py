"""
Craigslist -- listings activos por categoria (proxy de oferta local), via
scraping de la pagina de busqueda publica (sin login, sin API oficial).

HONESTIDAD: Craigslist NO tiene anti-bot fuerte (confirmado 2026-09-03,
0 bloqueos en el test), pero renderiza los resultados via JavaScript
DESPUES del load inicial -- requiere esperar varios segundos con Playwright
(domcontentloaded solo no alcanza, el HTML inicial llega con los <span>
de precio y titulo vacios). No mide "sold" -- como Facebook Marketplace,
mide listings activos + precio como proxy de oferta/densidad de mercado.

Craigslist es por ciudad (subdominio), no nacional -- se corre sobre un
set fijo de ciudades grandes cercanas al corredor de sourcing real de
MasterStock (Northeast US) para que la senal sea geograficamente relevante,
no un promedio nacional diluido.

Uso:
    python -m scrapers.craigslist
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

# Ciudades grandes del corredor Northeast/cerca de la operacion real de
# MasterStock (sourcing FOB NY/NJ) -- una sola ciudad por corrida para no
# multiplicar 5 categorias x N ciudades en cada ejecucion diaria.
CITY_SUBDOMAIN = "newyork"

# Ampliado 2026-09-03 por marca real validada en 35_Formula_del_Pallet_Ganador
# (antes 1 termino/categoria = 5 filas/corrida; ahora ~14). category se
# repite -- multiples queries por categoria, cada una su propia fila via
# naics_label en la PK (ver migracion de esquema en el commit).
QUERIES = {
    "apparel_footwear": [
        "champion hoodie",
        "hanes t shirt",
        "wrangler jeans",
        "adidas hoodie",
        "nike shorts",
    ],
    "sporting_hobby": [
        "lego set",
        "hot wheels case",
        "barbie doll",
        "squishmallow",
    ],
    "electronics_appliance": [
        "jbl speaker",
        "bose speaker",
    ],
    "furniture_home": [
        "sectional couch",
    ],
    "toys": [
        "hasbro board game",
        "mattel toy",
    ],
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
]

# Cada card: <div class="cl-search-result ..." title="<titulo completo>" data-pid="...">
# seguido en algun punto por <span class="priceinfo">$X</span> antes del
# siguiente cl-search-result. Se capturan ambos con dos regex separados en
# el mismo orden de aparicion (mas robusto que un regex unico enorme contra
# el HTML anidado real).
RESULT_TITLE_RE = re.compile(r'class="cl-search-result[^"]*"\s+title="([^"]*)"')
PRICE_RE = re.compile(r'class="priceinfo">\$([\d,]+)<')

MIN_DELAY_SECONDS = 3
MAX_DELAY_SECONDS = 6


def run() -> int:
    db = sqlite_utils.Database(DB_PATH)
    table = db["comps_rotation"]

    inserted = 0
    fetched_at = datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for category, queries in QUERIES.items():
            for query in queries:
                context = browser.new_context(user_agent=random.choice(USER_AGENTS))
                page = context.new_page()

                qs = urlencode({"query": query, "sort": "date"})
                url = f"https://{CITY_SUBDOMAIN}.craigslist.org/search/sss?{qs}"

                try:
                    page.goto(url, timeout=20000, wait_until="domcontentloaded")
                    page.wait_for_timeout(4000)  # el listado se puebla via JS despues del load
                    html = page.content()
                except Exception as exc:  # noqa: BLE001
                    print(f"[craigslist] ERROR {category} ({query}): {exc}")
                    html = ""
                finally:
                    context.close()

                titles = RESULT_TITLE_RE.findall(html)
                prices = [int(p.replace(",", "")) for p in PRICE_RE.findall(html)]
                median_price = sorted(prices)[len(prices) // 2] if prices else None

                table.upsert(
                    {
                        "category": category,
                        "period": today,
                        "source": "craigslist",
                        "channel_type": channel_type_for("craigslist"),
                        "fetched_at": fetched_at,
                        "naics_label": f"{query} ({CITY_SUBDOMAIN})",
                        "naics_code": None,
                        "metric": "active_listing_count",
                        "value": len(titles),
                        "geo": CITY_SUBDOMAIN,
                        "confidence": "med" if titles else "low",
                        "notes": f"query='{query}', city={CITY_SUBDOMAIN}, median_price_usd={median_price}",
                    },
                    pk=("category", "period", "source", "naics_label"),
                    alter=True,
                )
                inserted += 1
                print(f"[craigslist] OK {category} ({query}): {len(titles)} listings, median=${median_price}")
                time.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))

        browser.close()

    print(f"[craigslist] inserted {inserted} rows into {DB_PATH}")
    return inserted


if __name__ == "__main__":
    run()
