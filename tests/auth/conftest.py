"""
Fixtures de la batería de pruebas de autenticación (AUTH-088).

- `services/api` importable como en Docker (módulos planos).
- TinyDB de usuarios, perfiles y tokens de reset en `tmp_path`: ningún test toca
  `services/api/data/*.json`.
- Telemetría y email se sustituyen por grabadoras (`telemetry`, `outbox`) para poder
  afirmar *qué decidió* la lógica (¿se envió un email?, ¿qué motivo de fallo se registró?).
- Los limitadores en memoria se vacían antes de cada test.
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

# `setdefault`: no pisa el .env local, pero permite importar en un entorno limpio (CI).
# `email_service` exige RESEND_API_KEY al importarse.
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("RESEND_API_KEY", "test-resend-key")
os.environ.setdefault("DATABASE_URL", "postgresql://blocked:blocked@localhost:1/blocked")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")

DEFAULT_PASSWORD = "Sup3r-secreta!"


@pytest.fixture()
def outbox():
    """Emails de reset «enviados»: lista de `(email, token)`."""
    return []


@pytest.fixture()
def telemetry():
    """Eventos de telemetría emitidos por `routes.auth`: lista de `(evento, propiedades)`."""
    return []


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch, outbox, telemetry):
    import password_resets
    import rate_limit
    from routes import auth, profiles, users

    monkeypatch.setattr(users, "DB_PATH", tmp_path / "users.json")
    monkeypatch.setattr(profiles, "DB_PATH", tmp_path / "profiles.json")
    monkeypatch.setattr(password_resets, "DB_PATH", tmp_path / "password_resets.json")
    monkeypatch.setattr(
        auth,
        "log_telemetry_event",
        lambda event, **kwargs: telemetry.append((event, kwargs.get("properties") or {})),
    )
    monkeypatch.setattr(auth, "send_password_reset_email", lambda email, token: outbox.append((email, token)))
    for limiter in rate_limit.all_limiters():
        limiter.clear()
    yield
    for limiter in rate_limit.all_limiters():
        limiter.clear()


@pytest.fixture()
def api():
    """App mínima con los routers de auth, usuarios y perfiles (sin middleware de cabeceras)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import auth, profiles, users

    app = FastAPI()
    for module in (auth, users, profiles):
        app.include_router(module.router)
    return TestClient(app)


@pytest.fixture()
def register(api):
    """Registra por el endpoint público y devuelve el id del usuario."""

    def _register(email: str = "ana@example.com", password: str = DEFAULT_PASSWORD, **extra) -> str:
        response = api.post("/users", json={"email": email, "password": password, **extra})
        assert response.status_code == 201, response.text
        return response.json()["id"]

    return _register


@pytest.fixture()
def login(api):
    """Inicia sesión y devuelve la respuesta completa (para afirmar también los fallos)."""

    def _login(email: str, password: str = DEFAULT_PASSWORD):
        return api.post("/auth/login", json={"email": email, "password": password})

    return _login


@pytest.fixture()
def auth_header(login):
    """`Authorization` de un usuario ya registrado."""

    def _header(email: str = "ana@example.com", password: str = DEFAULT_PASSWORD) -> dict[str, str]:
        response = login(email, password)
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _header


@pytest.fixture()
def set_user_field():
    """Modifica un campo del usuario directamente en TinyDB (p. ej. `is_active`)."""

    def _set(user_id: str, **fields) -> None:
        from routes import users
        from tinydb import Query, TinyDB

        db = TinyDB(users.DB_PATH)
        try:
            db.table("users").update(fields, Query().id == user_id)
        finally:
            db.close()

    return _set
