"""Lectura de los KPIs publicados, con la forma del contrato del CONTEXT §6."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, select

from .db import get_engine, weekly_performance


def query_weekly_kpis(week_start: date | None = None) -> dict[str, Any] | None:
    """KPIs de todas las combinaciones oficina/programa de una semana.

    Sin `week_start` devuelve la semana calculada más reciente; `None` si la
    tabla está vacía. Una semana sin filas devuelve `entries: []`.
    """

    with get_engine().connect() as connection:
        if week_start is None:
            week_start = connection.execute(
                select(func.max(weekly_performance.c.week_start))
            ).scalar_one_or_none()
            if week_start is None:
                return None

        rows = connection.execute(
            select(
                weekly_performance.c.office,
                weekly_performance.c.programme_id,
                weekly_performance.c.total_material_cost,
                weekly_performance.c.kits_delivered_count,
                weekly_performance.c.shortage_events_count,
                weekly_performance.c.cost_variance_events_count,
                weekly_performance.c.currency,
            )
            .where(weekly_performance.c.week_start == week_start)
            .order_by(weekly_performance.c.office, weekly_performance.c.programme_id)
        ).mappings()
        entries = [
            {**row, "total_material_cost": float(row["total_material_cost"])} for row in rows
        ]

    return {"week_start": week_start.isoformat(), "entries": entries}
