"""
BLS CPI — indice de precios al consumidor por categoria, proxy de tendencia
de precio/demanda relativa.

Fuente: api.bls.gov/publicAPI/v2/timeseries/data (gobierno US, gratis, sin
scraping). Complementa a Census MARTS (que mide volumen de ventas) con
tendencia de PRECIOS por categoria -- util para detectar cuando una
categoria esta bajo presion de precio (liquidacion, exceso de oferta) vs.
subiendo (demanda fuerte).

Uso:
    python -m scrapers.bls_cpi

Requiere BLS_API_KEY en .env (gratis, registro en
https://data.bls.gov/registrationEngine/). Sin key: 25 queries/dia, 10 anos
de historico. Con key: 500 queries/dia, 20 anos de historico.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
import sqlite_utils
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "masterstock_resale.sqlite"

API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# Series CPI relevantes a categorias de liquidacion/overstock domestico.
# Codigo de serie BLS -> categoria interna de MasterStock.
SERIES = {
    "CUUR0000SEAE": "apparel_footwear",       # Apparel, US city average, NSA
    "CUUR0000SAF11": "food_at_home",           # Food at home (referencia macro)
    "CUUR0000SEHF01": "appliances",            # Major appliances
    "CUUR0000SERA": "electronics",             # Information technology / electronics
}


def run() -> int:
    api_key = os.getenv("BLS_API_KEY")
    db = sqlite_utils.Database(DB_PATH)
    table = db["comps_rotation"]

    inserted = 0
    fetched_at = datetime.now(timezone.utc).isoformat()

    payload: dict = {
        "seriesid": list(SERIES.keys()),
        "startyear": "2023",
        "endyear": str(datetime.now(timezone.utc).year),
    }
    if api_key:
        payload["registrationkey"] = api_key

    with httpx.Client() as client:
        resp = client.post(API_URL, json=payload, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()

    if data.get("status") != "REQUEST_SUCCEEDED":
        print(f"[bls_cpi] API error: {data.get('message')}")
        return 0

    for series in data["Results"]["series"]:
        series_id = series["seriesID"]
        category = SERIES.get(series_id, series_id)

        for point in series["data"]:
            if point.get("period") == "M13":  # annual average, skip
                continue
            period = f"{point['year']}-{point['period'].replace('M', '')}"
            table.upsert(
                {
                    "category": category,
                    "period": period,
                    "source": "bls_cpi",
                    "fetched_at": fetched_at,
                    "naics_label": series_id,
                    "naics_code": series_id,
                    "metric": "cpi_index",
                    "value": point.get("value"),
                    "geo": "US",
                    "confidence": "high",
                    "notes": "BLS CPI, not seasonally adjusted, proxy de tendencia de precio por categoria",
                },
                pk=("category", "period", "source"),
                alter=True,
            )
            inserted += 1

    print(f"[bls_cpi] inserted {inserted} rows into {DB_PATH}")
    return inserted


if __name__ == "__main__":
    run()
