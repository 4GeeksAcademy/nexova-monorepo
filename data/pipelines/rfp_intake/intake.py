"""
Recepción del PDF y ejecución del pipeline sobre un ticket.

- `register_upload`: valida el PDF, lo guarda en `data/raw/rfp/<ticket_id>.pdf`
  y crea el ticket en `analizando`. Lo llama la API antes de responder 202.
- `run_intake`: ejecuta el grafo sobre un ticket existente. Lo llama el
  worker de Celery (y `scripts/rfp_reprocess.py`). Re-ejecutable: cada
  escritura es un upsert y las secciones se rehacen en `orchestrate`.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Any

from rfp_intake.events import TicketCreatedPublisher, publish_ticket_created
from rfp_intake.graph import build_rfp_graph
from rfp_intake.llm import LlmFn, OpenAIJsonLLM
from rfp_intake.store import RfpStore, SqlRfpStore, TicketNotFound

logger = logging.getLogger("rfp_intake")

MAX_PDF_BYTES = 20 * 1024 * 1024
PDF_SIGNATURE = b"%PDF-"
RAW_RELATIVE_DIR = "data/raw/rfp"
_DEFAULT_RAW_DIR = Path(__file__).resolve().parents[3] / "data" / "raw" / "rfp"


class InvalidUpload(ValueError):
    """El archivo subido no es un PDF aceptable. `status_code` orienta a la API."""

    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


def raw_dir() -> Path:
    """Carpeta de los PDF subidos (`RFP_RAW_DIR`; en Docker, `/data/raw/rfp`)."""
    return Path(os.environ.get("RFP_RAW_DIR", _DEFAULT_RAW_DIR)).resolve()


def new_ticket_id() -> str:
    return f"RFP-{secrets.token_hex(4).upper()}"


def resolve_pdf_path(raw_pdf_path: str) -> Path:
    """Ruta local del PDF a partir de lo guardado en el ticket (`data/raw/rfp/<id>.pdf`)."""
    return raw_dir() / Path(raw_pdf_path).name


def validate_pdf_upload(content: bytes, filename: str | None) -> str:
    name = Path(filename or "").name
    if not name.lower().endswith(".pdf"):
        raise InvalidUpload("Solo se aceptan RFP en PDF (.pdf).", 415)
    if not content:
        raise InvalidUpload("El archivo está vacío.")
    if len(content) > MAX_PDF_BYTES:
        raise InvalidUpload(f"El PDF supera el máximo de {MAX_PDF_BYTES // (1024 * 1024)} MB.", 413)
    if not content.startswith(PDF_SIGNATURE):
        raise InvalidUpload("El archivo no es un PDF válido (firma %PDF ausente).", 415)
    return name


def register_upload(
    content: bytes, filename: str | None, created_by: str | None, store: RfpStore
) -> dict[str, Any]:
    """Guarda el PDF en `data/raw/rfp/` y crea el ticket en `analizando`."""
    original_filename = validate_pdf_upload(content, filename)
    ticket_id = new_ticket_id()
    target_dir = raw_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{ticket_id}.pdf"
    tmp = target.with_suffix(".pdf.tmp")
    tmp.write_bytes(content)
    tmp.replace(target)
    try:
        return store.create_ticket(
            ticket_id=ticket_id,
            raw_pdf_path=f"{RAW_RELATIVE_DIR}/{target.name}",
            original_filename=original_filename,
            created_by=created_by,
        )
    except Exception:
        target.unlink(missing_ok=True)  # sin ticket no debe quedar un PDF huérfano
        raise


def run_intake(
    ticket_id: str,
    *,
    store: RfpStore | None = None,
    llm: LlmFn | None = None,
    publisher: TicketCreatedPublisher | None = publish_ticket_created,
) -> dict[str, Any]:
    """Ejecuta el grafo completo sobre el ticket. Las excepciones suben al llamador (Celery)."""
    store = store or SqlRfpStore()
    ticket = store.start_attempt(ticket_id)
    pdf_path = resolve_pdf_path(ticket["raw_pdf_path"])
    if not pdf_path.is_file():
        raise FileNotFoundError(f"No existe el PDF del ticket {ticket_id}: {pdf_path}")

    graph = build_rfp_graph(llm=llm or OpenAIJsonLLM(), store=store, publisher=publisher)
    final = graph.invoke({"ticket_id": ticket_id, "pdf_path": str(pdf_path)})
    result = {
        "ticket_id": ticket_id,
        "status": final.get("status"),
        "departments": [a["department_id"] for a in final.get("assignments") or []],
        "attempt": ticket.get("attempts"),
    }
    logger.info("rfp_intake ticket=%s status=%s departments=%s", ticket_id, result["status"],
                result["departments"])
    return result


__all__ = [
    "InvalidUpload",
    "TicketNotFound",
    "register_upload",
    "resolve_pdf_path",
    "run_intake",
    "validate_pdf_upload",
]
