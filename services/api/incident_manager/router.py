"""
Endpoints del gestor de incidencias.

Viven bajo `ROUTER_PREFIX` y no bajo `/api/incidents` porque el módulo del
analizador (agente, MCP, RFP) ya ocupa esos paths con otro contrato. Cuando ese
módulo se retire, basta cambiar la constante.

Acceso: solo personal interno (`admin`/`manager`), como el resto de datos de
negocio tras la auditoría OWASP.
"""

from __future__ import annotations

from typing import Annotated

from authz import require_staff
from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from nexova_shared.incident_domain import Branch, Category, Origin, Status

from . import store
from .errors import ApiProblem, ManagerRoute
from .schemas import IncidentCreate, IncidentRead, IncidentStatusUpdate, IncidentSummary

ROUTER_PREFIX = "/api/incident-manager"

router = APIRouter(
    prefix=ROUTER_PREFIX,
    tags=["incident-manager"],
    route_class=ManagerRoute,
    dependencies=[Depends(require_staff)],
)

NOT_FOUND = "No existe ninguna incidencia con ese identificador."


@router.post("", response_model=IncidentRead, status_code=201)
def create_incident(body: IncidentCreate, db: Session = Depends(get_db)) -> IncidentRead:
    return IncidentRead.model_validate(store.create_incident(db, body))


@router.get("", response_model=list[IncidentRead])
def list_incidents(
    status: Status | None = None,
    origin: Origin | None = None,
    branch: Branch | None = None,
    category: Category | None = None,
    limit: Annotated[int, Query(ge=1, le=store.MAX_LIST_LIMIT)] = store.DEFAULT_LIST_LIMIT,
    db: Session = Depends(get_db),
) -> list[IncidentRead]:
    rows = store.list_incidents(
        db, status=status, origin=origin, branch=branch, category=category, limit=limit
    )
    return [IncidentRead.model_validate(r) for r in rows]


# Declarado antes de `/{incident_id}` para que "summary" no se interprete como id.
@router.get("/summary", response_model=IncidentSummary)
def incident_summary(db: Session = Depends(get_db)) -> IncidentSummary:
    return IncidentSummary(**store.summarize(db))


@router.get("/{incident_id}", response_model=IncidentRead)
def get_incident(incident_id: int, db: Session = Depends(get_db)) -> IncidentRead:
    try:
        return IncidentRead.model_validate(store.get_incident(db, incident_id))
    except store.IncidentNotFoundError:
        raise HTTPException(status_code=404, detail=NOT_FOUND) from None


@router.patch("/{incident_id}/status", response_model=IncidentRead)
def update_incident_status(
    incident_id: int, body: IncidentStatusUpdate, db: Session = Depends(get_db)
) -> IncidentRead:
    try:
        return IncidentRead.model_validate(store.update_status(db, incident_id, body.status))
    except store.IncidentNotFoundError:
        raise HTTPException(status_code=404, detail=NOT_FOUND) from None
    except store.InvalidTransitionError as exc:
        allowed = ", ".join(exc.allowed) or "ninguno (estado final)"
        raise ApiProblem(
            400,
            "status",
            f"No se puede pasar de «{exc.current}» a «{exc.requested}». Estados permitidos: {allowed}.",
            current_status=exc.current,
            requested_status=exc.requested,
            allowed=list(exc.allowed),
        ) from None
