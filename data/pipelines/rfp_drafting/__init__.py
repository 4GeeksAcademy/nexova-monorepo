"""
Generación y evaluación de respuestas a RFP (Hito 9 · Parte 2).

Consume el handoff de la Parte 1 (`rfp_intake.v1`), genera una sección por
departamento con su propio generador, la evalúa en paralelo (legibilidad,
pertinencia, cumplimiento del BRIEFING §5) y repite con feedback hasta pasar o
agotar `RFP_MAX_ITERATIONS`. Ver `docs/rfp/rfp-drafting-design.md`.
"""

from rfp_drafting.drafting import run_drafting
from rfp_drafting.graph import (
    INPUT_CONTRACT_VERSION,
    OUTPUT_CONTRACT_VERSION,
    HandoffNotReady,
    build_drafting_graph,
    validate_handoff,
)

__all__ = [
    "INPUT_CONTRACT_VERSION",
    "OUTPUT_CONTRACT_VERSION",
    "HandoffNotReady",
    "build_drafting_graph",
    "run_drafting",
    "validate_handoff",
]
