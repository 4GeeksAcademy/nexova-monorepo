"""
Aprobación humana y cierre del documento de RFP (Hito 9 · Parte 3).

Consume el handoff de la Parte 2 (`rfp_drafting.v1`): cada departamento activo
pasa por su propia pausa de aprobación humana (`interrupt` + checkpointer, un
hilo por departamento), con arbitraje determinista de conflictos (BRIEFING §7)
y un límite de rondas de cambios. Cuando todas las secciones están aprobadas se
genera el documento final y el ticket queda `terminado`.
Ver `docs/rfp/rfp-approval-design.md`.
"""

from rfp_approval.approval import (
    ApprovalNotPending,
    ApprovalNotReady,
    branch_status,
    max_approval_rounds,
    pending_interrupt,
    resume_approval,
    start_approval,
    validate_approval_handoff,
)
from rfp_approval.checkpointer import open_checkpointer, thread_id_for
from rfp_approval.final_document import active_departments, build_final_document, maybe_finalize
from rfp_approval.schemas import ApprovalDecision, InvalidDecision, validate_decision

__all__ = [
    "ApprovalDecision",
    "ApprovalNotPending",
    "ApprovalNotReady",
    "InvalidDecision",
    "active_departments",
    "branch_status",
    "build_final_document",
    "max_approval_rounds",
    "maybe_finalize",
    "open_checkpointer",
    "pending_interrupt",
    "resume_approval",
    "start_approval",
    "thread_id_for",
    "validate_approval_handoff",
    "validate_decision",
]
