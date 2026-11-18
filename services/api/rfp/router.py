"""
Endpoints de recepción de RFPs (Hito 9 · Parte 1). Solo HTTP: el grafo, la
persistencia y el guardado del PDF viven en `data/pipelines/rfp_intake/`.

- `POST /rfp/tickets`: guarda el PDF en `data/raw/rfp/`, crea el ticket en
  `analizando`, encola `rfp.run_intake` en Celery y responde 202 sin esperar.
- `GET /rfp/events`: stream SSE (`text/event-stream`) con el evento
  `rfp_ticket_created` cuando un documento se clasifica como RFP válida;
  mismo JWT que el resto, `Last-Event-ID` para reanudar (ver `rfp/events.py`).
- `GET /rfp/tickets`: tickets recientes (la UI hace poll mientras haya alguno
  en `analizando`).
- `POST /rfp/tickets/{ticket_id}/drafts`: (re)lanza la Parte 2 (borradores +
  evaluación) sobre el handoff de la Parte 1; 202, o 409 si no hay handoff o
  ya hay una generación en curso. La Parte 2 también arranca sola al terminar
  la Parte 1 (`tasks/rfp.py`).
- `GET /rfp/tickets/{ticket_id}`: detalle con metadatos, secciones por
  departamento, resumen para Ventas y payload de handoff.

Parte 3 (aprobación humana y cierre):

- `POST /rfp/tickets/{ticket_id}/approvals[?department_id=]`: (re)abre la
  pausa de aprobación de todas las secciones (o de una). Arranca sola al
  terminar la Parte 2.
- `POST /rfp/tickets/{ticket_id}/approvals/{department_id}`: decisión humana
  (`approve` | `request_changes` | `reject`) en nombre del responsable
  (`acting_as`); se valida antes de reanudar y se encola la reanudación SOLO de
  esa rama (202). Se guarda también el usuario real que la registró.
- `GET /rfp/tickets/{ticket_id}/trace`: agente, input, output y timestamps de
  cada nodo ejecutado (Partes 1–3), en orden.
- `GET /rfp/tickets/{ticket_id}/final-document`: documento final (404 hasta
  que todas las secciones activas estén aprobadas).

Acceso: personal interno de Nexova (rol `admin` o `manager`, ver `authz.py`);
cualquier otra cuenta recibe 403. Una decisión de aprobación solo la registra el
responsable configurado en `RFP_APPROVERS` (JSON nombre → email) o un admin
(auditoría OWASP, hallazgo C1).
"""

from __future__ import annotations

import json
import logging
import os
import unicodedata

from authz import is_admin, require_staff
from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from models import User
from rate_limit import enforce_llm_budget
from sqlalchemy.exc import SQLAlchemyError
from tasks.rfp import (
    enqueue_approvals,
    generate_rfp_responses,
    resume_approval_branch,
    run_rfp_intake,
)

from rfp import pipeline_bridge
from rfp.events import (
    SSE_HEADERS,
    EventReader,
    make_event_reader,
    parse_last_event_id,
    stream_events,
)
from rfp.schemas import (
    ApprovalDecisionIn,
    DepartmentId,
    FinalDocumentRead,
    RfpApprovalAccepted,
    RfpDecisionAccepted,
    RfpDraftingAccepted,
    RfpTicketAccepted,
    RfpTicketDetail,
    RfpTicketSummary,
    TraceEventRead,
)

logger = logging.getLogger("rfp")

router = APIRouter(prefix="/rfp", tags=["rfp"])


def require_internal_user(current_user: User = Depends(require_staff)) -> User:
    """Personal interno = rol admin/manager. Una cuenta autoregistrada (`user`) nunca lo es."""
    if current_user.client_company:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo personal interno de Nexova.")
    return current_user


def _norm_person(name: str | None) -> str:
    decomposed = unicodedata.normalize("NFKD", name or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold().strip()


def _approver_emails() -> dict[str, str]:
    """`RFP_APPROVERS`: JSON `{"Nombre del responsable": "email"}`; vacío o inválido = solo admin."""
    try:
        raw = json.loads(os.environ.get("RFP_APPROVERS") or "{}")
    except json.JSONDecodeError:
        logger.error("RFP_APPROVERS no es JSON válido: solo un admin puede registrar decisiones.")
        return {}
    return {_norm_person(k): str(v).strip().lower() for k, v in raw.items()}


def authorize_decision(user: User, expected_approver: str) -> None:
    """La decisión la registra el propio responsable (por email) o un admin; nadie más."""
    if is_admin(user):
        return
    email = _approver_emails().get(_norm_person(expected_approver))
    if email and email == (user.email or "").strip().lower():
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Esta sección solo la puede decidir {expected_approver} (o un administrador).",
    )


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
    enforce_llm_budget("rfp", current_user.id)  # cada RFP lanza varios agentes en el worker
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
def regenerate_drafts(ticket_id: str, current_user: User = Depends(require_internal_user)) -> RfpDraftingAccepted:
    enforce_llm_budget("rfp", current_user.id)
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


