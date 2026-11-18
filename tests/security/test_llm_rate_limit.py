"""
Informe NIST · Protect — rate limiting de los endpoints que disparan un LLM.

Antes de esta rama ningún endpoint con modelo tenía límite: un script en bucle
contra `/agent/query` o el chat convertía la factura del proveedor en un
incidente (y un usuario podía agotar la cuota diaria de toda la empresa, como
pasó con el `403 Key limit exceeded` del 2026-11-16).

    uv run --project services/api pytest tests/security/test_llm_rate_limit.py -v
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import rate_limit
from models import Role, User
from security import get_current_user

CLIENT = User(id="u-client", email="cliente@retailmax.com", hashed_password="x",
              role=Role.USER, client_company="RetailMax Corp")
OTHER = User(id="u-other", email="otro@nubesoft.com", hashed_password="x",
             role=Role.USER, client_company="NubeSoft")


def _agent_result(question: str, **kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(answer=f"ok: {question}", error=None, run_id="run-1",
                           conversation_id=kwargs.get("conversation_id"), memory_proposal=None,
                           memory_decision=None, guardrail=None)


@pytest.fixture()
def agent_api(monkeypatch: pytest.MonkeyPatch):
    from agent import router as agent_router

    calls: list[str] = []

    def fake_run_agent(question: str, **kwargs: Any) -> SimpleNamespace:
        calls.append(question)
        return _agent_result(question, **kwargs)

    monkeypatch.setattr(agent_router, "run_agent", fake_run_agent)
    app = FastAPI()
    app.include_router(agent_router.router)
    current = {"user": CLIENT}
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    return TestClient(app), calls, current


def test_eleventh_agent_query_in_a_minute_is_429_and_never_reaches_the_model(agent_api) -> None:
    client, calls, _ = agent_api
    codes = [client.post("/agent/query", json={"question": f"pregunta {i}"}).status_code for i in range(11)]
    assert codes == [200] * 10 + [429]
    assert len(calls) == 10  # la petición 11 no llegó al agente (ni al LLM)

    blocked = client.post("/agent/query", json={"question": "otra"})
    assert blocked.status_code == 429
    assert 0 < int(blocked.headers["Retry-After"]) <= 60
    assert "límite" in blocked.json()["detail"]


def test_budget_is_per_user(agent_api) -> None:
    client, _, current = agent_api
    for i in range(10):
        client.post("/agent/query", json={"question": f"p{i}"})
    assert client.post("/agent/query", json={"question": "x"}).status_code == 429
    current["user"] = OTHER  # otro usuario no paga el abuso del primero
    assert client.post("/agent/query", json={"question": "x"}).status_code == 200


def test_daily_budget_caps_a_slow_loop(agent_api, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un bucle lento esquiva el límite por minuto, pero no el diario."""
    client, _, _ = agent_api
    monkeypatch.setattr(rate_limit.llm_per_day, "max_attempts", 3)
    for i in range(3):
        assert client.post("/agent/query", json={"question": f"p{i}"}).status_code == 200
        rate_limit.llm_per_minute.clear()  # simula que pasó el minuto
    assert client.post("/agent/query", json={"question": "p3"}).status_code == 429


def test_rate_limit_is_logged_without_the_question(agent_api, caplog: pytest.LogCaptureFixture) -> None:
    client, _, _ = agent_api
    secret_question = "mi DNI es 12345678Z y mi salario 40.000"
    with caplog.at_level(logging.WARNING, logger="nexova.security"):
        for _ in range(11):
            client.post("/agent/query", json={"question": secret_question})
    records = [r for r in caplog.records if getattr(r, "event", None) == "llm_rate_limited"]
    assert len(records) == 1
    assert records[0].scope == "agent" and records[0].user_id == CLIENT.id
    assert "12345678Z" not in caplog.text  # minimización (RGPD art. 5.1.c)


def test_agent_and_knowledge_share_the_same_budget() -> None:
    for _ in range(10):
        assert rate_limit.consume_llm_budget("agent", "u-staff") == 0
    assert rate_limit.consume_llm_budget("knowledge", "u-staff") > 0


def test_rejected_calls_do_not_consume_budget() -> None:
    for _ in range(10):
        rate_limit.consume_llm_budget("agent", "u1")
    for _ in range(50):
        rate_limit.consume_llm_budget("agent", "u1")  # todas rechazadas
    rate_limit.llm_per_minute.clear()
    # Solo cuentan las 10 aceptadas: el diario (200) sigue lejos.
    assert rate_limit.consume_llm_budget("agent", "u1") == 0


# --- Chat WebSocket -------------------------------------------------------------------


def _fake_stream_agent(question: str, *, on_stream, abort, **kwargs: Any) -> SimpleNamespace:
    on_stream({"token": "ok"})
    return SimpleNamespace(answer="ok", error=None, run_id=kwargs.get("run_id"))


@pytest.fixture()
def chat_client(monkeypatch: pytest.MonkeyPatch):
    from chat import router as chat_router
    from chat.hub import ChatHub
    from chat.runner import ChatService
    from chat.store import InMemoryChatStore

    service = ChatService(InMemoryChatStore(), ChatHub(), agent_fn=_fake_stream_agent)
    monkeypatch.setattr(chat_router, "user_from_token", lambda token: CLIENT if token == "t" else None)
    app = FastAPI()
    app.include_router(chat_router.router)
    app.dependency_overrides[chat_router.get_chat_service] = lambda: service
    client = TestClient(app)
    client.__enter__()
    yield client, service
    client.__exit__(None, None, None)


def _recv_until(ws: Any, *events: str) -> dict[str, Any]:
    for _ in range(100):
        frame = ws.receive_json()
        if frame["event"] in events:
            return frame
    raise AssertionError(f"No llegó {events}")


def test_eleventh_chat_turn_gets_rate_limited_frame_and_socket_stays_open(chat_client) -> None:
    client, service = chat_client
    sid = service.create_session(CLIENT.id, CLIENT.client_company).session_id
    with client.websocket_connect(f"/chat/ws/{sid}?token=t") as ws:
        _recv_until(ws, "session_snapshot")
        for i in range(10):
            ws.send_json({"event": "user_message", "data": {"session_id": sid, "text": f"hola {i}"}})
            _recv_until(ws, "generation_completed")

        ws.send_json({"event": "user_message", "data": {"session_id": sid, "text": "hola 10"}})
        frame = _recv_until(ws, "error", "generation_completed")
        assert frame["event"] == "error"
        assert frame["data"]["code"] == "rate_limited"
        assert 0 < frame["data"]["retry_after"] <= 60

        # La conexión sigue viva: cuando pasa la ventana, el turno siguiente funciona.
        rate_limit.chat_turns.clear()
        ws.send_json({"event": "user_message", "data": {"session_id": sid, "text": "ya puedo"}})
        assert _recv_until(ws, "error", "generation_completed")["event"] == "generation_completed"
