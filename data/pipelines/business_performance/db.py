"""
Conexión a Postgres/Supabase y definición de las tablas de `reporting`.

El engine se crea de forma perezosa a partir de `DATABASE_URL` (la misma
variable que usa `services/api/database.py`), para que importar el paquete no
exija tener la base de datos configurada.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import Column, Date, Integer, MetaData, Numeric, Table, Text, create_engine, text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.engine import Engine

_REPO_ROOT = Path(__file__).resolve().parent / ".." / ".." / ".."

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


# Creadas por `services/api/migrations/003_reporting_schema.sql`; aquí solo se
# describen para construir las sentencias, nunca se hace `create_all()`.
_metadata = MetaData(schema="reporting")

weekly_performance = Table(
    "weekly_office_program_performance",
    _metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("office", Text, nullable=False),
    Column("programme_id", Text, nullable=False),
    Column("week_start", Date, nullable=False),
    Column("total_material_cost", Numeric, nullable=False),
    Column("kits_delivered_count", Integer, nullable=False),
    Column("shortage_events_count", Integer, nullable=False),
    Column("cost_variance_events_count", Integer, nullable=False),
    Column("currency", Text, nullable=False),
    Column("computed_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
)

pipeline_runs = Table(
    "pipeline_runs",
    _metadata,
    Column("run_id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("pipeline_name", Text, nullable=False),
    Column("trigger", Text, nullable=False),
    Column("window_start", TIMESTAMP(timezone=True), nullable=False),
    Column("window_end", TIMESTAMP(timezone=True), nullable=False),
    Column("started_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
    Column("finished_at", TIMESTAMP(timezone=True)),
    Column("status", Text, nullable=False),
    Column("phase", Text),
    Column("rows_read", Integer, nullable=False, server_default=text("0")),
    Column("rows_written", Integer, nullable=False, server_default=text("0")),
    Column("rows_revised", Integer, nullable=False, server_default=text("0")),
    Column("rows_missing_cost", Integer, nullable=False, server_default=text("0")),
    Column("rows_unassigned_programme", Integer, nullable=False, server_default=text("0")),
    Column("rows_invalid_office", Integer, nullable=False, server_default=text("0")),
    Column("rows_duplicates_dropped", Integer, nullable=False, server_default=text("0")),
    Column("error_message", Text),
    Column("prefect_flow_run_id", Text),
)
