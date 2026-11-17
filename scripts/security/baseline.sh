#!/usr/bin/env bash
# Línea base de seguridad del stack docker-compose (auditoría OWASP Top 10).
#
# Uso:  scripts/security/baseline.sh before|after
# Escribe docs/security/evidence/<fase>/stack-baseline.txt. Solo lee: no
# arranca ni para servicios (levanta el stack antes con `docker compose up -d`).
#
# Comprueba:
#   1. Puertos publicados en el host por servicio (y en qué interfaz).
#   2. Usuario (uid) con el que corre el proceso principal de cada contenedor.
#   3. Acceso SIN credenciales desde el host a Redis (6379), Qdrant (6333) y Flower (5555).
#   4. Proceso de la API (¿--reload?) y de las UIs (¿next dev?).
#   5. Cabeceras de seguridad de la API.
set -uo pipefail

PHASE="${1:?Uso: $0 before|after}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="$ROOT/docs/security/evidence/$PHASE/stack-baseline.txt"
mkdir -p "$(dirname "$OUT")"
cd "$ROOT"

section() { printf '\n===== %s =====\n' "$1"; }

probe_http() {  # probe_http <url> → "HTTP <código>" o "cerrado" si no hay conexión
  local code
  code="$(curl -s -m 3 -o /dev/null -w '%{http_code}' "$1" 2>/dev/null)"
  if [ -z "$code" ] || [ "$code" = "000" ]; then echo "cerrado / sin respuesta"; else echo "HTTP $code"; fi
}

probe_tcp() {  # probe_tcp <puerto> <payload> → primera línea de la respuesta o "cerrado"
  local port="$1" payload="$2" reply
  reply="$(printf '%b' "$payload" | nc -w 2 127.0.0.1 "$port" 2>/dev/null | head -c 200 | tr -d '\r' | head -1)"
  if [ -n "$reply" ]; then echo "ABIERTO → $reply"; else echo "cerrado / sin respuesta"; fi
}

{
  echo "Línea base de seguridad — fase: $PHASE — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "Rama: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"

  section "1. Puertos publicados (docker compose ps)"
  docker compose ps --format 'table {{.Service}}\t{{.State}}\t{{.Ports}}' 2>/dev/null

  section "1b. Puertos declarados en la configuración (docker compose config)"
  docker compose config --format json 2>/dev/null | python3 "$ROOT/scripts/security/compose_ports.py"

  section "2. Usuario efectivo por contenedor (uid 0 = root)"
  echo "(usuario por defecto del contenedor; redis-server baja después a 'redis' — ver 2b)"
  for svc in $(docker compose ps --services --status running 2>/dev/null); do
    printf '%-10s %s\n' "$svc" "$(docker compose exec -T "$svc" sh -c 'echo "uid=$(id -u) user=$(id -un 2>/dev/null)"' 2>/dev/null || echo '(no se pudo ejecutar id)')"
  done

  section "2b. Usuario real del proceso principal (docker compose top)"
  docker compose top redis qdrant 2>/dev/null | awk 'NR>1 && /redis-server|\.\/qdrant/ {cmd=""; for (i=10; i<=NF; i++) cmd=cmd" "$i; print $1 ": usuario=" $3 " proceso=" cmd}'

  section "3. Acceso sin credenciales desde el host"
  echo "Redis  6379 (PING sin AUTH):     $(probe_tcp 6379 'PING\r\n')"
  echo "Qdrant 6333 (GET /collections):  $(probe_http http://127.0.0.1:6333/collections)"
  echo "Flower 5555 (GET / panel web):   $(probe_http http://127.0.0.1:5555/)"
  echo "API    8000 (GET /api/health):   $(probe_http http://127.0.0.1:8000/api/health)"
  echo "Escucha en 0.0.0.0 vs 127.0.0.1 (lsof del host):"
  lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | awk 'NR==1 || /:(3000|3001|5555|6333|6379|8000|8100) /' || true

  section "4. Modo de ejecución"
  docker compose exec -T api sh -c 'tr "\0" " " < /proc/1/cmdline; echo' 2>/dev/null | sed 's/^/api: /' || echo "api: (no está corriendo)"
  docker compose top uis 2>/dev/null | grep -oE "next (dev|start|build)[^$]*|next-server \(v[0-9.]+\)" | sort -u | sed 's/^/uis: /' || echo "uis: (no está corriendo)"
  docker compose exec -T uis printenv UIS_MODE 2>/dev/null | sed 's/^/uis: UIS_MODE=/' 

  section "5. Cabeceras de la API"
  curl -s -m 3 -D - -o /dev/null http://127.0.0.1:8000/api/health 2>/dev/null \
    | grep -iE '^(x-content-type-options|x-frame-options|content-security-policy|referrer-policy|server):' \
    || echo "(sin cabeceras de seguridad o API caída)"
  echo "/docs: $(curl -s -m 3 -o /dev/null -w 'HTTP %{http_code}' http://127.0.0.1:8000/docs 2>/dev/null)"
} 2>&1 | tee "$OUT"

echo
echo "Evidencia guardada en ${OUT#"$ROOT/"}"
