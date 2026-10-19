"""
Conexión a Postgres/Supabase y definición de la tabla `job_runs`.

El engine se crea de forma perezosa a partir de `DATABASE_URL` (la misma
variable que usan la API y el pipeline), para que importar el paquete no exija
tener la base de datos configurada. No importa nada de FastAPI.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import Column, Date, MetaData, Table, Text, create_engine, text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.engine import Engine

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Al correr como script no hay nadie que cargue el `.env`: se buscan el de la
# API y el de la raíz. `load_dotenv` no pisa variables ya definidas (Docker).
for _env_file in (_REPO_ROOT / "services" / "api" / ".env", _REPO_ROOT / ".env"):
    if _env_file.is_file():
        load_dotenv(_env_file)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL no está definida: créala en services/api/.env o en el entorno."
        )
    return create_engine(database_url, pool_pre_ping=True)


# Creada por `services/api/migrations/004_job_runs.sql`; aquí solo se describe
# para construir las sentencias, nunca se hace `create_all()`.
_metadata = MetaData(schema="public")

job_runs = Table(
    "job_runs",
    _metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("job_name", Text, nullable=False),
    Column("target_date", Date, nullable=False),
    Column("status", Text, nullable=False),
    Column("started_at", TIMESTAMP(timezone=True)),
    Column("finished_at", TIMESTAMP(timezone=True)),
    Column("error_message", Text),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
)
