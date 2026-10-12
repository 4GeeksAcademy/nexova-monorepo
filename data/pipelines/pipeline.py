"""
Pipeline de Desempeño de Negocio — Nexova (Prefect 3).

Produce el "Reporte Semanal de Desempeño por Oficina y Programa": lee
`public.telemetry_events` (solo lectura), calcula por `office` × `programme_id`
× semana ISO los 4 KPIs del CONTEXT y hace upsert en
`reporting.weekly_office_program_performance`. Diseño: PIPELINE_DESIGN.md.

Ejecución (frecuencia prevista: lunes 04:00 UTC, más disparo manual):

    # ETL completo: semana actual + anterior
    uv run --project services/api python data/pipelines/pipeline.py

    # Recalcular hasta una semana concreta (lunes) con más semanas hacia atrás
    uv run --project services/api python data/pipelines/pipeline.py --week-start 2026-10-05 --lookback-weeks 4

    # Dejarlo programado con el cron `0 4 * * 1` (necesita `prefect server start`)
    uv run --project services/api python data/pipelines/pipeline.py --serve

Requiere `DATABASE_URL` (en `services/api/.env` o en el entorno) y la migración
`services/api/migrations/003_reporting_schema.sql` aplicada.

La lógica de cada etapa vive en `business_performance/`; aquí solo se orquesta.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from prefect import flow, get_run_logger, task
from prefect.context import TaskRunContext
from prefect.runtime import flow_run

from business_performance import extract, load, queries, runs, transform
from business_performance.config import LOOKBACK_WEEKS, PIPELINE_NAME, SCHEDULE_CRON
from business_performance.windows import resolve_window

FLOW_NAME = "weekly_office_program_performance_flow"

# Subir la versión invalida los resultados cacheados si cambia la lógica de
# `transform_weekly_aggregates`.
TRANSFORM_VERSION = "v1"

_DEFAULT_EVAL_DIR = Path(__file__).resolve().parent / ".." / "eval" / "business_performance"


class PipelineStageError(RuntimeError):
    """Una etapa crítica (extract/transform/load) falló tras agotar sus reintentos."""

    def __init__(self, phase: str, message: str) -> None:
        super().__init__(f"[{phase}] {message}")
        self.phase = phase


# --- Tasks -------------------------------------------------------------------


# retries=2: son dos sentencias cortas sobre `pipeline_runs`. Un corte breve del
# pooler de Supabase se resuelve en segundos; si persiste, es mejor no arrancar.
@task(name="open_run", retries=2, retry_delay_seconds=5)
def open_run(
    trigger: str, window_start: datetime, window_end: datetime, prefect_flow_run_id: str | None
) -> str | None:
    """Toma el lock del pipeline. `None` si otra corrida ya está `running`."""
    return runs.open_run(trigger, window_start, window_end, prefect_flow_run_id)


@task(name="mark_phase", retries=2, retry_delay_seconds=5)
def mark_phase(run_id: str, phase: str, prefect_flow_run_id: str | None = None) -> None:
    """Checkpoint en `pipeline_runs.phase`: si la corrida muere, se sabe dónde."""
    fields: dict[str, Any] = {"phase": phase}
    if prefect_flow_run_id is not None:
        fields["prefect_flow_run_id"] = prefect_flow_run_id
    runs.update_run(run_id, **fields)


# retries=3 con espera creciente (2 s, 10 s, 30 s): la lectura va contra el
# pooler de Supabase, cuyos fallos típicos (conexión reciclada, timeout de red,
# reinicio del pooler) duran segundos. Tres intentos cubren ~40 s de corte; más
# allá es una caída real y conviene fallar y dejarlo en `pipeline_runs`. Es una
# lectura, así que repetirla no tiene efectos.
@task(name="extract_events", retries=3, retry_delay_seconds=[2, 10, 30])
def extract_events(window_start: datetime, window_end: datetime) -> pd.DataFrame:
    """Lee de `telemetry_events` los 4 tipos de evento de la ventana `[start, end)`."""
    return extract.extract_events(window_start, window_end)


def transform_cache_key(context: TaskRunContext, parameters: dict[str, Any]) -> str:
    """Clave de caché de la transformación.

    La define el CONTENIDO de entrada: el hash de los `event_id` leídos (la
    fuente es append-only, así que un `event_id` identifica un evento
    inmutable) más la versión de la lógica. Si llega un evento tardío, el
    conjunto de ids cambia y la clave también, así que nunca se sirve un
    agregado obsoleto.
    """

    events: pd.DataFrame = parameters["events"]
    digest = hashlib.sha256(TRANSFORM_VERSION.encode())
    digest.update(str(len(events)).encode())
    for event_id in sorted(events["event_id"].dropna().astype(str)):
        digest.update(event_id.encode())
    return f"transform_weekly_aggregates-{digest.hexdigest()}"


# Caché válida 1 hora: si el pipeline se relanza (reintento manual, cron +
# disparo humano) sobre exactamente los mismos eventos, Prefect reutiliza el
# resultado persistido en vez de volver a agregar. Pasada la hora se recalcula.
# Es seguro porque la función es pura. No lleva `retries`: no toca servicios
# externos, un fallo aquí es un bug y reintentar daría el mismo error.
@task(
    name="transform_weekly_aggregates",
    cache_key_fn=transform_cache_key,
    cache_expiration=timedelta(hours=1),
    persist_result=True,
)
def transform_weekly_aggregates(events: pd.DataFrame) -> dict[str, Any]:
    """Limpia, deduplica y resume por oficina × programa × semana ISO."""
    return transform.transform_weekly_aggregates(events)


# retries=3 con la misma espera que la extracción. Reintentar una escritura
# solo es seguro porque la carga es idempotente: upsert con reemplazo en una
# única transacción, así que un intento a medias hace rollback y el siguiente
# deja exactamente el mismo resultado.
@task(name="load_weekly_performance", retries=3, retry_delay_seconds=[2, 10, 30])
def load_weekly_performance(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Upsert en `reporting.weekly_office_program_performance`."""
    return load.load_weekly_performance(rows)


