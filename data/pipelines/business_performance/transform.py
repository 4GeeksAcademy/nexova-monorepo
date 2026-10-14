"""
Transformación: de eventos sueltos a una fila por oficina × programa × semana ISO.

Función pura (sin I/O ni reloj): los mismos eventos dan siempre el mismo
resultado, que es lo que permite cachearla y reintentar la carga sin riesgo.
Sin loops: solo operaciones vectorizadas y `groupby`.
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


def _drop_duplicates(df: pd.DataFrame) -> pd.DataFrame:
    """Deduplica por `event_id` y, en los eventos con orden, por (event_type, order_id).

    Nunca se deduplica sobre claves nulas: colapsaría eventos distintos.
    """

    by_event_id = df["event_id"].notna() & df.duplicated(subset=["event_id"], keep="first")
    df = df[~by_event_id]

    has_order = df["event_type"].isin(ORDER_EVENT_TYPES) & df["order_id"].notna()
    by_order = has_order & df.duplicated(subset=["event_type", "order_id"], keep="first")
    return df[~by_order]


def transform_weekly_aggregates(events: pd.DataFrame) -> dict[str, Any]:
    """Calcula los 4 KPIs del CONTEXT y los contadores de calidad de dato.

    Devuelve `{"rows": [...], "quality": {...}}` con tipos planos (str, int,
    float), listo para persistir como resultado cacheado y para la carga.
    """

    quality = {
        "rows_read": int(len(events)),
        "rows_missing_cost": 0,
        "rows_unassigned_programme": 0,
        "rows_invalid_office": 0,
        "rows_duplicates_dropped": 0,
    }
    if events.empty:
        return {"rows": [], "quality": quality}

    df = events.dropna(subset=["timestamp", "event_type"]).copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values(["timestamp", "event_id"], kind="stable", na_position="last")

    deduplicated = _drop_duplicates(df)
    quality["rows_duplicates_dropped"] = int(len(df) - len(deduplicated))
    df = deduplicated.copy()

    # Solo `valencia` y `miami`; cualquier otro valor se descarta y se cuenta.
    df["office"] = df["office"].astype("string").str.strip().str.lower()
    valid_office = df["office"].isin(list(CURRENCY_BY_OFFICE)).fillna(False).astype(bool)
    quality["rows_invalid_office"] = int((~valid_office).sum())
    df = df[valid_office].copy()
    if df.empty:
        return {"rows": [], "quality": quality}

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

    is_inbound = df["event_type"] == EVENT_INBOUND
    # D4: un costo ausente se excluye de la suma y se cuenta; tratarlo como 0
    # ocultaría datos faltantes.
    has_cost = df["unit_cost"].notna() & df["quantity"].notna()
    quality["rows_missing_cost"] = int((is_inbound & ~has_cost).sum())

    line_cost = (df["unit_cost"] * df["quantity"]).where(is_inbound & has_cost, 0.0)
    df["total_material_cost"] = line_cost.astype("float64")
    # D8: kits entregados = conteo de eventos, no Σ quantity.
    df["kits_delivered_count"] = (df["event_type"] == EVENT_OUTBOUND).astype("int64")
    df["shortage_events_count"] = (df["event_type"] == EVENT_SHORTAGE).astype("int64")
    # D1: solo subidas de costo (`value` guarda `variance_pct`).
    df["cost_variance_events_count"] = (
        (df["event_type"] == EVENT_COST_VARIANCE) & (df["value"] > 0)
    ).astype("int64")

    aggregated = df.groupby(GROUP_KEYS, sort=True)[KPI_COLUMNS].sum().reset_index()
    aggregated["total_material_cost"] = aggregated["total_material_cost"].round(COST_DECIMALS)
    aggregated["currency"] = aggregated["office"].map(CURRENCY_BY_OFFICE)

    rows = [
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
        for row in aggregated.to_dict(orient="records")
    ]
    return {"rows": rows, "quality": quality}
