# `infra/server` · Endurecimiento del servidor (OWASP A05)

Script reproducible e idempotente que deja un VPS Ubuntu 24.04 recién creado en
un estado mínimo seguro para desplegar Nexova, más una prueba automatizada en un
contenedor desechable que genera la evidencia de antes/después.

| Archivo | Qué es |
|---|---|
| `bootstrap.sh` | Endurece el servidor. Se ejecuta como root; puede repetirse sin efectos secundarios. |
| `test/Dockerfile` | "VPS" de pruebas: `ubuntu:24.04` + openssh-server + ufw + netcat + openssh-client. |
| `test/verify.sh` | Construye la imagen, muestra el estado ANTES, ejecuta `bootstrap.sh` dos veces y muestra el estado DESPUÉS. |

Evidencia generada por `verify.sh`:

- [`docs/security/evidence/before/server-baseline.txt`](../../docs/security/evidence/before/server-baseline.txt)
- [`docs/security/evidence/after/server-hardened.txt`](../../docs/security/evidence/after/server-hardened.txt)

## Qué hace `bootstrap.sh`

1. **Usuario no-root `deploy`.** Sin contraseña utilizable (`*` en `/etc/shadow`),
   solo acceso por clave pública. La clave se pasa con `DEPLOY_PUBKEY` (texto) o
   `DEPLOY_PUBKEY_FILE` (ruta); no se duplica si ya existe. Si `authorized_keys`
   queda vacío el script **aborta antes de tocar SSH**, para no dejarte fuera.
2. **sudo limitado** en `/etc/sudoers.d/90-nexova-deploy` (validado con
   `visudo -c`, modo `0440`): solo comandos exactos de
   `docker compose --project-directory /srv/nexova/app {pull,up -d,down,ps,restart}`
   y `systemctl {status,restart} docker` / `systemctl {status,reload} ssh`.
   Sin comodines, así que no se puede añadir `-f otro.yml`, `run --privileged`,
   ni abrir una shell. `deploy` **no** se añade al grupo `docker`, porque eso
   equivale a ser root.
3. **SSH** mediante el drop-in `/etc/ssh/sshd_config.d/10-nexova.conf`:
   `PermitRootLogin no`, `PasswordAuthentication no`,
   `KbdInteractiveAuthentication no`, `AuthenticationMethods publickey`,
   `AllowUsers deploy`, `MaxAuthTries 3`, `LoginGraceTime 30`,
   `X11Forwarding no`, `AllowAgentForwarding no`, `PermitTunnel no`.
   Se valida con `sshd -t`; si falla, se restaura el archivo anterior. En
   Ubuntu, `sshd_config` incluye `sshd_config.d/*.conf` al principio y gana el
   primer valor, así que el prefijo `10-` se impone a `50-cloud-init.conf` (que
   algunos proveedores usan para activar `PasswordAuthentication yes`).
4. **Layout de carpetas con dueños y modos explícitos:**

   | Ruta | Dueño | Modo | Motivo |
   |---|---|---|---|
   | `/srv/nexova` | `root:root` | `0755` | Nadie salvo root puede crear o renombrar subcarpetas. |
   | `/srv/nexova/app` | `deploy:deploy` | `0755` | Código y `docker-compose.yml` que despliega `deploy`. |
   | `/srv/nexova/logs` | `deploy:adm` | `0750` | Logs legibles por el grupo de auditoría `adm`, no por el resto. |
   | `/srv/nexova/config` | `root:deploy` | `0750` | `deploy` entra a leer, pero no puede crear ni borrar archivos. |
   | `/srv/nexova/config/.env` | `root:deploy` | `0640` | Secretos: `deploy` los lee para `compose` y solo root los cambia. Su contenido nunca se sobreescribe. |

5. **Firewall ufw**: `default deny incoming`, `default allow outgoing`,
   `default deny routed`, y solo tres reglas (IPv4 e IPv6). Las reglas se añaden
   antes de `ufw enable` para no cortar la sesión SSH en curso. Si alguien abrió
   a mano un puerto interno, el script lo elimina.

## Puertos

