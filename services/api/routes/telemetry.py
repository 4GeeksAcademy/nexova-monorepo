"""
Router: /telemetry

Stub de captura de eventos (Fase 2 del proyecto de telemetría, Nexova).
`POST /telemetry/events` solo valida el formato del envelope y responde
200 — no persiste nada todavía (eso es una fase futura, con Supabase).

`log_telemetry_event` es el punto de entrada que usan otros routers para
emitir eventos "backend" (ej. `login_succeeded`, `inbound_order_created`)
sin hacer una llamada HTTP a este mismo endpoint: construye el mismo
`TelemetryEvent` y produce el mismo log, así el modelo es idéntico
en ambos caminos y reutilizable sin cambios cuando llegue la persistencia.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import APIRouter
from pydantic import BaseModel, Field

load_dotenv()

TELEMETRY_ENDPOINT = os.environ.get("TELEMETRY_ENDPOINT", "http://localhost:8000/telemetry/events")
TELEMETRY_SCHEMA_VERSION = "1.0.0"

router = APIRouter(prefix="/telemetry", tags=["telemetry"])
logger = logging.getLogger(__name__)


class TelemetryEvent(BaseModel):
    """Envelope estándar — ver docs/telemetry/event-schemas.json (`envelope.fields`)."""

    eventId: str
    timestamp: str
    event_type: str = Field(pattern=r"^[a-z]+(_[a-z]+)*$")
    schemaVersion: str
    sessionId: str | None
    userId: str | None
    requestId: str | None
    source: Literal["frontend", "backend"]
    properties: dict[str, Any]


class TelemetryBatch(BaseModel):
    events: list[TelemetryEvent]


class TelemetryReceivedResponse(BaseModel):
    received: int


def _log_batch(events: list[TelemetryEvent]) -> None:
    counts = Counter(event.event_type for event in events)
    logger.info("Received %d telemetry event(s): %s", len(events), dict(counts))


@router.post("/events", response_model=TelemetryReceivedResponse, status_code=200)
def receive_telemetry_events(payload: TelemetryBatch) -> TelemetryReceivedResponse:
    _log_batch(payload.events)
    return TelemetryReceivedResponse(received=len(payload.events))


def log_telemetry_event(
    event_type: str,
    *,
    source: Literal["frontend", "backend"],
    session_id: str | None,
    user_id: str | None,
    request_id: str | None,
    properties: dict[str, Any],
) -> None:
    """Emite (loguea) un evento de telemetría originado en el propio backend."""

    event = TelemetryEvent(
        eventId=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc).isoformat(),
        event_type=event_type,
        schemaVersion=TELEMETRY_SCHEMA_VERSION,
        sessionId=session_id,
        userId=user_id,
        requestId=request_id,
        source=source,
        properties=properties,
    )
    _log_batch([event])
