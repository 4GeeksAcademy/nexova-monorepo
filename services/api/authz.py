"""
Autorización por rol, por departamento y por empresa cliente, compartida por todos los routers.

`security.get_current_user` solo autentica (token válido → usuario). Aquí se decide QUÉ puede hacer
ese usuario, siempre en este orden:

1. **Rol** (eje de capacidad): `require_role("supervisor")` compara rangos (`employee` 1 <
   `supervisor` 2 < `admin` 3). Rol insuficiente → 403 explícito, nunca una respuesta vacía.
2. **Departamento** (eje de alcance de datos): `department_scope` / `ensure_department_access`.
   El Admin ve todos los departamentos (privilegio del rol, no un departamento vacío); el resto,
   solo el suyo.

Ninguna ruta compara roles a mano: declara `Depends(require_role(...))`. Además:

- Personal interno de Nexova = rango ≥ 1 (employee, supervisor, admin y el heredado `manager`).
  Es la única fuente de verdad y la misma que usa el agente (`agent/guardrails/tenancy.py`). El
  registro público crea siempre `user` (rango 0), que nunca es personal interno (OWASP C1).
- Usuario externo con `client_company`: solo los datos de su empresa (C2).
- `user` sin empresa: ni personal interno ni cliente; no accede a datos de negocio (fallo seguro).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from agent.guardrails.tenancy import Tenant, tenant_for_user
from fastapi import Depends, HTTPException, status
from models import User
from rbac import catalog, store
from security import get_current_user

STAFF_ONLY_DETAIL = "Solo personal interno de Nexova (rol employee, supervisor o admin)."
ADMIN_ONLY_DETAIL = "Solo administradores."
OTHER_DEPARTMENT_DETAIL = "Este recurso pertenece a otro departamento."
NO_DEPARTMENT_DETAIL = "Tu cuenta no tiene departamento asignado: no puedes acceder a datos departamentales."


# --- Rol ---------------------------------------------------------------------


def role_code(user: User) -> str:
    """Código de rol del usuario (tolera el enum `Role`, que es un `str`)."""
    raw = getattr(user, "role", None)
    return str(getattr(raw, "value", raw) or "")


def role_rank(user: User) -> int:
    return store.rank_of(role_code(user))


def has_role(user: User, minimum_role: str) -> bool:
    """¿El rol del usuario alcanza `minimum_role` en la jerarquía?"""
    return role_rank(user) >= store.rank_of(minimum_role)


def is_staff(user: User) -> bool:
    return tenant_for_user(user).is_staff


def is_admin(user: User) -> bool:
    return has_role(user, catalog.ROLE_ADMIN)


def _insufficient_role(minimum_role: str, user: User) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            f"Rol insuficiente: se requiere «{minimum_role}» o superior "
            f"(tu rol: «{role_code(user) or 'sin rol'}»)."
        ),
    )


def require_role(minimum_role: str) -> Callable[..., User]:
    """Dependencia de FastAPI: exige un rol de rango ≥ `minimum_role` (403 si no)."""

    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if not has_role(current_user, minimum_role):
            raise _insufficient_role(minimum_role, current_user)
        return current_user

    dependency.__name__ = f"require_role_{minimum_role}"
    return dependency


require_employee = require_role(catalog.ROLE_EMPLOYEE)
require_supervisor = require_role(catalog.ROLE_SUPERVISOR)
require_admin = require_role(catalog.ROLE_ADMIN)


def require_staff(current_user: User = Depends(get_current_user)) -> User:
    """Personal interno (rango ≥ employee). Los endpoints que mutan datos usan `require_supervisor`/`require_admin`."""
    if not is_staff(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=STAFF_ONLY_DETAIL)
    return current_user


# --- Departamento ------------------------------------------------------------


@dataclass(frozen=True)
class DepartmentScope:
    """Alcance departamental de quien llama: `department_id is None` = todos los departamentos."""

    department_id: str | None
    all_departments: bool

    def allows(self, department_id: str | None) -> bool:
        return self.all_departments or (self.department_id is not None and self.department_id == department_id)


def scope_for(user: User) -> DepartmentScope:
    """Alcance de datos del usuario. El Admin ve todos los departamentos; los demás roles, solo el suyo."""
    if is_admin(user):
        return DepartmentScope(department_id=None, all_departments=True)
    return DepartmentScope(department_id=getattr(user, "department_id", None) or None, all_departments=False)


def department_scope(current_user: User = Depends(require_employee)) -> DepartmentScope:
    """Dependencia para colecciones con alcance departamental (rol mínimo: employee)."""
    scope = scope_for(current_user)
    if not scope.all_departments and scope.department_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NO_DEPARTMENT_DETAIL)
    return scope


def ensure_department_access(user: User, record_department_id: str | None) -> None:
    """403 si el registro es de otro departamento y el usuario no es Admin (rol ya comprobado antes)."""
    scope = scope_for(user)
    if scope.all_departments:
        return
    if scope.department_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NO_DEPARTMENT_DETAIL)
    if not scope.allows(record_department_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=OTHER_DEPARTMENT_DETAIL)


# --- Empresa cliente (tenancy del agente y del gestor de incidencias) ----------


def current_tenant(current_user: User = Depends(get_current_user)) -> Tenant:
    """Tenant del usuario; 403 si no es personal interno ni cliente con empresa."""
    tenant = tenant_for_user(current_user)
    if not tenant.can_read_tickets:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tu cuenta no está asociada a ninguna empresa cliente.",
        )
    return tenant
