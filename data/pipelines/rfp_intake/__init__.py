"""
Flujo agéntico de recepción y enrutamiento de RFPs (Hito 9 · Parte 1).

PDF → Markdown (MarkItDown) → clasificador → metadatos + legibilidad →
orquestador → workers por departamento (en paralelo) → synthesizer.
Ver `docs/rfp/rfp-intake-design.md`.
"""

from rfp_intake.graph import build_rfp_graph
from rfp_intake.intake import InvalidUpload, register_upload, resolve_pdf_path, run_intake
from rfp_intake.store import InMemoryRfpStore, SqlRfpStore, TicketNotFound

__all__ = [
    "InMemoryRfpStore",
    "InvalidUpload",
    "SqlRfpStore",
    "TicketNotFound",
    "build_rfp_graph",
    "register_upload",
    "resolve_pdf_path",
    "run_intake",
]
