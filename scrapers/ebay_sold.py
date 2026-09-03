"""
eBay sold listings -- sell-through real por categoria, via scraping del
truco de URL LH_Complete=1&LH_Sold=1 (filtra listings vendidos).

HONESTIDAD (ver README): esto NO es "configurar y olvidar" como Census/BLS.
Un test con httpx simple (sin navegador) fue bloqueado 4/4 veces con 403
inmediato -- eBay exige un navegador real, no solo un user-agent falso.
Este scraper usa Playwright (Chromium real, headless) para tener alguna
chance, pero incluso asi eBay tiene anti-bot activo, y GitHub Actions corre
desde IPs de Azure conocidas y en lista negra -- el bloqueo es mas probable
corriendo desde ahi que desde una IP residencial.

Cuando esto se bloquea seguido, el fallback es correr manualmente desde una
maquina normal (no el runner de Actions) o usar el patron Docker+noVNC de
ai-marketplace-monitor para resolver un challenge una vez por sesion.

Uso:
    python -m scrapers.ebay_sold
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

SEARCH_URL = "https://www.ebay.com/sch/i.html"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
]

# Categorias GREEN/CONDICIONAL de 35_Formula_del_Pallet_Ganador -- termino
# de busqueda representativo por categoria (no exhaustivo, muestra).
QUERIES = {
    "apparel_footwear": "champion hoodie mens",
    "sporting_hobby": "lego set new",
    "electronics_appliance": "jbl bluetooth speaker",
    "toys": "hot wheels case",
}

MIN_DELAY_SECONDS = 4
MAX_DELAY_SECONDS = 9


def _looks_blocked(html: str) -> bool:
    # "captcha" solo (sin las otras frases exactas, que resultaron ser
    # falsos negativos en test real 2026-09-03 -- eBay varia el texto del
    # challenge entre corridas) es el marcador mas confiable observado.
    markers = ("captcha", "pardon our interruption", "unusual traffic", "verify you are a human", "robot check")
    lowered = html.lower()
    return any(m in lowered for m in markers)


def _count_sold_items(html: str) -> int | None:
    matches = re.findall(r's-item__title', html)
    if not matches:
        return None
    return len(matches)


def run() -> int:
    db = sqlite_utils.Database(DB_PATH)
    table = db["comps_rotation"]

    inserted = 0
    blocked = 0
    fetched_at = datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for category, query in QUERIES.items():
            context = browser.new_context(user_agent=random.choice(USER_AGENTS))
            page = context.new_page()

            qs = urlencode({"_nkw": query, "LH_Sold": "1", "LH_Complete": "1", "_ipg": "60"})
            url = f"{SEARCH_URL}?{qs}"

            try:
                page.goto(url, timeout=20000, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)
                html = page.content()
                status_blocked = _looks_blocked(html)
            except Exception as exc:  # noqa: BLE001 -- cualquier fallo de red/timeout cuenta como bloqueo
                print(f"[ebay_sold] ERROR {category} ({query}): {exc}")
                html = ""
                status_blocked = True
            finally:
                context.close()

            if status_blocked or not html:
                blocked += 1
                print(f"[ebay_sold] BLOCKED {category} ({query})")
                table.upsert(
                    {
                        "category": category,
                        "period": today,
                        "source": "ebay_sold",
                        "channel_type": channel_type_for("ebay_sold"),
                        "fetched_at": fetched_at,
                        "naics_label": query,
                        "naics_code": None,
                        "metric": "sold_count_snapshot",
                        "value": None,
                        "geo": "US",
                        "confidence": "blocked",
                        "notes": "eBay blocked/captcha -- requiere fallback manual o corrida desde IP no-datacenter",
                    },
                    pk=("category", "period", "source"),
                    alter=True,
                )
            else:
                count = _count_sold_items(html)
                table.upsert(
                    {
                        "category": category,
                        "period": today,
                        "source": "ebay_sold",
                        "channel_type": channel_type_for("ebay_sold"),
                        "fetched_at": fetched_at,
                        "naics_label": query,
                        "naics_code": None,
                        "metric": "sold_count_snapshot",
                        "value": count,
                        "geo": "US",
                        "confidence": "med" if count else "low",
                        "notes": f"conteo de listings con marcador sold en primera pagina de resultados, query='{query}'",
                    },
                    pk=("category", "period", "source"),
                    alter=True,
                )
                inserted += 1

            time.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))

        browser.close()

    print(f"[ebay_sold] inserted {inserted} rows, {blocked} blocked, into {DB_PATH}")
    return inserted


if __name__ == "__main__":
    run()
