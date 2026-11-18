"""
Chat por WebSocket con el agente de soporte (Hito 10 · Parte 2). Se ejecutan con:

    uv run --project services/api pytest tests/pipelines/test_chat_ws.py -v

Sin LLM, Qdrant ni base de datos: el agente se sustituye por `FakeAgent`, que
emite tokens como lo hace `agent.runner.stream_agent` (`on_stream`) y respeta
el `abort` igual que `agent.streaming.StreamingLLM`. Una parte final prueba el
camino real (`stream_agent` + grafo LangGraph + `StreamingLLM`) con un cliente
OpenAI falso.

Cubre: autenticación (query y primer frame, 4401/4403/4404), contrato de
eventos, abort medible a mitad de respuesta, turno nuevo con `new_input`,
reconexión con rehidratación, varios suscriptores con una sola llamada al
agente, guard que reemplaza el texto y el extractor del campo `respuesta`.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
API_DIR = str(ROOT / "services" / "api")
if API_DIR not in sys.path:
    sys.path.append(API_DIR)
os.environ.setdefault("SECRET_KEY", "test-secret")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from agent.streaming import (  # noqa: E402
    GenerationAborted,
    RespuestaFieldExtractor,
    StreamingLLM,
    TurnControl,
    _TextEmitter,
)
from chat import router as chat_router  # noqa: E402
from chat.hub import ChatHub  # noqa: E402
from chat.runner import ChatService, compose_after_interrupt  # noqa: E402
from chat.store import InMemoryChatStore, SqliteChatStore  # noqa: E402
from models import Role, User  # noqa: E402
from security import create_access_token, get_current_user  # noqa: E402

CLIENT = User(id="u-client", email="cliente@retailmax.com", hashed_password="x",
              role=Role.USER, client_company="RetailMax Corp")
OTHER = User(id="u-other", email="otro@nubesoft.com", hashed_password="x",
             role=Role.USER, client_company="NubeSoft")
STAFF = User(id="u-staff", email="sup@nexova-solutions.com", hashed_password="x", role=Role.ADMIN)
TOKENS = {"t-client": CLIENT, "t-other": OTHER, "t-staff": STAFF}


# --- Agente falso -------------------------------------------------------------------


class Script:
    """Lo que hace una llamada al agente: tokens, respuesta final y si se queda esperando."""

    def __init__(self, tokens: list[str], answer: str | None = None, *, hold_after: int | None = None,
                 error: str | None = None) -> None:
        self.tokens = tokens
        self.answer = answer if answer is not None else "".join(tokens)
        self.hold_after = hold_after  # tras emitir N tokens espera `release` (o el abort)
        self.error = error
        self.release = threading.Event()
        self.emitted = 0
        self.aborted = False
        self.stream_closed = False


class FakeAgent:
    def __init__(self, *scripts: Script) -> None:
        self.scripts = list(scripts)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, question: str, *, on_stream: Callable[[dict[str, Any]], None],
                 abort: threading.Event, **kwargs: Any) -> Any:
        script = self.scripts[len(self.calls)]
        self.calls.append({"question": question, **kwargs})
        try:
            for i, token in enumerate(script.tokens):
                if script.hold_after is not None and i == script.hold_after:
                    while not script.release.is_set():
                        if abort.is_set():
                            raise GenerationAborted()
                        time.sleep(0.005)
                if abort.is_set():
                    raise GenerationAborted()
                on_stream({"token": token})
                script.emitted += 1
        except GenerationAborted:
            script.aborted = True
            raise
        finally:
            script.stream_closed = True
        return SimpleNamespace(answer=None if script.error else script.answer, error=script.error,
                               run_id=kwargs.get("run_id"))


_open_clients: list[TestClient] = []


def make_client(agent: FakeAgent, store: Any | None = None) -> tuple[TestClient, ChatService]:
    """Un solo event loop por test (como el servidor real): sin `with`, TestClient abriría un
    loop nuevo por conexión y al cerrarla esperaría al hilo del agente."""
    service = ChatService(store or InMemoryChatStore(), ChatHub(), agent_fn=agent)
    app = FastAPI()
    app.include_router(chat_router.router)
    app.dependency_overrides[chat_router.get_chat_service] = lambda: service
    client = TestClient(app)
    client.__enter__()
    _open_clients.append(client)
    return client, service


@pytest.fixture(autouse=True)
def fake_tokens(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(chat_router, "user_from_token", lambda token: TOKENS.get(token))
    yield
    while _open_clients:
        _open_clients.pop().__exit__(None, None, None)


def new_session(service: ChatService, user: User = CLIENT) -> str:
    return service.create_session(user.id, user.client_company).session_id


def recv_until(ws: Any, event: str, limit: int = 200) -> list[dict[str, Any]]:
    frames = []
    for _ in range(limit):
        frame = ws.receive_json()
        frames.append(frame)
        if frame["event"] == event:
            return frames
    raise AssertionError(f"No llegó {event}: {frames}")


def send_user(ws: Any, session_id: str, text: str) -> None:
    ws.send_json({"event": "user_message", "data": {"session_id": session_id, "text": text}})


def close_code(client: TestClient, url: str, first_frame: dict[str, Any] | None = None) -> int:
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(url) as ws:
            if first_frame is not None:
                ws.send_json(first_frame)
            ws.receive_json()
    return exc.value.code


# --- Autenticación y vínculo con la sesión ----------------------------------------


def test_rejects_invalid_or_missing_credentials_before_any_chat_event() -> None:
    client, service = make_client(FakeAgent())
    sid = new_session(service)
    assert close_code(client, f"/chat/ws/{sid}?token=no-valido") == 4401
    assert close_code(client, f"/chat/ws/{sid}", {"event": "auth", "data": {"token": "no-valido"}}) == 4401
    assert close_code(client, f"/chat/ws/{sid}", {"event": "user_message",
                                                  "data": {"session_id": sid, "text": "hola"}}) == 4401


def test_accepts_token_in_query_or_first_auth_frame() -> None:
    client, service = make_client(FakeAgent())
    sid = new_session(service)
    with client.websocket_connect(f"/chat/ws/{sid}?token=t-client") as ws:
        assert ws.receive_json()["event"] == "session_snapshot"
    with client.websocket_connect(f"/chat/ws/{sid}") as ws:
        ws.send_json({"event": "auth", "data": {"token": "t-client"}})
        snapshot = ws.receive_json()
        assert snapshot == {"event": "session_snapshot",
                            "data": {"session_id": sid, "status": "active", "messages": []}}


def test_real_jwt_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()  # sin el atajo de tokens: decode_access_token real
    import routes.users

    monkeypatch.setattr(routes.users, "get_user_by_id", lambda uid: CLIENT if uid == CLIENT.id else None)
    client, service = make_client(FakeAgent())
    sid = new_session(service)
    with client.websocket_connect(f"/chat/ws/{sid}?token={create_access_token(CLIENT.id)}") as ws:
        assert ws.receive_json()["event"] == "session_snapshot"
    assert close_code(client, f"/chat/ws/{sid}?token={create_access_token('desconocido')}") == 4401


def test_socket_is_bound_to_an_existing_session_of_the_user() -> None:
    client, service = make_client(FakeAgent())
    sid = new_session(service)
    assert close_code(client, "/chat/ws/no-existe?token=t-client") == 4404
    assert close_code(client, f"/chat/ws/{sid}?token=t-other") == 4403


def test_create_session_uses_briefing_fields() -> None:
    client, service = make_client(FakeAgent())
    client.app.dependency_overrides[get_current_user] = lambda: CLIENT
    body = client.post("/chat/sessions").json()
    assert set(body) == {"session_id", "agent_id", "user_id", "client_id", "status", "created_at"}
    assert body["agent_id"] == "first_line_support"
    assert body["user_id"] == CLIENT.id and body["client_id"] == "RetailMax Corp"
    assert body["status"] == "active"
    assert service.store.get_session(body["session_id"]) is not None


# --- Contrato de eventos --------------------------------------------------------------


def test_contract_user_message_tokens_and_completed() -> None:
    agent = FakeAgent(Script(["Claro", ", tu ", "factura ", "vence el 30."]))
    client, service = make_client(agent)
    sid = new_session(service)
    with client.websocket_connect(f"/chat/ws/{sid}?token=t-client") as ws:
        ws.receive_json()
        send_user(ws, sid, "¿Cuándo vence mi factura?")
        frames = recv_until(ws, "generation_completed")

    assert [f["event"] for f in frames] == ["user_message"] + ["token_chunk"] * 4 + ["generation_completed"]
    assert frames[0]["data"]["text"] == "¿Cuándo vence mi factura?"
    chunks = [f["data"] for f in frames[1:5]]
    message_id = chunks[0]["message_id"]
    assert [c["sequence"] for c in chunks] == [1, 2, 3, 4]
    assert all(c["session_id"] == sid and c["message_id"] == message_id for c in chunks)
    assert "".join(c["token"] for c in chunks) == "Claro, tu factura vence el 30."
    assert frames[-1] == {"event": "generation_completed", "data": {"session_id": sid, "message_id": message_id}}
    # El agente recibe la sesión como conversation_id (memoria) y el tenant de quien chatea.
    assert agent.calls[0]["conversation_id"] == sid
    assert agent.calls[0]["tenant"].client_company == "RetailMax Corp"
    stored = service.store.list_messages(sid)
    assert [(m.role, m.status) for m in stored] == [("user", "completed"), ("assistant", "completed")]
    assert stored[1].content == "Claro, tu factura vence el 30."


def test_invalid_frames_get_an_error_and_do_not_call_the_agent() -> None:
    agent = FakeAgent()
    client, service = make_client(agent)
    sid = new_session(service)
    with client.websocket_connect(f"/chat/ws/{sid}?token=t-client") as ws:
        ws.receive_json()
        send_user(ws, sid, "   ")
        assert ws.receive_json()["data"]["code"] == "invalid_frame"
        send_user(ws, sid, "x" * 1001)
        assert ws.receive_json()["data"]["code"] == "invalid_frame"
        ws.send_json({"event": "token_chunk", "data": {"session_id": sid, "token": "x", "sequence": 1}})
        assert ws.receive_json()["data"]["code"] == "invalid_frame"
        send_user(ws, "otra-sesion", "hola")
        assert ws.receive_json()["data"]["code"] == "session_mismatch"
    assert agent.calls == []


def test_answer_without_streaming_is_sent_as_tokens() -> None:
    # Guard de entrada, small talk o fallback: la respuesta no pasa por el LLM en streaming.
    agent = FakeAgent(Script([], answer="Solo puedo ayudarte con soporte de Nexova."))
    client, service = make_client(agent)
    sid = new_session(service)
    with client.websocket_connect(f"/chat/ws/{sid}?token=t-client") as ws:
        ws.receive_json()
        send_user(ws, sid, "cuéntame un chiste")
        frames = recv_until(ws, "generation_completed")
    tokens = [f["data"]["token"] for f in frames if f["event"] == "token_chunk"]
    assert len(tokens) > 1 and "".join(tokens) == "Solo puedo ayudarte con soporte de Nexova."
    assert "content" not in frames[-1]["data"]


def test_output_guard_replacement_is_sent_in_completed() -> None:
    agent = FakeAgent(Script(["El ticket de NubeSoft ", "está abierto."],
                             answer="No puedo compartir información de otra empresa cliente."))
    client, service = make_client(agent)
    sid = new_session(service)
    with client.websocket_connect(f"/chat/ws/{sid}?token=t-client") as ws:
        ws.receive_json()
        send_user(ws, sid, "estado del ticket de NubeSoft")
        frames = recv_until(ws, "generation_completed")
    assert frames[-1]["data"]["content"] == "No puedo compartir información de otra empresa cliente."
    assert service.store.list_messages(sid)[1].content == "No puedo compartir información de otra empresa cliente."


def test_agent_failure_reports_error_and_keeps_the_thread_usable() -> None:
    agent = FakeAgent(Script([], error="agent_unavailable"), Script(["Ya estoy."]))
    client, service = make_client(agent)
    sid = new_session(service)
    with client.websocket_connect(f"/chat/ws/{sid}?token=t-client") as ws:
        ws.receive_json()
        send_user(ws, sid, "hola")
        frames = recv_until(ws, "generation_completed")
        assert frames[1]["event"] == "error" and frames[1]["data"]["code"] == "agent_unavailable"
        send_user(ws, sid, "¿sigues?")
        assert recv_until(ws, "generation_completed")[-2]["data"]["token"] == "Ya estoy."


# --- Interrupción -------------------------------------------------------------------


def test_interrupt_aborts_generation_keeps_partial_and_starts_new_turn() -> None:
    first = Script(["Para cambiar ", "la dirección de ", "envío ", "debes ", "entrar en…"], hold_after=2)
    second = Script(["Sobre la factura: ", "la encuentras en Facturación."])
    agent = FakeAgent(first, second)
    client, service = make_client(agent)
    sid = new_session(service)
    with client.websocket_connect(f"/chat/ws/{sid}?token=t-client") as ws:
        ws.receive_json()
        send_user(ws, sid, "¿Cómo cambio la dirección de envío?")
        frames = recv_until(ws, "token_chunk")
        frames += [ws.receive_json()]  # segundo token; el agente queda generando
        old_id = frames[-1]["data"]["message_id"]
        ws.send_json({"event": "interrupt_requested",
                      "data": {"session_id": sid, "new_input": "en realidad mi pregunta era sobre la factura"}})
        after = recv_until(ws, "generation_completed")

    events = [f["event"] for f in after]
    assert events[0] == "generation_interrupted"
    assert after[0]["data"] == {"session_id": sid, "message_id": old_id, "status": "interrupted"}
    assert events[1] == "user_message"
    assert after[1]["data"]["text"] == "en realidad mi pregunta era sobre la factura"
    # Abort medible: ningún token más de la generación original, y su stream se cortó.
    assert not any(f["event"] == "token_chunk" and f["data"]["message_id"] == old_id for f in after)
    assert first.aborted and first.stream_closed and first.emitted == 2
    # Turno nuevo: otro message_id, sequence desde 1, refleja new_input.
    new_chunks = [f["data"] for f in after if f["event"] == "token_chunk"]
    assert {c["message_id"] for c in new_chunks} != {old_id}
    assert [c["sequence"] for c in new_chunks] == [1, 2]
    question = agent.calls[1]["question"]
    assert "en realidad mi pregunta era sobre la factura" in question
    assert "¿Cómo cambio la dirección de envío?" in question
    # El parcial se conserva marcado como interrumpido; nada se sobrescribe.
    stored = service.store.list_messages(sid)
    assert [(m.role, m.status) for m in stored] == [
        ("user", "completed"), ("assistant", "interrupted"), ("user", "completed"), ("assistant", "completed"),
    ]
    assert stored[1].message_id == old_id and stored[1].content == "Para cambiar la dirección de "
    assert stored[3].content == "Sobre la factura: la encuentras en Facturación."
    assert service.store.get_session(sid).status == "active"


def test_user_message_while_generating_also_interrupts() -> None:
    first = Script(["Uno ", "dos ", "tres"], hold_after=1)
    agent = FakeAgent(first, Script(["Vale."]))
    client, service = make_client(agent)
    sid = new_session(service)
    with client.websocket_connect(f"/chat/ws/{sid}?token=t-client") as ws:
        ws.receive_json()
        send_user(ws, sid, "cuenta hasta tres")
        recv_until(ws, "token_chunk")
        send_user(ws, sid, "para")
        events = [f["event"] for f in recv_until(ws, "generation_completed")]
    assert events[:2] == ["generation_interrupted", "user_message"]
    assert first.aborted


def test_compose_after_interrupt_respects_limit() -> None:
    text = compose_after_interrupt("p" * 5000, "corrección")
    assert len(text) <= 1000 and text.endswith("Corrección del usuario: corrección")


# --- Reconexión y varios suscriptores ------------------------------------------------


def test_reconnect_with_same_session_restores_thread_and_resumes_stream(tmp_path: Path) -> None:
    store = SqliteChatStore(tmp_path / "chat.sqlite")
    done = Script(["Tu pedido ", "sale hoy."])
    live = Script(["Revisando ", "tu factura ", "de octubre: ", "está pagada."], hold_after=2)
    client, service = make_client(FakeAgent(done, live), store)
    sid = new_session(service)
    with client.websocket_connect(f"/chat/ws/{sid}?token=t-client") as ws:
        ws.receive_json()
        send_user(ws, sid, "¿Cuándo sale mi pedido?")
        recv_until(ws, "generation_completed")
        send_user(ws, sid, "¿Y mi factura?")
        recv_until(ws, "token_chunk")
        ws.receive_json()  # 2 tokens mostrados; se cae la conexión a mitad de respuesta

    with client.websocket_connect(f"/chat/ws/{sid}?token=t-client") as ws:
        snapshot = ws.receive_json()
        assert snapshot["event"] == "session_snapshot"
        messages = snapshot["data"]["messages"]
        assert [(m["role"], m["status"], m["content"]) for m in messages] == [
            ("user", "completed", "¿Cuándo sale mi pedido?"),
            ("assistant", "completed", "Tu pedido sale hoy."),
            ("user", "completed", "¿Y mi factura?"),
            ("assistant", "streaming", "Revisando tu factura "),
        ]
        assert messages[3]["sequence"] == 2
        live.release.set()
        rest = recv_until(ws, "generation_completed")
    assert [f["data"]["sequence"] for f in rest if f["event"] == "token_chunk"] == [3, 4]

    # Tras un reinicio del servidor el hilo sigue en SQLite.
    restored = SqliteChatStore(tmp_path / "chat.sqlite").list_messages(sid)
    assert restored[-1].content == "Revisando tu factura de octubre: está pagada."


def test_restart_marks_unfinished_message_as_interrupted(tmp_path: Path) -> None:
    store = SqliteChatStore(tmp_path / "chat.sqlite")
    live = Script(["a ", "b"], hold_after=1)
    client, service = make_client(FakeAgent(live), store)
    sid = new_session(service)
    with client.websocket_connect(f"/chat/ws/{sid}?token=t-client") as ws:
        ws.receive_json()
        send_user(ws, sid, "hola")
        recv_until(ws, "token_chunk")
    # "Reinicio" con el mensaje aún a medias: el store nuevo lo deja interrumpido, con su fila.
    restarted = SqliteChatStore(tmp_path / "chat.sqlite").list_messages(sid)
    assert [(m.role, m.status) for m in restarted] == [("user", "completed"), ("assistant", "interrupted")]
    live.release.set()


def test_supervisor_sees_same_events_without_a_second_agent_call() -> None:
    agent = FakeAgent(Script(["Hola, ", "te ayudo ", "con eso."], hold_after=1))
    client, service = make_client(agent)
    sid = new_session(service)
    with client.websocket_connect(f"/chat/ws/{sid}?token=t-client") as owner, \
            client.websocket_connect(f"/chat/ws/{sid}?token=t-staff") as supervisor:
        owner.receive_json()
        supervisor.receive_json()
        send_user(owner, sid, "necesito ayuda")
        recv_until(owner, "token_chunk")
        agent.scripts[0].release.set()
        owner_frames = recv_until(owner, "generation_completed")
        supervisor_frames = recv_until(supervisor, "generation_completed")
        send_user(supervisor, sid, "intervengo")
        assert supervisor.receive_json()["data"]["code"] == "read_only"
    assert len(agent.calls) == 1
    assert [f for f in supervisor_frames if f["event"] != "user_message"][-3:] == owner_frames[-3:]
    assert [f["event"] for f in supervisor_frames][0] == "user_message"


def test_slow_consumer_is_dropped_without_blocking_others() -> None:
    hub = ChatHub(max_queue=2)
    import asyncio

    async def scenario() -> None:
        slow, fast = hub.subscribe("chat.s"), hub.subscribe("chat.s")
        for i in range(3):
            hub.publish("chat.s", {"event": "token_chunk", "data": {"sequence": i}})
            if not fast.queue.empty():
                await fast.queue.get()
        assert slow.overflowed and not fast.overflowed
        assert hub.subscriber_count("chat.s") == 1

    asyncio.run(scenario())


# --- Streaming real del agente (sin LLM) -----------------------------------------------


def test_respuesta_extractor_handles_split_chunks_and_escapes() -> None:
    raw = '```json\n{"respuesta": "Hola \\"Ana\\",\\nla tarifa es 22 % \\u00e9 \\ud83d\\ude00", ' \
          '"propuesta_memoria": null}\n```'
    extractor = RespuestaFieldExtractor()
    out = "".join(extractor.feed(ch) for ch in raw)
    assert out == 'Hola "Ana",\nla tarifa es 22 % é 😀'
    assert extractor.done
    emitter = _TextEmitter()
    assert emitter.feed("  Texto ") + emitter.feed("plano") == "Texto plano"


class FakeStream:
    def __init__(self, pieces: list[str], on_piece: Callable[[int], None] | None = None) -> None:
        self.pieces = pieces
        self.on_piece = on_piece
        self.closed = False
        self.consumed = 0

    def __iter__(self):
        for i, piece in enumerate(self.pieces):
            if self.on_piece:
                self.on_piece(i)
            self.consumed += 1
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=piece))])

    def close(self) -> None:
        self.closed = True


class FakeOpenAI:
    def __init__(self, *streams: FakeStream) -> None:
        self.streams = list(streams)
        self.kwargs: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs: Any) -> FakeStream:
        self.kwargs.append(kwargs)
        return self.streams[len(self.kwargs) - 1]


def test_streaming_llm_aborts_and_closes_model_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent.streaming as streaming

    emitted: list[dict[str, Any]] = []
    monkeypatch.setattr(streaming, "emit", emitted.append)
    control = TurnControl()
    stream = FakeStream(["Hola", " mundo", " que", " tal"], on_piece=lambda i: control.abort.set() if i == 2 else None)
    llm = StreamingLLM(FakeOpenAI(stream), control)
    with pytest.raises(GenerationAborted):
        llm.chat.completions.create(model="m", messages=[])
    assert stream.closed and stream.consumed == 3
    assert emitted == [{"token": "Hola"}, {"token": " mundo"}]


def test_streaming_llm_returns_completion_shape_and_signals_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent.streaming as streaming

    emitted: list[dict[str, Any]] = []
    monkeypatch.setattr(streaming, "emit", emitted.append)
    client = FakeOpenAI(FakeStream(['{"respuesta": "Ho', 'la"}']), FakeStream(["Hola"]))
    llm = StreamingLLM(client, TurnControl())
    first = llm.chat.completions.create(model="m", messages=[], response_format={"type": "json_object"})
    assert first.choices[0].message.content == '{"respuesta": "Hola"}'
    assert client.kwargs[0]["stream"] is True
    llm.chat.completions.create(model="m", messages=[])
    assert emitted == [{"token": "Ho"}, {"token": "la"}, {"reset": True}, {"token": "Hola"}]


def test_stream_agent_streams_tokens_through_langgraph_custom_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import knowledge.pipeline_bridge as bridge
    from agent.graph import build_graph, sqlite_checkpointer
    from agent.routing import RouteDecision
    from agent.runner import stream_agent
    from agent.streaming import chat_generate
    from agent.tracing import load_trace

    monkeypatch.setenv("AGENT_TRACE_DIR", str(tmp_path / "traces"))
    chunk = {"source_document": "pricing-model", "section": "Headhunting", "text": "Tarifa: 22 %."}
    graph = build_graph(
        retrieve_fn=lambda q: [chunk],
        classify_fn=lambda q: RouteDecision(rag=True, method="rules"),
        generate_fn=chat_generate,
        checkpointer=sqlite_checkpointer(":memory:"),
    )

    def run(stream: FakeStream, abort: threading.Event) -> tuple[list[dict[str, Any]], Any]:
        monkeypatch.setattr(bridge, "get_llm_client", lambda: FakeOpenAI(stream))
        monkeypatch.setattr(bridge, "generation_model", lambda: "modelo-falso")
        seen: list[dict[str, Any]] = []
        result = stream_agent("¿Cuál es la tarifa?", on_stream=seen.append, abort=abort, graph=graph)
        return seen, result

    seen, result = run(FakeStream(["La tarifa ", "es el 22 %."]), threading.Event())
    assert seen == [{"token": "La tarifa "}, {"token": "es el 22 %."}]
    assert result.answer == "La tarifa es el 22 %."
    assert load_trace(result.run_id)["status"] == "completed"

    abort = threading.Event()
    stream = FakeStream(["La ", "tarifa ", "es"], on_piece=lambda i: abort.set() if i == 1 else None)
    with pytest.raises(GenerationAborted):
        run(stream, abort)
    assert stream.closed and stream.consumed == 2
