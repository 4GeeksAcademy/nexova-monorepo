"""
Puente hacia `data/pipelines/rfp_intake/`: el grafo y la persistencia viven allí.

Mismo recurso que `reporting/pipeline_bridge.py`: `data/pipelines` se añade
al final de `sys.path` (en Docker llega por `PIPELINES_PATH`). Importarlo
también registra las tablas `rfp_*` en `SQLModel.metadata`, así que
`create_db_and_tables()` las crea si faltan (como `incident`).
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

_DEFAULT_PIPELINES_PATH = (
    Path(__file__).resolve().parent / ".." / ".." / ".." / "data" / "pipelines"
)
PIPELINES_PATH = Path(os.environ.get("PIPELINES_PATH", _DEFAULT_PIPELINES_PATH)).resolve()

if str(PIPELINES_PATH) not in sys.path:
    sys.path.append(str(PIPELINES_PATH))

from rfp_intake import (  # noqa: E402
    InvalidUpload,
    SqlRfpStore,
    TicketNotFound,
    register_upload,
    run_intake,
)
from rfp_intake.events import EVENT_TICKET_CREATED, STREAM_KEY as EVENTS_STREAM_KEY  # noqa: E402
from rfp_intake.intake import MAX_PDF_BYTES  # noqa: E402
from rfp_drafting import HandoffNotReady, run_drafting, validate_handoff  # noqa: E402
from rfp_approval import (  # noqa: E402
    ApprovalDecision,
    ApprovalNotPending,
    ApprovalNotReady,
    InvalidDecision,
    active_departments,
    max_approval_rounds,
    open_checkpointer,
    pending_interrupt,
    resume_approval,
    start_approval,
    validate_approval_handoff,
    validate_decision,
)


@lru_cache(maxsize=1)
def get_store() -> SqlRfpStore:
    return SqlRfpStore()


def run_start_approval(ticket_id: str, department_id: str, store) -> dict:
    with open_checkpointer() as checkpointer:
        return start_approval(ticket_id, department_id, store=store, checkpointer=checkpointer)


def run_resume_approval(ticket_id: str, department_id: str, decision: dict | None, store) -> dict:
    with open_checkpointer() as checkpointer:
        return resume_approval(ticket_id, department_id, decision, store=store, checkpointer=checkpointer)


def run_branch_paused(ticket_id: str, department_id: str, store) -> bool:
    with open_checkpointer() as checkpointer:
        return pending_interrupt(ticket_id, department_id, store=store, checkpointer=checkpointer) is not None


__all__ = [
    "EVENTS_STREAM_KEY",
    "EVENT_TICKET_CREATED",
    "ApprovalDecision",
    "ApprovalNotPending",
    "ApprovalNotReady",
    "InvalidDecision",
    "active_departments",
    "max_approval_rounds",
    "run_branch_paused",
    "run_resume_approval",
    "run_start_approval",
    "validate_approval_handoff",
    "validate_decision",
    "MAX_PDF_BYTES",
    "HandoffNotReady",
    "run_drafting",
    "validate_handoff",
    "InvalidUpload",
    "TicketNotFound",
    "get_store",
    "register_upload",
    "run_intake",
]
