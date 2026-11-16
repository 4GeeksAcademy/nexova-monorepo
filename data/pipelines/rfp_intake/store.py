"""
Persistencia del ticket RFP: una sola interfaz para la API, el worker y los tests.

- `SqlRfpStore`: Postgres/Supabase vía SQLModel (`DATABASE_URL`). Es la fuente
  de verdad del ticket, sus metadatos y las secciones por departamento.
- `InMemoryRfpStore`: misma interfaz en memoria, para tests y corridas en seco.

Todas las escrituras del pipeline son idempotentes (upsert por clave natural),
porque Celery puede re-ejecutar el grafo completo tras un fallo.
"""

from __future__ import annotations

import copy
import os
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from dotenv import load_dotenv
from sqlalchemy import delete
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine, select

from rfp_intake.departments import (
    STATUS_ANALYSIS_COMPLETE,
    STATUS_ANALYZING,
    STATUS_DISCARDED,
)
from rfp_intake.models import RfpDepartmentSection, RfpMetadata, RfpTicket, utcnow

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _env_file in (_REPO_ROOT / "services" / "api" / ".env", _REPO_ROOT / ".env"):
    if _env_file.is_file():
        load_dotenv(_env_file)

_METADATA_FIELDS = (
    "client_name", "client_hq", "client_location", "currency", "rfp_type", "services_requested",
    "scope", "volumes", "deadline", "deadline_text", "budget_range", "client_contact",
    "departments_needed", "unmapped_services", "readability",
)
_SECTION_FIELDS = (
    "owner_name", "key_aspects", "requests_to_owner", "open_questions", "figures_cited", "excerpts",
)


class TicketNotFound(LookupError):
    """El ticket no existe."""


class RfpStore(Protocol):
    def create_ticket(
        self, *, ticket_id: str, raw_pdf_path: str, original_filename: str, created_by: str | None
    ) -> dict[str, Any]: ...
    def get_ticket(self, ticket_id: str) -> dict[str, Any] | None: ...
    def list_tickets(self, limit: int = 50) -> list[dict[str, Any]]: ...
    def start_attempt(self, ticket_id: str) -> dict[str, Any]: ...
    def save_markdown(self, ticket_id: str, markdown: str) -> None: ...
    def discard(self, ticket_id: str, classification: dict[str, Any]) -> None: ...
    def save_classification(self, ticket_id: str, classification: dict[str, Any]) -> None: ...
    def save_metadata(self, ticket_id: str, metadata: dict[str, Any], rfp_id: str | None) -> None: ...
    def reset_sections(self, ticket_id: str) -> None: ...
    def upsert_section(self, ticket_id: str, section: dict[str, Any]) -> None: ...
    def complete(self, ticket_id: str, synthesis: dict[str, Any], handoff: dict[str, Any]) -> None: ...
    def record_error(self, ticket_id: str, message: str) -> None: ...


