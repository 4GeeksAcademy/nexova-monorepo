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

Topología: el flow principal no contiene lógica; coordina subflows, uno por
fase, cada uno con entradas y salidas explícitas y ejecutable por separado:

    weekly_office_program_performance_flow
    ├─ extract_business_telemetry_events_flow          (ventana) → eventos
    ├─ compute_weekly_office_program_kpis_flow         (eventos) → filas + calidad
    ├─ publish_weekly_office_program_performance_flow  (filas) → filas escritas/revisadas
    ├─ check_capture_silence_flow                      opcional: ¿cero real o captura caída?
    └─ export_kpi_eval_snapshot_flow                   opcional: JSON de validación

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

from business_performance import extract, load, queries, runs, silence, transform
from business_performance.config import LOOKBACK_WEEKS, PIPELINE_NAME, SCHEDULE_CRON
from business_performance.windows import resolve_window

FLOW_NAME = "weekly_office_program_performance_flow"

# Subir la versión invalida los resultados cacheados si cambia la lógica de
# `prepare_business_events`.
TRANSFORM_VERSION = "v2"

_DEFAULT_EVAL_DIR = Path(__file__).resolve().parent / ".." / "eval" / "business_performance"


class PipelineStageError(RuntimeError):
    """Una etapa crítica (extract/transform/load) falló tras agotar sus reintentos."""

    def __init__(self, phase: str, message: str) -> None:
        super().__init__(f"[{phase}] {message}")
        self.phase = phase


# =============================================================================
# Tasks
# =============================================================================

# --- Bitácora y lock (`reporting.pipeline_runs`) ------------------------------


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


# --- Extracción ---------------------------------------------------------------


# retries=3 con espera creciente (2 s, 10 s, 30 s): la lectura va contra el
# pooler de Supabase, cuyos fallos típicos (conexión reciclada, timeout de red,
# reinicio del pooler) duran segundos. Tres intentos cubren ~40 s de corte; más
# allá es una caída real y conviene fallar y dejarlo en `pipeline_runs`. Es una
# lectura, así que repetirla no tiene efectos.
@task(name="extract_events", retries=3, retry_delay_seconds=[2, 10, 30])
def extract_events(window_start: datetime, window_end: datetime) -> pd.DataFrame:
    """Lee de `telemetry_events` los 4 tipos de evento de la ventana `[start, end)`."""
    return extract.extract_events(window_start, window_end)


# --- Transformación (puras: sin I/O, sin retries) -----------------------------
# No llevan `retries`: no tocan servicios externos, un fallo aquí es un bug y
# reintentar daría el mismo error.


def prepare_events_cache_key(context: TaskRunContext, parameters: dict[str, Any]) -> str:
    """Clave de caché de `prepare_business_events`.

    La define el CONTENIDO de entrada: el hash de los `event_id` leídos (la
    fuente es append-only, así que un `event_id` identifica un evento
    inmutable) más la versión de la lógica. Si llega un evento tardío, el
    conjunto de ids cambia y la clave también, así que nunca se sirve un
    resultado obsoleto.
    """

    events: pd.DataFrame = parameters["events"]
    digest = hashlib.sha256(TRANSFORM_VERSION.encode())
    digest.update(str(len(events)).encode())
    for event_id in sorted(events["event_id"].dropna().astype(str)):
        digest.update(event_id.encode())
    return f"prepare_business_events-{digest.hexdigest()}"


