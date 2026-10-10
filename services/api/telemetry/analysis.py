"""
Pipeline de análisis de telemetría: métricas técnicas/operacionales sobre
`telemetry_events`. Una función por métrica, todas con la misma fórmula:

    cargar (SQL) → refinar (Pandas) → convertir tipos → agrupar → agregar → servir

Reglas que cumple cada función:
- Reciben `start_date` / `end_date` ya resueltos por el endpoint (inicio
  inclusivo, fin exclusivo, UTC). La ventana y el filtro por `event_type` se
  aplican UNA vez, en SQL; nunca se carga la tabla entera ni se vuelve a
  filtrar la ventana en Pandas.
- `timestamp` se convierte con `pd.to_datetime(..., utc=True)` ANTES de
  agrupar: agrupar strings que parecen fechas no falla, pero da grupos erróneos.
- Sin loops: solo `groupby` / `agg` / operaciones vectorizadas.
- Sin efectos secundarios: mismos parámetros → mismo resultado.
- Devuelven `list[dict]` serializable a JSON (fechas como "YYYY-MM-DD").
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

import pandas as pd
from sqlalchemy import select

from database import engine
from telemetry_store import telemetry_events

AUTH_FAILED = "login_failed"
AUTH_SUCCEEDED = "login_succeeded"

_COLUMNS = (
    telemetry_events.c.timestamp,
    telemetry_events.c.event_type,
    telemetry_events.c.level,
    telemetry_events.c.value,
    telemetry_events.c.tags,
)


def _load_events(
    start_date: datetime, end_date: datetime, event_types: Sequence[str] | None = None
) -> pd.DataFrame:
    """Carga solo las filas necesarias: ventana [start, end) y tipos, en SQL."""

    statement = select(*_COLUMNS).where(
        telemetry_events.c.timestamp >= start_date,
        telemetry_events.c.timestamp < end_date,
    )
    if event_types is not None:
        statement = statement.where(telemetry_events.c.event_type.in_(list(event_types)))
    return pd.read_sql(statement, engine)


def _with_date(df: pd.DataFrame) -> pd.DataFrame:
    """Refina y convierte tipos: sin filas sin dimensión y con columna `date`."""

    df = df.dropna(subset=["timestamp", "event_type"]).copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["date"] = df["timestamp"].dt.strftime("%Y-%m-%d")
    return df


def events_per_day(start_date: datetime, end_date: datetime) -> list[dict[str, Any]]:
    """¿Qué eventos ocurren y con qué frecuencia cada día?"""

    df = _load_events(start_date, end_date)
    if df.empty:
        return []
    df = _with_date(df)
    counts = df.groupby(["date", "event_type"]).size().rename("count")
    return counts.reset_index().to_dict(orient="records")


def error_rate_by_type(start_date: datetime, end_date: datetime) -> list[dict[str, Any]]:
    """¿Qué tipos de error aparecen y qué peso tienen en el tráfico de cada día?

    `error_rate` = errores de ese `event_type` / total de eventos del día.
    """

    df = _load_events(start_date, end_date)
    if df.empty:
        return []
    df = _with_date(df)
    df["is_error"] = df["level"] == "error"

    total_per_day = df.groupby("date").size().rename("total_events")
    errors = (
        df[df["is_error"]].groupby(["date", "event_type"]).size().rename("errors").reset_index()
    )
    result = errors.merge(total_per_day.reset_index(), on="date")
    result["error_rate"] = (result["errors"] / result["total_events"]).round(4)
    return result.to_dict(orient="records")


def auth_failure_rate(start_date: datetime, end_date: datetime) -> list[dict[str, Any]]:
    """¿Qué fracción de los intentos de login falla cada día?

    `failure_rate` = login_failed / (login_failed + login_succeeded).
    """

    df = _load_events(start_date, end_date, event_types=(AUTH_FAILED, AUTH_SUCCEEDED))
    if df.empty:
        return []
    df = _with_date(df)

    attempts = (
        df.groupby(["date", "event_type"])
        .size()
        .unstack(fill_value=0)
        .reindex(columns=[AUTH_FAILED, AUTH_SUCCEEDED], fill_value=0)
        .rename(columns={AUTH_FAILED: "failed", AUTH_SUCCEEDED: "succeeded"})
    )
    attempts["failure_rate"] = (attempts["failed"] / (attempts["failed"] + attempts["succeeded"])).round(4)
    return attempts.reset_index().to_dict(orient="records")
