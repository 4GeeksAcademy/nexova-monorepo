"""
Router: /users

Administración de cuentas (email + password + rol + departamento). Los datos de perfil (nombre,
teléfono, dirección) viven en `Profile`, no aquí.

Roles y permisos (BRIEFING): rol y departamento son ejes independientes. `role` (capacidad) y
`department_id` (alcance de datos) se guardan por separado y se cambian por separado con
`PATCH /users/{id}/assignment`.

- `POST /users` y `GET /users`: solo `admin`.
- `GET/PUT /users/{id}`: el propio usuario o un `admin`. Cambiar el rol solo lo hace un `admin`.
- `DELETE /users/{id}`: solo `admin`.
- El registro público vive en `POST /auth/register` y crea siempre una cuenta externa (`user`).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from tinydb import Query, TinyDB

from authz import is_admin, require_admin, role_code
from models import (
    DeleteResponse,
    Role,
    User,
    UserAdminCreate,
    UserAssignment,
    UserCreate,
    UserRead,
    UserRegistered,
    UserUpdate,
)
from rbac import catalog, store
from routes.profiles import create_profile, delete_profile_by_user_id
from security import get_current_user, hash_password

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "users.json"
TABLE_NAME = "users"

router = APIRouter(prefix="/users", tags=["users"])


def _get_table() -> tuple[TinyDB, object]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = TinyDB(DB_PATH)
    return db, db.table(TABLE_NAME)


def get_user_by_id(user_id: str) -> User | None:
    db, table = _get_table()
    try:
        doc = table.get(Query().id == user_id)
        return User.model_validate(doc) if doc else None
    finally:
        db.close()


def get_user_by_email(email: str) -> User | None:
    db, table = _get_table()
    try:
        normalized_email = email.strip().lower()
        doc = next(
            (
                candidate
                for candidate in table.all()
                if str(candidate.get("email", "")).strip().lower() == normalized_email
            ),
            None,
        )
        return User.model_validate(doc) if doc else None
    finally:
        db.close()


def set_user_password(user_id: str, new_password: str) -> None:
    db, table = _get_table()
    try:
        table.update(
            {
                "hashed_password": hash_password(new_password),
                "password_changed_at": datetime.now(timezone.utc).isoformat(),
            },
            Query().id == user_id,
        )
    finally:
        db.close()


def create_user_record(
    *,
    email: str,
    password: str,
    role: str,
    department_id: str | None,
    name: str | None = None,
    phone: str | None = None,
    address: str | None = None,
) -> User:
    """Crea `User` + `Profile`. Compartido por el registro público y por `POST /users` (admin)."""
    db, table = _get_table()
    try:
        normalized_email = str(email).strip().lower()
        email_exists = any(
            str(existing.get("email", "")).strip().lower() == normalized_email for existing in table.all()
        )
        if email_exists:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ya existe un usuario con ese email.",
            )

        user = User(
            id=str(uuid.uuid4()),
            email=normalized_email,
            hashed_password=hash_password(password),
            role=role,
            department_id=department_id,
        )
        table.insert(user.model_dump(mode="json"))
    finally:
        db.close()

    # Crea el Profile vinculado (mismo `id` de usuario -> user_id).
    create_profile(user_id=user.id, name=name, phone=phone, address=address)
    return user


def register_external_user(payload: UserCreate) -> User:
    """Registro público: siempre una cuenta externa (`user`, rango 0), sin departamento ni empresa."""
    return create_user_record(
        email=str(payload.email),
        password=payload.password,
        role=Role.USER.value,
        department_id=None,
        name=payload.name,
        phone=payload.phone,
        address=payload.address,
    )


def _require_known_role(role_id: str) -> None:
    known = store.get_role(role_id) is not None or role_id in catalog.LEGACY_ROLE_ALIASES or role_id == catalog.EXTERNAL_ROLE
    if not known:
        raise HTTPException(status_code=422, detail="El rol indicado no existe.")


def _require_known_department(department_id: str) -> None:
    if store.get_department(department_id) is None:
        raise HTTPException(status_code=422, detail="El departamento indicado no existe.")


def _require_self_or_admin(target_user_id: str, current_user: User) -> None:
    if current_user.id != target_user_id and not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para acceder a este recurso.",
        )


@router.post("", response_model=UserRead, status_code=201)
def create_user(payload: UserAdminCreate, current_user: User = Depends(require_admin)) -> UserRead:
    """Solo administradores: alta de una cuenta con rol y departamento asignados por separado."""
    _require_known_role(payload.role_id)
    if payload.department_id is not None:
        _require_known_department(payload.department_id)
    user = create_user_record(
        email=str(payload.email),
        password=payload.password,
        role=payload.role_id,
        department_id=payload.department_id,
        name=payload.name,
    )
    return UserRead.model_validate(user)


@router.get("", response_model=list[UserRead])
def list_users(current_user: User = Depends(require_admin)) -> list[UserRead]:
    """Solo administradores: el listado expone email, rol y estado de todas las cuentas (OWASP A01)."""
    db, table = _get_table()
    try:
        return [UserRead.model_validate(doc) for doc in table.all()]
    finally:
        db.close()


@router.get("/{user_id}", response_model=UserRead)
def get_user(user_id: str, current_user: User = Depends(get_current_user)) -> UserRead:
    _require_self_or_admin(user_id, current_user)  # OWASP A01: sin IDOR sobre otras cuentas
    user = get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    return UserRead.model_validate(user)


@router.patch("/{user_id}/assignment", response_model=UserRead)
def update_assignment(
    user_id: str, payload: UserAssignment, current_user: User = Depends(require_admin)
) -> UserRead:
    """Cambia `role_id` y/o `department_id` de forma independiente: lo que no viene en el cuerpo no se toca."""
    updates: dict[str, object] = {}
    if "role_id" in payload.model_fields_set and payload.role_id is not None:
        _require_known_role(payload.role_id)
        updates["role"] = payload.role_id
    if "department_id" in payload.model_fields_set:
        if payload.department_id is not None:
            _require_known_department(payload.department_id)
        updates["department_id"] = payload.department_id

    db, table = _get_table()
    try:
        existing = table.get(Query().id == user_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Usuario no encontrado.")
        if "role" in updates and updates["role"] != role_code(User.model_validate(existing)):
            _ensure_not_last_admin(table, user_id, str(updates["role"]))
        table.update(updates, Query().id == user_id)
        return UserRead.model_validate(table.get(Query().id == user_id))
    finally:
        db.close()


def _ensure_not_last_admin(table: object, user_id: str, new_role: str) -> None:
    """No se puede dejar el sistema sin ningún admin activo (nadie podría administrar roles)."""
    if store.rank_of(new_role) >= store.rank_of(catalog.ROLE_ADMIN):
        return
    others = [
        doc
        for doc in table.all()  # type: ignore[attr-defined]
        if doc.get("id") != user_id
        and doc.get("is_active", True)
        and store.rank_of(role_code(User.model_validate(doc))) >= store.rank_of(catalog.ROLE_ADMIN)
    ]
    current = table.get(Query().id == user_id)  # type: ignore[attr-defined]
    if current and store.rank_of(str(current.get("role"))) >= store.rank_of(catalog.ROLE_ADMIN) and not others:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No se puede quitar el rol admin al último administrador activo.",
        )


@router.put("/{user_id}", response_model=UserRead)
def update_user(
    user_id: str,
    payload: UserUpdate,
    current_user: User = Depends(get_current_user),
) -> UserRead:
    _require_self_or_admin(user_id, current_user)

    updates: dict = {}
    if payload.email is not None:
        normalized_email = str(payload.email).strip().lower()
        updates["email"] = normalized_email
    if payload.role is not None:
        if not is_admin(current_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Solo un admin puede cambiar el rol de un usuario.",
            )
        _require_known_role(payload.role)
        updates["role"] = payload.role

    db, table = _get_table()
    try:
        existing = table.get(Query().id == user_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Usuario no encontrado.")

        if "role" in updates and updates["role"] != role_code(User.model_validate(existing)):
            _ensure_not_last_admin(table, user_id, str(updates["role"]))

        if "email" in updates:
            email_exists = any(
                candidate.get("id") != user_id
                and str(candidate.get("email", "")).strip().lower()
                == normalized_email
                for candidate in table.all()
            )
            if email_exists:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Ya existe un usuario con ese email.",
                )

        if updates:
            table.update(updates, Query().id == user_id)

        updated = table.get(Query().id == user_id)
        return UserRead.model_validate(updated)
    finally:
        db.close()


@router.delete("/{user_id}", response_model=DeleteResponse)
def delete_user(user_id: str, current_user: User = Depends(require_admin)) -> DeleteResponse:
    """Eliminar es una capacidad de `admin` (BRIEFING)."""
    db, table = _get_table()
    try:
        existing = table.get(Query().id == user_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Usuario no encontrado.")
        _ensure_not_last_admin(table, user_id, "")
        table.remove(Query().id == user_id)
    finally:
        db.close()

    delete_profile_by_user_id(user_id)

    return DeleteResponse(deleted=True)