# Caché válida 1 hora: es la etapa costosa de la transformación (limpieza,
# deduplicación y asignación de semana sobre todos los eventos). Si el pipeline
# se relanza (reintento manual, cron + disparo humano) sobre exactamente los
# mismos eventos, Prefect reutiliza el resultado persistido en vez de
# recalcularlo. Pasada la hora se recalcula. Es seguro porque la función es pura.
@task(
    name="prepare_business_events",
    cache_key_fn=prepare_events_cache_key,
    cache_expiration=timedelta(hours=1),
    persist_result=True,
)
def prepare_business_events(events: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Deduplica, descarta oficinas inválidas, asigna programa y semana ISO."""
    return transform.prepare_business_events(events)


@task(name="compute_total_material_cost")
def compute_total_material_cost(prepared: pd.DataFrame) -> pd.DataFrame:
    """KPI "Costo de material por oficina/programa"."""
    return transform.compute_total_material_cost(prepared)


@task(name="compute_kits_delivered_count")
def compute_kits_delivered_count(prepared: pd.DataFrame) -> pd.DataFrame:
    """KPI "Kits entregados"."""
    return transform.compute_kits_delivered_count(prepared)


@task(name="compute_shortage_events_count")
def compute_shortage_events_count(prepared: pd.DataFrame) -> pd.DataFrame:
    """KPI "Frecuencia de escasez"."""
    return transform.compute_shortage_events_count(prepared)


@task(name="compute_cost_variance_events_count")
def compute_cost_variance_events_count(prepared: pd.DataFrame) -> pd.DataFrame:
    """KPI "Frecuencia de variación de costo"."""
    return transform.compute_cost_variance_events_count(prepared)


@task(name="assemble_weekly_office_program_rows")
def assemble_weekly_office_program_rows(
    prepared: pd.DataFrame,
    total_material_cost: pd.DataFrame,
    kits_delivered_count: pd.DataFrame,
    shortage_events_count: pd.DataFrame,
    cost_variance_events_count: pd.DataFrame,
) -> list[dict[str, Any]]:
    """Una fila por oficina × programa × semana con los 4 KPIs y su moneda."""
    return transform.assemble_weekly_office_program_rows(
        prepared,
        total_material_cost,
        kits_delivered_count,
        shortage_events_count,
        cost_variance_events_count,
    )


# --- Carga --------------------------------------------------------------------


# retries=3 con la misma espera que la extracción. Reintentar una escritura
# solo es seguro porque la carga es idempotente: upsert con reemplazo en una
# única transacción, así que un intento a medias hace rollback y el siguiente
# deja exactamente el mismo resultado.
@task(name="load_weekly_performance", retries=3, retry_delay_seconds=[2, 10, 30])
def load_weekly_performance(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Upsert en `reporting.weekly_office_program_performance`."""
    return load.load_weekly_performance(rows)


# --- Pasos opcionales -----------------------------------------------------------


# retries=2: es una sola lectura de conteo; si aun así falla, el subflow
# opcional falla y la corrida sigue.
@task(name="count_session_events", retries=2, retry_delay_seconds=5)
def count_session_events(window_start: datetime, window_end: datetime) -> int:
    """Logins y vistas del backoffice en la ventana (solo lectura)."""
    return silence.count_session_events(window_start, window_end)


# Sin `retries` a propósito: si falla, el reporte ya está publicado.
@task(name="export_eval_snapshot")
def export_eval_snapshot(
    run_id: str,
    window_start: datetime,
    window_end: datetime,
    result: dict[str, Any],
    load_stats: dict[str, int],
    capture: dict[str, Any] | None = None,
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
        "capture": capture,
        "checks": checks,
        "rows": rows,
    }

    eval_dir = Path(os.environ.get("PIPELINE_EVAL_DIR", _DEFAULT_EVAL_DIR))
    eval_dir.mkdir(parents=True, exist_ok=True)
    # Un archivo por ventana: relanzar la misma ventana lo sobrescribe.
    path = eval_dir / f"snapshot_{window_start:%Y-%m-%d}_{window_end:%Y-%m-%d}.json"
    path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path.resolve())


# =============================================================================
# Subflows — uno por fase, con entradas y salidas explícitas
# =============================================================================


@flow(name="extract_business_telemetry_events_flow")
def extract_business_telemetry_events_flow(
    window_start: datetime, window_end: datetime
) -> pd.DataFrame:
    """Fase 1 — eventos de negocio (`inbound/outbound_order_created`,
    `stock_threshold_triggered`, `kit_cost_variance_detected`) de la ventana."""
    return extract_events(window_start, window_end)


@flow(name="compute_weekly_office_program_kpis_flow")
def compute_weekly_office_program_kpis_flow(events: pd.DataFrame) -> dict[str, Any]:
    """Fase 2 — los 4 KPIs del CONTEXT por oficina × programa × semana ISO.

    No toca la base de datos: se puede correr sobre cualquier DataFrame con la
    forma de la extracción. Devuelve `{"rows": [...], "quality": {...}}`.
    """

    prepared, quality = prepare_business_events(events)
    rows = assemble_weekly_office_program_rows(
        prepared,
        compute_total_material_cost(prepared),
        compute_kits_delivered_count(prepared),
        compute_shortage_events_count(prepared),
        compute_cost_variance_events_count(prepared),
    )
    return {"rows": rows, "quality": quality}


