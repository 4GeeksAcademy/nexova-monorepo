"""
Autorización por rol y por empresa cliente, compartida por todos los routers.

`security.get_current_user` solo autentica (token válido → usuario). Aquí se
decide QUÉ puede hacer ese usuario, con la misma definición que usa el agente
(`agent/guardrails/tenancy.py`):

- Personal interno de Nexova = rol `admin` o `manager`. Es la única fuente de
  verdad: el registro público (`POST /users`) crea siempre `user`, así que una
  cuenta autoregistrada nunca es personal interno (auditoría OWASP, hallazgo C1).
- Usuario externo con `client_company`: solo los datos de su empresa (C2).
- `user` sin empresa: ni personal interno ni cliente; no accede a datos de
  negocio (fallo seguro).
"""

from __future__ import annotations

from agent.guardrails.tenancy import Tenant, tenant_for_user
from fastapi import Depends, HTTPException, status
from models import Role, User
from security import get_current_user

STAFF_ONLY_DETAIL = "Solo personal interno de Nexova (rol admin o manager)."


def is_staff(user: User) -> bool:
    return tenant_for_user(user).is_staff


def is_admin(user: User) -> bool:
    return user.role == Role.ADMIN


def require_staff(current_user: User = Depends(get_current_user)) -> User:
    if not is_staff(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=STAFF_ONLY_DETAIL)
    return current_user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not is_admin(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo administradores.")
    return current_user


def current_tenant(current_user: User = Depends(get_current_user)) -> Tenant:
    """Tenant del usuario; 403 si no es personal interno ni cliente con empresa."""
    tenant = tenant_for_user(current_user)
    if not tenant.can_read_tickets:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tu cuenta no está asociada a ninguna empresa cliente.",
        )
    return tenant
