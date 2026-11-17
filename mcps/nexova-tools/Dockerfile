FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

# Dependencias desde el lockfile (reproducible), sin las de desarrollo.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY nexova_mcp ./nexova_mcp

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

EXPOSE 8100

CMD ["python", "-m", "nexova_mcp.server"]