# Paso OPCIONAL (no crítico): sin `retries` a propósito. Si falla, el reporte
# ya está publicado; se registra el aviso y el flow termina `completed`.
@task(name="export_eval_snapshot")
def export_eval_snapshot(
    run_id: str,
    window_start: datetime,
    window_end: datetime,
    result: dict[str, Any],
    load_stats: dict[str, int],
) -> str:
    """Escribe en `data/eval/` un JSON con las filas calculadas y chequeos de validación."""

    rows = result["rows"]
    checks = {
        "rows_written_matches_rows_computed": load_stats["rows_written"] == len(rows),
        "one_currency_per_office": all(
            len({row["currency"] for row in rows if row["office"] == office}) == 1
            for office in {row["office"] for row in rows}
        ),
        "no_negative_kpis": all(
            row["total_material_cost"] >= 0
            and row["kits_delivered_count"] >= 0
            and row["shortage_events_count"] >= 0
            and row["cost_variance_events_count"] >= 0
            for row in rows
        ),
        "unique_office_programme_week": len(
            {(row["office"], row["programme_id"], row["week_start"]) for row in rows}
        )
        == len(rows),
    }
    snapshot = {
        "pipeline": PIPELINE_NAME,
        "run_id": run_id,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "quality": result["quality"],
        "load": load_stats,
        "checks": checks,
        "rows": rows,
    }

    eval_dir = Path(os.environ.get("PIPELINE_EVAL_DIR", _DEFAULT_EVAL_DIR))
    eval_dir.mkdir(parents=True, exist_ok=True)
    # Un archivo por ventana: relanzar la misma ventana lo sobrescribe.
    path = eval_dir / f"snapshot_{window_start:%Y-%m-%d}_{window_end:%Y-%m-%d}.json"
    path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path.resolve())


# retries=2: si el cierre falla, la fila queda `running` y bloquea las
# siguientes corridas hasta que caduque (15 min), así que merece la pena insistir.
@task(name="close_run", retries=2, retry_delay_seconds=5)
def close_run(
    run_id: str, status: str, counters: dict[str, int], error_message: str | None = None
) -> None:
    """Cierra la corrida en `pipeline_runs` y libera el lock. Corre también ante fallo."""
    runs.close_run(run_id, status, error_message=error_message, counters=counters)


@task(name="record_skipped_run", retries=2, retry_delay_seconds=5)
def record_skipped_run(
    trigger: str, window_start: datetime, window_end: datetime, prefect_flow_run_id: str | None
) -> str:
    return runs.record_skipped(trigger, window_start, window_end, prefect_flow_run_id)


# --- Flow --------------------------------------------------------------------


def _failure_message(state: Any) -> str:
    return (state.message or state.name or "fallo sin mensaje")[:2000]


def _run_stages(
    run_id: str,
    window_start: datetime,
    window_end: datetime,
    counters: dict[str, int],
    prefect_flow_run_id: str | None,
) -> dict[str, Any]:
    """extract → transform → load, con el fallo de cada etapa manejado de forma explícita."""

    logger = get_run_logger()

    # Cada etapa crítica se invoca con `return_state=True`: el flow inspecciona
    # el estado en vez de dejar que la excepción se propague sola, para poder
    # anotar en `pipeline_runs` en qué fase falló y cerrar la corrida.
    mark_phase(run_id, "extract", prefect_flow_run_id)
    extract_state = extract_events(window_start, window_end, return_state=True)
    if extract_state.is_failed():
        raise PipelineStageError("extract", _failure_message(extract_state))
    events: pd.DataFrame = extract_state.result()
    counters["rows_read"] = int(len(events))

    mark_phase(run_id, "transform")
    transform_state = transform_weekly_aggregates(events, return_state=True)
    if transform_state.is_failed():
        raise PipelineStageError("transform", _failure_message(transform_state))
    result: dict[str, Any] = transform_state.result()
    counters.update(result["quality"])

    mark_phase(run_id, "load")
    load_state = load_weekly_performance(result["rows"], return_state=True)
    if load_state.is_failed():
        raise PipelineStageError("load", _failure_message(load_state))
    load_stats: dict[str, int] = load_state.result()
    counters.update(load_stats)

    # Paso opcional: su fallo se registra y NO interrumpe la corrida.
    snapshot_state = export_eval_snapshot(
        run_id, window_start, window_end, result, load_stats, return_state=True
    )
    if snapshot_state.is_failed():
        logger.warning(
            "Paso opcional export_eval_snapshot falló; el reporte ya está publicado: %s",
            _failure_message(snapshot_state),
        )
        snapshot_path = None
    else:
        snapshot_path = snapshot_state.result()

    return {"eval_snapshot": snapshot_path}