@router.get(
    "/events",
    summary="Stream SSE de eventos de RFP (rfp_ticket_created)",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "Stream SSE"}},
)
async def rfp_events(
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    _user: User = Depends(require_internal_user),
    reader: EventReader | None = Depends(make_event_reader),
) -> StreamingResponse:
    if reader is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Notificaciones en tiempo real no disponibles."
        )
    return StreamingResponse(
        stream_events(reader, parse_last_event_id(last_event_id)),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


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
    return RfpTicketDetail.model_validate({**ticket, "max_approval_rounds": pipeline_bridge.max_approval_rounds()})


# --------------------------------------------------------------------------- #
# Parte 3: aprobación humana, trazabilidad y documento final
# --------------------------------------------------------------------------- #
def _load_ticket(ticket_id: str) -> dict:
    try:
        ticket = pipeline_bridge.get_store().get_ticket(ticket_id)
    except SQLAlchemyError as exc:
        logger.exception("No se pudo leer el ticket %s", ticket_id)
        raise _storage_unavailable() from exc
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket de RFP no encontrado.")
    return ticket


@router.post(
    "/tickets/{ticket_id}/approvals",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RfpApprovalAccepted,
    summary="(Re)abre la aprobación humana de las secciones (todas o una)",
)
def open_approvals(
    ticket_id: str,
    department_id: DepartmentId | None = Query(default=None, description="Solo este departamento"),
    _: User = Depends(require_internal_user),
) -> RfpApprovalAccepted:
    ticket = _load_ticket(ticket_id)
    try:
        departments = pipeline_bridge.validate_approval_handoff(ticket, department_id)
    except pipeline_bridge.ApprovalNotReady as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if ticket["status"] == "terminado":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="El ticket ya está terminado: su documento final está generado.")
    targets = [department_id] if department_id else departments
    busy = [s["department_id"] for s in ticket["sections"]
            if s["department_id"] in targets and s.get("approval_status") == "procesando"]
    if busy:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"Hay una decisión en curso para: {', '.join(busy)}.")
    task_ids = enqueue_approvals(ticket_id, targets)
    if not any(task_ids.values()):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="La cola de tareas no está disponible.")
    return RfpApprovalAccepted(ticket_id=ticket_id, department_ids=targets, task_ids=task_ids)


@router.post(
    "/tickets/{ticket_id}/approvals/{department_id}",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RfpDecisionAccepted,
    summary="Registra la decisión humana de un departamento y reanuda solo su rama",
)
def submit_decision(
    ticket_id: str,
    department_id: DepartmentId,
    body: ApprovalDecisionIn,
    current_user: User = Depends(require_internal_user),
) -> RfpDecisionAccepted:
    ticket = _load_ticket(ticket_id)
    try:
        pipeline_bridge.validate_approval_handoff(ticket, department_id)
    except pipeline_bridge.ApprovalNotReady as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    section = next(s for s in ticket["sections"] if s["department_id"] == department_id)
    if section.get("approval_status") != "pendiente":
        detail = ("Ya hay una decisión en curso para esta sección." if section.get("approval_status") == "procesando"
                  else "Esta sección no está esperando aprobación.")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)

    # Identidad antes que formato: quien llama debe SER el aprobador esperado (o admin).
    authorize_decision(current_user, section.get("expected_approver") or "")
    # Guardrail antes de reanudar el grafo: formato, aprobador esperado y decisión permitida en esta ronda.
    try:
        decision = pipeline_bridge.validate_decision(
            {**body.model_dump(), "decided_by_user": current_user.email},
            expected_approver=section.get("expected_approver") or "",
            allowed_decisions=section.get("allowed_decisions") or [],
        )
    except pipeline_bridge.InvalidDecision as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    store = pipeline_bridge.get_store()
    try:
        store.set_approval_status(ticket_id, department_id, "procesando")
    except SQLAlchemyError as exc:
        logger.exception("No se pudo registrar la decisión de %s/%s", ticket_id, department_id)
        raise _storage_unavailable() from exc
    try:
        task = resume_approval_branch.apply_async(kwargs={
            "ticket_id": ticket_id, "department_id": department_id, "decision": decision.model_dump(mode="json"),
        })
    except Exception as exc:
        logger.exception("No se pudo encolar la decisión de %s/%s", ticket_id, department_id)
        try:
            store.set_approval_status(ticket_id, department_id, "pendiente")
        except SQLAlchemyError:
            logger.exception("No se pudo liberar la sección %s/%s", ticket_id, department_id)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="La cola de tareas no está disponible.") from exc
    logger.info("ticket=%s dept=%s decisión=%s como %s por %s", ticket_id, department_id, decision.decision,
                decision.acting_as, current_user.email)
    return RfpDecisionAccepted(ticket_id=ticket_id, department_id=department_id, decision=decision.decision,
                               acting_as=decision.acting_as, approval_status="procesando", task_id=task.id)


@router.get("/tickets/{ticket_id}/trace", response_model=list[TraceEventRead],
            summary="Trazabilidad: qué agente hizo qué y en qué orden (Partes 1–3)")
def get_trace(ticket_id: str, _: User = Depends(require_internal_user)) -> list[TraceEventRead]:
    _load_ticket(ticket_id)
    try:
        rows = pipeline_bridge.get_store().list_trace(ticket_id)
    except SQLAlchemyError as exc:
        logger.exception("No se pudo leer el trace de %s", ticket_id)
        raise _storage_unavailable() from exc
    return [TraceEventRead.model_validate(row) for row in rows]


@router.get("/tickets/{ticket_id}/final-document", response_model=FinalDocumentRead,
            summary="Documento final consolidado (solo cuando todas las secciones están aprobadas)")
def get_final_document(ticket_id: str, _: User = Depends(require_internal_user)) -> FinalDocumentRead:
    _load_ticket(ticket_id)
    try:
        document = pipeline_bridge.get_store().get_final_document(ticket_id)
    except SQLAlchemyError as exc:
        logger.exception("No se pudo leer el documento final de %s", ticket_id)
        raise _storage_unavailable() from exc
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="El documento final aún no está generado: faltan aprobaciones.")
    return FinalDocumentRead.model_validate(document)
