"""
Persistencia de tickets del gestor de incidentes (Supabase, tabla `incident`).

- `upsert_valid_tickets()`: guarda los registros válidos de un CSV analizado
  (misma validación que el análisis: `incidents_analyzer.validate_record`).
  Un único `INSERT … ON CONFLICT (ticket_id) DO UPDATE`, sin un viaje por fila.
- `get_ticket()` / `list_tickets()`: solo lectura, para los endpoints GET.
- `create_ticket()`: alta de un ticket individual (siempre en `OPEN`, id
  `NXV-NNNNNN` generado aquí).
- `update_status()`: ciclo de vida del ticket. Solo admite las transiciones de
  `ALLOWED_TRANSITIONS`; cualquier otra lanza `InvalidTransitionError`.

`customer_email` nunca se guarda ni se devuelve.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

import incidents_analyzer as ia
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from database import engine
from models import Incident

MAX_LIST_LIMIT = 100

TICKET_PREFIX = "NXV-"
TICKET_ID_RE = re.compile(r"^NXV-(\d{6})$")
CREATE_RETRIES = 3

# Ciclo de vida del ticket. DISCARDED es final; CLOSED solo puede reabrirse.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "OPEN": frozenset({"CLOSED", "DISCARDED"}),
    "CLOSED": frozenset({"OPEN"}),
    "DISCARDED": frozenset(),
}
if set(ALLOWED_TRANSITIONS) != ia.VALID_STATUSES:
    raise RuntimeError("ALLOWED_TRANSITIONS debe cubrir exactamente incidents_analyzer.VALID_STATUSES.")


class TicketNotFoundError(LookupError):
    pass


class InvalidTransitionError(ValueError):
    def __init__(self, current: str, target: str) -> None:
        allowed = sorted(ALLOWED_TRANSITIONS.get(current, ()))
        self.current = current
        self.target = target
        self.allowed = allowed
        super().__init__(
            f"No se puede pasar de {current} a {target}. "
            f"Desde {current} se permite: {', '.join(allowed) or 'ninguna (estado final)'}."
        )


def _to_row(record: dict[str, Any], score: int | None) -> dict[str, Any]:
    return {
        "ticket_id": record["ticket_id"].strip(),
        "date": date.fromisoformat(record["date"].strip()),
        "client_company": record["client_company"].strip(),
        "category": record["category"].strip(),
        "description": record["description"].strip(),
        "agent_id": record["agent_id"].strip(),
        "status": record["status"].strip(),
        "satisfaction_score": score,
        "updated_at": datetime.now(timezone.utc),
    }


def valid_ticket_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filas listas para guardar: solo registros válidos con `ticket_id` y fecha ISO."""
    rows: dict[str, dict[str, Any]] = {}
    for record in records:
        failed, score = ia.validate_record(record)
        if failed or not (record.get("ticket_id") or "").strip():
            continue
        try:
            row = _to_row(record, score)
        except ValueError:  # fecha no ISO: no se puede consultar por fecha
            continue
        rows[row["ticket_id"]] = row  # el último gana si el CSV repite un ticket
    return list(rows.values())


def upsert_valid_tickets(records: list[dict[str, Any]]) -> int:
    rows = valid_ticket_rows(records)
    if not rows:
        return 0
    stmt = insert(Incident.__table__).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["ticket_id"],
        set_={col: stmt.excluded[col] for col in rows[0] if col != "ticket_id"},
    )
    with Session(engine) as session:
        session.exec(stmt)
        session.commit()
    return len(rows)


def get_ticket(session: Session, ticket_id: str) -> Incident | None:
    return session.get(Incident, ticket_id.strip().upper())


def list_tickets(
    session: Session,
    *,
    status: str | None = None,
    category: str | None = None,
    client_company: str | None = None,
    limit: int = 20,
) -> list[Incident]:
    query = select(Incident)
    if status:
        query = query.where(Incident.status == status.strip().upper())
    if category:
        query = query.where(Incident.category == category.strip().upper())
    if client_company:
        query = query.where(Incident.client_company.ilike(f"%{client_company.strip()}%"))
    query = query.order_by(Incident.date.desc(), Incident.ticket_id).limit(min(limit, MAX_LIST_LIMIT))
    return list(session.exec(query).all())


def next_ticket_id(session: Session) -> str:
    """Siguiente id libre `NXV-NNNNNN` (máximo numérico actual + 1)."""
    ids = session.exec(
        select(Incident.ticket_id).where(Incident.ticket_id.like(f"{TICKET_PREFIX}%"))
    ).all()
    numbers = [int(m.group(1)) for tid in ids if (m := TICKET_ID_RE.match(tid))]
    return f"{TICKET_PREFIX}{(max(numbers, default=0) + 1):06d}"


def create_ticket(
    session: Session,
    *,
    client_company: str,
    category: str,
    description: str,
    agent_id: str,
    ticket_date: date | None = None,
) -> Incident:
    """Crea un ticket en `OPEN`. Reintenta si otro alta tomó el mismo id a la vez."""
    for attempt in range(CREATE_RETRIES):
        ticket = Incident(
            ticket_id=next_ticket_id(session),
            date=ticket_date or datetime.now(timezone.utc).date(),
            client_company=client_company.strip(),
            category=category.strip().upper(),
            description=description.strip(),
            agent_id=agent_id.strip(),
            status="OPEN",
            satisfaction_score=None,
            updated_at=datetime.now(timezone.utc),
        )
        session.add(ticket)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            if attempt == CREATE_RETRIES - 1:
                raise
            continue
        session.refresh(ticket)
        return ticket
    raise RuntimeError("unreachable")


def update_status(session: Session, ticket_id: str, new_status: str) -> Incident:
    """Aplica una transición del ciclo de vida o lanza el error correspondiente."""
    ticket = get_ticket(session, ticket_id)
    if ticket is None:
        raise TicketNotFoundError(ticket_id)
    target = new_status.strip().upper()
    if target not in ALLOWED_TRANSITIONS.get(ticket.status, frozenset()):
        raise InvalidTransitionError(ticket.status, target)
    ticket.status = target
    ticket.updated_at = datetime.now(timezone.utc)
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket
