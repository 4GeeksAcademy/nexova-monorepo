# "Servidor" Ubuntu desechable para probar infra/server/bootstrap.sh.
# Reproduce un VPS Ubuntu 24.04 recién creado: sshd con la config por defecto
# y ufw instalado pero inactivo. No usar en producción.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      openssh-server openssh-client ufw sudo netcat-openbsd iptables iproute2 procps \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /run/sshd \
 && ssh-keygen -A

# Sin systemd: PID 1 duerme y verify.sh arranca sshd a mano.
CMD ["sleep", "infinity"]
