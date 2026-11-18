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


def create_incident(
    session: Session, data: IncidentCreate, *, client_company: str | None = None
) -> ManagedIncident:
    """La empresa la decide el llamador (el cliente no elige): ver `router.create_incident`."""
    values = data.model_dump(exclude={"client_company"})
    incident = ManagedIncident(**values, client_company=client_company, status="open")
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
    client_company: str | None = None,
    exact_company: bool = False,
    limit: int = DEFAULT_LIST_LIMIT,
) -> list[ManagedIncident]:
    """`client_company`: coincidencia parcial (personal interno) o exacta (`exact_company`, clientes)."""
    statement = select(ManagedIncident)
    for column, value in (
        (ManagedIncident.status, status),
        (ManagedIncident.origin, origin),
        (ManagedIncident.branch, branch),
        (ManagedIncident.category, category),
    ):
        if value is not None:
            statement = statement.where(column == value)
    if client_company:
        company = client_company.strip()
        if exact_company:
            statement = statement.where(func.lower(ManagedIncident.client_company) == company.lower())
        else:
            statement = statement.where(ManagedIncident.client_company.ilike(f"%{company}%"))
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


def _count_by(
    session: Session, column, values: tuple[str, ...], company: str | None
) -> dict[str, int]:
    counts = dict.fromkeys(values, 0)
    statement = select(column, func.count()).group_by(column)
    if company is not None:
        statement = statement.where(func.lower(ManagedIncident.client_company) == company.lower())
    for value, total in session.exec(statement).all():
        if value in counts:
            counts[value] = total
    return counts


def summarize(session: Session, *, client_company: str | None = None) -> dict[str, object]:
    """Métricas agregadas. Con `client_company` solo cuenta las incidencias de esa empresa."""
    company = client_company.strip() if client_company else client_company
    by_status = _count_by(session, ManagedIncident.status, STATUSES, company)
    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "by_category": _count_by(session, ManagedIncident.category, CATEGORIES, company),
        "by_origin": _count_by(session, ManagedIncident.origin, ORIGINS, company),
        "by_branch": _count_by(session, ManagedIncident.branch, BRANCHES, company),
    }
