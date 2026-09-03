#!/usr/bin/env bash
# Commit + push robusto a conflictos de merge binario en data/masterstock_resale.sqlite.
#
# Problema real (visto en produccion 2026-09-03): dos workflows disparados
# en la misma ventana de minutos (ebay-sold + fb-marketplace) generaron un
# conflicto real de merge sobre el sqlite -- "git pull --rebase" NO resuelve
# un binario divergente, git no sabe mergear bytes. Uno de los dos push
# gano la carrera, el otro murio con exit 1 y perdio su commit (el runner
# es efimero, no queda rastro salvo re-correr el cron al dia siguiente).
#
# Fix: si el rebase falla por el sqlite, no es un error real -- es que
# otro proceso ya escribio datos nuevos. Como todos los scrapers son
# upsert-idempotentes por clave natural (category, period, source), la
# resolucion correcta es: abortar el rebase, re-correr el scraper que
# llamo a este script sobre la version fresca de origin/main (que ya trae
# los datos del otro workflow), y reintentar el commit+push. Maximo 3
# intentos con backoff corto.
#
# Uso: commit_and_push.sh "<scraper module, ej scrapers.fb_marketplace>" "<commit message>"

set -euo pipefail

SCRAPER_MODULE="$1"
COMMIT_MSG="$2"
MAX_ATTEMPTS=3

git config user.name "masterstock-rotation-bot"
git config user.email "actions@users.noreply.github.com"

for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
  echo "[commit_and_push] intento $attempt/$MAX_ATTEMPTS"

  git add data/masterstock_resale.sqlite
  if git diff --staged --quiet; then
    echo "[commit_and_push] nada que commitear, saliendo limpio"
    exit 0
  fi
  git commit -q -m "$COMMIT_MSG"

  if git pull --rebase origin main 2>/tmp/rebase_err.log; then
    if git push origin main; then
      echo "[commit_and_push] push exitoso en intento $attempt"
      exit 0
    fi
    echo "[commit_and_push] push fallo tras rebase limpio, reintentando"
  else
    echo "[commit_and_push] rebase con conflicto (esperado si otro workflow escribio antes) -- abortando y re-generando data"
    cat /tmp/rebase_err.log || true
    git rebase --abort

    # Traer la version mas reciente (con los datos del otro workflow ya
    # adentro) y re-correr ESTE scraper encima -- upsert no duplica nada
    # de lo que el otro workflow ya escribio, solo agrega/actualiza lo
    # propio.
    git reset --hard origin/main
    python -m "$SCRAPER_MODULE" || true
  fi

  sleep $((attempt * 5))
done

echo "[commit_and_push] agoto los $MAX_ATTEMPTS intentos sin poder pushear"
exit 1
