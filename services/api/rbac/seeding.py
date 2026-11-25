"""
Semillas y migración de datos de RBAC (idempotentes). Las usan `scripts/seed_rbac.py` y
`scripts/migrate_roles_departments.py`, y se prueban en `tests/rbac/`.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from tinydb import Query, TinyDB

from rbac import catalog, store


def seed_roles_and_departments() -> None:
    store.ensure_roles()
    store.ensure_departments()


def seed_users(users_path: Path, profiles_path: Path, hashed_password: str) -> dict[str, int]:
    """Siembra los usuarios del BRIEFING (por email; no pisa cuentas existentes). Devuelve contadores."""
    from datetime import datetime, timezone

    created = skipped = 0
    users_db = TinyDB(users_path)
    profiles_db = TinyDB(profiles_path)
    try:
        users = users_db.table("users")
        profiles = profiles_db.table("profiles")
        existing = {str(u.get("email", "")).strip().lower() for u in users.all()}
        for seed in catalog.USERS_SEED:
            if seed["email"].lower() in existing:
                skipped += 1
                continue
            user_id = str(uuid.uuid4())
            users.insert(
                {
                    "id": user_id,
                    "email": seed["email"],
                    "hashed_password": hashed_password,
                    "is_active": True,
                    "role": seed["role"],
                    "department_id": seed["department"],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "password_changed_at": None,
                    "client_company": None,
                }
            )
            profiles.insert(
                {"id": str(uuid.uuid4()), "user_id": user_id, "name": seed["name"], "phone": None, "address": None}
            )
            created += 1
    finally:
        users_db.close()
        profiles_db.close()
    return {"created": created, "skipped": skipped}


def seed_internal_reports(users_path: Path) -> dict[str, int]:
    """Siembra un informe por departamento del BRIEFING, firmado por el admin sembrado (si existe)."""
    admin_email = next(u["email"] for u in catalog.USERS_SEED if u["role"] == catalog.ROLE_ADMIN)
    db = TinyDB(users_path)
    try:
        admin = next((u for u in db.table("users").all() if str(u.get("email", "")).lower() == admin_email), None)
    finally:
        db.close()
    author = admin["id"] if admin else "seed"
    existing = {(r["department_id"], r["title"]) for r in store.list_reports()}
    created = skipped = 0
    for seed in catalog.INTERNAL_REPORTS_SEED:
        if (seed["department"], seed["title"]) in existing:
            skipped += 1
            continue
        store.create_report(seed["department"], seed["title"], seed["body"], author)
        created += 1
    return {"created": created, "skipped": skipped}


def migrate_users(users_path: Path, *, dry_run: bool = False) -> list[dict[str, Any]]:
    """Adapta `users.json` al modelo de RBAC. Devuelve la lista de cambios aplicados (o que se aplicarían).

    - `manager` → `supervisor` (mismas capacidades internas; ahora con alcance departamental).
    - `admin` sin departamento → `technology` (el Admin sigue perteneciendo a un departamento).
    - `user` (cuenta externa) no se toca.
    Rol y departamento se tratan por separado: un usuario que ya tiene `department_id` lo conserva.
    """
    changes: list[dict[str, Any]] = []
    db = TinyDB(users_path)
    try:
        table = db.table("users")
        for doc in table.all():
            update: dict[str, Any] = {}
            role = doc.get("role")
            mapped = catalog.LEGACY_ROLE_ALIASES.get(role)
            if mapped:
                update["role"] = mapped
            if (mapped or role) == catalog.ROLE_ADMIN and not doc.get("department_id"):
                update["department_id"] = catalog.ADMIN_DEFAULT_DEPARTMENT
            if update:
                changes.append({"email": doc.get("email"), "from_role": role, **update})
                if not dry_run:
                    table.update(update, Query().id == doc["id"])
    finally:
        db.close()
    return changes
