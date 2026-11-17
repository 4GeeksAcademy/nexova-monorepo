#!/usr/bin/env bash
# =============================================================================
# Prueba reproducible de infra/server/bootstrap.sh en un "servidor" Ubuntu
# 24.04 desechable (contenedor Docker) más un segundo contenedor "cliente"
# que sondea los puertos desde fuera, como haría un atacante en Internet.
#
#   ./infra/server/test/verify.sh
#
# Escribe la evidencia en:
#   docs/security/evidence/before/server-baseline.txt
#   docs/security/evidence/after/server-hardened.txt
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$(cd "$HERE/.." && pwd)"
REPO_ROOT="$(cd "$SERVER_DIR/../.." && pwd)"
BEFORE_FILE="$REPO_ROOT/docs/security/evidence/before/server-baseline.txt"
AFTER_FILE="$REPO_ROOT/docs/security/evidence/after/server-hardened.txt"

IMAGE="nexova-server-test"
NET="nexova-hardening-net"
SRV="nexova-srv"        # el "VPS"
CLI="nexova-probe"      # cliente externo
SSH_OPTS="-o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -o LogLevel=ERROR"
# Puertos a sondear desde fuera: los permitidos y los internos del stack.
PROBE_PORTS=(22 80 443 3000 3001 5555 6333 6379 8000 8100)

mkdir -p "$(dirname "$BEFORE_FILE")" "$(dirname "$AFTER_FILE")"

