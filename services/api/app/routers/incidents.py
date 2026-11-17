"""
Router: /api/incidents

- POST /api/incidents/analyze          -> analiza un CSV subido, devuelve JSON
                                          y guarda sus tickets válidos (Supabase)
- GET  /api/incidents/results/export   -> devuelve el último análisis en CSV
- GET  /api/incidents                  -> lista tickets (filtros status/category/client_company)
- GET  /api/incidents/{ticket_id}      -> un ticket; 404 si no existe
- POST /api/incidents                  -> crea un ticket (siempre OPEN)
- PATCH /api/incidents/{ticket_id}/status -> ciclo de vida del ticket
                                          (404 / 409 invalid_transition / 422)

Estos endpoints los consume el MCP Server `mcps/nexova-tools`, que es el
único camino del agente LangGraph hacia el gestor de incidentes.
"""

from __future__ import annotations

import logging
from typing import get_args

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlmodel import Session

import incidents_analyzer as ia

from app import incidents_store
from app.schemas import (
    IncidentAnalysisSummary,
    IncidentCategory,
    IncidentCreate,
    IncidentRead,
    IncidentStatus,
    IncidentStatusUpdate,
)
from app.state import get_last_summary, set_last_summary
from database import get_db
from models import User
from security import get_current_user

logger = logging.getLogger(__name__)

# Los Literal del contrato HTTP deben ser los mismos valores que valida el analizador.
if set(get_args(IncidentCategory)) != ia.VALID_CATEGORIES or set(get_args(IncidentStatus)) != ia.VALID_STATUSES:
    raise RuntimeError("Las categorías/estados de app.schemas no coinciden con incidents_analyzer.")

router = APIRouter(prefix="/api/incidents", tags=["incidents"])

MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB, margen para producción


@router.post("/analyze", response_model=IncidentAnalysisSummary)
async def analyze_incidents(
    file: UploadFile = File(...), current_user: User = Depends(get_current_user)
) -> IncidentAnalysisSummary:
    """
    Recibe un CSV como multipart/form-data, ejecuta la misma lógica de
    validación y análisis que el script de la Fase 1, y devuelve el
    resumen en JSON. Nunca incluye datos personales (emails) en la
    respuesta.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No se recibió ningún fichero.")

    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=400,
            detail=f"Formato no soportado: '{file.filename}'. Se espera un fichero .csv.",
        )

    raw_bytes = await file.read()

    if not raw_bytes:
        raise HTTPException(status_code=400, detail="El fichero está vacío.")

    if len(raw_bytes) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail="El fichero supera el tamaño máximo permitido (50 MB).",
        )

    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="No se pudo leer el fichero: la codificación debe ser UTF-8.",
        )

    try:
        records = ia.parse_csv_text(text)
    except (ia.EmptyFileError, ia.MissingHeaderError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="El fichero no tiene un formato CSV válido.",
        )

    result = ia.analyze(records, source_name=file.filename)
    summary = ia.build_summary(result)

    # Se guarda para que GET /api/incidents/results/export pueda descargarlo.
    set_last_summary(summary)

    # Tickets válidos a Supabase para consultarlos uno a uno (GET /api/incidents/{id}).
    # Si la base de datos falla, el análisis se devuelve igual: el resumen no depende de ella.
    try:
        stored = await run_in_threadpool(incidents_store.upsert_valid_tickets, records)
        logger.info("Incidentes: %d tickets válidos guardados de %s", stored, file.filename)
    except Exception:
        logger.exception("No se pudieron guardar los tickets de %s", file.filename)

    return IncidentAnalysisSummary.model_validate(summary)


@router.get("/results/export")
async def export_results(current_user: User = Depends(get_current_user)) -> Response:
    """
    Devuelve el último análisis ejecutado como un CSV descargable
    (una fila por métrica), igual que el script de la Fase 1.
    """
    summary = get_last_summary()

    if summary is None:
        raise HTTPException(
            status_code=404,
            detail="No hay ningún análisis previo. Ejecuta primero POST /api/incidents/analyze.",
        )

    csv_text = ia.summary_to_csv_text(summary)

    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="results.csv"'},
    )


@router.get("", response_model=list[IncidentRead])
def list_incidents(
    status: str | None = Query(default=None, max_length=20),
    category: str | None = Query(default=None, max_length=20),
    client_company: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=20, ge=1, le=incidents_store.MAX_LIST_LIMIT),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[IncidentRead]:
    """Tickets guardados, del más reciente al más antiguo. Solo lectura."""
    tickets = incidents_store.list_tickets(
        db, status=status, category=category, client_company=client_company, limit=limit
    )
    return [IncidentRead.model_validate(t) for t in tickets]


@router.post("", response_model=IncidentRead, status_code=201)
def create_incident(
    payload: IncidentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> IncidentRead:
    """Crea un ticket individual. El servidor asigna `ticket_id` (NXV-NNNNNN) y `status = OPEN`."""
    ticket = incidents_store.create_ticket(
        db,
        client_company=payload.client_company,
        category=payload.category,
        description=payload.description,
        agent_id=payload.agent_id,
        ticket_date=payload.date,
    )
    logger.info("Incidentes: ticket %s creado por %s", ticket.ticket_id, current_user.id)
    return IncidentRead.model_validate(ticket)


@router.patch("/{ticket_id}/status", response_model=IncidentRead)
def update_incident_status(
    ticket_id: str,
    payload: IncidentStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> IncidentRead:
    """
    Endpoint de ciclo de vida: OPEN→CLOSED, OPEN→DISCARDED y CLOSED→OPEN.
    DISCARDED es final. Otra transición responde 409 (`invalid_transition`).
    """
    try:
        ticket = incidents_store.update_status(db, ticket_id, payload.status)
    except incidents_store.TicketNotFoundError:
        raise HTTPException(status_code=404, detail=f"No existe el ticket '{ticket_id}'.") from None
    except incidents_store.InvalidTransitionError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "invalid_transition",
                "message": str(exc),
                "current_status": exc.current,
                "requested_status": exc.target,
                "allowed": exc.allowed,
            },
        ) from None
    logger.info("Incidentes: ticket %s → %s por %s", ticket.ticket_id, ticket.status, current_user.id)
    return IncidentRead.model_validate(ticket)


@router.get("/{ticket_id}", response_model=IncidentRead)
def get_incident(
    ticket_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> IncidentRead:
    """Un ticket por su id (p. ej. NXV-000001). Solo lectura."""
    ticket = incidents_store.get_ticket(db, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"No existe el ticket '{ticket_id}'.") from None
    return IncidentRead.model_validate(ticket)
