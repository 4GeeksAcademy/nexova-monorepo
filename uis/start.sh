#!/bin/sh
# Arranca website (3000) y backoffice (3001).
#
# UIS_MODE=production (por defecto): `next build` + `next start`. Nunca se expone
#   el servidor de desarrollo (HMR, overlay de errores con código fuente y el
#   endpoint MCP del dev server de Next) — auditoría OWASP, C5.
# UIS_MODE=development: `next dev` con hot reload (lo fija docker-compose.dev.yml).
set -e

MODE="${UIS_MODE:-production}"

run_app() {  # run_app <dir> <puerto>
  cd "$1"
  if [ "$MODE" = "development" ]; then
    exec ./node_modules/.bin/next dev --webpack -p "$2" -H 0.0.0.0
  fi
  ./node_modules/.bin/next build
  exec ./node_modules/.bin/next start -p "$2" -H 0.0.0.0
}

trap 'kill -TERM $website_pid $backoffice_pid 2>/dev/null' TERM INT

(run_app /app/uis/website 3000) &
website_pid=$!

(run_app /app/uis/backoffice 3001) &
backoffice_pid=$!

wait $website_pid $backoffice_pid
