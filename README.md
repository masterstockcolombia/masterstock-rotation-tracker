# masterstock-rotation-tracker

Base de datos propia de rotacion/sell-through de reventa US por categoria, para
decidir que pallet de liquidacion comprar con evidencia propia -- no con el
criterio del liquidador ni con research de terceros.

Contexto completo del plan: `52_Plan_Moat_Rotacion_Domestica_US_GitHub.md` en
el vault (`03_Operations_and_Logistics/04_Sourcing_Engine_Framework/`).

## Como funciona (patron "git scraping", Simon Willison)

No hay servidor de base de datos. `data/masterstock_resale.sqlite` vive en
este repo. Cada workflow de GitHub Actions corre en cron, agrega filas nuevas,
y hace `git commit` solo si el archivo cambio. **El historial de git es la
base de datos versionada** -- gratis, propia, con timestamp de cada snapshot.

## Bug real de calidad de datos: Craigslist sin seccion contaminaba precios (encontrado y corregido 2026-09-03)

Buscar en `sss` (all-for-sale, sin seccion) hace match de texto libre contra
TODAS las categorias de Craigslist -- un query como "bose speaker" devolvia,
junto con parlantes reales, una **Chevrolet Silverado a $43,230** porque su
descripcion mencionaba "Bose sound system" de fabrica. Ese unico outlier
inflaba la mediana de precio de toda la categoria electronics_appliance.

Fix: `CRAIGSLIST_SECTIONS` mapea cada categoria interna a su seccion real de
Craigslist (`ela` electronics, `cla` clothing, `fua` furniture, `tag` toys,
`sga` general) -- verificado uno por uno contra el `<title>` real de cada
pagina antes de usarlo (un primer intento con `sya` resultó ser "computers",
no "clothing" como se asumia). Con la seccion correcta, el mismo query
"bose speaker" en Atlanta paso de precios $40-49,230 a $15-1,225 (limpio).

**Leccion para cualquier scraper de busqueda de texto libre**: nunca asumir
que un termino de marca esta acotado a la categoria que uno espera --
verificar contra la seccion/categoria real de la plataforma, no confiar en
que el query string por si solo filtra bien.

## Bug de clasificacion: LEGO/Hot Wheels/Barbie estaban mal categorizados como "deportes" (encontrado y corregido 2026-09-03)

Al ampliar categorias, LEGO/Hot Wheels/Barbie/Squishmallow/Nerf se pusieron
bajo `sporting_hobby` en vez de `toys` -- error conceptual, no tecnico. Se
detecto probando la seccion real de Craigslist "sporting goods" (`spo`,
encontrada en esta misma ronda) con esos terminos: **0 resultados** en las
4 marcas, contra 32-60 resultados con terminos deportivos reales (golf
clubs, weight bench, bicycle). Confirma que la seccion funciona bien --
el problema era la categoria asignada a las marcas, no el codigo de
seccion. Movidas a `toys` en las 3 fuentes (Craigslist, FB Marketplace,
Google Trends); `sporting_hobby` ahora usa marcas/equipos deportivos
reales + la seccion dedicada `spo` en vez de `sga` (general).

## Multiples queries por categoria (ampliado 2026-09-03)

Cada categoria puede tener varias marcas/terminos de busqueda (ej.
`apparel_footwear` corre Champion, Hanes, Wrangler, Adidas, Nike por
separado), validadas contra `35_Formula_del_Pallet_Ganador` del vault.
Esto requirio **ampliar la clave primaria** de la tabla de
`(category, period, source)` a `(category, period, source, naics_label)`
-- sin esto, dos marcas de la misma categoria en el mismo dia se pisaban
entre si via upsert (el campo `naics_label` guarda el termino/marca de
cada fila). Migracion aplicada sobre los datos existentes sin perder
ninguna fila (verificado: 0 colisiones bajo la PK nueva antes de migrar).

Si clonas este repo desde antes de esa fecha, tu `data/masterstock_resale.sqlite`
local tiene la PK vieja -- correr los scrapers actualizados sobre esa copia
puede fallar o comportarse distinto. Traé la version mas reciente
(`git pull`) en vez de mezclar.

