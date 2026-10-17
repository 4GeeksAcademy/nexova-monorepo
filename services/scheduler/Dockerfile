# Contenedor scheduler: ejecuta los jobs nocturnos con supercronic, fuera del
# proceso de la API. El código (scripts/, services/job_runner/, data/) llega por
# bind mount desde docker-compose.yml.
FROM python:3.11-slim

ARG TARGETARCH
ARG SUPERCRONIC_VERSION=v0.2.49
ARG SUPERCRONIC_SHA256_AMD64=a53ae236602c7338aba3fbaff40bda6300eae3b9fedb8261eb06cfe3724430c1
ARG SUPERCRONIC_SHA256_ARM64=02aa0cb229ba09050cba6638059dadb9eedc2276632ea43d6a57a2f8c1629dd5

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && case "${TARGETARCH:-amd64}" in \
      amd64) SHA256="$SUPERCRONIC_SHA256_AMD64" ;; \
      arm64) SHA256="$SUPERCRONIC_SHA256_ARM64" ;; \
      *) echo "Arquitectura no soportada: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
 && curl -fsSL -o /usr/local/bin/supercronic \
      "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH:-amd64}" \
 && echo "${SHA256}  /usr/local/bin/supercronic" | sha256sum -c - \
 && chmod +x /usr/local/bin/supercronic

RUN pip install --no-cache-dir uv

# Mismas dependencias que la API: el pipeline necesita Prefect, Pandas y SQLAlchemy.
COPY api/requirements.txt ./requirements.txt
RUN uv pip install --system --no-cache -r requirements.txt

COPY scheduler/crontab /etc/crontab.scheduler

WORKDIR /repo

CMD ["supercronic", "-passthrough-logs", "/etc/crontab.scheduler"]
