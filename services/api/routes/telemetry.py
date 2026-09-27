"""
Router: /telemetry

Ingesta de eventos de telemetría (Nexova). `POST /telemetry/events` recibe
lotes del `TelemetryService` del backoffice, valida cada evento por separado
contra `TelemetryEvent` y persiste los válidos en Supabase
(`telemetry_events`) con un único bulk insert por lote. Un evento inválido se
rechaza solo, sin cancelar el resto del lote. Ver `telemetry_store.py` y
docs/telemetry/telemetry-plan.md §4.

`log_telemetry_event` es el punto de entrada que usan otros routers para
emitir eventos "backend" (ej. `login_succeeded`, `inbound_order_created`)
sin hacer una llamada HTTP a este mismo endpoint: construye el mismo
`TelemetryEvent` y lo persiste con el mismo mapeo, así el modelo es
idéntico en ambos caminos.
"""

from __future__ import annotations

import logging
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from telemetry_store import TelemetryRejected, bulk_insert, to_row

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


class RawTelemetryBatch(BaseModel):
    """Envelope del lote, laxo a propósito: cada evento se valida uno a uno en
    el handler. Tiparlo como `list[TelemetryEvent]` haría que un solo evento
    inválido devolviera 422 para todo el lote."""

    events: list[Any]


class TelemetryIngestResponse(BaseModel):
    received: int
    stored: int
    rejected: int


def _log_batch(event_types: list[str], rejected: int) -> None:
    counts = Counter(event_types)
    logger.info(
        "Stored %d telemetry event(s), rejected %d: %s", len(event_types), rejected, dict(counts)
    )


@router.post("/events", response_model=TelemetryIngestResponse, status_code=200)
def receive_telemetry_events(payload: RawTelemetryBatch) -> TelemetryIngestResponse:
    rows: list[dict[str, Any]] = []
    rejected = 0

    for raw in payload.events:
        try:
            rows.append(to_row(TelemetryEvent.model_validate(raw)))
        except (ValidationError, TelemetryRejected) as exc:
            rejected += 1
            logger.info("Telemetry event rejected: %s", exc)

    try:
        bulk_insert(rows)
    except SQLAlchemyError:
        # 5xx para que el TelemetryService reintente el lote (solo mira res.ok).
        logger.exception("Telemetry bulk insert failed (%d row(s))", len(rows))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telemetry storage unavailable.",
        )

    _log_batch([row["event_type"] for row in rows], rejected)
    return TelemetryIngestResponse(
        received=len(payload.events), stored=len(rows), rejected=rejected
    )


def log_telemetry_event(
    event_type: str,
    *,
    source: Literal["frontend", "backend"],
    session_id: str | None,
    user_id: str | None,
    request_id: str | None,
    properties: dict[str, Any],
) -> None:
    """Emite y persiste un evento de telemetría originado en el propio backend.

    Nunca lanza: si el evento no cumple el contrato o Supabase falla, se
    registra un warning y la petición de negocio que lo emitió sigue su curso.
    """

    try:
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
        bulk_insert([to_row(event)])
    except (ValidationError, TelemetryRejected, SQLAlchemyError):
        logger.warning("Backend telemetry event %s not stored", event_type, exc_info=True)
        return
    _log_batch([event_type], 0)
