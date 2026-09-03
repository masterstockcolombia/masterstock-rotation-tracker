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

## Honestidad sobre que fuentes son "gratis y listo" vs "gratis + mantenimiento"

| Fuente | Costo en dinero | Costo en tiempo humano | Riesgo de romperse |
|---|---|---|---|
| Census MARTS + BLS CPI | $0 para siempre | Cero despues de configurar | Ninguno (API gubernamental) |
| eBay / Facebook Marketplace | $0 | ~15-30 min/semana resolviendo CAPTCHA | Alto -- GitHub Actions corre desde IPs de Azure que Cloudflare trata con sospecha estructural. No prometer automatizacion 100% |

No confundir las dos ramas. La primera es "configurar una vez y olvidar". La
segunda es gratis en dinero, no en atencion.

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
| `census-marts.yml` | Lunes 09:00 UTC | Scrapea Census, upsert al sqlite, commit+push si cambio |
| `bls-cpi.yml` | Lunes 09:30 UTC | Scrapea BLS, upsert al sqlite, commit+push si cambio |
| `publish-pages.yml` | Al detectar cambio en `data/masterstock_resale.sqlite` | Copia el sqlite a `docs/` para servirlo via GitHub Pages |

Los dos scrapers corren desfasados 30 min para minimizar choque de push sobre
el mismo archivo binario; cada uno hace `git pull --rebase` antes de pushear
por si igual coinciden.

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