## Trazabilidad: `channel_type`

Con 8 fuentes en la misma tabla `comps_rotation`, la columna `source` (nombre
de la fuente) no alcanza para analizar con criterio -- mezclar
`retail_sales_monthly` (Census, mide TODO USA) con `sold_count_snapshot`
(eBay, una busqueda puntual) como si fueran comparables lleva a
conclusiones falsas. Cada fila trae `channel_type`, uno de:

| channel_type | Que es | Fuentes |
|---|---|---|
| `macro_gov` | Dato oficial de gobierno, mide la industria retail completa, sin riesgo de anti-bot | Census MARTS, BLS CPI |
| `resale_marketplace` | Listing/venta real en un canal de reventa, riesgo de anti-bot variable | eBay, Facebook Marketplace, Craigslist |
| `search_interest` | Interes/busqueda, NO transaccion confirmada -- proxy de atencion | Google Trends |
| `import_data` | Dato de aduana/bill-of-lading, proxy de oferta futura | ImportGenius (Fase 3, no implementado) |

Definido en `scrapers/schema.py` -- toda fuente nueva debe registrarse ahi
antes de escribir a la tabla (falla ruidoso con `KeyError` si no, a
proposito, para no dejar filas mal clasificadas).

## Categorias: bug real encontrado -- nombres no coinciden entre fuentes

`category` NO es directamente comparable entre fuentes sin pasar por
`canonical_category()` (`scrapers/schema.py`). Ejemplo real detectado
2026-09-03: `bls_cpi` guarda `electronics` y `appliances` como dos series
CPI separadas (son indices de precio distintos, fusionarlos en el scraper
perderia precision real), mientras Census/eBay/Facebook/Craigslist/Google
Trends usan la categoria fusionada `electronics_appliance`. Un `JOIN`/query
directo comparando `category` entre `bls_cpi` y cualquier otra fuente para
electronica **devuelve 0 filas sin ningun error** -- el tipo de bug mas
peligroso porque no avisa que existe.

`canonical_category(category)` traduce las categorias de fuente a su
categoria canonica para cruce. Usar SIEMPRE esta funcion al comparar
`category` entre fuentes distintas, nunca comparar el string crudo.

```python
from scrapers.schema import canonical_category
# mal: row_bls['category'] == row_fb['category']  -- silenciosamente False
# bien:
canonical_category(row_bls['category']) == canonical_category(row_fb['category'])
```

## Periodos: granularidad distinta por channel_type, no un bug

`period` no tiene el mismo formato en toda la tabla, y es intencional --
refleja la frecuencia real de publicacion de cada fuente:

| channel_type | Formato de `period` | Granularidad |
|---|---|---|
| `macro_gov` (Census, BLS) | `YYYY-MM` | Mensual |
| `resale_marketplace`, `search_interest` | `YYYY-MM-DD` | Diaria |

No normalizar todo a mensual -- perderia la resolucion diaria real de las
fuentes que si la tienen. `PERIOD_GRANULARITY` en `scrapers/schema.py`
documenta esto para consulta rapida al escribir queries que agrupen o
comparen fechas entre fuentes de distinto channel_type.

## Honestidad sobre que fuentes son "gratis y listo" vs "gratis + mantenimiento"

| Fuente | channel_type | Costo en dinero | Costo en tiempo humano | Riesgo de romperse |
|---|---|---|---|---|
| Census MARTS + BLS CPI | macro_gov | $0 para siempre | Cero despues de configurar | Ninguno (API gubernamental) |
| Google Trends (trendspy) | search_interest | $0 | Cero -- 5/5 categorias OK en test real | Bajo (rate limit mitigado con `request_delay`) |
| Craigslist | resale_marketplace | $0 | Cero -- 5/5 OK, sin anti-bot detectado | Bajo, pero renderiza via JS (requiere espera fija de Playwright, no solo domcontentloaded) |
| Facebook Marketplace | resale_marketplace | $0 | Bajo -- 4/5 OK en test real | Medio (formato de HTML varia entre corridas) |
| eBay | resale_marketplace | $0 | ~15-30 min/semana resolviendo CAPTCHA | Alto -- confirmado EMPIRICO, no solo teorico (ver abajo) |
| Mercari, OfferUp | -- | -- | -- | **Descartados, ver abajo** |

