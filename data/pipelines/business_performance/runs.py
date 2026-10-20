"""
Bitácora y lock de corridas en `reporting.pipeline_runs` (PIPELINE_DESIGN.md §7).

El lock es por pipeline: el índice único parcial
`(pipeline_name) WHERE status = 'running'` rechaza una segunda corrida
`running`, aunque su ventana sea distinta.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError

from .config import PIPELINE_NAME, STALE_RUN_MINUTES
from .db import get_engine, pipeline_runs

_UNIQUE_VIOLATION = "23505"

COUNTER_FIELDS = (
    "rows_read",
    "rows_written",
    "rows_revised",
    "rows_missing_cost",
    "rows_unassigned_programme",
    "rows_invalid_office",
    "rows_duplicates_dropped",
)
_UPDATABLE_FIELDS = frozenset({*COUNTER_FIELDS, "phase", "prefect_flow_run_id"})


def _new_run(
    trigger: str,
    window_start: datetime,
    window_end: datetime,
    prefect_flow_run_id: str | None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "pipeline_name": PIPELINE_NAME,
        "trigger": trigger,
        "window_start": window_start,
        "window_end": window_end,
        "prefect_flow_run_id": prefect_flow_run_id,
        **extra,
    }


def open_run(
    trigger: str,
    window_start: datetime,
    window_end: datetime,
    prefect_flow_run_id: str | None = None,
) -> str | None:
    """Toma el lock insertando una fila `running`. Devuelve `None` si ya está tomado.

    Antes marca como `failed` las corridas `running` de más de
    `STALE_RUN_MINUTES` (proceso muerto sin cerrar su fila).
    """

    stale_before = datetime.now(timezone.utc) - timedelta(minutes=STALE_RUN_MINUTES)
    try:
        with get_engine().begin() as connection:
            connection.execute(
                update(pipeline_runs)
                .where(
                    pipeline_runs.c.pipeline_name == PIPELINE_NAME,
                    pipeline_runs.c.status == "running",
                    pipeline_runs.c.started_at < stale_before,
                )
                .values(status="failed", finished_at=func.now(), error_message="stale run")
            )
            run_id = connection.execute(
                insert(pipeline_runs)
                .values(
                    _new_run(trigger, window_start, window_end, prefect_flow_run_id, status="running")
                )
                .returning(pipeline_runs.c.run_id)
            ).scalar_one()
    except IntegrityError as exc:
        if getattr(exc.orig, "pgcode", None) == _UNIQUE_VIOLATION:
            return None
        raise
    return str(run_id)


def record_skipped(
    trigger: str,
    window_start: datetime,
    window_end: datetime,
    prefect_flow_run_id: str | None = None,
) -> str:
    """Deja constancia de una corrida que no arrancó porque el lock estaba tomado."""

    with get_engine().begin() as connection:
        run_id = connection.execute(
            insert(pipeline_runs)
            .values(
                _new_run(
                    trigger,
                    window_start,
                    window_end,
                    prefect_flow_run_id,
                    status="skipped",
                    finished_at=func.now(),
                    error_message="otra corrida tiene el lock del pipeline",
                )
            )
            .returning(pipeline_runs.c.run_id)
        ).scalar_one()
    return str(run_id)


def update_run(run_id: str, **fields: Any) -> None:
    """Actualiza el checkpoint (`phase`), los contadores o el id de Prefect."""

    unknown = set(fields) - _UPDATABLE_FIELDS
    if unknown:
        raise ValueError(f"Campos no actualizables en pipeline_runs: {sorted(unknown)}")
    if not fields:
        return
    with get_engine().begin() as connection:
        connection.execute(
            update(pipeline_runs).where(pipeline_runs.c.run_id == run_id).values(**fields)
        )


def close_run(
    run_id: str,
    status: str,
    error_message: str | None = None,
    counters: dict[str, int] | None = None,
) -> None:
    """Cierra la corrida (`completed` o `failed`) y libera el lock."""

    values: dict[str, Any] = {
        key: int(value) for key, value in (counters or {}).items() if key in COUNTER_FIELDS
    }
    with get_engine().begin() as connection:
        connection.execute(
            update(pipeline_runs)
            .where(pipeline_runs.c.run_id == run_id)
            .values(status=status, finished_at=func.now(), error_message=error_message, **values)
        )


def fail_run_if_running(run_id: str, error_message: str) -> bool:
    """Marca la corrida como `failed` solo si sigue `running`. `True` si la cerró.

    A diferencia de `close_run`, no pisa una corrida que el flow ya cerró: lo usa
    quien libera el lock desde fuera del flow (cola de tareas, encolado fallido).
    """

    with get_engine().begin() as connection:
        result = connection.execute(
            update(pipeline_runs)
            .where(pipeline_runs.c.run_id == run_id, pipeline_runs.c.status == "running")
            .values(status="failed", finished_at=func.now(), error_message=error_message[:2000])
        )
    return result.rowcount > 0


def get_latest_run() -> dict[str, Any] | None:
    """Última corrida del pipeline (cualquier estado), o `None` si nunca corrió."""

    with get_engine().connect() as connection:
        row = (
            connection.execute(
                select(pipeline_runs)
                .where(pipeline_runs.c.pipeline_name == PIPELINE_NAME)
                .order_by(pipeline_runs.c.started_at.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
    if row is None:
        return None
    return {**row, "run_id": str(row["run_id"])}
