"""
Auditoría de gestión de errores (backend): ninguna respuesta ni dato visible para el
cliente incluye trazas, texto de excepciones, rutas internas o cadenas de conexión.

    uv run --project services/api pytest tests/pipelines/test_error_handling.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT / "services" / "api", ROOT / "packages" / "incidents-analyzer"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("DATABASE_URL", "postgresql://blocked:blocked@localhost:1/blocked")

SECRET = "postgresql://admin:Sup3rSecret@db.interno.example:5432/prod"  # secret-scan: allow (valor falso de prueba)


@pytest.fixture()
def boom_client():
    """La API real con una ruta que lanza una excepción con datos sensibles en el mensaje."""
    from app.main import app
    from fastapi.testclient import TestClient

    async def boom():
        raise RuntimeError(f"no se pudo conectar a {SECRET} (archivo /srv/app/secret.py)")

    app.add_api_route("/__boom", boom, methods=["GET"])
    try:
        # Sin `with`: no se ejecuta el arranque de la API (base de datos, grafo del agente).
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) != "/__boom"]


def test_unhandled_exception_returns_clean_json_500(boom_client) -> None:
    res = boom_client.get("/__boom")

    assert res.status_code == 500
    assert res.headers["content-type"].startswith("application/json")
    assert res.json() == {"detail": "Ha ocurrido un error inesperado. Inténtalo de nuevo más tarde."}


def test_unhandled_exception_never_leaks_internals(boom_client) -> None:
    text = boom_client.get("/__boom").text
    for secret in ("Sup3rSecret", "db.interno", "/srv/app", "Traceback", "RuntimeError", "secret.py"):
        assert secret not in text


def test_unhandled_exception_is_logged_server_side(boom_client, caplog) -> None:
    with caplog.at_level("ERROR", logger="api.timing"):
        boom_client.get("/__boom")
    assert any("RuntimeError" in r.getMessage() for r in caplog.records)  # el detalle va al log, no al cliente


def test_task_status_exposes_only_the_exception_type(monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from models import User
    from security import get_current_user
    from tasks import router as tasks_router

    class Failed:
        state = "FAILURE"
        result = RuntimeError(f"fallo en {SECRET}")

    monkeypatch.setattr(tasks_router, "AsyncResult", lambda *a, **k: Failed())
    app = FastAPI()
    app.include_router(tasks_router.router)
    app.dependency_overrides[get_current_user] = lambda: User(
        id="u", email="u@example.com", hashed_password="x", role="user"
    )
    body = TestClient(app).get("/tasks/00000000-0000-4000-8000-000000000001").json()

    assert body["status"] == "failure"
    assert body["error"].startswith("RuntimeError:")
    assert "Sup3rSecret" not in body["error"] and "db.interno" not in body["error"]


def test_rfp_trace_event_stores_only_the_exception_type() -> None:
    """`GET /rfp/tickets/{id}/trace` sirve estos eventos: el texto de la excepción no se guarda."""
    from rfp_intake.trace import traced

    events: list[dict] = []

    class Store:
        def append_trace(self, ticket_id: str, event: dict) -> None:
            events.append(event)

    def failing_node(state: dict) -> dict:
        raise RuntimeError(f"fallo leyendo {SECRET}")

    wrapped = traced(failing_node, store=Store(), part="parte1", node="clasificar", agent="clasificador")
    with pytest.raises(RuntimeError):  # el error sigue subiendo para que Celery reintente
        wrapped({"ticket_id": "RFP-TEST0001"})

    assert [e["status"] for e in events] == ["error"]
    assert events[0]["output"] == {"error": "RuntimeError"}
    assert "Sup3rSecret" not in str(events[0]) and "db.interno" not in str(events[0])
