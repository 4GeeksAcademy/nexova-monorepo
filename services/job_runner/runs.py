"""
Máquina de estados de `public.job_runs`: pending → processing → completed | failed.

El lock es el propio estado `processing`: una instancia solo trabaja si
consigue pasar su fila a `processing`, y el índice único parcial
`(job_name) WHERE status = 'processing'` hace que la segunda reciba
unique_violation en vez de colarse en una carrera. Al cerrar la fila
(`completed` o `failed`) el lock se libera.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError

from .db import get_engine, job_runs

PENDING = "pending"
PROCESSING = "processing"
COMPLETED = "completed"
FAILED = "failed"

# Una fila `processing` más vieja que esto pertenece a un proceso que murió
# sin pasar por su `except` (SIGKILL, OOM, host caído). Se cierra como `failed`
# para que el lock no quede tomado para siempre.
STALE_PROCESSING_HOURS = 2

LOCK_HELD_MESSAGE = "otra instancia tiene el lock (status = processing)"

_UNIQUE_VIOLATION = "23505"


def has_processing_lock(job_name: str) -> bool:
    """`True` si otra ejecución de `job_name` está en `processing`."""

    with get_engine().connect() as connection:
        found = connection.execute(
            select(job_runs.c.id)
            .where(job_runs.c.job_name == job_name, job_runs.c.status == PROCESSING)
            .limit(1)
        ).first()
    return found is not None


def has_completed_for_date(job_name: str, target_date: date) -> bool:
    """`True` si `job_name` ya terminó en `completed` para `target_date`."""

    with get_engine().connect() as connection:
        found = connection.execute(
            select(job_runs.c.id)
            .where(
                job_runs.c.job_name == job_name,
                job_runs.c.target_date == target_date,
                job_runs.c.status == COMPLETED,
            )
            .limit(1)
        ).first()
    return found is not None


def release_stale_locks(job_name: str, older_than_hours: int = STALE_PROCESSING_HOURS) -> int:
    """Cierra como `failed` las filas `processing` abandonadas. Devuelve cuántas."""

    stale_before = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
    with get_engine().begin() as connection:
        result = connection.execute(
            update(job_runs)
            .where(
                job_runs.c.job_name == job_name,
                job_runs.c.status == PROCESSING,
                job_runs.c.started_at < stale_before,
            )
            .values(
                status=FAILED,
                finished_at=func.now(),
                error_message=f"stale run: más de {older_than_hours} h en processing",
            )
        )
    return result.rowcount


def create_run(job_name: str, target_date: date) -> str:
    """Inserta la fila en `pending` y devuelve su id."""

    with get_engine().begin() as connection:
        run_id = connection.execute(
            insert(job_runs)
            .values(job_name=job_name, target_date=target_date, status=PENDING)
            .returning(job_runs.c.id)
        ).scalar_one()
    return str(run_id)


def mark_processing(run_id: str) -> bool:
    """Toma el lock pasando la fila a `processing`.

    Devuelve `False` si otra instancia lo tomó primero; en ese caso la fila se
    cierra como `failed` para que no quede `pending` para siempre.
    """

    try:
        with get_engine().begin() as connection:
            connection.execute(
                update(job_runs)
                .where(job_runs.c.id == run_id, job_runs.c.status == PENDING)
                .values(status=PROCESSING, started_at=func.now())
            )
    except IntegrityError as exc:
        if getattr(exc.orig, "pgcode", None) != _UNIQUE_VIOLATION:
            raise
        mark_failed(run_id, LOCK_HELD_MESSAGE)
        return False
    return True


def mark_completed(run_id: str) -> None:
    _close(run_id, COMPLETED, None)


def mark_failed(run_id: str, error_message: str) -> None:
    _close(run_id, FAILED, error_message)


def _close(run_id: str, status: str, error_message: str | None) -> None:
    with get_engine().begin() as connection:
        connection.execute(
            update(job_runs)
            .where(job_runs.c.id == run_id)
            .values(status=status, finished_at=func.now(), error_message=error_message)
        )
