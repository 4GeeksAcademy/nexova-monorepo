"""
Persistencia de telemetría en Supabase (`telemetry_events`).

Traduce un `TelemetryEvent` (el envelope validado, ver `routes/telemetry.py`)
a una fila de la tabla creada por `migrations/002_telemetry_events.sql`, y la
inserta. Mapeo documentado en docs/telemetry/telemetry-plan.md §4.

El catálogo de eventos y sus allowlists NO se duplican aquí: se leen de
`docs/telemetry/event-schemas.json`, la misma fuente de verdad del plan.

La tabla vive en un `MetaData` propio, fuera de `SQLModel.metadata`, para que
`create_db_and_tables()` nunca la cree sin el trigger de inmutabilidad ni la RLS.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import Column, MetaData, Numeric, Table, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID, insert
from sqlmodel import Session

from database import engine

if TYPE_CHECKING:
    from routes.telemetry import TelemetryEvent

_DEFAULT_SCHEMAS_PATH = (
    Path(__file__).resolve().parent / ".." / ".." / "docs" / "telemetry" / "event-schemas.json"
)
TELEMETRY_SCHEMAS_PATH = Path(os.environ.get("TELEMETRY_SCHEMAS_PATH", _DEFAULT_SCHEMAS_PATH))

SERVICE_BY_SOURCE = {"frontend": "backoffice", "backend": "api"}

_ERROR_EVENTS = frozenset({"api_request_failed", "frontend_error_captured"})
_WARN_EVENTS = frozenset(
    {"login_failed", "stock_threshold_triggered", "kit_cost_variance_detected"}
)

# Propiedad de `properties` que se copia a la columna numérica `value`.
VALUE_FIELD: dict[str, str] = {
    "inbound_order_created": "quantity",
    "outbound_order_created": "quantity",
    "stock_threshold_triggered": "quantity",
    "kit_cost_variance_detected": "variance_pct",
    "outbound_order_rejected": "requested_quantity",
    "supplier_rate_updated": "variance_pct",
    "session_expired": "last_active_seconds_ago",
    "api_request_completed": "duration_ms",
    "frontend_page_load_recorded": "lcp_ms",
    "frontend_error_captured": "occurrence_count",
    "csv_analysis_rejected": "file_size_bytes",
    "inventory_order_form_abandoned": "time_on_form_ms",
}


class TelemetryRejected(ValueError):
    """El envelope es válido, pero el evento no se puede persistir."""


def _load_catalog(path: Path) -> tuple[dict[str, frozenset[str]], dict[str, str]]:
    with path.open(encoding="utf-8") as fh:
        events: dict[str, dict[str, Any]] = json.load(fh)["events"]
    allowlist = {name: frozenset(spec.get("properties", {})) for name, spec in events.items()}
    descriptions = {name: str(spec.get("description", "")) for name, spec in events.items()}
    return allowlist, descriptions


ALLOWLIST, DESCRIPTIONS = _load_catalog(TELEMETRY_SCHEMAS_PATH)

_metadata = MetaData()

telemetry_events = Table(
    "telemetry_events",
    _metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("timestamp", TIMESTAMP(timezone=True), nullable=False),
    Column("service", Text, nullable=False),
    Column("event_type", Text, nullable=False),
    Column("level", Text, nullable=False),
    Column("value", Numeric),
    Column("message", Text),
    Column("tags", JSONB, nullable=False),
)


def _level(event_type: str) -> str:
    if event_type in _ERROR_EVENTS:
        return "error"
    if event_type in _WARN_EVENTS or event_type.endswith("_rejected"):
        return "warn"
    return "info"


def _value(event_type: str, properties: dict[str, Any]) -> float | int | None:
    field = VALUE_FIELD.get(event_type)
    raw = properties.get(field) if field else None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return raw


def _parse_timestamp(raw: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise TelemetryRejected(f"timestamp no es ISO 8601: {raw!r}") from exc
    if parsed.tzinfo is None:
        raise TelemetryRejected(f"timestamp sin zona horaria: {raw!r}")
    return parsed


def to_row(event: TelemetryEvent) -> dict[str, Any]:
    """Mapea un evento a una fila de `telemetry_events` (sin `id`, lo pone la DB)."""

    allowed = ALLOWLIST.get(event.event_type)
    if allowed is None:
        raise TelemetryRejected(f"event_type fuera del catálogo: {event.event_type}")

    tags: dict[str, Any] = {k: v for k, v in event.properties.items() if k in allowed}
    tags["_envelope"] = {
        "eventId": event.eventId,
        "sessionId": event.sessionId,
        "userId": event.userId,
        "requestId": event.requestId,
        "schemaVersion": event.schemaVersion,
    }
    try:
        json.dumps(tags)
    except (TypeError, ValueError) as exc:
        raise TelemetryRejected("properties no serializables a JSON") from exc

    return {
        "timestamp": _parse_timestamp(event.timestamp),
        "service": SERVICE_BY_SOURCE[event.source],
        "event_type": event.event_type,
        "level": _level(event.event_type),
        "value": _value(event.event_type, event.properties),
        "message": DESCRIPTIONS.get(event.event_type) or None,
        "tags": tags,
    }


def bulk_insert(rows: list[dict[str, Any]]) -> None:
    """Inserta todas las filas en una sola sentencia y una sola transacción.

    `ON CONFLICT DO NOTHING` absorbe los reintentos del frontend: un `eventId`
    ya guardado (índice único `telemetry_events_event_id_uidx`) no se duplica.
    """

    if not rows:
        return
    statement = insert(telemetry_events).values(rows).on_conflict_do_nothing()
    with Session(engine) as session, session.begin():
        session.execute(statement)
