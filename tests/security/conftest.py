"""
Tests de la auditoría OWASP Top 10 que no pertenecen a un dominio concreto
(autenticación, autorización por rol, cabeceras, WebSocket).

- `services/api` importable como en Docker (módulos planos).
- TinyDB de usuarios/perfiles/resets en `tmp_path`: nunca toca `services/api/data/`.
- La telemetría se sustituye por un no-op (no hay Supabase).
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
os.environ.setdefault("DATABASE_URL", "postgresql://blocked:blocked@localhost:1/blocked")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")


@pytest.fixture(autouse=True)
def default_rate_limits(monkeypatch):
    """Los tests asumen los límites por defecto (10/min, 200/día, 10 turnos/min, 20 RFP/h).

    `LLM_RATE_LIMIT_PER_MINUTE=3` en el `.env` (o exportado en la terminal, p. ej. tras
    `source .env`) cambiaría esos límites al importar `rate_limit` y rompería los tests.
    """
    import rate_limit

    for limiter, attempts in (
        (rate_limit.llm_per_minute, 10),
        (rate_limit.llm_per_day, 200),
        (rate_limit.chat_turns, 10),
        (rate_limit.rfp_jobs, 20),
    ):
        monkeypatch.setattr(limiter, "max_attempts", attempts)


@pytest.fixture(autouse=True)
def isolated_users(tmp_path, monkeypatch):
    import password_resets
    import rate_limit
    from routes import auth, profiles, users

    monkeypatch.setattr(users, "DB_PATH", tmp_path / "users.json")
    monkeypatch.setattr(profiles, "DB_PATH", tmp_path / "profiles.json")
    monkeypatch.setattr(password_resets, "DB_PATH", tmp_path / "password_resets.json")
    monkeypatch.setattr(auth, "log_telemetry_event", lambda *a, **k: None)
    monkeypatch.setattr(auth, "send_password_reset_email", lambda *a, **k: None)
    for limiter in rate_limit.all_limiters():
        limiter.clear()
    yield


@pytest.fixture()
def api():
    """App con los routers de auth/usuarios/conocimiento y el middleware real de cabeceras."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from http_security import security_headers
    from knowledge import router as knowledge
    from routes import auth, profiles, users

    app = FastAPI()
    app.middleware("http")(security_headers)
    for module in (auth, users, profiles, knowledge):
        app.include_router(module.router)
    return TestClient(app)


@pytest.fixture()
def register(api):
    """Registra por el endpoint público y devuelve (id, token)."""

    def _register(email: str, password: str = "Sup3r-secreta!", role: str | None = None):
        r = api.post("/auth/register", json={"email": email, "password": password})
        assert r.status_code == 201, r.text
        user_id = r.json()["id"]
        if role:
            from routes import users
            from tinydb import Query, TinyDB

            db = TinyDB(users.DB_PATH)
            db.table("users").update({"role": role}, Query().id == user_id)
            db.close()
        token = api.post("/auth/login", json={"email": email, "password": password}).json()["access_token"]
        return user_id, token

    return _register

