"""
Persistencia de roles, departamentos e informes internos (TinyDB, como usuarios y perfiles).

Las rutas de archivo son variables de módulo para poder redirigirlas en las pruebas. Las lecturas de
roles y departamentos combinan el archivo con el catálogo del BRIEFING sin escribir nada (una
instalación nueva ya conoce los rangos); las escrituras siembran antes el catálogo, de forma
idempotente y sin pisar filas existentes.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tinydb import Query, TinyDB

from rbac import catalog

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ROLES_PATH = DATA_DIR / "roles.json"
DEPARTMENTS_PATH = DATA_DIR / "departments.json"
REPORTS_PATH = DATA_DIR / "internal_reports.json"


def _open(path: Path, table_name: str) -> tuple[TinyDB, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = TinyDB(path)
    return db, db.table(table_name)


# --- Roles -------------------------------------------------------------------


def ensure_roles() -> None:
    db, table = _open(ROLES_PATH, "roles")
    try:
        existing = {doc["code"] for doc in table.all()}
        for role in catalog.ROLES_SEED:
            if role["code"] not in existing:
                table.insert({"id": role["code"], **role})
    finally:
        db.close()


def _snapshot(path: Path, table_name: str, seeds: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Filas por `id`: las del archivo (si existe) más las semillas que falten. No escribe nada."""
    rows: dict[str, dict[str, Any]] = {str(s["code"]): {"id": s["code"], **s} for s in seeds}
    if path.exists():
        db = TinyDB(path)
        try:
            for doc in db.table(table_name).all():
                rows[str(doc["id"])] = dict(doc)
        finally:
            db.close()
    return rows


def list_roles() -> list[dict[str, Any]]:
    rows = _snapshot(ROLES_PATH, "roles", catalog.ROLES_SEED)  # type: ignore[arg-type]
    return sorted(rows.values(), key=lambda d: (d["rank"], d["code"]))


def get_role(role_id: str) -> dict[str, Any] | None:
    return _snapshot(ROLES_PATH, "roles", catalog.ROLES_SEED).get(role_id)  # type: ignore[arg-type]


def create_role(code: str, name: str, rank: int) -> dict[str, Any]:
    ensure_roles()
    db, table = _open(ROLES_PATH, "roles")
    try:
        doc = {"id": code, "code": code, "name": name, "rank": rank}
        table.insert(doc)
        return doc
    finally:
        db.close()


def update_role(role_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
    ensure_roles()
    db, table = _open(ROLES_PATH, "roles")
    try:
        if table.get(Query().id == role_id) is None:
            return None
        table.update(changes, Query().id == role_id)
        return dict(table.get(Query().id == role_id))
    finally:
        db.close()


def rank_of(role_code: str | None) -> int:
    """Rango de un rol por código (0 = sin acceso interno: cuentas externas o rol desconocido)."""
    if not role_code:
        return catalog.EXTERNAL_RANK
    code = catalog.LEGACY_ROLE_ALIASES.get(role_code, role_code)
    role = get_role(code)
    return int(role["rank"]) if role else catalog.EXTERNAL_RANK


# --- Departamentos -----------------------------------------------------------


def ensure_departments() -> None:
    db, table = _open(DEPARTMENTS_PATH, "departments")
    try:
        existing = {doc["code"] for doc in table.all()}
        for dept in catalog.DEPARTMENTS_SEED:
            if dept["code"] not in existing:
                table.insert({"id": dept["code"], **dept})
    finally:
        db.close()


def list_departments() -> list[dict[str, Any]]:
    rows = _snapshot(DEPARTMENTS_PATH, "departments", catalog.DEPARTMENTS_SEED)  # type: ignore[arg-type]
    return sorted(rows.values(), key=lambda d: d["code"])


def get_department(department_id: str) -> dict[str, Any] | None:
    return _snapshot(DEPARTMENTS_PATH, "departments", catalog.DEPARTMENTS_SEED).get(department_id)  # type: ignore[arg-type]


def create_department(code: str, name: str) -> dict[str, Any]:
    ensure_departments()
    db, table = _open(DEPARTMENTS_PATH, "departments")
    try:
        doc = {"id": code, "code": code, "name": name}
        table.insert(doc)
        return doc
    finally:
        db.close()


def update_department(department_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
    ensure_departments()
    db, table = _open(DEPARTMENTS_PATH, "departments")
    try:
        if table.get(Query().id == department_id) is None:
            return None
        table.update(changes, Query().id == department_id)
        return dict(table.get(Query().id == department_id))
    finally:
        db.close()


# --- Informes internos (colección con alcance departamental) ------------------


def create_report(department_id: str, title: str, body: str, created_by: str) -> dict[str, Any]:
    db, table = _open(REPORTS_PATH, "internal_reports")
    try:
        doc = {
            "id": str(uuid.uuid4()),
            "department_id": department_id,
            "title": title,
            "body": body,
            "created_by": created_by,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        table.insert(doc)
        return doc
    finally:
        db.close()


def list_reports(department_id: str | None = None) -> list[dict[str, Any]]:
    """Todos los informes, o solo los de `department_id` (el alcance lo decide quien llama)."""
    db, table = _open(REPORTS_PATH, "internal_reports")
    try:
        docs = [dict(d) for d in table.all() if department_id is None or d.get("department_id") == department_id]
        return sorted(docs, key=lambda d: d["created_at"], reverse=True)
    finally:
        db.close()


def get_report(report_id: str) -> dict[str, Any] | None:
    db, table = _open(REPORTS_PATH, "internal_reports")
    try:
        doc = table.get(Query().id == report_id)
        return dict(doc) if doc else None
    finally:
        db.close()


def update_report(report_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
    db, table = _open(REPORTS_PATH, "internal_reports")
    try:
        if table.get(Query().id == report_id) is None:
            return None
        table.update(changes, Query().id == report_id)
        return dict(table.get(Query().id == report_id))
    finally:
        db.close()


def delete_report(report_id: str) -> bool:
    db, table = _open(REPORTS_PATH, "internal_reports")
    try:
        return bool(table.remove(Query().id == report_id))
    finally:
        db.close()