No confundir las ramas. `macro_gov` y `search_interest` son "configurar una
vez y olvidar". `resale_marketplace` va de "sin fricción" (Craigslist,
Facebook) a "requiere atencion humana" (eBay) segun la plataforma -- no
generalizar de una a otra.

### Mercari y OfferUp: descartados con evidencia, no por pereza (2026-09-03)

- **Mercari US**: la pagina de busqueda es una SPA (Next.js) que NO carga
  resultados via URL con query string -- `?keyword=lego+set` devuelve la
  homepage generica con `"pageProps":{}` vacio en el JSON embebido. La
  busqueda real requiere simular interaccion con el input de UI (escribir +
  submit), no solo navegar a una URL. Mas caro de automatizar que el resto;
  no se justifico el esfuerzo para esta ronda.
- **OfferUp**: bloqueado por **Cloudflare Bot Management** (confirmado via
  `<script src="/cdn-cgi/challenge-platform/...">` en la respuesta, la firma
  inconfundible del challenge de Cloudflare). A diferencia de eBay/Facebook
  (bloqueo variable), este fue 100% consistente en el test -- no vale la
  pena reintentar sin proxies residenciales de pago, que es exactamente el
  costo recurrente que este proyecto evita a proposito.

### eBay: resultado real del primer test (2026-09-03)

Con Playwright + Chromium real (no solo requests HTTP, que fallan 4/4 con
403 inmediato), corriendo desde una maquina normal (no GitHub Actions):
**3 de 4 categorias bloqueadas con CAPTCHA real**, la 4ta sin bloqueo
explicito pero sin poder extraer datos (probable cambio de layout/selector
de eBay). Confirma el hallazgo del research: el bloqueo es real y no es
solo teoria de "IP de datacenter" -- pasa incluso desde IP residencial.
El scraper (`scrapers/ebay_sold.py`) queda commiteado y corriendo en cron
diario igual, porque el bloqueo NO es 100% consistente (una corrida a otra
cambia que categoria pasa) -- cuando pasa, el dato es real y se guarda con
`confidence=med`; cuando no, se guarda `confidence=blocked` en vez de fallar
silenciosamente, asi el historial de bloqueos es data en si misma (permite
ver si mejora/empeora con el tiempo).

### Facebook Marketplace: resultado real, notablemente mas confiable que eBay

Primer test (2026-09-03): **4 de 5 categorias con datos reales, 0 bloqueadas**
(vs. 1 de 4 de eBay). No mide "sold" -- Marketplace no expone historial de
vendidos publicamente -- mide **listings activos + precio mediano** por
categoria, como proxy de oferta y densidad de mercado local.

Hallazgo tecnico real durante la implementacion: el HTML de Facebook **varia
entre corridas** (A/B testing de su propio frontend) -- a veces sirve
`aria-label="<titulo>, US$<precio>, <ciudad>, publicacion <id>"` plano, a
veces JSON embebido mas rico (`listing_price.amount` numerico limpio,
`city`, `marketplace_listing_title`). El scraper (`scrapers/fb_marketplace.py`)
prueba el metodo JSON primero (mas preciso) y cae al aria-label si no
encuentra nada. Tambien se corrigio un falso positivo real: el detector de
bloqueo original disparaba con la palabra "checkpoint", que aparece como
substring en JS interno de Facebook (`"is_checkpointed":false`, rutas
`/checkpoint/block/`) incluso en paginas SIN bloqueo real -- el fix usa el
flag JSON explicito `"is_checkpointed":true` y frases completas de UI, no
substrings sueltos.

## Setup

```bash
git clone <este repo>
cd masterstock-rotation-tracker
pip install -r requirements.txt
cp .env.example .env
# Editar .env con tu CENSUS_API_KEY (gratis, registro en
# https://api.census.gov/data/key_signup.html -- la API ya NO responde
# sin key, confirmado 2026-09-03) y BLS_API_KEY si se suma ese scraper.

python -m scrapers.census_marts
python -m scrapers.bls_cpi
```

