"""
Transformación CSV (helpdesk legacy) → modelo del gestor de incidencias.

La validación de cada fila NO se reescribe: se reutiliza `validate_record` del
analizador (`incidents_analyzer`), igual que hace la API del analizador. Aquí
solo se añaden el mapeo de campos del BRIEFING y las comprobaciones propias
del modelo nuevo (fecha parseable, título no vacío).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from incidents_analyzer import validate_record  # noqa: F401  (reexportada)

from .incident_domain import (
    CSV_CATEGORY_MAP,
    CSV_STATUS_MAP,
    SEED_BRANCH,
    SEED_ORIGIN,
    TITLE_MAX_LENGTH,
)

__all__ = ["SeedRow", "transform_csv_row", "validate_record"]

INVALID_DATE = "invalid_date"
EMPTY_TITLE = "empty_title"


@dataclass(frozen=True)
class SeedRow:
    """Fila del CSV ya transformada al modelo (sin el `ticket_id`, que no se almacena)."""

    source_key: str  # identificador para idempotencia: ticket_id o title + created_at
    title: str
    description: str
    category: str
    status: str
    origin: str
    branch: str
    created_at: datetime


def _parse_date(value: str) -> datetime | None:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def transform_csv_row(record: dict[str, Any]) -> tuple[SeedRow | None, list[str]]:
    """
    Devuelve `(fila, [])` si el registro es válido, o `(None, reglas_incumplidas)`.

    Las reglas son las claves de `incidents_analyzer.RULE_LABELS` más
    `invalid_date` y `empty_title`.
    """
    failed, _score = validate_record(record)

    created_at = _parse_date(record.get("date") or "")
    if created_at is None:
        failed.append(INVALID_DATE)

    description = (record.get("description") or "").strip()
    title = description[:TITLE_MAX_LENGTH].strip()
    if not title:
        failed.append(EMPTY_TITLE)

    if failed or created_at is None:
        return None, failed

    ticket_id = (record.get("ticket_id") or "").strip()
    source_key = ticket_id or f"{title}|{created_at.isoformat()}"

    return (
        SeedRow(
            source_key=source_key,
            title=title,
            # BRIEFING: la descripción se copia literalmente.
            description=record["description"],
            category=CSV_CATEGORY_MAP[record["category"].strip()],
            status=CSV_STATUS_MAP[record["status"].strip()],
            origin=SEED_ORIGIN,
            branch=SEED_BRANCH,
            created_at=created_at,
        ),
        [],
    )
