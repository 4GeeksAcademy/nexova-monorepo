#!/usr/bin/env bash
# =============================================================================
# Nexova · Endurecimiento base de un servidor Ubuntu 24.04 (OWASP A05)
#
#   - Usuario de despliegue `deploy` sin contraseña, solo clave pública.
#   - sudo limitado a comandos concretos (sin shell de root).
#   - SSH: sin login de root, sin contraseñas, solo `deploy`.
#   - Layout /srv/nexova con dueños y modos explícitos.
#   - ufw: deny incoming por defecto; solo 22 (limitado), 80 y 443.
#
# Idempotente: ejecutarlo N veces deja el sistema en el mismo estado.
#
# Uso (como root):
#   DEPLOY_PUBKEY="ssh-ed25519 AAAA... yo@portatil" ./bootstrap.sh
#   DEPLOY_PUBKEY_FILE=/root/deploy.pub            ./bootstrap.sh
#
# Variables opcionales:
#   DEPLOY_USER       (deploy)       usuario de despliegue
#   NEXOVA_ROOT       (/srv/nexova)  raíz de la aplicación
#   SSH_PORT          (22)           puerto SSH a permitir en ufw
#   SKIP_APT          (0)            1 = no instalar paquetes que falten
# =============================================================================
set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-deploy}"
NEXOVA_ROOT="${NEXOVA_ROOT:-/srv/nexova}"
SSH_PORT="${SSH_PORT:-22}"
SKIP_APT="${SKIP_APT:-0}"
SSHD_DROPIN="/etc/ssh/sshd_config.d/10-nexova.conf"
SUDOERS_DROPIN="/etc/sudoers.d/90-nexova-deploy"
# Puertos internos que NUNCA deben abrirse al exterior (Redis, Qdrant, Flower,
# API, MCP, website, backoffice). Se eliminan si alguien los añadió a mano.
FORBIDDEN_PORTS=(6379 6333 5555 8000 8100 3000 3001)