| Puerto | Regla | Por qué |
|---|---|---|
| 22/tcp | `limit` | Administración por SSH. `limit` bloquea una IP que abra 6 o más conexiones en 30 s (frena la fuerza bruta). |
| 443/tcp | `allow` | Única entrada pública de la aplicación: el proxy inverso con TLS (website, backoffice y `/api`). |
| 80/tcp | `allow` | Solo para redirigir a HTTPS (y retos HTTP-01 de Let's Encrypt). Nunca sirve contenido. |
| 3000, 3001 | — | website y backoffice en Next.js: solo se llega a ellos a través del proxy. |
| 8000 | — | API FastAPI: solo a través del proxy (`/api`). |
| 8100 | — | Servidor MCP del agente: es interno. |
| 6379 | — | Redis: sin TLS y, por defecto, sin autenticación. Nunca debe quedar expuesto. |
| 6333 | — | Qdrant: la base vectorial es interna. |
| 5555 | — | Flower, el monitor de Celery: es una herramienta de desarrollo. |

> ⚠️ **Docker se salta ufw.** Un `ports: "6379:6379"` en `docker-compose.yml`
> crea reglas en la cadena `DOCKER` de iptables que se evalúan **antes** que las
> de ufw, así que el puerto queda abierto aunque ufw diga lo contrario. En
> producción, los servicios internos no deben publicar puertos (que usen la red
> interna de compose) o deben atarse a `127.0.0.1:` (por ejemplo,
> `"127.0.0.1:6379:6379"`). Solo el proxy publica 80 y 443.

## Uso en un VPS real

```bash
# 1. Desde tu máquina: copia el script (todavía entras como root o con el usuario inicial del proveedor)
scp infra/server/bootstrap.sh root@IP_DEL_VPS:/root/

# 2. Ejecútalo en el VPS como root, pasándole TU clave pública local
ssh root@IP_DEL_VPS "DEPLOY_PUBKEY='$(cat ~/.ssh/id_ed25519.pub)' bash /root/bootstrap.sh"

# 3. SIN cerrar la sesión anterior, abre otra terminal y comprueba que entras como deploy
ssh deploy@IP_DEL_VPS 'id && sudo -n -l'

# 4. Comprueba que root ya no puede entrar
ssh root@IP_DEL_VPS   # -> Permission denied (publickey).
```

Variables opcionales: `DEPLOY_USER` (por defecto `deploy`), `NEXOVA_ROOT`
(por defecto `/srv/nexova`), `SSH_PORT` (por defecto `22`) y `SKIP_APT=1`, que
no instala los paquetes que falten (`openssh-server`, `ufw`, `sudo`).

Recomendaciones fuera del alcance del script: activar `unattended-upgrades`,
añadir `fail2ban` además de `ufw limit`, y usar el firewall del proveedor como
segunda capa con las mismas tres reglas.

## Prueba local (sin VPS)

```bash
./infra/server/test/verify.sh
```

Requiere Docker. Levanta dos contenedores en una red propia: `nexova-srv` (el
"VPS", con `--cap-add NET_ADMIN` para que ufw pueda programar iptables) y
`nexova-probe`, un cliente externo que sondea con `nc` los puertos
22/80/443/3000/3001/5555/6333/6379/8000/8100. En el servidor se dejan
listeners en todos ellos para simular el stack escuchando en `0.0.0.0`.
Con la clave generada durante la prueba se demuestra que:

- **ANTES**: root entra por SSH con clave, el servidor ofrece `password`, ufw
  está inactivo y los 10 puertos son alcanzables desde fuera.
- **DESPUÉS**: root queda rechazado aunque su clave sea válida, solo se ofrece
  `publickey`, `deploy` entra pero no consigue una shell de root con sudo, solo
  22/80/443 son alcanzables y los permisos son los de la tabla anterior.
- **Idempotencia**: una segunda ejecución no cambia nada (se compara `diff` del
  estado de sshd, ufw, permisos, sudoers y usuario).

Limitaciones del contenedor: no hay systemd, así que sshd se arranca a mano y
`bootstrap.sh` lo recarga con `SIGHUP` en lugar de usar `systemctl reload ssh`.
ufw sí se activa de verdad gracias a `NET_ADMIN`. Además, las conexiones a
`localhost` pasan siempre por la interfaz `lo`, que ufw permite, y por eso el
filtrado de puertos se prueba desde el segundo contenedor.
