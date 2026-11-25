"""
Routers de RBAC: /roles, /departments e /internal-reports.

Ninguna ruta compara roles a mano: el rol se exige con `Depends(require_*)` y el alcance departamental
con `department_scope` / `ensure_department_access` (ambos en `authz.py`).

- /roles y /departments: solo `admin`.
- /internal-reports (colección canónica con alcance departamental):
  `employee` lee y crea en su departamento, `supervisor` actualiza en su departamento,
  `admin` ve todos los departamentos y elimina.
"""

from __future__ import annotations

from authz import (
    DepartmentScope,
    department_scope,
    ensure_department_access,
    require_admin,
    require_employee,
    require_supervisor,
    scope_for,
)
from fastapi import APIRouter, Depends, HTTPException, status
from models import DeleteResponse, User
from rbac import store
from rbac.schemas import (
    DepartmentCreate,
    DepartmentRead,
    DepartmentUpdate,
    InternalReportCreate,
    InternalReportRead,
    InternalReportUpdate,
    RoleCreate,
    RoleRead,
    RoleUpdate,
)

ROLE_NOT_FOUND = "Rol no encontrado."
DEPARTMENT_NOT_FOUND = "Departamento no encontrado."
REPORT_NOT_FOUND = "Informe no encontrado."

roles_router = APIRouter(prefix="/roles", tags=["roles"], dependencies=[Depends(require_admin)])
departments_router = APIRouter(
    prefix="/departments", tags=["departments"], dependencies=[Depends(require_admin)]
)
reports_router = APIRouter(prefix="/internal-reports", tags=["internal-reports"])


# --- /roles ------------------------------------------------------------------


@roles_router.get("", response_model=list[RoleRead])
def list_roles() -> list[RoleRead]:
    return [RoleRead.model_validate(r) for r in store.list_roles()]


@roles_router.post("", response_model=RoleRead, status_code=status.HTTP_201_CREATED)
def create_role(payload: RoleCreate) -> RoleRead:
    if store.get_role(payload.code) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ya existe un rol con ese código.")
    _ensure_rank_keeps_hierarchy(payload.code, payload.rank)
    return RoleRead.model_validate(store.create_role(payload.code, payload.name, payload.rank))


@roles_router.get("/{role_id}", response_model=RoleRead)
def get_role(role_id: str) -> RoleRead:
    role = store.get_role(role_id)
    if role is None:
        raise HTTPException(status_code=404, detail=ROLE_NOT_FOUND)
    return RoleRead.model_validate(role)


@roles_router.put("/{role_id}", response_model=RoleRead)
def update_role(role_id: str, payload: RoleUpdate) -> RoleRead:
    if store.get_role(role_id) is None:
        raise HTTPException(status_code=404, detail=ROLE_NOT_FOUND)
    changes = payload.model_dump(exclude_none=True)
    if "rank" in changes:
        _ensure_rank_keeps_hierarchy(role_id, int(changes["rank"]))
    updated = store.update_role(role_id, changes)
    assert updated is not None
    return RoleRead.model_validate(updated)


def _ensure_rank_keeps_hierarchy(role_id: str, new_rank: int) -> None:
    """El rango es estrictamente jerárquico: nunca dos roles con el mismo rango, ni `admin` fuera de la cima."""
    others = [r for r in store.list_roles() if r["id"] != role_id]
    if any(int(r["rank"]) == new_rank for r in others):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ya existe un rol con ese rango.")
    if role_id == "admin" and any(int(r["rank"]) > new_rank for r in others):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="`admin` debe seguir siendo el rol de mayor rango.")
    if role_id != "admin" and any(r["id"] == "admin" and int(r["rank"]) <= new_rank for r in others):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ningún rol puede igualar o superar a `admin`.")


# --- /departments --------------------------------------------------------------


@departments_router.get("", response_model=list[DepartmentRead])
def list_departments() -> list[DepartmentRead]:
    return [DepartmentRead.model_validate(d) for d in store.list_departments()]


@departments_router.post("", response_model=DepartmentRead, status_code=status.HTTP_201_CREATED)
def create_department(payload: DepartmentCreate) -> DepartmentRead:
    if store.get_department(payload.code) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ya existe un departamento con ese código.")
    return DepartmentRead.model_validate(store.create_department(payload.code, payload.name))


@departments_router.get("/{department_id}", response_model=DepartmentRead)
def get_department(department_id: str) -> DepartmentRead:
    department = store.get_department(department_id)
    if department is None:
        raise HTTPException(status_code=404, detail=DEPARTMENT_NOT_FOUND)
    return DepartmentRead.model_validate(department)


@departments_router.put("/{department_id}", response_model=DepartmentRead)
def update_department(department_id: str, payload: DepartmentUpdate) -> DepartmentRead:
    updated = store.update_department(department_id, {"name": payload.name}) if store.get_department(department_id) else None
    if updated is None:
        raise HTTPException(status_code=404, detail=DEPARTMENT_NOT_FOUND)
    return DepartmentRead.model_validate(updated)


# --- /internal-reports -----------------------------------------------------------


def _load_report_or_404(report_id: str) -> dict:
    report = store.get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=REPORT_NOT_FOUND)
    return report


@reports_router.get("", response_model=list[InternalReportRead])
def list_reports(scope: DepartmentScope = Depends(department_scope)) -> list[InternalReportRead]:
    """Alcance, no denegación: el Admin ve todos los departamentos; el resto, solo el suyo."""
    reports = store.list_reports(None if scope.all_departments else scope.department_id)
    return [InternalReportRead.model_validate(r) for r in reports]


@reports_router.post("", response_model=InternalReportRead, status_code=status.HTTP_201_CREATED)
def create_report(
    payload: InternalReportCreate, current_user: User = Depends(require_employee)
) -> InternalReportRead:
    scope = scope_for(current_user)
    department_id = payload.department_id or scope.department_id
    if department_id is None:
        raise HTTPException(
            status_code=422,
            detail="Indica `department_id`: tu cuenta no tiene departamento asignado.",
        )
    ensure_department_access(current_user, department_id)  # un no-admin solo crea en su departamento
    if store.get_department(department_id) is None:
        raise HTTPException(status_code=422, detail="El departamento indicado no existe.")
    report = store.create_report(department_id, payload.title, payload.body, current_user.id)
    return InternalReportRead.model_validate(report)


@reports_router.get("/{report_id}", response_model=InternalReportRead)
def get_report(report_id: str, current_user: User = Depends(require_employee)) -> InternalReportRead:
    report = _load_report_or_404(report_id)
    ensure_department_access(current_user, report["department_id"])
    return InternalReportRead.model_validate(report)


@reports_router.put("/{report_id}", response_model=InternalReportRead)
def update_report(
    report_id: str, payload: InternalReportUpdate, current_user: User = Depends(require_supervisor)
) -> InternalReportRead:
    report = _load_report_or_404(report_id)
    ensure_department_access(current_user, report["department_id"])
    updated = store.update_report(report_id, payload.model_dump(exclude_none=True))
    assert updated is not None
    return InternalReportRead.model_validate(updated)


@reports_router.delete("/{report_id}", response_model=DeleteResponse)
def delete_report(report_id: str, current_user: User = Depends(require_admin)) -> DeleteResponse:
    _load_report_or_404(report_id)
    store.delete_report(report_id)
    return DeleteResponse(deleted=True)


routers = (roles_router, departments_router, reports_router)