def _iso(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _ticket_summary(ticket: dict[str, Any], metadata: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "ticket_id": ticket["ticket_id"],
        "rfp_id": ticket.get("rfp_id"),
        "status": ticket["status"],
        "original_filename": ticket["original_filename"],
        "client_name": (metadata or {}).get("client_name"),
        "client_hq": (metadata or {}).get("client_hq"),
        "currency": (metadata or {}).get("currency"),
        "departments_needed": (metadata or {}).get("departments_needed") or [],
        "error_message": ticket.get("error_message"),
        "attempts": ticket.get("attempts", 0),
        "created_at": _iso(ticket["created_at"]),
        "updated_at": _iso(ticket["updated_at"]),
    }


def _ticket_detail(
    ticket: dict[str, Any], metadata: dict[str, Any] | None, sections: list[dict[str, Any]]
) -> dict[str, Any]:
    detail = _ticket_summary(ticket, metadata)
    detail.update(
        {
            "raw_pdf_path": ticket["raw_pdf_path"],
            "classification": ticket.get("classification"),
            "synthesis": ticket.get("synthesis"),
            "handoff_payload": ticket.get("handoff_payload"),
            "ready_for_drafting": bool(ticket.get("ready_for_drafting")),
            "metadata": {k: _iso(v) for k, v in metadata.items()} if metadata else None,
            "sections": [
                {k: _iso(v) for k, v in section.items() if k not in ("id", "ticket_id")}
                for section in sorted(sections, key=lambda s: s["department_id"])
            ],
        }
    )
    return detail


# --------------------------------------------------------------------------- #
# En memoria
# --------------------------------------------------------------------------- #
class InMemoryRfpStore:
    def __init__(self) -> None:
        self.tickets: dict[str, dict[str, Any]] = {}
        self.metadata: dict[str, dict[str, Any]] = {}
        self.sections: dict[str, dict[str, dict[str, Any]]] = {}

    def _ticket(self, ticket_id: str) -> dict[str, Any]:
        try:
            return self.tickets[ticket_id]
        except KeyError:
            raise TicketNotFound(ticket_id) from None

    def _touch(self, ticket_id: str, **changes: Any) -> None:
        ticket = self._ticket(ticket_id)
        ticket.update(changes)
        ticket["updated_at"] = utcnow()

    def create_ticket(self, *, ticket_id, raw_pdf_path, original_filename, created_by):
        now = utcnow()
        self.tickets[ticket_id] = {
            "ticket_id": ticket_id, "rfp_id": None, "status": STATUS_ANALYZING,
            "raw_pdf_path": raw_pdf_path, "original_filename": original_filename,
            "markdown": None, "classification": None, "synthesis": None,
            "handoff_payload": None, "ready_for_drafting": False, "error_message": None,
            "attempts": 0, "created_by": created_by, "created_at": now, "updated_at": now,
        }
        return copy.deepcopy(self.tickets[ticket_id])

    def get_ticket(self, ticket_id):
        if ticket_id not in self.tickets:
            return None
        return copy.deepcopy(
            _ticket_detail(
                self.tickets[ticket_id],
                self.metadata.get(ticket_id),
                list(self.sections.get(ticket_id, {}).values()),
            )
        )

    def list_tickets(self, limit=50):
        tickets = sorted(self.tickets.values(), key=lambda t: t["created_at"], reverse=True)
        return [_ticket_summary(t, self.metadata.get(t["ticket_id"])) for t in tickets[:limit]]

    def start_attempt(self, ticket_id):
        ticket = self._ticket(ticket_id)
        self._touch(ticket_id, attempts=ticket["attempts"] + 1, status=STATUS_ANALYZING,
                    ready_for_drafting=False)
        return copy.deepcopy(self.tickets[ticket_id])

    def save_markdown(self, ticket_id, markdown):
        self._touch(ticket_id, markdown=markdown)

    def save_classification(self, ticket_id, classification):
        self._touch(ticket_id, classification=classification)

    def discard(self, ticket_id, classification):
        self._touch(ticket_id, classification=classification, status=STATUS_DISCARDED,
                    error_message=None, ready_for_drafting=False)

    def save_metadata(self, ticket_id, metadata, rfp_id):
        self._ticket(ticket_id)
        self.metadata[ticket_id] = {k: copy.deepcopy(metadata.get(k)) for k in _METADATA_FIELDS}
        self._touch(ticket_id, rfp_id=rfp_id)

    def reset_sections(self, ticket_id):
        self.sections[ticket_id] = {}
        self._touch(ticket_id)

    def upsert_section(self, ticket_id, section):
        self._ticket(ticket_id)
        row = {"ticket_id": ticket_id, "department_id": section["department_id"]}
        row.update({k: copy.deepcopy(section.get(k)) for k in _SECTION_FIELDS})
        row.update({"draft_content": None, "evaluation_results": None, "approval_status": None,
                    "approver": None, "approved_at": None})
        self.sections.setdefault(ticket_id, {})[section["department_id"]] = row
        self._touch(ticket_id)

    def complete(self, ticket_id, synthesis, handoff):
        self._touch(ticket_id, synthesis=synthesis, handoff_payload=handoff,
                    ready_for_drafting=True, status=STATUS_ANALYSIS_COMPLETE, error_message=None)

    def record_error(self, ticket_id, message):
        self._touch(ticket_id, error_message=message[:2000])


# --------------------------------------------------------------------------- #
# Postgres / Supabase
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def get_engine() -> Engine:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL no está definida: créala en services/api/.env o en el entorno.")
    return create_engine(database_url, pool_pre_ping=True)


def _row(model: Any) -> dict[str, Any]:
    return model.model_dump()


class SqlRfpStore:
    def __init__(self, engine: Engine | None = None) -> None:
        self._engine = engine

    @property
    def engine(self) -> Engine:
        return self._engine or get_engine()

    def _load(self, session: Session, ticket_id: str) -> RfpTicket:
        ticket = session.get(RfpTicket, ticket_id)
        if ticket is None:
            raise TicketNotFound(ticket_id)
        return ticket

    def _update(self, ticket_id: str, **changes: Any) -> None:
        with Session(self.engine) as session:
            ticket = self._load(session, ticket_id)
            for key, value in changes.items():
                setattr(ticket, key, value)
            ticket.updated_at = utcnow()
            session.add(ticket)
            session.commit()

    def create_ticket(self, *, ticket_id, raw_pdf_path, original_filename, created_by):
        with Session(self.engine) as session:
            ticket = RfpTicket(ticket_id=ticket_id, raw_pdf_path=raw_pdf_path,
                               original_filename=original_filename, created_by=created_by)
            session.add(ticket)
            session.commit()
            session.refresh(ticket)
            return _row(ticket)

    def get_ticket(self, ticket_id):
        with Session(self.engine) as session:
            ticket = session.get(RfpTicket, ticket_id)
            if ticket is None:
                return None
            metadata = session.get(RfpMetadata, ticket_id)
            sections = session.exec(
                select(RfpDepartmentSection).where(RfpDepartmentSection.ticket_id == ticket_id)
            ).all()
            return _ticket_detail(
                _row(ticket), _row(metadata) if metadata else None, [_row(s) for s in sections]
            )

    def list_tickets(self, limit=50):
        with Session(self.engine) as session:
            rows = session.exec(
                select(RfpTicket, RfpMetadata)
                .join(RfpMetadata, RfpMetadata.ticket_id == RfpTicket.ticket_id, isouter=True)
                .order_by(RfpTicket.created_at.desc())
                .limit(limit)
            ).all()
            return [
                _ticket_summary(_row(ticket), _row(metadata) if metadata else None)
                for ticket, metadata in rows
            ]

    def start_attempt(self, ticket_id):
        with Session(self.engine) as session:
            ticket = self._load(session, ticket_id)
            ticket.attempts = (ticket.attempts or 0) + 1
            ticket.status = STATUS_ANALYZING
            ticket.ready_for_drafting = False
            ticket.updated_at = utcnow()
            session.add(ticket)
            session.commit()
            session.refresh(ticket)
            return _row(ticket)

    def save_markdown(self, ticket_id, markdown):
        self._update(ticket_id, markdown=markdown)

    def save_classification(self, ticket_id, classification):
        self._update(ticket_id, classification=classification)

    def discard(self, ticket_id, classification):
        self._update(ticket_id, classification=classification, status=STATUS_DISCARDED,
                     error_message=None, ready_for_drafting=False)

    def save_metadata(self, ticket_id, metadata, rfp_id):
        with Session(self.engine) as session:
            ticket = self._load(session, ticket_id)
            row = session.get(RfpMetadata, ticket_id) or RfpMetadata(ticket_id=ticket_id)
            for key in _METADATA_FIELDS:
                setattr(row, key, metadata.get(key))
            ticket.rfp_id = rfp_id
            ticket.updated_at = utcnow()
            session.add(row)
            session.add(ticket)
            session.commit()

    def reset_sections(self, ticket_id):
        with Session(self.engine) as session:
            self._load(session, ticket_id)
            session.exec(delete(RfpDepartmentSection).where(RfpDepartmentSection.ticket_id == ticket_id))
            session.commit()

    def upsert_section(self, ticket_id, section):
        with Session(self.engine) as session:
            row = session.exec(
                select(RfpDepartmentSection).where(
                    RfpDepartmentSection.ticket_id == ticket_id,
                    RfpDepartmentSection.department_id == section["department_id"],
                )
            ).first() or RfpDepartmentSection(ticket_id=ticket_id, department_id=section["department_id"])
            for key in _SECTION_FIELDS:
                value = section.get(key)
                setattr(row, key, value if value is not None or key == "owner_name" else [])
            row.updated_at = utcnow()
            session.add(row)
            session.commit()

    def complete(self, ticket_id, synthesis, handoff):
        self._update(ticket_id, synthesis=synthesis, handoff_payload=handoff, ready_for_drafting=True,
                     status=STATUS_ANALYSIS_COMPLETE, error_message=None)

    def record_error(self, ticket_id, message):
        self._update(ticket_id, error_message=message[:2000])
