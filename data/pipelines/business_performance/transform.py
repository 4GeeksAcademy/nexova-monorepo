"""
Transformación: de eventos sueltos a una fila por oficina × programa × semana ISO.

Funciones puras (sin I/O ni reloj): los mismos eventos dan siempre el mismo
resultado, que es lo que permite cachearlas, testearlas sin base de datos y
reintentar la carga sin riesgo. Sin loops: solo operaciones vectorizadas.

Se divide en una función por KPI del CONTEXT para que cada una sea una task
independiente en el flow (ver `pipeline.py`):

    prepare_business_events               limpia, deduplica y asigna semana
      ├─ compute_total_material_cost        Costo de material por oficina/programa
      ├─ compute_kits_delivered_count       Kits entregados
      ├─ compute_shortage_events_count      Frecuencia de escasez
      └─ compute_cost_variance_events_count Frecuencia de variación de costo
    assemble_weekly_office_program_rows   une los 4 KPIs y añade la moneda
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .config import (
    COST_DECIMALS,
    CURRENCY_BY_OFFICE,
    EVENT_COST_VARIANCE,
    EVENT_INBOUND,
    EVENT_OUTBOUND,
    EVENT_SHORTAGE,
    ORDER_EVENT_TYPES,
    UNASSIGNED_PROGRAMME,
)

GROUP_KEYS = ["office", "programme_id", "week_start"]
KPI_COLUMNS = [
    "total_material_cost",
    "kits_delivered_count",
    "shortage_events_count",
    "cost_variance_events_count",
]
PREPARED_COLUMNS = [*GROUP_KEYS, "event_type", "value", "unit_cost", "quantity"]


def empty_quality(rows_read: int = 0) -> dict[str, int]:
    return {
        "rows_read": int(rows_read),
        "rows_missing_cost": 0,
        "rows_unassigned_programme": 0,
        "rows_invalid_office": 0,
        "rows_duplicates_dropped": 0,
    }


def _drop_duplicates(df: pd.DataFrame) -> pd.DataFrame:
    """Deduplica por `event_id` y, en los eventos con orden, por (event_type, order_id).

    Nunca se deduplica sobre claves nulas: colapsaría eventos distintos.
    """

    by_event_id = df["event_id"].notna() & df.duplicated(subset=["event_id"], keep="first")
    df = df[~by_event_id]

    has_order = df["event_type"].isin(ORDER_EVENT_TYPES) & df["order_id"].notna()
    by_order = has_order & df.duplicated(subset=["event_type", "order_id"], keep="first")
    return df[~by_order]


def prepare_business_events(events: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Deja los eventos listos para agregar y cuenta los problemas de calidad.

    Devuelve los eventos válidos con `office` normalizada, `programme_id`
    (o 'unassigned') y `week_start` (lunes UTC), más los contadores que se
    guardan en `pipeline_runs`.
    """

    quality = empty_quality(len(events))
    if events.empty:
        return pd.DataFrame(columns=PREPARED_COLUMNS), quality

    df = events.dropna(subset=["timestamp", "event_type"]).copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    for column in ("value", "unit_cost", "quantity", "order_id"):
        df[column] = pd.to_numeric(df[column], errors="coerce").astype("float64")
    df = df.sort_values(["timestamp", "event_id"], kind="stable", na_position="last")

    deduplicated = _drop_duplicates(df)
    quality["rows_duplicates_dropped"] = int(len(df) - len(deduplicated))
    df = deduplicated.copy()

    # Solo `valencia` y `miami`; cualquier otro valor (o nulo) se descarta y se cuenta.
    df["office"] = df["office"].astype("string").str.strip().str.lower()
    valid_office = df["office"].isin(list(CURRENCY_BY_OFFICE)).fillna(False).astype(bool)
    quality["rows_invalid_office"] = int((~valid_office).sum())
    df = df[valid_office].copy()
    if df.empty:
        return pd.DataFrame(columns=PREPARED_COLUMNS), quality

    # D2: `programme_id` nulo o vacío → 'unassigned'.
    programme = df["programme_id"].astype("string").str.strip()
    unassigned = (programme.isna() | (programme == "")).astype(bool)
    quality["rows_unassigned_programme"] = int(unassigned.sum())
    df["programme_id"] = programme.mask(unassigned, UNASSIGNED_PROGRAMME).astype(object)
    df["office"] = df["office"].astype(object)

    # `week_start`: lunes 00:00 UTC de la semana ISO del evento.
    day = df["timestamp"].dt.normalize()
    monday = day - pd.to_timedelta(df["timestamp"].dt.weekday, unit="D")
    df["week_start"] = monday.dt.strftime("%Y-%m-%d")

    # D4: una entrada sin costo (o con cantidad ilegible) no suma, pero se cuenta.
    is_inbound = df["event_type"] == EVENT_INBOUND
    missing_cost = df["unit_cost"].isna() | df["quantity"].isna()
    quality["rows_missing_cost"] = int((is_inbound & missing_cost).sum())

    return df[PREPARED_COLUMNS].reset_index(drop=True), quality


