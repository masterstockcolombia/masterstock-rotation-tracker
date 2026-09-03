"""
Craigslist -- listings activos por categoria (proxy de oferta local), via
scraping de la pagina de busqueda publica (sin login, sin API oficial).

HONESTIDAD: Craigslist NO tiene anti-bot fuerte (confirmado 2026-09-03,
0 bloqueos en el test), pero renderiza los resultados via JavaScript
DESPUES del load inicial -- requiere esperar varios segundos con Playwright
(domcontentloaded solo no alcanza, el HTML inicial llega con los <span>
de precio y titulo vacios). No mide "sold" -- como Facebook Marketplace,
mide listings activos + precio como proxy de oferta/densidad de mercado.

Craigslist es por ciudad (subdominio), no nacional. Ampliado 2026-09-03
(pedido explicito: "necesitamos la data de todos los estados y de todas
las categorias que manejamos") a 18 ciudades grandes, una por region/
mercado metro clave de USA -- no las 50, pero suficiente para ver patron
nacional real sin que el cron tarde horas.

PARALELIZACION: con 18 ciudades x ~20 queries = 360 requests, el diseno
secuencial original (1 a la vez, 3-6s delay) tardaria 30+ minutos. Este
scraper corre CIUDADES en paralelo (un browser context por ciudad
simultaneo, limitado por MAX_CONCURRENT_CITIES) mientras mantiene el
delay entre queries DENTRO de cada ciudad (Craigslist es por-ciudad, no
hay riesgo de que el rate limit de una ciudad afecte a otra).

Uso:
    python -m scrapers.craigslist
"""
from __future__ import annotations

import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import sqlite_utils
from playwright.sync_api import sync_playwright

from scrapers.schema import channel_type_for

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "masterstock_resale.sqlite"

# 18 ciudades grandes, una por region/mercado metro clave de USA. No son
# las 50 (eso multiplicaria el tiempo de corrida sin agregar mucha senal
# nueva -- mercados chicos vecinos a uno grande ya cubierto no aportan
# patron distinto), pero cubre costa este/oeste, sur, medio oeste y
# mountain west con densidad real de poblacion/comercio.
CITY_SUBDOMAINS = [
    # Ronda 1 (2026-09-03)
    "newyork", "newjersey", "boston", "philadelphia",
    "chicago", "detroit", "minneapolis",
    "houston", "dallas", "sanantonio", "atlanta", "miami",
    "losangeles", "sfbay", "seattle", "phoenix", "denver", "washingtondc",
    # Ronda 2 (2026-09-03, ampliacion a mas volumen): 30 ciudades mas,
    # cada codigo de subdominio verificado individualmente contra la API
    # real de Craigslist antes de agregarlo (status 200 + title esperado).
    "sandiego", "sacramento", "portland", "lasvegas", "saltlakecity",
    "stlouis", "kansascity", "columbus", "indianapolis", "nashville",
    "charlotte", "raleigh", "orlando", "tampa", "jacksonville",
    "pittsburgh", "baltimore", "milwaukee", "cincinnati", "cleveland",
    "austin", "elpaso", "oklahomacity", "albuquerque", "tucson",
    "neworleans", "memphis", "louisville", "richmond", "providence",
]

MAX_CONCURRENT_CITIES = 8  # subido de 4 a 8 (2026-09-03) para sostener 48 ciudades sin disparar el tiempo de corrida

# Categoria interna -> seccion de Craigslist. BUG REAL encontrado y
# corregido 2026-09-03: buscar en "sss" (all-for-sale) sin seccion
# devuelve ruido de otras categorias que mencionan la marca en texto libre
# (ej. "bose speaker" matcheaba una Chevrolet Silverado con "Bose sound
# system" de fabrica, precio $43,230 contaminando el promedio/mediana de
# electronica). Usar la seccion especifica de Craigslist filtra esto de
# raiz. Categorias ampliadas 2026-09-03 con hogar/decoracion, mascotas y
# bano/cocina (validadas contra 20_Censo_Vendedores_B-Stock_Filtrado del
# vault, categorias que sobreviven el filtro de sourcing real).
CRAIGSLIST_SECTIONS = {
    "apparel_footwear": "cla",  # clothing & accessories
    "sporting_hobby": "spo",  # sporting goods -- corregido 2026-09-03, antes "sga" (general) devolvia muy pocos resultados; "spo" es la seccion real dedicada, no encontrada en la ronda anterior
    "electronics_appliance": "ela",  # electronics
    "furniture_home": "fua",  # furniture
    "toys": "tag",  # toys & games
    "home_decor": "hsh",  # household items -- verificado
    "pet_supplies": "sga",  # "pet" en CL es adopcion de animales vivos, no productos -- productos van a general
    "bath_kitchen": "sga",  # general for sale, sin seccion propia en Craigslist
}

