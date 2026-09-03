"""
Google Trends -- interes de busqueda por categoria, via trendspy (el
sucesor activo de pytrends, que esta muerto/archivado desde abr-2025).

HONESTIDAD (ver schema.py): esto es channel_type="search_interest", NO
"resale_marketplace". Mide cuanta gente BUSCA un termino en Google, no
cuanto se vende. Es una senal adelantada de interes/atencion -- util como
proxy de demanda futura, pero nunca se debe leer como venta confirmada.

Resultado real del primer test (2026-09-03): funciono sin friccion, sin
login, sin bloqueo -- 93 dias de historico diario en una sola llamada.
La fuente mas limpia de las 4 nuevas evaluadas en esa ronda.

Ampliado 2026-09-03 (pedido de mas volumen): antes 1 termino generico
por categoria. Ahora multiples marcas reales por categoria, igual que
Craigslist/Facebook Marketplace -- Google Trends soporta hasta 5 terminos
por llamada `interest_over_time`, asi que se agrupan en tandas de 5 en
vez de 1 query = 1 llamada (mas eficiente en cuota).

Uso:
    python -m scrapers.google_trends
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import sqlite_utils
from trendspy import Trends

from scrapers.schema import channel_type_for

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "masterstock_resale.sqlite"

# Mismas marcas reales usadas en craigslist.py / fb_marketplace.py, para
# poder cruzar interes de busqueda vs. oferta real de la misma marca en
# el analisis posterior. category se repite -- multiples queries por
# categoria, agrupadas en tandas de 5 (limite de la API de Trends).
QUERIES = {
    "apparel_footwear": [
        "champion hoodie", "hanes t shirt", "wrangler jeans",
        "adidas hoodie", "nike shoes", "skechers shoes", "new balance shoes",
    ],
    "sporting_hobby": [
        "lego set", "hot wheels", "barbie doll", "squishmallow", "nerf gun",
    ],
    "electronics_appliance": [
        "jbl speaker", "bose speaker", "beats headphones",
        "ge appliance", "whirlpool appliance",
    ],
    "furniture_home": [
        "sectional couch", "recliner chair", "ashley furniture",
    ],
    "toys": [
        "hasbro board game", "mattel toy",
    ],
    "home_decor": [
        "home decor", "world market",
    ],
    "pet_supplies": [
        "pet supplies", "dog crate",
    ],
    "bath_kitchen": [
        "kohler faucet", "kitchen fixture",
    ],
}

TIMEFRAME = "today 3-m"  # 90 dias, suficiente para ver estacionalidad de corto plazo
BATCH_SIZE = 5  # limite real de la API de Google Trends por llamada


def _batched(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def run() -> int:
    db = sqlite_utils.Database(DB_PATH)
    table = db["comps_rotation"]

    inserted = 0
    fetched_at = datetime.now(timezone.utc).isoformat()
    tr = Trends(request_delay=3.0)

    for category, queries in QUERIES.items():
        for batch in _batched(queries, BATCH_SIZE):
            try:
                df = tr.interest_over_time(batch, geo="US", timeframe=TIMEFRAME)
            except Exception as exc:  # noqa: BLE001 -- trendspy puede lanzar varios tipos segun el fallo de Google
                print(f"[google_trends] SKIP {category} batch={batch}: {exc}")
                continue

            if df is None or df.empty:
                print(f"[google_trends] EMPTY {category} batch={batch}")
                continue

            for query in batch:
                if query not in df.columns:
                    continue
                for ts, value in df[query].items():
                    if value is None:
                        continue
                    period = ts.strftime("%Y-%m-%d")
                    table.upsert(
                        {
                            "category": category,
                            "period": period,
                            "source": "google_trends",
                            "channel_type": channel_type_for("google_trends"),
                            "fetched_at": fetched_at,
                            "naics_label": query,
                            "naics_code": None,
                            "metric": "search_interest_index_0_100",
                            "value": int(value),
                            "geo": "US",
                            "confidence": "med",
                            "notes": f"Google Trends interest_over_time, query='{query}', escala relativa 0-100 (no volumen absoluto)",
                        },
                        pk=("category", "period", "source", "naics_label"),
                        alter=True,
                    )
                    inserted += 1

    print(f"[google_trends] inserted {inserted} rows into {DB_PATH}")
    return inserted


if __name__ == "__main__":
    run()