def _per_group(prepared: pd.DataFrame, contribution: pd.Series, column: str) -> pd.DataFrame:
    """Suma la contribución de cada evento por oficina × programa × semana."""

    frame = prepared[GROUP_KEYS].assign(**{column: contribution.to_numpy()})
    return frame.groupby(GROUP_KEYS, sort=True, as_index=False)[column].sum()


def compute_total_material_cost(prepared: pd.DataFrame) -> pd.DataFrame:
    """KPI "Costo de material por oficina/programa": Σ(unit_cost × quantity) de
    `inbound_order_created`, en la moneda local. Las entradas sin costo no suman (D4)."""

    is_inbound = prepared["event_type"] == EVENT_INBOUND
    has_cost = prepared["unit_cost"].notna() & prepared["quantity"].notna()
    line_cost = (prepared["unit_cost"] * prepared["quantity"]).where(is_inbound & has_cost, 0.0)
    result = _per_group(prepared, line_cost.astype("float64"), "total_material_cost")
    # D11: redondeo para que el agregado no dependa del orden de suma.
    result["total_material_cost"] = result["total_material_cost"].round(COST_DECIMALS)
    return result


def compute_kits_delivered_count(prepared: pd.DataFrame) -> pd.DataFrame:
    """KPI "Kits entregados": conteo de eventos `outbound_order_created`, no Σ quantity (D8)."""

    delivered = (prepared["event_type"] == EVENT_OUTBOUND).astype("int64")
    return _per_group(prepared, delivered, "kits_delivered_count")


def compute_shortage_events_count(prepared: pd.DataFrame) -> pd.DataFrame:
    """KPI "Frecuencia de escasez": conteo de `stock_threshold_triggered`."""

    shortage = (prepared["event_type"] == EVENT_SHORTAGE).astype("int64")
    return _per_group(prepared, shortage, "shortage_events_count")


def compute_cost_variance_events_count(prepared: pd.DataFrame) -> pd.DataFrame:
    """KPI "Frecuencia de variación de costo": conteo de `kit_cost_variance_detected`
    con subida de costo (`value` = variance_pct > 0, D1)."""

    increase = ((prepared["event_type"] == EVENT_COST_VARIANCE) & (prepared["value"] > 0)).astype(
        "int64"
    )
    return _per_group(prepared, increase, "cost_variance_events_count")


def assemble_weekly_office_program_rows(
    prepared: pd.DataFrame,
    total_material_cost: pd.DataFrame,
    kits_delivered_count: pd.DataFrame,
    shortage_events_count: pd.DataFrame,
    cost_variance_events_count: pd.DataFrame,
) -> list[dict[str, Any]]:
    """Une los 4 KPIs en una fila por oficina × programa × semana y añade la moneda.

    Solo hay fila para las combinaciones con algún evento. Devuelve tipos
    planos (str, int, float), listos para persistir y para la carga.
    """

    if prepared.empty:
        return []

    combined = prepared[GROUP_KEYS].drop_duplicates()
    for kpi in (
        total_material_cost,
        kits_delivered_count,
        shortage_events_count,
        cost_variance_events_count,
    ):
        combined = combined.merge(kpi, on=GROUP_KEYS, how="left")
    combined[KPI_COLUMNS] = combined[KPI_COLUMNS].fillna(0)
    # La moneda sale de la oficina: nunca se mezclan EUR y USD en una fila.
    combined["currency"] = combined["office"].map(CURRENCY_BY_OFFICE)
    combined = combined.sort_values(GROUP_KEYS, kind="stable")

    return [
        {
            "office": str(row["office"]),
            "programme_id": str(row["programme_id"]),
            "week_start": str(row["week_start"]),
            "total_material_cost": float(row["total_material_cost"]),
            "kits_delivered_count": int(row["kits_delivered_count"]),
            "shortage_events_count": int(row["shortage_events_count"]),
            "cost_variance_events_count": int(row["cost_variance_events_count"]),
            "currency": str(row["currency"]),
        }
        for row in combined.to_dict(orient="records")
    ]


def transform_weekly_aggregates(events: pd.DataFrame) -> dict[str, Any]:
    """Toda la transformación en una llamada: `{"rows": [...], "quality": {...}}`."""

    prepared, quality = prepare_business_events(events)
    rows = assemble_weekly_office_program_rows(
        prepared,
        compute_total_material_cost(prepared),
        compute_kits_delivered_count(prepared),
        compute_shortage_events_count(prepared),
        compute_cost_variance_events_count(prepared),
    )
    return {"rows": rows, "quality": quality}
