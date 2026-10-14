"""
Alerta de silencio: ¿un cero de negocio es real o la captura se cayó?

Responde la pregunta 4 de PIPELINE_DESIGN.md ("silencio vs. ausencia"). Si la
ventana tiene eventos de negocio, la captura funciona. Si no los tiene, se
cuentan las señales de uso del sistema (logins, vistas del backoffice) en la
misma ventana: con uso, el cero es real; sin uso de ningún tipo, lo más
probable es que la telemetría haya dejado de llegar.

Lee `telemetry_events` en modo SOLO LECTURA.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy import bindparam, text

from .config import SESSION_EVENT_TYPES
from .db import get_engine

CaptureStatus = Literal["active", "no_business_activity", "possible_capture_outage"]

_COUNT_SESSION_EVENTS_SQL = text(
    """
    SELECT count(*)
    FROM public.telemetry_events
    WHERE "timestamp" >= :window_start
      AND "timestamp" < :window_end
      AND event_type IN :event_types
    """
).bindparams(bindparam("event_types", expanding=True))


def count_session_events(window_start: datetime, window_end: datetime) -> int:
    """Logins y vistas del backoffice en la ventana `[start, end)`."""

    with get_engine().connect() as connection:
        return int(
            connection.execute(
                _COUNT_SESSION_EVENTS_SQL,
                {
                    "window_start": window_start,
                    "window_end": window_end,
                    "event_types": list(SESSION_EVENT_TYPES),
                },
            ).scalar_one()
        )


def classify_capture_status(business_events: int, session_events: int) -> CaptureStatus:
    """Interpreta el volumen de la ventana.

    - `active`: hay eventos de negocio, la captura funciona.
    - `no_business_activity`: la gente usó el sistema pero no movió inventario;
      el cero del reporte es real.
    - `possible_capture_outage`: ni negocio ni uso; probablemente la telemetría
      dejó de llegar y el cero del reporte no es fiable.
    """

    if business_events < 0 or session_events < 0:
        raise ValueError("Los conteos de eventos no pueden ser negativos.")
    if business_events > 0:
        return "active"
    if session_events > 0:
        return "no_business_activity"
    return "possible_capture_outage"
