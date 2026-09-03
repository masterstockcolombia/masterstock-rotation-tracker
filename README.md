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
```

Esto crea/actualiza `data/masterstock_resale.sqlite` con las series de Census.

## Secrets de GitHub Actions

Para que el workflow corra en la nube, agregar en Settings > Secrets and
variables > Actions del repo:
- `CENSUS_API_KEY`

## Consultar la data

```bash
sqlite3 data/masterstock_resale.sqlite
> select category, period, value from comps_rotation order by period desc limit 20;
```

O (cuando este montado) via Datasette Lite en GitHub Pages -- consulta desde
el navegador, sin backend, gratis.

## Reglas de captura

Mismas reglas vinculantes que `05_Data_Moat_Captura_Sistematica.md` en el
vault: toda fila lleva `source`, `date`/`period`, `confidence`. Nada se
sobreescribe -- solo se agrega.
