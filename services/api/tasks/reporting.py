"""
Tareas del dominio de reporting: la corrida manual del pipeline semanal.

Antes corría en `BackgroundTasks`, dentro del proceso de FastAPI; ahora la
encola `POST /reporting/pipeline-runs` y la ejecuta el worker. El mensaje solo
lleva `run_id`, `week_start` y `lookback_weeks`: el flow lee los eventos él
mismo de la base de datos. La lógica sigue en `data/pipelines/` (vía
`pipeline_bridge`), aquí solo hay reintentos y manejo del lock.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from reporting import pipeline_bridge
from tasks.base import ObservedTask
from tasks.celery_app import app

MAX_RETRIES = 3
# Backoff exponencial: 15 s, 30 s y 60 s antes de cada reintento.
RETRY_BASE_COUNTDOWN_SECONDS = 15

logger = logging.getLogger("tasks")


class PipelineLockBusy(RuntimeError):
    """Otra corrida del pipeline tiene el lock: este intento no puede empezar."""


def retry_countdown(retries: int) -> int:
    """Espera antes del reintento número `retries + 1`."""

    return RETRY_BASE_COUNTDOWN_SECONDS * 2**retries


def _release_lock(run_id: str, exc: BaseException) -> None:
    """Cierra la corrida si el flow no llegó a hacerlo (p. ej. timeout). Nunca lanza."""

    try:
        if pipeline_bridge.fail_manual_run(run_id, f"{type(exc).__name__}: {exc}"):
            logger.warning("run_id=%s cerrada como failed desde la tarea", run_id)
    except Exception:
        logger.exception("run_id=%s no se pudo liberar el lock", run_id)


@app.task(
    bind=True,
    base=ObservedTask,
    name="reporting.run_weekly_performance_pipeline",
    max_retries=MAX_RETRIES,
)
def run_weekly_performance_pipeline(
    self: ObservedTask, run_id: str, week_start: str | None, lookback_weeks: int
) -> dict[str, Any]:
    """Ejecuta el flow con el lock que tomó la API (o uno nuevo en cada reintento)."""

    week = date.fromisoformat(week_start) if week_start else None
    # El primer intento usa la corrida que abrió la API antes de responder 202.
    # Si falló, el flow ya la cerró: cada reintento abre la suya.
    attempt_run_id: str | None = run_id if self.request.retries == 0 else None

    try:
        if attempt_run_id is None:
            run = pipeline_bridge.start_manual_run(week, lookback_weeks)
            if run is None:
                raise PipelineLockBusy("Ya hay una corrida del pipeline en curso.")
            attempt_run_id = run["run_id"]

        logger.info(
            "task_id=%s attempt=%d run_id=%s week_start=%s lookback_weeks=%d",
            self.request.id,
            self.attempt(),
            attempt_run_id,
            week_start,
            lookback_weeks,
        )
        return pipeline_bridge.run_weekly_performance_pipeline(
            week_start=week,
            lookback_weeks=lookback_weeks,
            trigger="manual",
            run_id=attempt_run_id,
        )
    except Exception as exc:
        if attempt_run_id is not None:
            _release_lock(attempt_run_id, exc)
        countdown = retry_countdown(self.request.retries)
        if self.request.retries < self.max_retries:
            logger.warning(
                "task_id=%s attempt=%d reintento en %ds",
                self.request.id,
                self.attempt(),
                countdown,
            )
        # Sin reintentos restantes, `retry` relanza `exc` y Celery llama a
        # `on_failure`, que la manda a la DLQ.
        raise self.retry(exc=exc, countdown=countdown)
