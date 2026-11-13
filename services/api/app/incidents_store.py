"""
Persistencia de tickets del gestor de incidentes (Supabase, tabla `incident`).

- `upsert_valid_tickets()`: guarda los registros válidos de un CSV analizado
  (misma validación que el análisis: `incidents_analyzer.validate_record`).
  Un único `INSERT … ON CONFLICT (ticket_id) DO UPDATE`, sin un viaje por fila.
- `get_ticket()` / `list_tickets()`: solo lectura, para los endpoints GET.

`customer_email` nunca se guarda ni se devuelve.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import incidents_analyzer as ia
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, select

from database import engine
from models import Incident

MAX_LIST_LIMIT = 100


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
