"""
Respuesta humana en la pausa de aprobación (Parte 3).

Toda decisión se valida aquí ANTES de reanudar el grafo (`resume_approval`) y
otra vez dentro del nodo `human_approval`: lo que entra al grafo es siempre un
`ApprovalDecision` válido para esa pausa concreta (aprobador esperado y
decisiones permitidas en esa ronda).
"""

from __future__ import annotations

import unicodedata
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

DECISION_APPROVE = "approve"
DECISION_REQUEST_CHANGES = "request_changes"
DECISION_REJECT = "reject"
DECISIONS: tuple[str, ...] = (DECISION_APPROVE, DECISION_REQUEST_CHANGES, DECISION_REJECT)

Decision = Literal["approve", "request_changes", "reject"]

MIN_COMMENT_CHARS = 5


class InvalidDecision(ValueError):
    """La respuesta humana no es válida para la pausa en curso."""


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision_id: str = Field(default_factory=lambda: uuid4().hex, max_length=64)
    decision: Decision
    # Responsable nombrado (BRIEFING §2.1) o árbitro fijo (§7) en cuyo nombre se decide.
    acting_as: str = Field(min_length=1, max_length=120)
    comment: str = Field(default="", max_length=2000)
    # Usuario real del backoffice que registró la decisión (lo pone la API, no el cliente).
    decided_by_user: str | None = Field(default=None, max_length=255)

    @field_validator("comment", mode="before")
    @classmethod
    def _none_is_empty(cls, value: Any) -> Any:
        return "" if value is None else value

    @model_validator(mode="after")
    def _comment_required_for_changes(self) -> "ApprovalDecision":
        if self.decision != DECISION_APPROVE and len(self.comment) < MIN_COMMENT_CHARS:
            raise ValueError("Para pedir cambios o rechazar hay que explicar el motivo en el comentario.")
        return self


def _same_person(a: str | None, b: str | None) -> bool:
    def norm(value: str | None) -> str:
        value = unicodedata.normalize("NFKD", value or "")
        return "".join(ch for ch in value if not unicodedata.combining(ch)).casefold().strip()

    return bool(a) and norm(a) == norm(b)


def validate_decision(raw: Any, *, expected_approver: str, allowed_decisions: list[str] | tuple[str, ...]
                      ) -> ApprovalDecision:
    """Valida `raw` contra la pausa: formato, aprobador esperado y decisiones permitidas."""
    try:
        decision = raw if isinstance(raw, ApprovalDecision) else ApprovalDecision.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise InvalidDecision(str(first.get("msg", "Decisión inválida.")).removeprefix("Value error, ")) from exc
    if not _same_person(decision.acting_as, expected_approver):
        raise InvalidDecision(
            f"Esta sección la decide {expected_approver}; no se puede registrar en nombre de {decision.acting_as}."
        )
    if decision.decision not in allowed_decisions:
        raise InvalidDecision(
            f"La decisión '{decision.decision}' no está permitida en esta ronda "
            f"(permitidas: {', '.join(allowed_decisions)})."
        )
    return decision.model_copy(update={"acting_as": expected_approver})
