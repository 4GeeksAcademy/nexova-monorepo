"""
Extracción: lee de `public.telemetry_events` en modo SOLO LECTURA.

La ventana `[start, end)` y el filtro por `event_type` se aplican en SQL; nunca
se carga la tabla entera. Los campos de negocio viven dentro de `tags` (JSONB).
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy import bindparam, text

from .config import EVENT_TYPES
from .db import get_engine

EVENT_COLUMNS = (
    "timestamp",
    "event_type",
    "value",
    "office",
    "programme_id",
    "unit_cost",
    "quantity",
    "order_id",
    "event_id",
)

# Los numéricos del JSONB se leen como texto y se convierten en Pandas con
# `errors="coerce"`: un `::int` en SQL haría fallar toda la extracción por un
# solo evento mal formado. El ORDER BY hace el resultado determinista.
_EXTRACT_SQL = text(
    """
    SELECT "timestamp",
           event_type,
           CAST(value AS double precision)     AS value,
           tags ->> 'office'                 AS office,
           NULLIF(tags ->> 'programme_id', '') AS programme_id,
           tags ->> 'unit_cost'                AS unit_cost,
           tags ->> 'quantity'                 AS quantity,
           tags ->> 'order_id'                 AS order_id,
           tags -> '_envelope' ->> 'eventId'   AS event_id
    FROM public.telemetry_events
    WHERE "timestamp" >= :window_start
      AND "timestamp" < :window_end
      AND event_type IN :event_types
    ORDER BY "timestamp", event_id
    """
).bindparams(bindparam("event_types", expanding=True))


def extract_events(window_start: datetime, window_end: datetime) -> pd.DataFrame:
    """Eventos de negocio de la ventana, con tipos ya convertidos."""

    with get_engine().connect() as connection:
        events = pd.read_sql(
            _EXTRACT_SQL,
            connection,
            params={
                "window_start": window_start,
                "window_end": window_end,
                "event_types": list(EVENT_TYPES),
            },
        )

    events = events.reindex(columns=list(EVENT_COLUMNS))
    events["timestamp"] = pd.to_datetime(events["timestamp"], utc=True)
    for column in ("value", "unit_cost", "quantity", "order_id"):
        events[column] = pd.to_numeric(events[column], errors="coerce").astype("float64")
    return events
