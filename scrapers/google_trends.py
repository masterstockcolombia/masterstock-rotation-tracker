"""
Google Trends -- interes de busqueda por categoria, via trendspy (el
sucesor activo de pytrends, que esta muerto/archivado desde abr-2025).

HONESTIDAD (ver schema.py): esto es channel_type="search_interest", NO
"resale_marketplace". Mide cuanta gente BUSCA un termino en Google, no
cuanto se vende. Es una senal adelantada de interes/atencion -- util como
proxy de demanda futura, pero nunca se debe leer como venta confirmada.

Resultado real del primer test (2026-09-03): funciono sin friccion, sin
login, sin bloqueo -- 93 dias de historico diario en una sola llamada.
La fuente mas limpia de las 4 nuevas evaluadas en esta ronda (junto con
mercari, offerup, craigslist).

Uso:
    python -m scrapers.google_trends
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import sqlite_utils
from trendspy import Trends

from scrapers.schema import channel_type_for

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "masterstock_resale.sqlite"

# Mismo mapeo categoria -> termino de busqueda representativo usado en
# ebay_sold.py / fb_marketplace.py, para poder cruzar interes vs. oferta
# real de la misma categoria en el analisis posterior.
QUERIES = {
    "apparel_footwear": "champion hoodie",
    "sporting_hobby": "lego set",
    "electronics_appliance": "jbl speaker",
    "toys": "hot wheels",
    "furniture_home": "sectional couch",
    "home_decor": "home decor",
    "pet_supplies": "pet supplies",
    "bath_kitchen": "kohler faucet",
}

TIMEFRAME = "today 3-m"  # 90 dias, suficiente para ver estacionalidad de corto plazo


def run() -> int:
    db = sqlite_utils.Database(DB_PATH)
    table = db["comps_rotation"]

    inserted = 0
    fetched_at = datetime.now(timezone.utc).isoformat()
    # request_delay: Google rate-limita (429) con llamadas consecutivas
    # rapidas -- confirmado en test real 2026-09-03, 2/5 categorias
    # fallaron sin delay. La propia libreria sugiere subir el delay.
    tr = Trends(request_delay=3.0)

    for category, query in QUERIES.items():
        try:
            df = tr.interest_over_time([query], geo="US", timeframe=TIMEFRAME)
        except Exception as exc:  # noqa: BLE001 -- trendspy puede lanzar varios tipos segun el fallo de Google
            print(f"[google_trends] SKIP {category} ({query}): {exc}")
            continue

        if df is None or df.empty:
            print(f"[google_trends] EMPTY {category} ({query})")
            continue

        for ts, row in df.iterrows():
            period = ts.strftime("%Y-%m-%d")
            value = row.get(query)
            if value is None:
                continue
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
