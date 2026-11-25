"""
Endpoints del gestor de incidencias: `/api/incidents`.

Acceso (auditoría OWASP C2, la misma tenancy que el agente):
- Personal interno (`admin`/`manager`): todas las incidencias.
- Cliente con `client_company`: solo las de su empresa; una ajena responde 404
  (no se revela que existe) y al crear, la empresa se fuerza a la suya.
- `user` sin empresa: 403.
- Cambiar el estado: solo personal interno.

Convive en el mismo prefijo con `POST /analyze` y `GET /results/export` del
analizador CSV (`app/routers/incidents.py`): no hay solapamiento de rutas.
"""

from __future__ import annotations

from typing import Annotated

from agent.guardrails.tenancy import Tenant
from authz import current_tenant, require_supervisor
from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from nexova_shared.incident_domain import Branch, Category, Origin, Status

from . import store
from .errors import ApiProblem, ManagerRoute
from .schemas import IncidentCreate, IncidentRead, IncidentStatusUpdate, IncidentSummary

ROUTER_PREFIX = "/api/incidents"

router = APIRouter(prefix=ROUTER_PREFIX, tags=["incidents"], route_class=ManagerRoute)

NOT_FOUND = "No existe ninguna incidencia con ese identificador."


@router.post("", response_model=IncidentRead, status_code=201)
def create_incident(
    body: IncidentCreate,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(current_tenant),
) -> IncidentRead:
    company = body.client_company if tenant.is_staff else tenant.client_company
    return IncidentRead.model_validate(store.create_incident(db, body, client_company=company))


@router.get("", response_model=list[IncidentRead])
def list_incidents(
    status: Status | None = None,
    origin: Origin | None = None,
    branch: Branch | None = None,
    category: Category | None = None,
    client_company: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=store.MAX_LIST_LIMIT)] = store.DEFAULT_LIST_LIMIT,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(current_tenant),
) -> list[IncidentRead]:
    if not tenant.is_staff:
        client_company = tenant.client_company  # forzado: el cliente no elige la empresa
    rows = store.list_incidents(
        db,
        status=status,
        origin=origin,
        branch=branch,
        category=category,
        client_company=client_company,
        exact_company=not tenant.is_staff,
        limit=limit,
    )
    return [IncidentRead.model_validate(r) for r in rows if tenant.owns(r.client_company)]


# Declarado antes de `/{incident_id}` para que "summary" no se interprete como id.
@router.get("/summary", response_model=IncidentSummary)
def incident_summary(
    db: Session = Depends(get_db), tenant: Tenant = Depends(current_tenant)
) -> IncidentSummary:
    company = None if tenant.is_staff else tenant.client_company
    return IncidentSummary(**store.summarize(db, client_company=company))


@router.get("/{incident_id}", response_model=IncidentRead)
def get_incident(
    incident_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(current_tenant),
) -> IncidentRead:
    try:
        incident = store.get_incident(db, incident_id)
    except store.IncidentNotFoundError:
        raise HTTPException(status_code=404, detail=NOT_FOUND) from None
    if not tenant.owns(incident.client_company):
        raise HTTPException(status_code=404, detail=NOT_FOUND)  # no se revela que existe
    return IncidentRead.model_validate(incident)


@router.patch(
    "/{incident_id}/status",
    response_model=IncidentRead,
    dependencies=[Depends(require_supervisor)],
)
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
