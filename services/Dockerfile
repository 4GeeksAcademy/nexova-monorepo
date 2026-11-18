FROM python:3.11-slim

# Usuario sin privilegios (auditoría OWASP, C5). uid/gid 1000 por defecto para
# que coincida con el dueño de los bind mounts en Linux/Codespaces; se puede
# cambiar con --build-arg APP_UID=... APP_GID=...
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd --gid "${APP_GID}" app \
 && useradd --uid "${APP_UID}" --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY api/requirements.txt ./requirements.txt
RUN uv pip install --system --no-cache -r requirements.txt

# El código es de root y de solo lectura para `app`; solo data/ (TinyDB, traces,
# checkpoints SQLite) es escribible por el proceso.
COPY api/. .
RUN mkdir -p /app/data/agent /data/raw/rfp \
 && chown -R app:app /app/data /data/raw/rfp

# Prefect y otras librerías escriben en $HOME: un directorio temporal propio del usuario.
ENV HOME=/tmp/app-home PREFECT_HOME=/tmp/app-home/.prefect
RUN mkdir -p /tmp/app-home && chown app:app /tmp/app-home

USER app

EXPOSE 8000

# Sin --reload: el modo desarrollo lo activa docker-compose.dev.yml.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]
