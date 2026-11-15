"""
`AgentMemory`: el ciclo completo propuesta → decisión → escritura → limpieza.

Es la única puerta del grafo hacia la memoria. Reglas que hace cumplir:

- **Nunca escribe sin decisión explícita.** Solo `approve`/`edit` clasificados
  con confianza suficiente escriben; el resto descarta (`classifier.py`).
- **Una propuesta pendiente a la vez** por conversación (también lo impone el
  índice único parcial de la base de datos).
- **Todo queda auditado**, se apruebe o no: propuesta, bloqueo por política,
  decisión, escritura, deduplicación, reemplazo, expiración y expulsión.
- **Política del BRIEFING** (`policy.py`) antes de proponer y antes de escribir.
- **Anti-envenenamiento:** límite de propuestas por usuario y día, autoría en
  cada hecho, correcciones como versiones nuevas (la anterior queda
  `superseded`, nunca se borra) y solo quien recibió la propuesta puede decidirla.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from agent.memory import consolidation
from agent.memory.classifier import DecisionClassifier, classify_decision, effective_label
from agent.memory.policy import check_fact, redact
from agent.memory.schemas import (
    BUSINESS_LINE_LABELS,
    CATEGORY_LABELS,
    AuditEvent,
    MemoryCandidate,
    MemoryFact,
    MemoryProposal,
)
from agent.memory.store import MemoryStore, PendingProposalExists

logger = logging.getLogger(__name__)

MAX_PROPOSALS_PER_USER_PER_DAY = 10

_OUTCOME_BY_LABEL = {
    "approve": "approved",
    "edit": "edited",
    "reject": "rejected",
    "unrelated": "discarded",
    "ambiguous": "discarded",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Resolution:
    """Resultado de evaluar un mensaje contra la propuesta pendiente."""

    proposal: MemoryProposal
    outcome: str  # approved | edited | rejected | discarded
    label: str  # etiqueta efectiva (approve, reject, edit, unrelated, ambiguous, expired)
    remaining_question: str | None
    notice: str
    fact_id: str | None = None


@dataclass(frozen=True)
class ProposalOutcome:
    proposal: MemoryProposal | None = None
    blocked_code: str | None = None
    blocked_reason: str | None = None


@dataclass
class AgentMemory:
    store: MemoryStore
    classify: Callable[..., object] = classify_decision
    decision_llm: DecisionClassifier | None = None
    clock: Callable[[], datetime] = _utcnow
    max_proposals_per_day: int = MAX_PROPOSALS_PER_USER_PER_DAY
    read_k: int = 6

    # --- Lectura --------------------------------------------------------------------------

    def read_relevant(self, question: str) -> list[MemoryFact]:
        """Hechos aprobados afines a la pregunta (y se anota su uso para el TTL)."""
        facts = consolidation.select_relevant(question, self.store.active_facts(), k=self.read_k)
        if facts:
            self.store.touch_facts([f.id for f in facts], self.clock())
        return facts

    # --- Propuesta ------------------------------------------------------------------------

    def _audit(self, event: str, **kwargs: object) -> None:
        self.store.add_audit(AuditEvent(event=event, created_at=self.clock(), **kwargs))

    def _blocked(self, code: str, reason: str, conversation_id: str, user_id: str,
                 candidate: MemoryCandidate) -> ProposalOutcome:
        # Sin el texto del hecho: si estaba prohibido, tampoco debe vivir en la auditoría.
        self._audit(
            "blocked", conversation_id=conversation_id, user_id=user_id,
            details={"code": code, "business_line": candidate.business_line, "category": candidate.category},
        )
        return ProposalOutcome(blocked_code=code, blocked_reason=reason)

    def propose(
        self, conversation_id: str, user_id: str, candidate: MemoryCandidate, source_message: str
    ) -> ProposalOutcome:
        """Registra la propuesta como `pending`. No escribe nada en la memoria."""
        verdict = check_fact(candidate.fact)
        if not verdict.allowed:
            return self._blocked(verdict.code or "policy", verdict.reason or "", conversation_id, user_id, candidate)

        related = consolidation.find_related(
            candidate.fact, candidate.business_line, candidate.category, self.store.active_facts()
        )
        if related.duplicate is not None:
            return self._blocked("already_known", "ya está en la memoria", conversation_id, user_id, candidate)

        now = self.clock()
        if self.store.count_user_proposals_since(user_id, now - timedelta(days=1)) >= self.max_proposals_per_day:
            return self._blocked("rate_limited", "límite diario de propuestas", conversation_id, user_id, candidate)

        proposal = MemoryProposal(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            user_id=user_id,
            fact=candidate.fact.strip(),
            business_line=candidate.business_line,
            category=candidate.category,
            reason=candidate.reason,
            source_message=redact(source_message) or "",
            created_at=now,
        )
        try:
            with self.store.transaction():
                self.store.add_proposal(proposal)
                self._audit(
                    "proposed", proposal_id=proposal.id, conversation_id=conversation_id, user_id=user_id,
                    message=proposal.source_message,
                    details={"fact": proposal.fact, "business_line": proposal.business_line,
                             "category": proposal.category, "reason": proposal.reason},
                )
        except PendingProposalExists:
            return ProposalOutcome(blocked_code="pending_exists", blocked_reason="ya hay una propuesta pendiente")
        return ProposalOutcome(proposal=proposal)

    # --- Decisión -------------------------------------------------------------------------

    def get_pending(self, conversation_id: str, user_id: str) -> MemoryProposal | None:
        pending = self.store.get_pending(conversation_id)
        # Solo quien recibió la propuesta puede decidirla.
        return pending if pending is not None and pending.user_id == user_id else None

    def _close(self, proposal: MemoryProposal, *, outcome: str, label: str, confidence: float | None,
               message: str | None, final_fact: str | None) -> MemoryProposal:
        closed = proposal.model_copy(update={
            "status": outcome,
            "decision_label": label,
            "decision_confidence": confidence,
            "decision_message": redact(message),
            "final_fact": final_fact,
            "decided_at": self.clock(),
        })
        self.store.close_proposal(closed)
        return closed

    def resolve_pending(self, conversation_id: str, user_id: str, message: str) -> Resolution | None:
        """Clasifica `message` contra la propuesta pendiente y aplica la decisión."""
        pending = self.get_pending(conversation_id, user_id)
        if pending is None:
            return None

        if consolidation.is_pending_stale(pending.created_at, self.clock()):
            with self.store.transaction():
                closed = self._close(pending, outcome="discarded", label="expired", confidence=None,
                                     message=None, final_fact=None)
                self._audit("discarded", proposal_id=pending.id, conversation_id=conversation_id,
                            user_id=user_id, details={"label": "expired"})
            return Resolution(closed, "discarded", "expired", message,
                              "La propuesta de memoria anterior caducó sin respuesta; no se guardó nada.")

        if self.decision_llm is not None:
            decision = self.classify(pending, message, llm_classifier=self.decision_llm)
        else:
            decision = self.classify(pending, message)
        label = effective_label(decision)
        outcome = _OUTCOME_BY_LABEL[label]
        final_fact = None
        if outcome == "approved":
            final_fact = pending.fact
        elif outcome == "edited":
            final_fact = (decision.edited_fact or "").strip()

        blocked = None
        if final_fact is not None:
            verdict = check_fact(final_fact)
            if not verdict.allowed:
                blocked, outcome, final_fact = verdict, "discarded", None

        details = {"label": label, "raw_label": decision.label, "confidence": decision.confidence,
                   "method": decision.method}
        fact_id = None
        write_kind = None
        with self.store.transaction():
            closed = self._close(pending, outcome=outcome, label=label, confidence=decision.confidence,
                                 message=message, final_fact=final_fact)
            self._audit(outcome, proposal_id=pending.id, conversation_id=conversation_id, user_id=user_id,
                        message=redact(message), details=details)
            if blocked is not None:
                self._audit("blocked", proposal_id=pending.id, conversation_id=conversation_id,
                            user_id=user_id, details={"code": blocked.code, "stage": "write"})
            if final_fact is not None:
                fact_id, write_kind = self._write_fact(closed, final_fact, user_id)

        remaining = decision.remaining_question
        if label == "unrelated" and not remaining:
            remaining = message
        return Resolution(closed, outcome, label, remaining,
                          self._notice(outcome, label, final_fact, write_kind, blocked), fact_id)

    @staticmethod
    def _notice(outcome: str, label: str, fact: str | None, write_kind: str | None, blocked) -> str:
        if blocked is not None:
            return f"No puedo guardar esa versión en la memoria: {blocked.reason}. No se guardó nada."
        if write_kind == "deduplicated":
            return "Ese hecho ya estaba en la memoria del equipo; no lo dupliqué."
        if outcome == "approved":
            return f"Guardado en la memoria del equipo: «{fact}»."
        if outcome == "edited":
            return f"Guardé tu versión corregida en la memoria del equipo: «{fact}»."
        if outcome == "rejected":
            return "Entendido, no guardaré esa propuesta en la memoria."
        if label == "unrelated":
            return "Descarté la propuesta de memoria pendiente porque no respondiste a ella; no se guardó nada."
        return "Descarté la propuesta de memoria pendiente porque no quedó clara tu decisión; no se guardó nada."

    # --- Escritura y consolidación --------------------------------------------------------

    def _write_fact(self, proposal: MemoryProposal, text: str, user_id: str) -> tuple[str, str]:
        now = self.clock()
        related = consolidation.find_related(text, proposal.business_line, proposal.category,
                                             self.store.active_facts())
        base = {"proposal_id": proposal.id, "conversation_id": proposal.conversation_id, "user_id": user_id}
        if related.duplicate is not None:
            self.store.touch_facts([related.duplicate.id], now)
            self._audit("deduplicated", entry_id=related.duplicate.id, details={"fact": text}, **base)
            return related.duplicate.id, "deduplicated"

        fact = MemoryFact(
            id=str(uuid.uuid4()),
            business_line=proposal.business_line,
            category=proposal.category,
            fact=text,
            fingerprint=consolidation.fingerprint(text),
            approved_by=user_id,
            proposal_id=proposal.id,
            supersedes_id=related.superseded.id if related.superseded else None,
            created_at=now,
        )
        kind = "written"
        if related.superseded is not None:
            self.store.set_fact_status(related.superseded.id, "superseded", f"reemplazado por {fact.id}", now)
            self._audit("superseded", entry_id=related.superseded.id,
                        details={"replaced_by": fact.id, "old_fact": related.superseded.fact}, **base)
            kind = "superseded"
        self.store.add_fact(fact)
        self._audit("written", entry_id=fact.id,
                    details={"fact": text, "business_line": fact.business_line, "category": fact.category,
                             "supersedes_id": fact.supersedes_id}, **base)
        self._apply_changes(consolidation.plan_consolidation(self.store.active_facts(), now))
        return fact.id, kind

    def _apply_changes(self, changes: list[consolidation.StatusChange]) -> None:
        now = self.clock()
        for change in changes:
            self.store.set_fact_status(change.fact_id, change.status, change.reason, now)
            self._audit("evicted" if change.reason == "evicted" else "expired",
                        entry_id=change.fact_id, details={"reason": change.reason})

    def consolidate(self) -> dict[str, int]:
        """Limpieza periódica: propuestas pendientes caducadas + TTL y topes de hechos."""
        now = self.clock()
        stale = self.store.list_stale_pending(now - timedelta(hours=consolidation.PENDING_TTL_HOURS))
        with self.store.transaction():
            for pending in stale:
                self._close(pending, outcome="discarded", label="expired", confidence=None,
                            message=None, final_fact=None)
                self._audit("discarded", proposal_id=pending.id, conversation_id=pending.conversation_id,
                            user_id=pending.user_id, details={"label": "expired"})
            changes = consolidation.plan_consolidation(self.store.active_facts(), now)
            self._apply_changes(changes)
        return {
            "pending_expired": len(stale),
            "facts_expired": sum(1 for c in changes if c.reason == "ttl"),
            "facts_evicted": sum(1 for c in changes if c.reason == "evicted"),
        }


def confirmation_question(proposal: MemoryProposal) -> str:
    """Pregunta que se añade a la respuesta del agente (misma conversación)."""
    return (
        "\n\n---\n"
        f"¿Quieres que recuerde esto para la próxima vez? «{proposal.fact}» "
        f"({CATEGORY_LABELS[proposal.category]} · {BUSINESS_LINE_LABELS[proposal.business_line]}). "
        "Responde «sí», «no» o envíame la versión corregida."
    )


def memory_chunks(facts: list[dict]) -> list[dict]:
    """Hechos aprobados como chunks de contexto (datos etiquetados, no instrucciones)."""
    return [
        {
            "source_document": "Memoria aprobada del equipo",
            "section": f"{CATEGORY_LABELS.get(f['category'], f['category'])} · "
                       f"{BUSINESS_LINE_LABELS.get(f['business_line'], f['business_line'])}",
            "text": f"Hecho confirmado por el equipo de soporte: {f['fact']}",
        }
        for f in facts
    ]
