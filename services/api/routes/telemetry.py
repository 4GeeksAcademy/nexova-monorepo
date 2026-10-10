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
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError

import cache
from models import User
from security import get_current_user
from telemetry import analysis
from telemetry_store import TelemetryRejected, bulk_insert, to_row

TELEMETRY_SCHEMA_VERSION = "1.0.0"

REPORT_DEFAULT_WINDOW = timedelta(days=7)
REPORT_CACHE_TTL_SECONDS = 60

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


class ReportPeriod(BaseModel):
    # `from` es palabra reservada en Python: el campo se llama `from_` y se
    # serializa como "from".
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str


class EventsPerDayRow(BaseModel):
    date: str
    event_type: str
    count: int


class ErrorRateRow(BaseModel):
    date: str
    event_type: str
    errors: int
    total_events: int
    error_rate: float


class AuthFailureRateRow(BaseModel):
    date: str
    failed: int
    succeeded: int
    failure_rate: float


class ReportMetrics(BaseModel):
    events_per_day: list[EventsPerDayRow]
    error_rate_by_type: list[ErrorRateRow]
    auth_failure_rate: list[AuthFailureRateRow]


class TelemetryReport(BaseModel):
    period: ReportPeriod
    metrics: ReportMetrics


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


def _to_utc(value: datetime) -> datetime:
    """Fecha sin zona horaria → se asume UTC; con zona → se convierte a UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _build_report(start: datetime, end: datetime) -> dict[str, Any]:
    return {
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "metrics": {
            "events_per_day": analysis.events_per_day(start, end),
            "error_rate_by_type": analysis.error_rate_by_type(start, end),
            "auth_failure_rate": analysis.auth_failure_rate(start, end),
        },
    }


@router.get("/report", response_model=TelemetryReport)
def get_telemetry_report(
    start_date: datetime | None = Query(None, description="ISO 8601, inicio inclusivo."),
    end_date: datetime | None = Query(None, description="ISO 8601, fin exclusivo."),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Métricas operacionales calculadas con Pandas sobre `telemetry_events`.

    Accesible a cualquier usuario autenticado. TODO: restringir a admins
    (Role.ADMIN) cuando exista un flujo para crear administradores.

    Por defecto, los últimos 7 días. El resultado se cachea 60 s; sin
    parámetros la clave es fija, porque "ahora − 7 d" cambia en cada request
    y nunca habría un cache hit.
    """

    if start_date is None and end_date is None:
        def compute() -> dict[str, Any]:
            now = datetime.now(timezone.utc)
            return _build_report(now - REPORT_DEFAULT_WINDOW, now)

        return cache.get_or_set("telemetry:report:default", REPORT_CACHE_TTL_SECONDS, compute)

    # Con una sola cota, la otra se resuelve respecto a ahora / a la ventana por defecto.
    now = datetime.now(timezone.utc)
    end = _to_utc(end_date) if end_date is not None else now
    start = _to_utc(start_date) if start_date is not None else end - REPORT_DEFAULT_WINDOW
    if start >= end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date debe ser anterior a end_date.",
        )

    key = f"telemetry:report:{start.isoformat()}:{end.isoformat()}"
    return cache.get_or_set(key, REPORT_CACHE_TTL_SECONDS, lambda: _build_report(start, end))


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
