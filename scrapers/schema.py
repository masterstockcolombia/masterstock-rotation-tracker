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


# ---------------------------------------------------------------------------
# Mapeo de categoria canonica -- resuelve el hueco real encontrado
# 2026-09-03: bls_cpi usa "electronics" y "appliances" como series CPI
# separadas (son indices de precio distintos, fusionarlos perderia
# precision real), mientras el resto de las fuentes usa la categoria
# fusionada "electronics_appliance". Sin este mapeo, un JOIN directo por
# `category` entre bls_cpi y cualquier otra fuente para esa categoria
# falla EN SILENCIO (0 filas, sin error) -- el bug mas peligroso porque
# no avisa que existe.
#
# Regla: NO renombrar las categorias en las tablas fuente (perderia
# informacion real de BLS). En su lugar, este mapeo declara que
# categorias "de fuente" pertenecen a que "categoria canonica" para
# analisis cruzado. Usar CANONICAL_CATEGORY.get(category, category) al
# leer, nunca comparar `category` crudo entre fuentes sin pasar por esto.
CANONICAL_CATEGORY = {
    "electronics": "electronics_appliance",  # bls_cpi
    "appliances": "electronics_appliance",  # bls_cpi
    "footwear": "apparel_footwear",  # bls_cpi (footwear separado de apparel)
}


def canonical_category(category: str) -> str:
    """Traduce una categoria especifica de fuente a su categoria canonica
    para cruce entre fuentes. Categorias ya canonicas (o sin mapeo
    conocido, ej. las macro sin equivalente en marketplace: food_at_home,
    housekeeping_supplies, motor_vehicles, shelter) devuelven el mismo
    valor sin cambios."""
    return CANONICAL_CATEGORY.get(category, category)
