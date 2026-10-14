"""
Router: /reporting

Reporte de NEGOCIO para Laura (CEO) y Elena (L&D): expone el resultado del
pipeline semanal de desempeño por oficina y programa y permite consultarlo y
dispararlo. Módulo separado de `telemetry/` (reporte técnico de ingeniería),
que no se toca.

Toda la lógica ETL se importa de `data/pipelines/` vía `pipeline_bridge`; aquí
solo hay HTTP: autenticación, validación de entrada, caché y códigos de estado.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError

import cache
from models import User
from reporting import pipeline_bridge
from reporting.schemas import (
    PipelineRunAccepted,
    PipelineRunRead,
    PipelineRunRequest,
    WeeklyPerformanceReport,
)
from security import get_current_user

KPIS_CACHE_PREFIX = "reporting:"
KPIS_CACHE_TTL_SECONDS = 60

router = APIRouter(prefix="/reporting", tags=["reporting"])
logger = logging.getLogger(__name__)


def _storage_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Reporting storage unavailable.",
    )


def _require_monday(week_start: date | None) -> None:
    if week_start is not None and week_start.weekday() != 0:
        raise HTTPException(
            status_code=422,
            detail="week_start debe ser un lunes (inicio de semana ISO).",
        )


def _run_pipeline_in_background(run_id: str, payload: PipelineRunRequest) -> None:
    """Corre el flow con el lock ya tomado. Nunca lanza: el resultado queda en `pipeline_runs`."""

    try:
        pipeline_bridge.run_weekly_performance_pipeline(
            week_start=payload.week_start,
            lookback_weeks=payload.lookback_weeks,
            trigger="manual",
            run_id=run_id,
        )
    except Exception:
        logger.exception("Manual pipeline run %s failed", run_id)
    finally:
        # Haya escrito o no, la próxima consulta de KPIs debe leer la tabla.
        cache.invalidate(KPIS_CACHE_PREFIX)


@router.get("/pipeline-runs/latest", response_model=PipelineRunRead)
def get_latest_pipeline_run(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Estado y metadata de la última corrida del pipeline.

    TODO: restringir a admins (Role.ADMIN) cuando exista un flujo para crearlos.
    """

    try:
        run = pipeline_bridge.get_latest_run()
    except SQLAlchemyError:
        logger.exception("Could not read reporting.pipeline_runs")
        raise _storage_unavailable()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="El pipeline todavía no ha corrido."
        )
    return run


@router.post(
    "/pipeline-runs", response_model=PipelineRunAccepted, status_code=status.HTTP_202_ACCEPTED
)
def trigger_pipeline_run(
    background_tasks: BackgroundTasks,
    payload: PipelineRunRequest | None = None,
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Dispara una corrida manual del flow.

    El lock se toma aquí, antes de responder: si ya hay una corrida `running`
    se devuelve `409` en vez de aceptar un trabajo que no va a ejecutarse.

    TODO: restringir a admins (Role.ADMIN) cuando exista un flujo para crearlos.
    """

    payload = payload or PipelineRunRequest()
    _require_monday(payload.week_start)

    try:
        run = pipeline_bridge.start_manual_run(payload.week_start, payload.lookback_weeks)
    except SQLAlchemyError:
        logger.exception("Could not open a pipeline run")
        raise _storage_unavailable()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya hay una corrida del pipeline en curso.",
        )

    background_tasks.add_task(_run_pipeline_in_background, run["run_id"], payload)
    return {**run, "status": "running"}


@router.get("/weekly-office-program-performance", response_model=WeeklyPerformanceReport)
def get_weekly_office_program_performance(
    week_start: date | None = Query(
        None, description="Lunes de la semana (YYYY-MM-DD). Por defecto, la última calculada."
    ),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """KPIs por oficina y programa de una semana: lo que consume el dashboard.

    El resultado se cachea 60 s y se invalida al terminar un disparo manual.
    """

    _require_monday(week_start)
    key = f"{KPIS_CACHE_PREFIX}kpis:{week_start.isoformat() if week_start else 'latest'}"
    try:
        report = cache.get_or_set(
            key, KPIS_CACHE_TTL_SECONDS, lambda: pipeline_bridge.query_weekly_kpis(week_start)
        )
    except SQLAlchemyError:
        logger.exception("Could not read reporting.weekly_office_program_performance")
        raise _storage_unavailable()
    if report is None:
        # No se cachea el "vacío": la primera corrida debe verse de inmediato.
        cache.invalidate(key)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Todavía no hay semanas calculadas.",
        )
    return report
