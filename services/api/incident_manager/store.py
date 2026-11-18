"""Acceso a datos del gestor de incidencias (lógica de negocio sin HTTP)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func
from sqlmodel import Session, select

from nexova_shared.incident_domain import (
    ALLOWED_TRANSITIONS,
    BRANCHES,
    CATEGORIES,
    ORIGINS,
    STATUSES,
)

from .models import ManagedIncident
from .schemas import IncidentCreate

DEFAULT_LIST_LIMIT = 100
MAX_LIST_LIMIT = 500


class IncidentNotFoundError(LookupError):
    """No existe una incidencia con ese id."""


class InvalidTransitionError(ValueError):
    def __init__(self, current: str, requested: str) -> None:
        self.current = current
        self.requested = requested
        self.allowed = ALLOWED_TRANSITIONS[current]
        super().__init__(f"{current} → {requested} no es una transición válida")


def create_incident(session: Session, data: IncidentCreate) -> ManagedIncident:
    incident = ManagedIncident(**data.model_dump(), status="open")
    session.add(incident)
    session.commit()
    session.refresh(incident)
    return incident


def get_incident(session: Session, incident_id: int) -> ManagedIncident:
    incident = session.get(ManagedIncident, incident_id)
    if incident is None:
        raise IncidentNotFoundError(incident_id)
    return incident


def list_incidents(
    session: Session,
    *,
    status: str | None = None,
    origin: str | None = None,
    branch: str | None = None,
    category: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
) -> list[ManagedIncident]:
    statement = select(ManagedIncident)
    for column, value in (
        (ManagedIncident.status, status),
        (ManagedIncident.origin, origin),
        (ManagedIncident.branch, branch),
        (ManagedIncident.category, category),
    ):
        if value is not None:
            statement = statement.where(column == value)
    statement = statement.order_by(
        ManagedIncident.created_at.desc(), ManagedIncident.id.desc()  # type: ignore[union-attr]
    ).limit(min(limit, MAX_LIST_LIMIT))
    return list(session.exec(statement).all())


def update_status(session: Session, incident_id: int, new_status: str) -> ManagedIncident:
    incident = get_incident(session, incident_id)
    if new_status not in ALLOWED_TRANSITIONS[incident.status]:
        raise InvalidTransitionError(incident.status, new_status)
    incident.status = new_status
    incident.updated_at = datetime.now(timezone.utc)
    session.add(incident)
    session.commit()
    session.refresh(incident)
    return incident


def _count_by(session: Session, column, values: tuple[str, ...]) -> dict[str, int]:
    counts = dict.fromkeys(values, 0)
    for value, total in session.exec(select(column, func.count()).group_by(column)).all():
        if value in counts:
            counts[value] = total
    return counts


def summarize(session: Session) -> dict[str, object]:
    by_status = _count_by(session, ManagedIncident.status, STATUSES)
    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "by_category": _count_by(session, ManagedIncident.category, CATEGORIES),
        "by_origin": _count_by(session, ManagedIncident.origin, ORIGINS),
        "by_branch": _count_by(session, ManagedIncident.branch, BRANCHES),
    }
