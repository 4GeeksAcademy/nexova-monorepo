"""
Dead Letter Queue en `public.task_dead_letters` (migración 005).

Una fila por tarea que agotó sus reintentos. Se escribe con SQLAlchemy Core
sobre el engine de la API (`database.engine`, mismo `DATABASE_URL`).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Column, Integer, MetaData, Table, Text, insert, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.engine import Engine

# Import en la carga del módulo, no perezoso: `celery -A` solo añade el
# directorio de trabajo a `sys.path` mientras importa la app, así que un
# `import database` dentro de `on_failure` fallaría con ModuleNotFoundError.
import database

# Creada por `services/api/migrations/005_task_dead_letters.sql`; aquí solo se
# describe para construir las sentencias, nunca se hace `create_all()`.
_metadata = MetaData(schema="public")

task_dead_letters = Table(
    "task_dead_letters",
    _metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("task_id", Text, nullable=False),
    Column("task_name", Text, nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("error_type", Text),
    Column("error_message", Text, nullable=False),
    Column("traceback", Text),
    Column("task_kwargs", JSONB, nullable=False),
    Column("failed_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")),
)


def get_engine() -> Engine:
    return database.engine


def record_dead_letter(
    *,
    task_id: str,
    task_name: str,
    attempt: int,
    error_type: str,
    error_message: str,
    traceback: str | None,
    task_kwargs: dict[str, Any],
) -> None:
    with get_engine().begin() as connection:
        connection.execute(
            insert(task_dead_letters).values(
                task_id=task_id,
                task_name=task_name,
                attempt=attempt,
                error_type=error_type,
                error_message=error_message or "fallo sin mensaje",
                traceback=traceback,
                task_kwargs=task_kwargs,
            )
        )
