"""
Checkpointer del grafo de aprobación (Parte 3).

Por defecto **Postgres** (la misma Supabase de `DATABASE_URL`): la API y el
worker de Celery son contenedores distintos y ambos tienen que ver la misma
pausa, así que un SQLite local no sirve. `RFP_CHECKPOINTER` permite:

- `postgres` (por defecto): `PostgresSaver` (psycopg v3). Las tablas las crea
  `setup()` la primera vez en cada proceso.
- `sqlite`: archivo en `RFP_CHECKPOINT_PATH` (desarrollo local con un solo proceso).
- `memory`: `InMemorySaver`, solo para tests o corridas en seco.

Cada rama vive en su propio hilo: `rfp-{ticket_id}:{department_id}`.
"""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from langgraph.checkpoint.memory import InMemorySaver

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_SQLITE = _REPO_ROOT / "services" / "api" / "data" / "rfp" / "approval_checkpoints.sqlite"

_memory_saver: InMemorySaver | None = None
_postgres_ready = False


def thread_id_for(ticket_id: str, department_id: str) -> str:
    """Hilo del checkpointer de una rama: tickets (y departamentos) concurrentes no comparten checkpoint."""
    return f"rfp-{ticket_id}:{department_id}"


def _postgres_url() -> str:
    url = os.environ.get("RFP_CHECKPOINT_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL no está definida: el checkpointer de aprobaciones usa Postgres.")
    # SQLAlchemy usa `postgresql+psycopg2://`; psycopg v3 solo entiende el esquema libpq.
    return url.replace("postgresql+psycopg2://", "postgresql://").replace("postgres+psycopg2://", "postgresql://")


@contextmanager
def open_checkpointer(kind: str | None = None) -> Iterator[Any]:
    global _memory_saver, _postgres_ready
    kind = (kind or os.environ.get("RFP_CHECKPOINTER") or "postgres").lower()
    if kind == "memory":
        if _memory_saver is None:
            _memory_saver = InMemorySaver()
        yield _memory_saver
        return
    if kind == "sqlite":
        from langgraph.checkpoint.sqlite import SqliteSaver

        path = Path(os.environ.get("RFP_CHECKPOINT_PATH", _DEFAULT_SQLITE))
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False)
        try:
            yield SqliteSaver(conn)
        finally:
            conn.close()
        return
    if kind != "postgres":
        raise RuntimeError(f"RFP_CHECKPOINTER no soportado: {kind!r} (postgres | sqlite | memory).")

    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg import Connection
    from psycopg.rows import dict_row

    # prepare_threshold=None: SIN sentencias preparadas (0 significaría prepararlas todas). El pooler
    # de Supabase reparte las sentencias entre conexiones de servidor y las preparadas se pierden.
    with Connection.connect(_postgres_url(), autocommit=True, prepare_threshold=None,
                            row_factory=dict_row) as conn:
        saver = PostgresSaver(conn)
        if not _postgres_ready:
            _setup(saver)
            _postgres_ready = True
        yield saver


def _setup(saver: Any, attempts: int = 4) -> None:
    """`setup()` tolerante a carreras: varias ramas pueden arrancar a la vez y crear las tablas en paralelo.

    `setup()` es idempotente (lleva su propia tabla de versiones), así que ante un choque de DDL
    concurrente basta con esperar a que el otro proceso termine y repetir.
    """
    from psycopg import errors

    for attempt in range(1, attempts + 1):
        try:
            saver.setup()
            return
        except (errors.UniqueViolation, errors.DuplicateTable, errors.DuplicateObject):
            if attempt == attempts:
                raise
            time.sleep(0.5 * attempt)