log()  { printf '[bootstrap] %s\n' "$*"; }
fail() { printf '[bootstrap] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || fail "debe ejecutarse como root"

# ---------------------------------------------------------------------------
# 0. Paquetes necesarios (solo si faltan)
# ---------------------------------------------------------------------------
missing=()
command -v sshd  >/dev/null 2>&1 || [[ -x /usr/sbin/sshd ]] || missing+=(openssh-server)
command -v ufw   >/dev/null 2>&1 || missing+=(ufw)
command -v sudo  >/dev/null 2>&1 || missing+=(sudo)
if ((${#missing[@]})); then
  [[ "$SKIP_APT" == "1" ]] && fail "faltan paquetes: ${missing[*]}"
  log "instalando paquetes: ${missing[*]}"
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${missing[@]}" >/dev/null
fi

# ---------------------------------------------------------------------------
# 1. Usuario deploy (sin contraseña, solo clave pública)
# ---------------------------------------------------------------------------
if id "$DEPLOY_USER" >/dev/null 2>&1; then
  log "usuario '$DEPLOY_USER' ya existe"
else
  log "creando usuario '$DEPLOY_USER'"
  useradd --create-home --shell /bin/bash --user-group "$DEPLOY_USER"
fi
# '*' = ninguna contraseña válida (no se puede usar `su`/password), pero la
# cuenta no queda "locked" (con '!' algunas configs de sshd rechazan la clave).
usermod -p '*' "$DEPLOY_USER"

DEPLOY_HOME="$(getent passwd "$DEPLOY_USER" | cut -d: -f6)"
SSH_DIR="$DEPLOY_HOME/.ssh"
AUTH_KEYS="$SSH_DIR/authorized_keys"
install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 0700 "$SSH_DIR"
[[ -f "$AUTH_KEYS" ]] || install -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 0600 /dev/null "$AUTH_KEYS"

pubkey=""
if [[ -n "${DEPLOY_PUBKEY:-}" ]]; then
  pubkey="$DEPLOY_PUBKEY"
elif [[ -n "${DEPLOY_PUBKEY_FILE:-}" ]]; then
  [[ -r "$DEPLOY_PUBKEY_FILE" ]] || fail "no se puede leer DEPLOY_PUBKEY_FILE=$DEPLOY_PUBKEY_FILE"
  pubkey="$(cat "$DEPLOY_PUBKEY_FILE")"
fi
if [[ -n "$pubkey" ]]; then
  while IFS= read -r line; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    if grep -qxF -- "$line" "$AUTH_KEYS"; then
      log "clave ya presente en authorized_keys"
    else
      log "añadiendo clave a authorized_keys"
      printf '%s\n' "$line" >>"$AUTH_KEYS"
    fi
  done <<<"$pubkey"
fi
chown "$DEPLOY_USER:$DEPLOY_USER" "$AUTH_KEYS"
chmod 0600 "$AUTH_KEYS"

# Salvaguarda anti-bloqueo: con AllowUsers deploy, si deploy no tiene clave
# nadie podría volver a entrar.
grep -qE '^(ssh-|ecdsa-|sk-)' "$AUTH_KEYS" \
  || fail "$AUTH_KEYS está vacío: pasa DEPLOY_PUBKEY o DEPLOY_PUBKEY_FILE (evita quedarte fuera)"

# ---------------------------------------------------------------------------
# 2. sudo limitado (sin shell, sin comodines)
# ---------------------------------------------------------------------------
DOCKER_BIN="$(command -v docker 2>/dev/null || echo /usr/bin/docker)"
SYSTEMCTL_BIN="$(command -v systemctl 2>/dev/null || echo /usr/bin/systemctl)"
COMPOSE="$DOCKER_BIN compose --project-directory $NEXOVA_ROOT/app"
tmp_sudo="$(mktemp)"
cat >"$tmp_sudo" <<EOF
# Gestionado por infra/server/bootstrap.sh — no editar a mano.
# '$DEPLOY_USER' NO está en el grupo docker (equivaldría a root): solo puede
# ejecutar estos comandos exactos. Sin comodines para impedir argumentos extra
# (p. ej. 'docker compose -f /otro.yml' o 'run --privileged').
Cmnd_Alias NEXOVA_COMPOSE = $COMPOSE pull, \\
                            $COMPOSE up -d, \\
                            $COMPOSE up -d --remove-orphans, \\
                            $COMPOSE down, \\
                            $COMPOSE ps, \\
                            $COMPOSE restart
Cmnd_Alias NEXOVA_SYSTEMCTL = $SYSTEMCTL_BIN status docker, \\
                              $SYSTEMCTL_BIN restart docker, \\
                              $SYSTEMCTL_BIN status ssh, \\
                              $SYSTEMCTL_BIN reload ssh
Defaults:$DEPLOY_USER !visiblepw, use_pty, logfile=/var/log/sudo-nexova.log
$DEPLOY_USER ALL=(root) NOPASSWD: NEXOVA_COMPOSE, NEXOVA_SYSTEMCTL
EOF
visudo -cqf "$tmp_sudo" || { rm -f "$tmp_sudo"; fail "sudoers generado inválido"; }
if cmp -s "$tmp_sudo" "$SUDOERS_DROPIN" 2>/dev/null; then
  log "sudoers sin cambios"
else
  log "escribiendo $SUDOERS_DROPIN"
  install -o root -g root -m 0440 "$tmp_sudo" "$SUDOERS_DROPIN"
fi
rm -f "$tmp_sudo"

# ---------------------------------------------------------------------------
# 3. SSH endurecido (drop-in: en Ubuntu el primer valor gana y
#    sshd_config incluye sshd_config.d/*.conf al principio)
# ---------------------------------------------------------------------------
install -d -m 0755 /run/sshd
ls /etc/ssh/ssh_host_*_key >/dev/null 2>&1 || ssh-keygen -A >/dev/null
tmp_ssh="$(mktemp)"
cat >"$tmp_ssh" <<EOF
# Gestionado por infra/server/bootstrap.sh — no editar a mano.
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
PubkeyAuthentication yes
AuthenticationMethods publickey
AllowUsers $DEPLOY_USER
MaxAuthTries 3
LoginGraceTime 30
X11Forwarding no
AllowAgentForwarding no
PermitTunnel no
EOF
sshd_changed=0
if cmp -s "$tmp_ssh" "$SSHD_DROPIN" 2>/dev/null; then
  log "sshd drop-in sin cambios"
else
  backup=""
  [[ -f "$SSHD_DROPIN" ]] && { backup="$(mktemp)"; cp -p "$SSHD_DROPIN" "$backup"; }
  install -o root -g root -m 0644 "$tmp_ssh" "$SSHD_DROPIN"
  if sshd -t; then
    log "escrito $SSHD_DROPIN (sshd -t OK)"
    sshd_changed=1
  else
    if [[ -n "$backup" ]]; then cp -p "$backup" "$SSHD_DROPIN"; else rm -f "$SSHD_DROPIN"; fi
    rm -f "$tmp_ssh" "$backup"
    fail "sshd -t falló: configuración revertida"
  fi
  [[ -n "$backup" ]] && rm -f "$backup"
fi
rm -f "$tmp_ssh"
sshd -t || fail "sshd -t falló con la configuración actual"

# Recargar sshd: systemd en un VPS; HUP al PID en entornos sin systemd (test).
if ((sshd_changed)); then
  if [[ -d /run/systemd/system ]]; then
    systemctl reload ssh 2>/dev/null || systemctl restart ssh
    log "sshd recargado (systemd)"
  elif pid="$(pgrep -o -x sshd 2>/dev/null)"; then
    kill -HUP "$pid"
    log "sshd recargado (SIGHUP a $pid)"
  else
    log "sshd no está en ejecución: la configuración se aplicará al arrancar"
  fi
fi

# ---------------------------------------------------------------------------
# 4. Layout /srv/nexova con dueños y modos explícitos
# ---------------------------------------------------------------------------
getent group adm >/dev/null || groupadd --system adm
install -d -o root          -g root          -m 0755 "$NEXOVA_ROOT"
install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 0755 "$NEXOVA_ROOT/app"
install -d -o "$DEPLOY_USER" -g adm           -m 0750 "$NEXOVA_ROOT/logs"
install -d -o root          -g "$DEPLOY_USER" -m 0750 "$NEXOVA_ROOT/config"
# .env: legible por deploy (para docker compose), escribible solo por root.
# Nunca se sobreescribe su contenido; solo se corrigen dueño y modo.
[[ -e "$NEXOVA_ROOT/config/.env" ]] || : >"$NEXOVA_ROOT/config/.env"
chown root:"$DEPLOY_USER" "$NEXOVA_ROOT/config/.env"
chmod 0640 "$NEXOVA_ROOT/config/.env"
log "layout $NEXOVA_ROOT aplicado"

# ---------------------------------------------------------------------------
# 5. Firewall (ufw): deny incoming, solo SSH limitado + HTTP(S)
#    Las reglas se añaden ANTES de habilitar para no cortar la sesión SSH.
# ---------------------------------------------------------------------------
ufw default deny incoming  >/dev/null
ufw default allow outgoing >/dev/null
ufw default deny routed    >/dev/null
ufw limit "$SSH_PORT/tcp" comment 'SSH (rate limited)'    >/dev/null
ufw allow 443/tcp         comment 'HTTPS (proxy inverso)' >/dev/null
ufw allow 80/tcp          comment 'HTTP (solo redirect a HTTPS)' >/dev/null
# Elimina cualquier regla previa sobre puertos internos.
for p in "${FORBIDDEN_PORTS[@]}"; do
  for proto in "" "/tcp" "/udp"; do
    for verb in allow limit; do
      ufw --force delete "$verb" "$p$proto" >/dev/null 2>&1 || true
    done
  done
done
ufw --force enable >/dev/null
log "ufw activo: $(ufw status | head -1)"

log "OK — servidor endurecido"
