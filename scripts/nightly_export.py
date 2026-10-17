"""
Script nocturno de telemetría (ticket #DEV-53).

Para el día objetivo (por defecto, ayer en UTC; `TARGET_DATE=YYYY-MM-DD` lo
sobrescribe):

1. Exporta las filas de `telemetry_events` de ese día a
   `data/raw/telemetry_YYYY-MM-DD.csv`, solo si el archivo no existe. El CSV es
   un backup para auditoría: el pipeline lee de la base de datos, no de aquí.
2. Lanza el pipeline de desempeño (`data/pipelines/pipeline.py`) como
   subproceso, sobre la semana del día objetivo y la anterior.
3. Deja la ejecución en `public.job_runs` (pending → processing → completed |
   failed) a través de `services/job_runner`.

Es un proceso independiente de la API: no importa FastAPI. Lo dispara el
contenedor `scheduler` (ver `services/scheduler/crontab`).

Uso:
    python scripts/nightly_export.py
    TARGET_DATE=2026-10-14 python scripts/nightly_export.py
"""

from __future__ import annotations

import csv
import json
import logging
import os
import signal
import subprocess
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from time import gmtime
from typing import Any, NoReturn

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "services"))

import job_runner  # noqa: E402  (requiere `services/` en sys.path)
from job_runner.db import get_engine  # noqa: E402
from sqlalchemy import text  # noqa: E402

JOB_NAME = "nightly_export"

RAW_DIR = Path(os.environ.get("NIGHTLY_EXPORT_RAW_DIR", REPO_ROOT / "data" / "raw"))
PIPELINES_DIR = Path(os.environ.get("PIPELINES_PATH", REPO_ROOT / "data" / "pipelines"))

# El pipeline tarda minutos; si se cuelga, se corta antes de que la fila se
# considere abandonada (`job_runner.runs.STALE_PROCESSING_HOURS`).
PIPELINE_TIMEOUT_SECONDS = 60 * 60

CSV_COLUMNS = ("id", "timestamp", "service", "event_type", "level", "value", "message", "tags")

_EXPORT_SQL = text(
    """
    SELECT id, "timestamp", service, event_type, level, value, message, tags
    FROM public.telemetry_events
    WHERE "timestamp" >= :window_start
      AND "timestamp" < :window_end
    ORDER BY "timestamp", id
    """
)

logger = logging.getLogger(JOB_NAME)


def _log(level: int, status: str, target_date: date | None, message: str) -> None:
    logger.log(level, message, extra={"status": status, "target_date": target_date or "-"})


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s job=" + JOB_NAME + " status=%(status)s "
        "target_date=%(target_date)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    formatter.converter = gmtime  # timestamps del log siempre en UTC
    handler.setFormatter(formatter)
    logger.handlers[:] = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False


# --- Fecha objetivo ----------------------------------------------------------


def resolve_target_date(env: dict[str, str] | None = None, today: date | None = None) -> date:
    """`TARGET_DATE` si está definida (YYYY-MM-DD); si no, ayer en UTC."""

    raw = (env if env is not None else os.environ).get("TARGET_DATE", "").strip()
    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            raise ValueError(f"TARGET_DATE inválida: {raw!r} (formato esperado YYYY-MM-DD)") from None
    return (today or datetime.now(timezone.utc).date()) - timedelta(days=1)


