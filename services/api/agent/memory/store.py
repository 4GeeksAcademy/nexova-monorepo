"""
Almacenes de la memoria: interfaz explícita de lectura/escritura.

`MemoryStore` es el contrato; el agente nunca accede a la memoria por otra vía
(ni la acumula en el system prompt). Dos implementaciones:

- `PostgresMemoryStore`: Supabase/Postgres, tablas de la migración
  `007_agent_memory.sql` (`agent_memory_proposals`, `agent_memory_entries`,
  `agent_memory_audit`). Nunca toca Qdrant ni `nexova_knowledge`.
- `InMemoryMemoryStore`: misma semántica (una pendiente por conversación,
  propuestas decididas inmutables, auditoría append-only) para tests y demos
  sin base de datos.

`transaction()` agrupa varias operaciones (cerrar propuesta + escribir hecho +
auditar) en una sola transacción.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Protocol

from agent.memory.schemas import AuditEvent, MemoryFact, MemoryProposal


class PendingProposalExists(RuntimeError):
    """Ya hay una propuesta pendiente en esta conversación."""


class ProposalAlreadyDecided(RuntimeError):
    """La propuesta ya no está pendiente (las decididas son inmutables)."""


class MemoryStore(Protocol):
    def transaction(self) -> Any: ...

    # Propuestas
    def get_pending(self, conversation_id: str) -> MemoryProposal | None: ...
    def add_proposal(self, proposal: MemoryProposal) -> None: ...
    def close_proposal(self, proposal: MemoryProposal) -> None: ...
    def count_user_proposals_since(self, user_id: str, since: datetime) -> int: ...
    def list_stale_pending(self, before: datetime) -> list[MemoryProposal]: ...

    # Hechos
    def active_facts(self) -> list[MemoryFact]: ...
    def add_fact(self, fact: MemoryFact) -> None: ...
    def set_fact_status(self, fact_id: str, status: str, reason: str, at: datetime) -> None: ...
    def touch_facts(self, fact_ids: list[str], at: datetime) -> None: ...

    # Auditoría
    def add_audit(self, event: AuditEvent) -> None: ...
    def list_audit(self, *, conversation_id: str | None = None, limit: int = 200) -> list[AuditEvent]: ...


# --- En memoria ---------------------------------------------------------------------------


class InMemoryMemoryStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.proposals: dict[str, MemoryProposal] = {}
        self.facts: dict[str, MemoryFact] = {}
        self.audit: list[AuditEvent] = []

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            yield

    def get_pending(self, conversation_id: str) -> MemoryProposal | None:
        with self._lock:
            return next(
                (p.model_copy() for p in self.proposals.values()
                 if p.conversation_id == conversation_id and p.status == "pending"),
                None,
            )

    def add_proposal(self, proposal: MemoryProposal) -> None:
        with self._lock:
            if self.get_pending(proposal.conversation_id) is not None:
                raise PendingProposalExists(proposal.conversation_id)
            self.proposals[proposal.id] = proposal.model_copy()

    def close_proposal(self, proposal: MemoryProposal) -> None:
        with self._lock:
            current = self.proposals.get(proposal.id)
            if current is None or current.status != "pending":
                raise ProposalAlreadyDecided(proposal.id)
            self.proposals[proposal.id] = proposal.model_copy()

    def count_user_proposals_since(self, user_id: str, since: datetime) -> int:
        with self._lock:
            return sum(1 for p in self.proposals.values() if p.user_id == user_id and p.created_at >= since)

    def list_stale_pending(self, before: datetime) -> list[MemoryProposal]:
        with self._lock:
            return [p.model_copy() for p in self.proposals.values() if p.status == "pending" and p.created_at < before]

    def active_facts(self) -> list[MemoryFact]:
        with self._lock:
            return [f.model_copy() for f in self.facts.values() if f.status == "active"]

    def add_fact(self, fact: MemoryFact) -> None:
        with self._lock:
            if any(f.fingerprint == fact.fingerprint and f.status == "active" for f in self.facts.values()):
                raise ValueError("Ya existe un hecho activo con la misma huella")
            self.facts[fact.id] = fact.model_copy()

    def set_fact_status(self, fact_id: str, status: str, reason: str, at: datetime) -> None:
        with self._lock:
            fact = self.facts[fact_id]
            self.facts[fact_id] = fact.model_copy(
                update={"status": status, "status_reason": reason, "status_changed_at": at}
            )

    def touch_facts(self, fact_ids: list[str], at: datetime) -> None:
        with self._lock:
            for fact_id in fact_ids:
                fact = self.facts[fact_id]
                self.facts[fact_id] = fact.model_copy(
                    update={"use_count": fact.use_count + 1, "last_used_at": at}
                )

    def add_audit(self, event: AuditEvent) -> None:
        with self._lock:
            self.audit.append(event.model_copy())

    def list_audit(self, *, conversation_id: str | None = None, limit: int = 200) -> list[AuditEvent]:
        with self._lock:
            events = [e for e in self.audit if conversation_id is None or e.conversation_id == conversation_id]
            return [e.model_copy() for e in events[-limit:]]


# --- Postgres -------------------------------------------------------------------------------

_PROPOSAL_COLUMNS = (
    "id, conversation_id, user_id, fact, business_line, category, reason, source_message, status, "
    "decision_label, decision_confidence, decision_message, final_fact, created_at, decided_at"
)
_FACT_COLUMNS = (
    "id, business_line, category, fact, fingerprint, approved_by, proposal_id, status, status_reason, "
    "supersedes_id, use_count, created_at, last_used_at, status_changed_at"
)


def _row_dict(row: Any) -> dict[str, Any]:
    data = dict(row._mapping)
    for key in ("id", "conversation_id", "proposal_id", "supersedes_id", "entry_id"):
        if data.get(key) is not None:
            data[key] = str(data[key])
    return data


class PostgresMemoryStore:
    """Memoria en Supabase/Postgres. `engine` por defecto: el de `database.py` (DATABASE_URL)."""

    def __init__(self, engine: Any | None = None) -> None:
        self._engine = engine
        self._local = threading.local()

    @property
    def engine(self) -> Any:
        if self._engine is None:
            from database import engine  # import diferido: `database` exige DATABASE_URL

            self._engine = engine
        return self._engine

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if getattr(self._local, "conn", None) is not None:  # transacción anidada: se reutiliza
            yield
            return
        with self.engine.begin() as conn:
            self._local.conn = conn
            try:
                yield
            finally:
                self._local.conn = None

    def _execute(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        from sqlalchemy import text

        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn.execute(text(sql), params or {})
        with self.engine.begin() as own:
            result = own.execute(text(sql), params or {})
            return result.fetchall() if result.returns_rows else result

    @staticmethod
    def _rows(result: Any) -> list[Any]:
        return result if isinstance(result, list) else result.fetchall()

    # Propuestas
    def get_pending(self, conversation_id: str) -> MemoryProposal | None:
        rows = self._rows(self._execute(
            f"SELECT {_PROPOSAL_COLUMNS} FROM public.agent_memory_proposals "
            "WHERE conversation_id = CAST(:cid AS uuid) AND status = 'pending'",
            {"cid": conversation_id},
        ))
        return MemoryProposal.model_validate(_row_dict(rows[0])) if rows else None

    def add_proposal(self, proposal: MemoryProposal) -> None:
        from sqlalchemy.exc import IntegrityError

        data = proposal.model_dump()
        try:
            self._execute(
                "INSERT INTO public.agent_memory_proposals "
                "(id, conversation_id, user_id, fact, business_line, category, reason, source_message, "
                "status, created_at) VALUES (CAST(:id AS uuid), CAST(:conversation_id AS uuid), :user_id, "
                ":fact, :business_line, :category, :reason, :source_message, 'pending', :created_at)",
                data,
            )
        except IntegrityError as exc:
            raise PendingProposalExists(proposal.conversation_id) from exc

    def close_proposal(self, proposal: MemoryProposal) -> None:
        result = self._execute(
            "UPDATE public.agent_memory_proposals SET status = :status, decision_label = :decision_label, "
            "decision_confidence = :decision_confidence, decision_message = :decision_message, "
            "final_fact = :final_fact, decided_at = :decided_at "
            "WHERE id = CAST(:id AS uuid) AND status = 'pending'",
            proposal.model_dump(),
        )
        if getattr(result, "rowcount", 1) == 0:
            raise ProposalAlreadyDecided(proposal.id)

    def count_user_proposals_since(self, user_id: str, since: datetime) -> int:
        rows = self._rows(self._execute(
            "SELECT count(*) AS n FROM public.agent_memory_proposals WHERE user_id = :uid AND created_at >= :since",
            {"uid": user_id, "since": since},
        ))
        return int(rows[0]._mapping["n"])

    def list_stale_pending(self, before: datetime) -> list[MemoryProposal]:
        rows = self._rows(self._execute(
            f"SELECT {_PROPOSAL_COLUMNS} FROM public.agent_memory_proposals "
            "WHERE status = 'pending' AND created_at < :before",
            {"before": before},
        ))
        return [MemoryProposal.model_validate(_row_dict(r)) for r in rows]

    # Hechos
    def active_facts(self) -> list[MemoryFact]:
        rows = self._rows(self._execute(
            f"SELECT {_FACT_COLUMNS} FROM public.agent_memory_entries WHERE status = 'active'"
        ))
        return [MemoryFact.model_validate(_row_dict(r)) for r in rows]

    def add_fact(self, fact: MemoryFact) -> None:
        self._execute(
            "INSERT INTO public.agent_memory_entries "
            "(id, business_line, category, fact, fingerprint, approved_by, proposal_id, status, "
            "supersedes_id, use_count, created_at) VALUES (CAST(:id AS uuid), :business_line, :category, "
            ":fact, :fingerprint, :approved_by, CAST(:proposal_id AS uuid), 'active', "
            "CAST(:supersedes_id AS uuid), 0, :created_at)",
            fact.model_dump(),
        )

    def set_fact_status(self, fact_id: str, status: str, reason: str, at: datetime) -> None:
        self._execute(
            "UPDATE public.agent_memory_entries SET status = :status, status_reason = :reason, "
            "status_changed_at = :at WHERE id = CAST(:id AS uuid)",
            {"id": fact_id, "status": status, "reason": reason, "at": at},
        )

    def touch_facts(self, fact_ids: list[str], at: datetime) -> None:
        for fact_id in fact_ids:
            self._execute(
                "UPDATE public.agent_memory_entries SET use_count = use_count + 1, last_used_at = :at "
                "WHERE id = CAST(:id AS uuid)",
                {"id": fact_id, "at": at},
            )

    # Auditoría
    def add_audit(self, event: AuditEvent) -> None:
        data = event.model_dump()
        data["details"] = json.dumps(data["details"], ensure_ascii=False, default=str)
        self._execute(
            "INSERT INTO public.agent_memory_audit "
            "(event, proposal_id, entry_id, conversation_id, user_id, message, details, created_at) "
            "VALUES (:event, CAST(:proposal_id AS uuid), CAST(:entry_id AS uuid), "
            "CAST(:conversation_id AS uuid), :user_id, :message, CAST(:details AS jsonb), :created_at)",
            data,
        )

    def list_audit(self, *, conversation_id: str | None = None, limit: int = 200) -> list[AuditEvent]:
        where = "WHERE conversation_id = CAST(:cid AS uuid)" if conversation_id else ""
        rows = self._rows(self._execute(
            "SELECT event, proposal_id, entry_id, conversation_id, user_id, message, details, created_at "
            f"FROM public.agent_memory_audit {where} ORDER BY id DESC LIMIT :limit",
            {"cid": conversation_id, "limit": limit},
        ))
        return [AuditEvent.model_validate(_row_dict(r)) for r in reversed(rows)]