QUERIES = {
    "apparel_footwear": [
        "champion hoodie",
        "hanes t shirt",
        "wrangler jeans",
        "adidas hoodie",
        "nike shorts",
        "nike shoes",
        "adidas shoes new",
    ],
    # Corregido 2026-09-03: LEGO/Hot Wheels/Barbie/Squishmallow/Nerf son
    # juguetes de coleccion, no equipamiento deportivo -- movidos a "toys".
    # Confirmado con evidencia real: 0 resultados en la seccion "spo"
    # (sporting goods) para esos terminos, pero 32-60 resultados con
    # terminos deportivos reales (golf clubs, weight bench, bicycle).
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
    ],
    "furniture_home": [
        "sectional couch",
        "recliner chair",
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
    # Ampliado 2026-09-03 -- categorias adicionales de 20_Censo_Vendedores_B-Stock_Filtrado
    "home_decor": [
        "world market decor",
        "trillion home",
    ],
    "pet_supplies": [
        "petco pet supplies",
    ],
    "bath_kitchen": [
        "kohler faucet",
        "signature hardware",
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


def _scrape_city(city: str) -> list[dict]:
    """Corre TODAS las categorias/queries para una ciudad, secuencial
    dentro de la ciudad (respeta rate limit por ciudad) pero esta funcion
    en si corre en paralelo con las demas ciudades via ThreadPoolExecutor."""
    rows = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fetched_at = datetime.now(timezone.utc).isoformat()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for category, queries in QUERIES.items():
            section = CRAIGSLIST_SECTIONS[category]
            for query in queries:
                context = browser.new_context(user_agent=random.choice(USER_AGENTS))
                page = context.new_page()

                qs = urlencode({"query": query, "sort": "date"})
                url = f"https://{city}.craigslist.org/search/{section}?{qs}"

                try:
                    page.goto(url, timeout=20000, wait_until="domcontentloaded")
                    page.wait_for_timeout(4000)  # el listado se puebla via JS despues del load
                    html = page.content()
                except Exception as exc:  # noqa: BLE001
                    print(f"[craigslist] ERROR {city}/{category} ({query}): {exc}")
                    html = ""
                finally:
                    context.close()

                titles = RESULT_TITLE_RE.findall(html)
                prices = [int(p.replace(",", "")) for p in PRICE_RE.findall(html)]
                median_price = sorted(prices)[len(prices) // 2] if prices else None

                rows.append(
                    {
                        "category": category,
                        "period": today,
                        "source": "craigslist",
                        "channel_type": channel_type_for("craigslist"),
                        "fetched_at": fetched_at,
                        "naics_label": f"{query} ({city})",
                        "naics_code": None,
                        "metric": "active_listing_count",
                        "value": len(titles),
                        "geo": city,
                        "confidence": "med" if titles else "low",
                        "notes": f"query='{query}', city={city}, median_price_usd={median_price}",
                    }
                )
                print(f"[craigslist] OK {city}/{category} ({query}): {len(titles)} listings, median=${median_price}")
                time.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))

        browser.close()

    return rows


def run() -> int:
    db = sqlite_utils.Database(DB_PATH)
    table = db["comps_rotation"]

    inserted = 0

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_CITIES) as executor:
        futures = {executor.submit(_scrape_city, city): city for city in CITY_SUBDOMAINS}
        for future in as_completed(futures):
            city = futures[future]
            try:
                rows = future.result()
            except Exception as exc:  # noqa: BLE001
                print(f"[craigslist] CITY FAILED {city}: {exc}")
                continue

            for row in rows:
                table.upsert(row, pk=("category", "period", "source", "naics_label"), alter=True)
                inserted += 1

    print(f"[craigslist] inserted {inserted} rows into {DB_PATH}")
    return inserted


if __name__ == "__main__":
    run()
