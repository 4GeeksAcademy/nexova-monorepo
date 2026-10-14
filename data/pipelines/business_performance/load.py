"""
Carga: upsert idempotente en `reporting.weekly_office_program_performance`.

Se apoya en `unique (office, programme_id, week_start)` y REEMPLAZA valores
(nunca suma), todo en una sola transacción: si falla a mitad, el rollback deja
la tabla como estaba y la siguiente corrida converge al mismo resultado.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from .config import COST_DECIMALS
from .db import get_engine, weekly_performance

_CONFLICT_KEYS = ["office", "programme_id", "week_start"]
_REPLACED_COLUMNS = [
    "total_material_cost",
    "kits_delivered_count",
    "shortage_events_count",
    "cost_variance_events_count",
    "currency",
]


def _comparable(row: dict[str, Any]) -> tuple[Any, ...]:
    cost = round(Decimal(str(row["total_material_cost"])), COST_DECIMALS)
    return (
        cost,
        int(row["kits_delivered_count"]),
        int(row["shortage_events_count"]),
        int(row["cost_variance_events_count"]),
        row["currency"],
    )


def load_weekly_performance(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Publica las filas y devuelve `{"rows_written", "rows_revised"}`.

    `rows_revised` cuenta las filas que ya estaban publicadas y cambian de
    valor: en una semana cerrada delata un evento tardío.
    """

    if not rows:
        return {"rows_written": 0, "rows_revised": 0}

    values = [{**row, "week_start": date.fromisoformat(row["week_start"])} for row in rows]
    weeks = sorted({value["week_start"] for value in values})

    statement = insert(weekly_performance).values(values)
    statement = statement.on_conflict_do_update(
        index_elements=_CONFLICT_KEYS,
        set_={
            **{column: statement.excluded[column] for column in _REPLACED_COLUMNS},
            "computed_at": func.now(),
        },
    )

    with get_engine().begin() as connection:
        published = connection.execute(
            select(
                *(weekly_performance.c[key] for key in _CONFLICT_KEYS),
                *(weekly_performance.c[column] for column in _REPLACED_COLUMNS),
            ).where(weekly_performance.c.week_start.in_(weeks))
        ).mappings()
        before = {
            tuple(row[key] for key in _CONFLICT_KEYS): _comparable(dict(row)) for row in published
        }
        connection.execute(statement)

    revised = sum(
        1
        for value in values
        if (key := tuple(value[k] for k in _CONFLICT_KEYS)) in before
        and before[key] != _comparable(value)
    )
    return {"rows_written": len(values), "rows_revised": revised}