cleanup() {
  docker rm -f "$SRV" "$CLI" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

section() { printf '\n==================== %s ====================\n' "$*"; }

# Ejecuta un comando en el servidor, mostrando el comando y su exit code.
srv() {
  printf '\n$ %s\n' "$*"
  local rc=0
  docker exec "$SRV" bash -c "$*" 2>&1 || rc=$?
  ((rc == 0)) || printf '(exit %d)\n' "$rc"
}

# Sondea los puertos del servidor desde el contenedor cliente.
probe_ports() {
  printf '\n$ (desde %s) nc -z -w 3 %s <puerto>\n' "$CLI" "$SRV"
  local p
  for p in "${PROBE_PORTS[@]}"; do
    if docker exec "$CLI" nc -z -w 3 "$SRV" "$p" >/dev/null 2>&1; then
      printf '  %-5s ABIERTO (alcanzable desde fuera)\n' "$p"
    else
      printf '  %-5s bloqueado/cerrado\n' "$p"
    fi
  done
}

# Simula el stack: listeners en todos los puertos sondeados (como si Redis,
# Qdrant, la API, etc. escucharan en 0.0.0.0). 22 ya lo atiende sshd.
start_listeners() {
  local p
  for p in "${PROBE_PORTS[@]}"; do
    [[ "$p" == 22 ]] && continue
    docker exec -d "$SRV" nc -lk "$p"
  done
  sleep 1
}

# Huella del estado para comprobar la idempotencia.
snapshot() {
  docker exec "$SRV" bash -c '
    sshd -T 2>/dev/null | sort
    ufw status verbose
    stat -c "%U:%G %a %n" /srv/nexova /srv/nexova/* /srv/nexova/config/.env
    sha256sum /etc/sudoers.d/90-nexova-deploy /etc/ssh/sshd_config.d/10-nexova.conf /home/deploy/.ssh/authorized_keys
    getent passwd deploy; id deploy'
}

# ---------------------------------------------------------------------------
# Preparación
# ---------------------------------------------------------------------------
cleanup
echo "[verify] construyendo imagen $IMAGE ..."
docker build -q -t "$IMAGE" "$HERE" >/dev/null
docker network create "$NET" >/dev/null
docker run -d --name "$SRV" --hostname "$SRV" --network "$NET" \
  --cap-add NET_ADMIN \
  -v "$SERVER_DIR:/opt/nexova-server:ro" \
  "$IMAGE" >/dev/null
docker run -d --name "$CLI" --hostname "$CLI" --network "$NET" "$IMAGE" >/dev/null

# sshd con la configuración por defecto de Ubuntu (sin systemd en el contenedor).
docker exec "$SRV" /usr/sbin/sshd
# Clave de prueba generada aquí: la usaremos para root (ANTES) y deploy (DESPUÉS).
docker exec "$SRV" bash -c 'ssh-keygen -q -t ed25519 -N "" -C verify@nexova-test -f /root/testkey
  install -d -m 0700 /root/.ssh && cp /root/testkey.pub /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys'
start_listeners

HEADER="$(printf 'Nexova · evidencia de endurecimiento del servidor (OWASP A05)\nGenerado: %s\nImagen: %s (%s)\nDocker: %s\nScript: infra/server/test/verify.sh' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$IMAGE" \
  "$(docker exec "$SRV" bash -c '. /etc/os-release; echo $PRETTY_NAME')" \
  "$(docker version --format '{{.Server.Version}}')")"

# ---------------------------------------------------------------------------
# ANTES
# ---------------------------------------------------------------------------
{
  echo "$HEADER"
  section "ANTES: Ubuntu 24.04 recién instalado, sin endurecer"
  srv 'whoami; id root'
  srv "sshd -T | grep -Ei '^(permitrootlogin|passwordauthentication|kbdinteractiveauthentication|allowusers|maxauthtries|x11forwarding) '"
  echo "  -> por defecto: root puede entrar con clave (without-password = prohibit-password),"
  echo "     se aceptan contraseñas y no hay lista de usuarios permitidos."
  srv 'ls /etc/ssh/sshd_config.d/'
  srv "ssh $SSH_OPTS -i /root/testkey root@localhost 'echo LOGIN_ROOT_OK: \$(whoami)@\$(hostname)'"
  echo "  -> root entra por SSH (riesgo: acceso directo a la cuenta más privilegiada)."
  srv "ssh $SSH_OPTS -o PubkeyAuthentication=no -o PreferredAuthentications=password,keyboard-interactive root@localhost true"
  echo "  -> el servidor ofrece 'password' como método de autenticación (fuerza bruta posible)."
  srv 'id deploy'
  srv 'ufw status verbose'
  srv 'ls -ld /srv/nexova'
  section "ANTES: puertos alcanzables desde un cliente externo"
  echo "(el stack simulado escucha en 0.0.0.0 en todos los puertos internos)"
  probe_ports
} 2>&1 | tee "$BEFORE_FILE"

# ---------------------------------------------------------------------------
# bootstrap + DESPUÉS
# ---------------------------------------------------------------------------
{
  echo "$HEADER"
  section "Ejecución 1 de bootstrap.sh"
  srv 'DEPLOY_PUBKEY_FILE=/root/testkey.pub bash /opt/nexova-server/bootstrap.sh'

  section "DESPUÉS: configuración efectiva de sshd"
  srv 'cat /etc/ssh/sshd_config.d/10-nexova.conf'
  srv "sshd -T | grep -Ei '^(permitrootlogin|passwordauthentication|kbdinteractiveauthentication|allowusers|maxauthtries|x11forwarding|authenticationmethods) '"

  section "DESPUÉS: login de root rechazado (aunque tiene una clave válida)"
  srv "ssh $SSH_OPTS -i /root/testkey root@localhost 'echo LOGIN_ROOT_OK'"
  srv "ssh $SSH_OPTS -o PubkeyAuthentication=no -o PreferredAuthentications=password,keyboard-interactive deploy@localhost true"
  echo "  -> ya solo se ofrece 'publickey': las contraseñas no son un método válido."

  section "DESPUÉS: login de deploy con la clave generada en la prueba"
  srv "ssh $SSH_OPTS -i /root/testkey deploy@localhost 'echo LOGIN_DEPLOY_OK: \$(whoami)@\$(hostname); id'"
  srv 'getent shadow deploy | cut -d: -f1,2'
  echo "  -> '*' = sin contraseña utilizable."

  section "DESPUÉS: sudo limitado de deploy"
  srv 'cat /etc/sudoers.d/90-nexova-deploy'
  srv "ssh $SSH_OPTS -i /root/testkey deploy@localhost 'sudo -n /bin/bash -c id'"
  echo "  -> deploy NO puede obtener una shell de root."
  srv "ssh $SSH_OPTS -i /root/testkey deploy@localhost 'sudo -n /usr/bin/docker compose -f /tmp/otro.yml up -d'"
  echo "  -> ni ejecutar docker compose con argumentos distintos de los permitidos."
  srv "ssh $SSH_OPTS -i /root/testkey deploy@localhost 'sudo -n -l' | sed -n '/may run/,\$p'"

  section "DESPUÉS: firewall"
  srv 'ufw status verbose'
  srv 'ufw show added'
  srv 'iptables -S ufw-user-input'

  section "DESPUÉS: puertos alcanzables desde un cliente externo"
  echo "(los mismos listeners siguen escuchando en 0.0.0.0; ahora los filtra ufw)"
  probe_ports

  section "DESPUÉS: permisos de /srv/nexova"
  srv "stat -c '%U:%G %a %n' /srv/nexova /srv/nexova/app /srv/nexova/logs /srv/nexova/config /srv/nexova/config/.env"
  srv "ssh $SSH_OPTS -i /root/testkey deploy@localhost 'cat /srv/nexova/config/.env >/dev/null && echo deploy puede LEER .env; echo x >> /srv/nexova/config/.env'"
  echo "  -> deploy lee la configuración pero no puede modificarla."

  section "Ejecución 2 de bootstrap.sh (idempotencia)"
  snapshot >"${TMPDIR:-/tmp}/nexova-snap1.txt" 2>&1
  srv 'DEPLOY_PUBKEY_FILE=/root/testkey.pub bash /opt/nexova-server/bootstrap.sh'
  snapshot >"${TMPDIR:-/tmp}/nexova-snap2.txt" 2>&1
  printf '\n$ diff <estado tras ejecución 1> <estado tras ejecución 2>\n'
  if diff "${TMPDIR:-/tmp}/nexova-snap1.txt" "${TMPDIR:-/tmp}/nexova-snap2.txt"; then
    echo "  -> SIN DIFERENCIAS: sshd -T, ufw, permisos, sudoers, authorized_keys y usuario idénticos."
  else
    echo "  -> ¡DIFERENCIAS! bootstrap.sh no es idempotente."
  fi
  srv 'grep -c verify@nexova-test /home/deploy/.ssh/authorized_keys'
  echo "  -> la clave no se duplica."
  rm -f "${TMPDIR:-/tmp}/nexova-snap1.txt" "${TMPDIR:-/tmp}/nexova-snap2.txt"
} 2>&1 | tee "$AFTER_FILE"

echo
echo "[verify] evidencia escrita en:"
echo "  ${BEFORE_FILE#"$REPO_ROOT/"}"
echo "  ${AFTER_FILE#"$REPO_ROOT/"}"