def day_window(target_date: date) -> tuple[datetime, datetime]:
    """Ventana `[00:00, 24:00)` UTC del día objetivo."""

    start = datetime.combine(target_date, time.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def week_start_of(target_date: date) -> date:
    """Lunes de la semana ISO del día objetivo (lo que espera `--week-start`)."""

    return target_date - timedelta(days=target_date.weekday())


# --- Export CSV --------------------------------------------------------------


def csv_path_for(target_date: date) -> Path:
    return RAW_DIR / f"telemetry_{target_date.isoformat()}.csv"


def _csv_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    """Escribe el CSV de forma atómica: un fallo a medias no deja un archivo válido."""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    try:
        with tmp_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            for row in rows:
                writer.writerow({column: _csv_value(row.get(column)) for column in CSV_COLUMNS})
        tmp_path.replace(path)
    finally:
        tmp_path.unlink(missing_ok=True)


def fetch_telemetry_rows(target_date: date) -> list[dict[str, Any]]:
    window_start, window_end = day_window(target_date)
    with get_engine().connect() as connection:
        result = connection.execute(
            _EXPORT_SQL, {"window_start": window_start, "window_end": window_end}
        )
        return [dict(row) for row in result.mappings()]


def export_csv(target_date: date) -> Path | None:
    """Exporta el día a CSV. Devuelve la ruta, o `None` si el archivo ya existía."""

    path = csv_path_for(target_date)
    if path.exists():
        return None
    write_csv(fetch_telemetry_rows(target_date), path)
    return path


# --- Pipeline ----------------------------------------------------------------


def pipeline_command(target_date: date) -> list[str]:
    return [
        sys.executable,
        str(PIPELINES_DIR / "pipeline.py"),
        "--trigger",
        "schedule",
        "--week-start",
        week_start_of(target_date).isoformat(),
    ]


def parse_pipeline_summary(stdout: str) -> dict[str, Any] | None:
    """Último objeto JSON de nivel superior que imprime la CLI del pipeline."""

    start = stdout.rfind("\n{")
    start = start + 1 if start != -1 else (0 if stdout.startswith("{") else -1)
    if start == -1:
        return None
    try:
        summary = json.loads(stdout[start:])
    except json.JSONDecodeError:
        return None
    return summary if isinstance(summary, dict) else None


def run_pipeline(target_date: date) -> dict[str, Any] | None:
    """Corre el pipeline en un subproceso; lanza `RuntimeError` si no termina bien."""

    completed = subprocess.run(
        pipeline_command(target_date),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,  # el código de salida se trata abajo, con el stderr en el mensaje
        timeout=PIPELINE_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()[-5:]
        raise RuntimeError(
            f"el pipeline terminó con código {completed.returncode}: " + " | ".join(detail)
        )
    summary = parse_pipeline_summary(completed.stdout)
    # Si el pipeline encontró su propio lock tomado, no recalculó nada: la
    # noche no está completa y la siguiente ejecución debe reintentarla.
    if summary and summary.get("status") == "skipped":
        raise RuntimeError("el pipeline se omitió: otra corrida tiene su lock en pipeline_runs")
    return summary


# --- Orquestación ------------------------------------------------------------


def _raise_on_sigterm(signum: int, _frame: Any) -> NoReturn:
    # `docker stop` manda SIGTERM: se convierte en excepción para que el
    # `except` cierre la fila como `failed` antes de salir.
    raise SystemExit(128 + signum)


def run(target_date: date) -> int:
    """Ejecuta el job completo. Devuelve el código de salida del proceso."""

    stale = job_runner.release_stale_locks(JOB_NAME)
    if stale:
        _log(logging.WARNING, job_runner.FAILED, target_date, f"{stale} ejecución(es) abandonadas cerradas como failed")

    if job_runner.has_processing_lock(JOB_NAME):
        _log(logging.INFO, "aborted", target_date, "otra instancia está en processing; se cancela esta")
        return 0

    if job_runner.has_completed_for_date(JOB_NAME, target_date):
        _log(logging.INFO, "skipped", target_date, "ya existe una ejecución completed para este día; omitida por duplicado")
        return 0

    run_id = job_runner.create_run(JOB_NAME, target_date)
    _log(logging.INFO, job_runner.PENDING, target_date, f"run_id={run_id} creado")

    if not job_runner.mark_processing(run_id):
        _log(logging.INFO, "aborted", target_date, f"run_id={run_id} otra instancia tomó el lock primero; se cancela esta")
        return 0
    _log(logging.INFO, job_runner.PROCESSING, target_date, f"run_id={run_id} inicio")

    try:
        csv_path = export_csv(target_date)
        if csv_path is None:
            _log(logging.INFO, job_runner.PROCESSING, target_date, f"run_id={run_id} CSV ya existía, no se reescribe: {csv_path_for(target_date)}")
        else:
            _log(logging.INFO, job_runner.PROCESSING, target_date, f"run_id={run_id} CSV exportado: {csv_path}")

        summary = run_pipeline(target_date)
        pipeline_run = summary.get("run_id") if summary else None
        _log(logging.INFO, job_runner.PROCESSING, target_date, f"run_id={run_id} pipeline completado (pipeline_run_id={pipeline_run})")

        job_runner.mark_completed(run_id)
    except BaseException as exc:
        message = f"{type(exc).__name__}: {exc}"
        job_runner.mark_failed(run_id, message)
        _log(logging.ERROR, job_runner.FAILED, target_date, f"run_id={run_id} {message}")
        if not isinstance(exc, Exception):
            raise
        return 1

    _log(logging.INFO, job_runner.COMPLETED, target_date, f"run_id={run_id} fin")
    return 0


def main() -> int:
    configure_logging()
    signal.signal(signal.SIGTERM, _raise_on_sigterm)
    try:
        target_date = resolve_target_date()
    except ValueError as exc:
        _log(logging.ERROR, job_runner.FAILED, None, str(exc))
        return 2
    _log(logging.INFO, "started", target_date, "inicio del script")
    try:
        return run(target_date)
    except Exception:
        # Fallo antes de tener fila propia (p. ej. la base de datos no responde).
        logger.exception("error sin fila en job_runs", extra={"status": job_runner.FAILED, "target_date": target_date})
        return 1


if __name__ == "__main__":
    sys.exit(main())