@flow(name=FLOW_NAME)
def weekly_office_program_performance_flow(
    week_start: date | None = None,
    lookback_weeks: int = LOOKBACK_WEEKS,
    trigger: str = "schedule",
    run_id: str | None = None,
) -> dict[str, Any]:
    """Recalcula el reporte semanal para `lookback_weeks` semanas hasta `week_start`.

    `week_start` (lunes) es por defecto la semana actual. `run_id` solo lo pasa
    el disparo manual de la API, que ya tomó el lock antes de responder `202`.
    """

    logger = get_run_logger()
    window_start, window_end = resolve_window(week_start, lookback_weeks)
    prefect_flow_run_id = str(flow_run.id) if flow_run.id else None
    summary: dict[str, Any] = {
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "trigger": trigger,
    }

    if run_id is None:
        run_id = open_run(trigger, window_start, window_end, prefect_flow_run_id)
        if run_id is None:
            skipped_id = record_skipped_run(trigger, window_start, window_end, prefect_flow_run_id)
            logger.warning("Otra corrida tiene el lock de %s: se omite esta.", PIPELINE_NAME)
            return {**summary, "run_id": skipped_id, "status": "skipped"}

    counters: dict[str, int] = {}
    try:
        extra = _run_stages(run_id, window_start, window_end, counters, prefect_flow_run_id)
    except Exception as exc:
        # `close_run` corre también ante fallo; con `return_state=True` para que
        # un error al cerrar no tape la causa original.
        close_run(run_id, "failed", counters, str(exc)[:2000], return_state=True)
        raise

    close_run(run_id, "completed", counters)
    logger.info("Corrida %s completada: %s", run_id, counters)
    return {**summary, **counters, **extra, "run_id": run_id, "status": "completed"}


# --- Funciones públicas para `services/api/reporting/` -----------------------


def start_manual_run(
    week_start: date | None = None, lookback_weeks: int = LOOKBACK_WEEKS
) -> dict[str, Any] | None:
    """Toma el lock para un disparo manual. `None` si ya hay una corrida `running`."""

    window_start, window_end = resolve_window(week_start, lookback_weeks)
    run_id = runs.open_run("manual", window_start, window_end)
    if run_id is None:
        return None
    return {"run_id": run_id, "window_start": window_start, "window_end": window_end}


def run_weekly_performance_pipeline(
    week_start: date | None = None,
    lookback_weeks: int = LOOKBACK_WEEKS,
    trigger: str = "schedule",
    run_id: str | None = None,
) -> dict[str, Any]:
    """Ejecuta el flow completo y devuelve el resumen de la corrida."""

    return weekly_office_program_performance_flow(
        week_start=week_start, lookback_weeks=lookback_weeks, trigger=trigger, run_id=run_id
    )


def get_latest_run() -> dict[str, Any] | None:
    return runs.get_latest_run()


def query_weekly_kpis(week_start: date | None = None) -> dict[str, Any] | None:
    return queries.query_weekly_kpis(week_start)


# --- CLI ---------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    parser.add_argument(
        "--week-start",
        type=date.fromisoformat,
        default=None,
        help="Lunes (YYYY-MM-DD) de la última semana a recalcular. Por defecto, la actual.",
    )
    parser.add_argument(
        "--lookback-weeks",
        type=int,
        default=LOOKBACK_WEEKS,
        help=f"Semanas a recalcular, incluida --week-start (por defecto {LOOKBACK_WEEKS}).",
    )
    parser.add_argument(
        "--trigger",
        choices=("schedule", "manual"),
        default="manual",
        help="Origen de la corrida que queda en pipeline_runs (por defecto manual).",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help=f"Programa el flow con el cron '{SCHEDULE_CRON}' (UTC) en vez de correrlo ahora.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.serve:
        weekly_office_program_performance_flow.serve(
            name="weekly-office-program-performance",
            cron=SCHEDULE_CRON,
            parameters={"trigger": "schedule"},
        )
    else:
        print(
            json.dumps(
                run_weekly_performance_pipeline(
                    week_start=args.week_start,
                    lookback_weeks=args.lookback_weeks,
                    trigger=args.trigger,
                ),
                indent=2,
                default=str,
            )
        )
