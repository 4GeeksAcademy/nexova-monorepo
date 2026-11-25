"""
Tests de las notificaciones SSE de RFPs (Hito 10 · Parte 1): publicador,
enganche en el grafo `rfp_intake` y endpoint `GET /rfp/events` con su framing.

Sin Redis real (`fakeredis`), sin base de datos y sin modelos.

    uv run --project services/api pytest tests/pipelines/test_rfp_sse.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import fakeredis
import fakeredis.aioredis
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "services" / "api") not in sys.path:
    sys.path.insert(0, str(ROOT / "services" / "api"))
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from rfp_fakes import (  # noqa: E402
    FORMAL_RESPONSES,
    INFORMAL_PDF,
    INFORMAL_RESPONSES,
    INVALID_PDF,
    INVALID_RESPONSES,
    FakeLLM,
    upload,
)
from rfp_intake import InMemoryRfpStore, run_intake  # noqa: E402
from rfp_intake.events import (  # noqa: E402
    EVENT_TICKET_CREATED,
    STREAM_KEY,
    build_ticket_created_payload,
    publish_ticket_created,
)

# Campos del BRIEFING §3, con los nombres que ya usa el sistema de RFPs.
PAYLOAD_FIELDS = {
    "ticket_id", "rfp_id", "client_name", "client_hq", "services_requested", "status", "created_at",
}


@pytest.fixture(autouse=True)
def raw_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RFP_RAW_DIR", str(tmp_path / "raw" / "rfp"))
    return tmp_path / "raw" / "rfp"


def _payload(ticket_id: str = "RFP-0001") -> dict:
    return {
        "ticket_id": ticket_id, "rfp_id": "rfp_0127", "client_name": "NubeSoft", "client_hq": "Miami",
        "services_requested": ["soporte"], "status": "analizando", "created_at": "2026-07-24T14:32:00Z",
    }


# --------------------------------------------------------------------------- #
# Publicador
# --------------------------------------------------------------------------- #
def test_publish_adds_a_named_event_with_the_briefing_fields():
    redis = fakeredis.FakeRedis(decode_responses=True)
    assert publish_ticket_created(_payload(), client=redis) is True

    [(_entry_id, fields)] = redis.xrange(STREAM_KEY)
    assert fields["event"] == EVENT_TICKET_CREATED == "rfp_ticket_created"
    data = json.loads(fields["data"])
    assert set(data) == PAYLOAD_FIELDS
    assert data["status"] == "analizando"


def test_publish_is_deduplicated_per_ticket_so_celery_retries_do_not_repeat_it():
    redis = fakeredis.FakeRedis(decode_responses=True)
    assert publish_ticket_created(_payload("RFP-A"), client=redis) is True
    assert publish_ticket_created(_payload("RFP-A"), client=redis) is False
    assert publish_ticket_created(_payload("RFP-B"), client=redis) is True
    assert redis.xlen(STREAM_KEY) == 2


def test_publish_never_raises_and_is_a_noop_without_redis(monkeypatch):
    class Broken:
        def set(self, *a, **k):
            raise ConnectionError("redis caído")

    assert publish_ticket_created(_payload(), client=Broken()) is False
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert publish_ticket_created(_payload()) is False


def test_stream_keeps_only_the_latest_entries():
    from rfp_intake import events

    redis = fakeredis.FakeRedis(decode_responses=True)
    for i in range(events.STREAM_MAXLEN + 200):
        publish_ticket_created(_payload(f"RFP-{i}"), client=redis)
    assert redis.xlen(STREAM_KEY) <= events.STREAM_MAXLEN + 100  # recorte aproximado (`~`)


def test_payload_uses_ticket_and_metadata_names_without_an_envelope():
    created = SimpleNamespace(isoformat=lambda: "2026-07-24T14:32:00+00:00")
    payload = build_ticket_created_payload(
        {"created_at": created, "rfp_id": "x"},
        {"client_name": "NubeSoft", "client_hq": "Miami", "services_requested": ["soporte"]},
        "rfp_0127", "RFP-1",
    )
    assert set(payload) == PAYLOAD_FIELDS
    assert payload["rfp_id"] == "rfp_0127"
    assert payload["created_at"] == "2026-07-24T14:32:00+00:00"


# --------------------------------------------------------------------------- #
# Enganche en el grafo: solo RFP válida, una vez, sin romper el análisis
# --------------------------------------------------------------------------- #
def test_valid_rfp_emits_one_event_with_metadata_after_classification():
    store = InMemoryRfpStore()
    ticket_id = upload(store, INFORMAL_PDF)
    published: list[dict] = []

    run_intake(ticket_id, store=store, llm=FakeLLM(INFORMAL_RESPONSES), publisher=published.append)

    [event] = published
    assert set(event) == PAYLOAD_FIELDS
    assert event["ticket_id"] == ticket_id
    assert event["client_name"] == "NubeSoft"
    assert event["client_hq"] == "Miami"
    assert event["services_requested"] == ["Equipo de soporte al cliente 24/7"]
    assert event["status"] == "analizando"
    assert event["created_at"]


def test_discarded_document_emits_nothing():
    store = InMemoryRfpStore()
    ticket_id = upload(store, INVALID_PDF)
    published: list[dict] = []

    run_intake(ticket_id, store=store, llm=FakeLLM(INVALID_RESPONSES), publisher=published.append)

    assert published == []
    assert store.get_ticket(ticket_id)["status"] == "descartado"


def test_a_failing_publisher_does_not_break_the_analysis():
    store = InMemoryRfpStore()
    ticket_id = upload(store, INFORMAL_PDF)

    def broken(_payload: dict) -> None:
        raise ConnectionError("redis caído")

    result = run_intake(ticket_id, store=store, llm=FakeLLM(INFORMAL_RESPONSES), publisher=broken)
    assert result["status"] == "analisis_completo"


def test_the_analysis_makes_no_extra_model_call_for_the_notification():
    store = InMemoryRfpStore()
    ticket_id = upload(store, INFORMAL_PDF)
    llm = FakeLLM(INFORMAL_RESPONSES)
    run_intake(ticket_id, store=store, llm=llm, publisher=lambda _p: None)
    assert set(llm.tasks()) <= set(INFORMAL_RESPONSES)  # solo las tareas del grafo existente


# --------------------------------------------------------------------------- #
# Endpoint SSE
# --------------------------------------------------------------------------- #
class ScriptedReader:
    """Lector finito: entrega las entradas pedidas y luego corta el stream."""

    def __init__(self, entries=(), idle_polls: int = 0) -> None:
        from rfp.events import StreamEntry

        self.entries = [StreamEntry(*e) for e in entries]
        self.idle_polls = idle_polls
        self.cursors: list[str] = []
        self.closed = False

    async def last_id(self) -> str:
        return "5-0"

    async def read(self, after_id: str, block_ms: int):
        self.cursors.append(after_id)
        if self.idle_polls:
            self.idle_polls -= 1
            return []
        if self.entries:
            batch, self.entries = self.entries, []
            return batch
        raise ConnectionError("fin del guion")

    async def aclose(self) -> None:
        self.closed = True


def _app(reader, user=None, override_auth: bool = True):
    from models import Role, User
    from rfp import events as rfp_events, router as rfp_router
    from security import get_current_user

    user = user or User(id="u1", email="ventas@nexova-solutions.com", hashed_password="x", role=Role.MANAGER)  # staff (OWASP C1)
    app = FastAPI()
    app.include_router(rfp_router.router)
    if override_auth:
        app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[rfp_events.make_event_reader] = lambda: reader
    return app


def _frames(text: str) -> list[str]:
    return [f for f in text.split("\n\n") if f]


def test_stream_has_event_stream_headers_named_event_and_json_data():
    data = json.dumps(_payload("RFP-0341"))
    reader = ScriptedReader(entries=[("10-0", EVENT_TICKET_CREATED, data)])
    client = TestClient(_app(reader))

    with client.stream("GET", "/rfp/events") as response:
        body = "".join(response.iter_text())
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"].startswith("no-cache")
        assert response.headers["x-accel-buffering"] == "no"

    frame = next(f for f in _frames(body) if "event:" in f)
    lines = frame.splitlines()
    assert lines[0] == "id: 10-0"
    assert lines[1] == "event: rfp_ticket_created"
    assert lines[2].startswith("data: ")
    assert set(json.loads(lines[2][len("data: "):])) == PAYLOAD_FIELDS
    assert reader.closed is True


def test_stream_sends_keepalive_comment_frames_when_idle():
    reader = ScriptedReader(idle_polls=2)
    with TestClient(_app(reader)).stream("GET", "/rfp/events") as response:
        body = "".join(response.iter_text())
    assert body.count(": keepalive") == 2


def test_last_event_id_resumes_after_the_given_entry_and_bad_ids_are_ignored():
    reader = ScriptedReader()
    with TestClient(_app(reader)).stream("GET", "/rfp/events", headers={"Last-Event-ID": "7-3"}) as r:
        "".join(r.iter_text())
    assert reader.cursors[0] == "7-3"

    reader = ScriptedReader()
    with TestClient(_app(reader)).stream("GET", "/rfp/events", headers={"Last-Event-ID": "no-es-un-id"}) as r:
        "".join(r.iter_text())
    assert reader.cursors[0] == "5-0"  # sin id válido: solo eventos posteriores a la conexión


def test_stream_requires_the_backoffice_jwt():
    client = TestClient(_app(ScriptedReader(), override_auth=False))
    assert client.get("/rfp/events").status_code == 401
    assert client.get("/rfp/events", headers={"Authorization": "Bearer basura"}).status_code == 401


def test_external_client_users_cannot_listen():
    from models import Role, User

    external = User(id="u2", email="cliente@vantex.es", hashed_password="x", role=Role.USER,
                    client_company="Vantex")
    assert TestClient(_app(ScriptedReader(), user=external)).get("/rfp/events").status_code == 403


def test_endpoint_is_unavailable_without_redis():
    assert TestClient(_app(None)).get("/rfp/events").status_code == 503


# --------------------------------------------------------------------------- #
# Lector real de Redis: replay con Last-Event-ID sin duplicados
# --------------------------------------------------------------------------- #
def test_redis_reader_replays_only_what_came_after_last_event_id(monkeypatch):
    from rfp import events as rfp_events

    async def scenario() -> list[str]:
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        ids = [await redis.xadd(STREAM_KEY, {"event": EVENT_TICKET_CREATED, "data": json.dumps(_payload(f"T{i}"))})
               for i in range(3)]
        reader = rfp_events.RedisEventReader.__new__(rfp_events.RedisEventReader)
        reader._redis = redis  # noqa: SLF001 - se inyecta el Redis falso
        seen: list[str] = []
        async for frame in rfp_events.stream_events(reader, last_event_id=ids[0], block_ms=10):
            if frame.startswith("id:"):
                seen.append(json.loads(frame.split("data: ")[1])["ticket_id"])
            if len(seen) == 2:
                break
        return seen

    assert asyncio.run(scenario()) == ["T1", "T2"]  # T0 ya se recibió antes de la caída


def test_redis_reader_socket_timeout_outlasts_the_blocking_read():
    """Regresión: con el `socket_timeout` por defecto (5 s) un `XREAD BLOCK` de 15 s
    moría con TimeoutError a los 5 s y el stream se cerraba sin enviar el keep-alive.
    Se construye el lector real (los demás tests lo crean con `__new__`, sin `__init__`)."""
    from rfp import events as rfp_events

    async def scenario() -> float | None:
        reader = rfp_events.RedisEventReader("redis://localhost:6379/0")  # no conecta hasta el primer comando
        try:
            kwargs = reader._redis.connection_pool.connection_kwargs  # noqa: SLF001
            return kwargs.get("socket_timeout", kwargs.get("orig_socket_timeout"))
        finally:
            await reader.aclose()

    timeout = asyncio.run(scenario())
    assert timeout is None or timeout > rfp_events.KEEPALIVE_INTERVAL_MS / 1000
