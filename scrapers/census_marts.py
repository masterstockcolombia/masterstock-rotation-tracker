"""
Census MARTS/MRTS — estacionalidad macro de ventas retail US por categoria.

Fuente: api.census.gov/data/timeseries/eits/marts (gobierno US, gratis, sin
scraping, sin riesgo de bloqueo). Da series mensuales "seasonally adjusted"
y "not adjusted" de ventas retail por categoria NAICS.

No mide rotacion de reventa/liquidacion especifica -- mide el mercado retail
completo. Sirve como proxy de estacionalidad: si una categoria sube fuerte en
un mes a nivel de toda la industria US, es una senal (no una confirmacion) de
que la demanda de esa categoria esta subiendo, coherente con lo que se venda
en liquidacion/reventa de esa misma categoria.

Uso:
    python -m scrapers.census_marts

Requiere (opcional): CENSUS_API_KEY en .env -- sin clave el limite es
500 queries/dia por IP; con clave gratis (census.gov/data/key_signup.html)
el limite documentado sigue siendo 500/dia (algunas fuentes reportan mas,
no confirmado oficialmente -- no planear asumiendo el numero alto).
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

API_BASE = "https://api.census.gov/data/timeseries/eits/marts"

# Categorias NAICS relevantes a liquidacion/overstock domestico (alineadas a
# las categorias GREEN de 35_Formula_del_Pallet_Ganador en el vault).
# Codigo NAICS -> nombre legible, mapeado a categoria interna de MasterStock.
CATEGORIES = {
    "448": ("apparel_footwear", "Clothing and clothing accessories stores"),
    "452": ("general_merch", "General merchandise stores"),
    "44X72": ("retail_total", "Retail and food services sales, total"),
    "4471": ("beauty_health", "Health and personal care stores"),
}

DATA_TYPE = "SM"  # Sales - Monthly


def _fetch_category(client: httpx.Client, naics_code: str, api_key: str | None) -> list[dict]:
    params = {
        "get": "cell_value,time_slot_id,error_data",
        "for": "us:*",
        "time": "from 2023",
        "category_code": naics_code,
        "data_type_code": DATA_TYPE,
    }
    if api_key:
        params["key"] = api_key

    resp = client.get(API_BASE, params=params, timeout=30.0)
    resp.raise_for_status()
    rows = resp.json()
    if not rows or len(rows) < 2:
        return []

    header, *body = rows
    return [dict(zip(header, row)) for row in body]


def run() -> int:
    api_key = os.getenv("CENSUS_API_KEY")
    db = sqlite_utils.Database(DB_PATH)
    table = db["comps_rotation"]

    inserted = 0
    fetched_at = datetime.now(timezone.utc).isoformat()

    with httpx.Client() as client:
        for naics_code, (category, label) in CATEGORIES.items():
            try:
                rows = _fetch_category(client, naics_code, api_key)
            except httpx.HTTPError as exc:
                print(f"[census_marts] SKIP {category} ({naics_code}): {exc}")
                continue

            for row in rows:
                table.insert(
                    {
                        "fetched_at": fetched_at,
                        "category": category,
                        "naics_label": label,
                        "naics_code": naics_code,
                        "metric": "retail_sales_monthly",
                        "value": row.get("cell_value"),
                        "period": row.get("time"),
                        "geo": "US",
                        "source": "census_marts",
                        "confidence": "high",
                        "notes": "seasonally-adjusted monthly retail sales, macro proxy",
                    },
                    pk="id",
                    alter=True,
                )
                inserted += 1

    print(f"[census_marts] inserted {inserted} rows into {DB_PATH}")
    return inserted


if __name__ == "__main__":
    run()