Esto crea/actualiza `data/masterstock_resale.sqlite` con las series de Census
(volumen de venta retail) y BLS (indice de precio/CPI) por categoria.

## Secrets de GitHub Actions

Ya configurados en este repo (Settings > Secrets and variables > Actions):
- `CENSUS_API_KEY` -- seteado
- `BLS_API_KEY` -- opcional, sin ella el scraper igual corre (25 queries/dia
  alcanza para 1 corrida semanal de 4 series)

## Cron activo

| Workflow | Cuando | Que hace |
|---|---|---|
| `census-marts.yml` | Lunes 09:00 UTC | Scrapea Census (11 categorias), upsert al sqlite, commit+push si cambio |
| `bls-cpi.yml` | Lunes 09:30 UTC | Scrapea BLS (10 series), upsert al sqlite, commit+push si cambio |
| `ebay-sold.yml` | Diario 14:00 UTC | Intenta sell-through de eBay (4 categorias); guarda `confidence=blocked` cuando falla en vez de nada |
| `fb-marketplace.yml` | Diario 15:00 UTC | Listings activos de Facebook Marketplace (5 categorias); mas confiable que eBay (ver abajo) |
| `google-trends.yml` | Diario 16:00 UTC | Interes de busqueda por categoria (5 categorias), sin friccion |
| `craigslist.yml` | Diario 17:00 UTC | Listings activos en NYC (5 categorias), sin friccion detectada |
| `publish-pages.yml` | Al detectar cambio en `data/masterstock_resale.sqlite` | Copia el sqlite a `docs/` para servirlo via GitHub Pages |

**Regla operativa: `docs/masterstock_resale.sqlite` es SOLO responsabilidad de
`publish-pages.yml`.** Ningun scraper ni humano deberia copiar/editar ese
archivo a mano -- confirmado en produccion que hacerlo rompe
`scripts/commit_and_push.sh` (deja el working tree sucio con un archivo que
el script no sabe manejar, y el reintento de rebase falla). Si necesitas
verificar que `docs/` esta al dia, mira que `publish-pages.yml` haya corrido
despues del ultimo cambio en `data/`, no lo copies vos mismo.

Todos los workflows de scraping usan `scripts/commit_and_push.sh`, que
resuelve el conflicto de merge binario automaticamente: si el rebase falla
porque OTRO workflow ya escribio al sqlite en la misma ventana (paso en
produccion 2026-09-03 con ebay-sold + fb-marketplace corriendo casi
simultaneos, uno de los dos perdio la carrera de push), el script aborta el
rebase, trae la version mas reciente, y RE-CORRE el scraper encima -- seguro
porque todo es `upsert` con clave natural, nunca duplica. Hasta 3 intentos.

## Consultar la data

```bash
sqlite3 data/masterstock_resale.sqlite
> select category, source, period, value from comps_rotation order by period desc limit 20;
```

O sin instalar nada: **https://masterstockcolombia.github.io/masterstock-rotation-tracker/**
-- Datasette Lite corre SQLite compilado a WebAssembly en el navegador,
consulta el `.sqlite` servido estatico desde GitHub Pages, cero backend,
cero costo.

## Nota tecnica: sqlite es binario, cuidado con conflictos de merge

Si dos procesos (local + cron, o dos crons) modifican `data/masterstock_resale.sqlite`
y divergen, git NO puede mergear el binario automaticamente -- va a marcar
conflicto. La resolucion correcta nunca es "elegir un lado a ciegas": como
todos los scrapers usan `upsert` con clave natural (`category`, `period`,
`source`), es seguro re-correr todos los scrapers sobre la version mas
reciente y regenerar el archivo, o verificar que una version sea superset
estricto de la otra antes de forzar. Ya paso una vez en el setup inicial
(2026-09-03) y se resolvio verificando que el local contenia 100% de las
filas del remoto antes de hacer force-push.

## Reglas de captura

Mismas reglas vinculantes que `05_Data_Moat_Captura_Sistematica.md` en el
vault: toda fila lleva `source`, `date`/`period`, `confidence`. Nada se
sobreescribe -- solo se agrega.
