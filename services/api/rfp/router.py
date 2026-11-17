"""
Endpoints de recepción de RFPs (Hito 9 · Parte 1). Solo HTTP: el grafo, la
persistencia y el guardado del PDF viven en `data/pipelines/rfp_intake/`.

- `POST /rfp/tickets`: guarda el PDF en `data/raw/rfp/`, crea el ticket en
  `analizando`, encola `rfp.run_intake` en Celery y responde 202 sin esperar.
- `GET /rfp/tickets`: tickets recientes (la UI hace poll mientras haya alguno
  en `analizando`).
- `POST /rfp/tickets/{ticket_id}/drafts`: (re)lanza la Parte 2 (borradores +
  evaluación) sobre el handoff de la Parte 1; 202, o 409 si no hay handoff o
  ya hay una generación en curso. La Parte 2 también arranca sola al terminar
  la Parte 1 (`tasks/rfp.py`).
- `GET /rfp/tickets/{ticket_id}`: detalle con metadatos, secciones por
  departamento, resumen para Ventas y payload de handoff.

Acceso: personal de Nexova. Un usuario externo (con `client_company`) recibe 403.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.exc import SQLAlchemyError

from models import User
from rfp import pipeline_bridge
from rfp.schemas import RfpDraftingAccepted, RfpTicketAccepted, RfpTicketDetail, RfpTicketSummary
from security import get_current_user
from tasks.rfp import generate_rfp_responses, run_rfp_intake

logger = logging.getLogger("rfp")

router = APIRouter(prefix="/rfp", tags=["rfp"])


def require_internal_user(current_user: User = Depends(get_current_user)) -> User:
    if current_user.client_company:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo personal interno de Nexova.")
    return current_user


def _storage_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="No se pudo acceder a la base de datos de RFPs. Inténtalo de nuevo en unos segundos.",
    )


@router.post(
    "/tickets",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RfpTicketAccepted,
    summary="Sube una RFP en PDF y lanza su análisis en segundo plano",
)
def upload_rfp(
    file: UploadFile = File(..., description="RFP en PDF"),
    current_user: User = Depends(require_internal_user),
) -> RfpTicketAccepted:
    content = file.file.read(pipeline_bridge.MAX_PDF_BYTES + 1)
    store = pipeline_bridge.get_store()
    try:
        ticket = pipeline_bridge.register_upload(content, file.filename, current_user.email, store)
    except pipeline_bridge.InvalidUpload as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        logger.exception("No se pudo crear el ticket RFP")
        raise _storage_unavailable() from exc

    try:
        task = run_rfp_intake.apply_async(kwargs={"ticket_id": ticket["ticket_id"]})
    except Exception as exc:
        logger.exception("No se pudo encolar el ticket %s", ticket["ticket_id"])
        try:
            store.record_error(ticket["ticket_id"], "No se pudo encolar el análisis (cola no disponible).")
        except SQLAlchemyError:
            logger.exception("No se pudo registrar el error del ticket %s", ticket["ticket_id"])
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"El ticket {ticket['ticket_id']} se creó, pero la cola de tareas no está disponible.",
        ) from exc

    logger.info("ticket=%s encolado task_id=%s por %s", ticket["ticket_id"], task.id, current_user.email)
    return RfpTicketAccepted(ticket_id=ticket["ticket_id"], status=ticket["status"], task_id=task.id)


_DRAFTING_IN_PROGRESS = ("generando_borrador", "en_evaluación")


@router.post(
    "/tickets/{ticket_id}/drafts",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RfpDraftingAccepted,
    summary="(Re)genera y evalúa los borradores por departamento (Parte 2)",
)
def regenerate_drafts(ticket_id: str, _: User = Depends(require_internal_user)) -> RfpDraftingAccepted:
    store = pipeline_bridge.get_store()
    try:
        ticket = store.get_ticket(ticket_id)
    except SQLAlchemyError as exc:
        logger.exception("No se pudo leer el ticket %s", ticket_id)
        raise _storage_unavailable() from exc
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket de RFP no encontrado.")
    try:
        pipeline_bridge.validate_handoff(ticket)
    except pipeline_bridge.HandoffNotReady as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    in_progress = ticket["status"] in _DRAFTING_IN_PROGRESS and not ticket["ready_for_approval"]
    if in_progress and not ticket.get("error_message"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="La generación de borradores ya está en curso para este ticket.")
    try:
        task = generate_rfp_responses.apply_async(kwargs={"ticket_id": ticket_id})
    except Exception as exc:
        logger.exception("No se pudo encolar la generación del ticket %s", ticket_id)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="La cola de tareas no está disponible.") from exc
    return RfpDraftingAccepted(ticket_id=ticket_id, status=ticket["status"], task_id=task.id)


@router.get("/tickets", response_model=list[RfpTicketSummary], summary="Tickets de RFP recientes")
def list_rfp_tickets(
    limit: int = Query(default=50, ge=1, le=200),
    _: User = Depends(require_internal_user),
) -> list[RfpTicketSummary]:
    try:
        rows = pipeline_bridge.get_store().list_tickets(limit=limit)
    except SQLAlchemyError as exc:
        logger.exception("No se pudieron listar los tickets RFP")
        raise _storage_unavailable() from exc
    return [RfpTicketSummary.model_validate(row) for row in rows]


@router.get("/tickets/{ticket_id}", response_model=RfpTicketDetail, summary="Detalle de un ticket de RFP")
def get_rfp_ticket(ticket_id: str, _: User = Depends(require_internal_user)) -> RfpTicketDetail:
    try:
        ticket = pipeline_bridge.get_store().get_ticket(ticket_id)
    except SQLAlchemyError as exc:
        logger.exception("No se pudo leer el ticket %s", ticket_id)
        raise _storage_unavailable() from exc
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket de RFP no encontrado.")
    return RfpTicketDetail.model_validate(ticket)
