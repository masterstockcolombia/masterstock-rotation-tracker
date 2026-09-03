"""
Esquema compartido y utilidades de trazabilidad para
data/masterstock_resale.sqlite.

Por que existe este modulo: con 4+ fuentes acumulando filas en la misma
tabla (`comps_rotation`), la columna `source` (nombre de la fuente, ej.
"ebay_sold") no alcanza para analizar con criterio -- mezclar
"retail_sales_monthly" (Census, un indice macro de TODO USA) con
"sold_count_snapshot" (eBay, un conteo de una busqueda puntual) como si
fueran comparables lleva a conclusiones falsas. `channel_type` clasifica
cada fuente por lo que estructuralmente ES (gobierno/macro vs canal de
reventa vs dato de importacion), para que el analisis pueda filtrar por
tipo antes de comparar numeros entre fuentes.

Toda fuente nueva DEBE registrar su CHANNEL_TYPE aca antes de escribir a
la tabla -- no hardcodear el string suelto en cada scraper.
"""
from __future__ import annotations

# Tipo de canal por fuente. Usar SIEMPRE estas constantes, no strings sueltos.
CHANNEL_TYPES = {
    "macro_gov": "Dato oficial de gobierno US, mide la industria retail completa (no reventa/liquidacion especifica). Ningun riesgo de anti-bot. Ejemplos: Census MARTS, BLS CPI.",
    "resale_marketplace": "Listing/venta real en un marketplace de reventa/segunda mano. Riesgo de anti-bot variable por plataforma. Ejemplos: eBay, Facebook Marketplace, Mercari, OfferUp, Craigslist.",
    "search_interest": "Senal de interes/busqueda, no de transaccion real -- proxy de atencion, no de venta confirmada. Ejemplo: Google Trends.",
    "import_data": "Dato transaccional de aduana/bill-of-lading, proxy de OFERTA FUTURA (que esta entrando al pais) mas que de demanda de reventa. Ejemplo: ImportGenius (Fase 3, no implementado aun).",
}

SOURCE_TO_CHANNEL = {
    "census_marts": "macro_gov",
    "bls_cpi": "macro_gov",
    "ebay_sold": "resale_marketplace",
    "fb_marketplace": "resale_marketplace",
    "mercari": "resale_marketplace",
    "offerup": "resale_marketplace",
    "craigslist": "resale_marketplace",
    "google_trends": "search_interest",
}


def channel_type_for(source: str) -> str:
    """Devuelve el channel_type para una fuente conocida. Falla ruidoso
    (no silencioso) si alguien agrega una fuente nueva sin registrarla
    arriba -- mejor un KeyError en desarrollo que una fila mal clasificada
    en produccion."""
    return SOURCE_TO_CHANNEL[source]
