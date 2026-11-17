"""Estado compartido del grafo `rfp_intake`."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class IntakeState(TypedDict, total=False):
    ticket_id: str
    pdf_path: str
    markdown: str
    classification: dict[str, Any]
    discarded: bool
    metadata: dict[str, Any]
    rfp_id: str | None
    assignments: list[dict[str, Any]]
    unmapped_services: list[str]
    # Los workers corren en paralelo (Send): cada uno añade su sección.
    department_results: Annotated[list[dict[str, Any]], operator.add]
    synthesis: dict[str, Any]
    handoff: dict[str, Any]
    status: str
