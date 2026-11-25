"""
Fixtures de la batería de Roles y Permisos (RBAC).

- `services/api` importable como en Docker (módulos planos).
- TinyDB de usuarios, perfiles, roles, departamentos, informes y proveedores en `tmp_path`: ningún
  test toca `services/api/data/*.json`.
- Usuarios sembrados con los de verdad del BRIEFING (Javier/Megan/Elena/Patricia/Sergio) usando
  `rbac.seeding`, para probar con los mismos emails, roles y departamentos que pedirá Legal.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT / "services" / "api", ROOT / "data" / "pipelines", ROOT / "packages" / "incidents-analyzer"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("RESEND_API_KEY", "test-resend-key")
os.environ.setdefault("DATABASE_URL", "postgresql://blocked:blocked@localhost:1/blocked")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")

PASSWORD = "Sup3r-secreta!"

JAVIER = "javier.talent@nexova.example"  # supervisor · talent_selection
MEGAN = "megan.sales@nexova.example"  # supervisor · sales
ELENA = "elena.training@nexova.example"  # employee · corporate_training
PATRICIA = "patricia.hr@nexova.example"  # employee · human_resources
SERGIO = "sergio.admin@nexova.example"  # admin · technology


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    import cache
    import password_resets
    import rate_limit
    from rbac import store
    from routes import auth, profiles, suppliers, users

    monkeypatch.setattr(users, "DB_PATH", tmp_path / "users.json")
    monkeypatch.setattr(profiles, "DB_PATH", tmp_path / "profiles.json")
    monkeypatch.setattr(password_resets, "DB_PATH", tmp_path / "password_resets.json")
    monkeypatch.setattr(suppliers, "DB_PATH", tmp_path / "suppliers.json")
    monkeypatch.setattr(store, "ROLES_PATH", tmp_path / "roles.json")
    monkeypatch.setattr(store, "DEPARTMENTS_PATH", tmp_path / "departments.json")
    monkeypatch.setattr(store, "REPORTS_PATH", tmp_path / "internal_reports.json")
    monkeypatch.setattr(auth, "log_telemetry_event", lambda *a, **k: None)
    monkeypatch.setattr(auth, "send_password_reset_email", lambda *a, **k: None)
    for limiter in rate_limit.all_limiters():
        limiter.clear()
    cache.invalidate("")
    yield


@pytest.fixture()
def seeded(tmp_path):
    """Roles, departamentos, los 5 usuarios y los 2 informes del BRIEFING."""
    from rbac import seeding
    from security import hash_password

    seeding.seed_roles_and_departments()
    seeding.seed_users(tmp_path / "users.json", tmp_path / "profiles.json", hash_password(PASSWORD))
    seeding.seed_internal_reports(tmp_path / "users.json")


@pytest.fixture()
def api(seeded):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from rbac import router as rbac
    from routes import auth, profiles, suppliers, users

    app = FastAPI()
    for module in (auth, users, profiles, suppliers):
        app.include_router(module.router)
    for router in rbac.routers:
        app.include_router(router)
    return TestClient(app)


@pytest.fixture()
def as_user(api):
    """Cabecera `Authorization` de un usuario sembrado, iniciando sesión por la API real."""

    def _header(email: str) -> dict[str, str]:
        response = api.post("/auth/login", json={"email": email, "password": PASSWORD})
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _header


@pytest.fixture()
def user_id(api, as_user):
    """Id de un usuario sembrado (vía `/auth/me` no existe id; se lee del TinyDB de la prueba)."""
    from routes import users

    def _id(email: str) -> str:
        found = users.get_user_by_email(email)
        assert found is not None
        return found.id

    return _id


@pytest.fixture()
def report_id():
    """Id del informe sembrado de un departamento."""
    from rbac import store

    def _id(department_id: str) -> str:
        return next(r["id"] for r in store.list_reports() if r["department_id"] == department_id)

    return _id