@flow(name="publish_weekly_office_program_performance_flow")
def publish_weekly_office_program_performance_flow(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    """Fase 3 — upsert idempotente en `reporting.weekly_office_program_performance`.

    Devuelve `{"rows_written", "rows_revised"}`.
    """
    return load_weekly_performance(rows)


@flow(name="check_capture_silence_flow")
def check_capture_silence_flow(
    window_start: datetime, window_end: datetime, business_events: int
) -> dict[str, Any]:
    """Opcional — distingue "no hubo movimiento" de "la captura se cayó".

    Responde la pregunta 4 de PIPELINE_DESIGN.md. Solo consulta la base de
    datos cuando la ventana no tiene eventos de negocio.
    """

    logger = get_run_logger()
    session_events = (
        None if business_events > 0 else count_session_events(window_start, window_end)
    )
    status = silence.classify_capture_status(business_events, session_events or 0)
    if status == "possible_capture_outage":
        logger.warning(
            "Ventana sin eventos de negocio ni de sesión (%s → %s): posible caída de la "
            "captura de telemetría; el reporte de esta ventana puede no ser fiable.",
            window_start.isoformat(),
            window_end.isoformat(),
        )
    elif status == "no_business_activity":
        logger.info(
            "Sin movimientos de inventario, pero con %d eventos de sesión: el cero es real.",
            session_events,
        )
    return {"status": status, "business_events": business_events, "session_events": session_events}


@flow(name="export_kpi_eval_snapshot_flow")
def export_kpi_eval_snapshot_flow(
    run_id: str,
    window_start: datetime,
    window_end: datetime,
    result: dict[str, Any],
    load_stats: dict[str, int],
    capture: dict[str, Any] | None = None,
) -> str:
    """Opcional — JSON de validación en `data/eval/business_performance/`."""
    return export_eval_snapshot(run_id, window_start, window_end, result, load_stats, capture)


# =============================================================================
# Flow principal — solo coordina
# =============================================================================


def _failure_message(state: Any) -> str:
    return (state.message or state.name or "fallo sin mensaje")[:2000]


def _run_phases(
    run_id: str,
    window_start: datetime,
    window_end: datetime,
    counters: dict[str, int],
    prefect_flow_run_id: str | None,
) -> dict[str, Any]:
    """extract → compute → publish, con el fallo de cada subflow manejado de forma explícita."""

    logger = get_run_logger()

    # Cada subflow crítico se invoca con `return_state=True`: el flow principal
    # inspecciona el estado en vez de dejar que la excepción se propague sola,
    # para anotar en `pipeline_runs` en qué fase falló y cerrar la corrida.
    mark_phase(run_id, "extract", prefect_flow_run_id)
    extract_state = extract_business_telemetry_events_flow(
        window_start, window_end, return_state=True
    )
    if extract_state.is_failed():
        raise PipelineStageError("extract", _failure_message(extract_state))
    events: pd.DataFrame = extract_state.result()
    counters["rows_read"] = int(len(events))

    mark_phase(run_id, "transform")
    kpis_state = compute_weekly_office_program_kpis_flow(events, return_state=True)
    if kpis_state.is_failed():
        raise PipelineStageError("transform", _failure_message(kpis_state))
    result: dict[str, Any] = kpis_state.result()
    counters.update(result["quality"])

    mark_phase(run_id, "load")
    publish_state = publish_weekly_office_program_performance_flow(
        result["rows"], return_state=True
    )
    if publish_state.is_failed():
        raise PipelineStageError("load", _failure_message(publish_state))
    load_stats: dict[str, int] = publish_state.result()
    counters.update(load_stats)

    # Subflows opcionales: su fallo se registra y NO interrumpe la corrida.
    capture: dict[str, Any] | None = None
    silence_state = check_capture_silence_flow(
        window_start, window_end, counters["rows_read"], return_state=True
    )
    if silence_state.is_failed():
        logger.warning(
            "Subflow opcional check_capture_silence_flow falló: %s",
            _failure_message(silence_state),
        )
    else:
        capture = silence_state.result()

    snapshot_state = export_kpi_eval_snapshot_flow(
        run_id, window_start, window_end, result, load_stats, capture, return_state=True
    )
    if snapshot_state.is_failed():
        logger.warning(
            "Subflow opcional export_kpi_eval_snapshot_flow falló; el reporte ya está publicado: %s",
            _failure_message(snapshot_state),
        )
        snapshot_path = None
    else:
        snapshot_path = snapshot_state.result()

    return {
        "capture_status": capture["status"] if capture else None,
        "eval_snapshot": snapshot_path,
    }


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
        extra = _run_phases(run_id, window_start, window_end, counters, prefect_flow_run_id)
    except Exception as exc:
        # `close_run` corre también ante fallo; con `return_state=True` para que
        # un error al cerrar no tape la causa original.
        close_run(run_id, "failed", counters, str(exc)[:2000], return_state=True)
        raise

    close_run(run_id, "completed", counters)
    logger.info("Corrida %s completada: %s", run_id, counters)
    return {**summary, **counters, **extra, "run_id": run_id, "status": "completed"}


# =============================================================================
# Funciones públicas para `services/api/reporting/`
# =============================================================================


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


# =============================================================================
# CLI
# =============================================================================


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
