"""
Stream SSE de eventos de RFPs (Hito 10 · Parte 1). Solo comunicación: no llama a ningún modelo.

El worker publica en un Redis Stream (`rfp_intake/events.py`); aquí cada
conexión lo lee de forma independiente (Redis hace de fan-out) y lo emite como
frames SSE con `id:` (el id de la entrada de Redis), `event:` y `data:` JSON.
Con `Last-Event-ID` se reanuda desde donde se cortó (replay de las últimas
entradas retenidas); sin él, solo se reciben eventos posteriores a la conexión.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol

from rfp import pipeline_bridge

KEEPALIVE_INTERVAL_MS = 15_000
RETRY_MS = 3_000
READ_BATCH = 100
_REDIS_ID = re.compile(r"^\d+-\d+$")

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # evita que nginx buferice el stream
}


@dataclass(frozen=True)
class StreamEntry:
    id: str
    event: str
    data: str


class EventReader(Protocol):
    async def last_id(self) -> str: ...
    async def read(self, after_id: str, block_ms: int) -> list[StreamEntry]: ...
    async def aclose(self) -> None: ...


class RedisEventReader:
    """Lector del Redis Stream con `redis.asyncio` (una conexión por cliente SSE)."""

    def __init__(self, url: str) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(url, decode_responses=True)

    async def last_id(self) -> str:
        latest = await self._redis.xrevrange(pipeline_bridge.EVENTS_STREAM_KEY, count=1)
        return latest[0][0] if latest else "0-0"

    async def read(self, after_id: str, block_ms: int) -> list[StreamEntry]:
        response: Any = await self._redis.xread(
            {pipeline_bridge.EVENTS_STREAM_KEY: after_id}, count=READ_BATCH, block=block_ms
        )
        entries: list[StreamEntry] = []
        for _stream, items in response or []:
            for entry_id, fields in items:
                entries.append(StreamEntry(entry_id, fields.get("event", ""), fields.get("data", "")))
        return entries

    async def aclose(self) -> None:
        await self._redis.aclose()


def make_event_reader() -> EventReader | None:
    """Dependencia de FastAPI; los tests la sustituyen por un lector en memoria."""
    url = os.environ.get("REDIS_URL")
    return RedisEventReader(url) if url else None


def parse_last_event_id(value: str | None) -> str | None:
    """Solo se acepta un id válido de Redis Stream; cualquier otro valor se ignora."""
    value = (value or "").strip()
    return value if _REDIS_ID.match(value) else None


def format_frame(entry: StreamEntry) -> str:
    data = entry.data.replace("\r", "").replace("\n", "")  # JSON de una sola línea
    return f"id: {entry.id}\nevent: {entry.event}\ndata: {data}\n\n"


async def stream_events(
    reader: EventReader, last_event_id: str | None = None, block_ms: int = KEEPALIVE_INTERVAL_MS
) -> AsyncIterator[str]:
    """Frames SSE. Termina si Redis falla: el cliente reconecta con `Last-Event-ID`."""
    try:
        cursor = last_event_id or await reader.last_id()
        yield f"retry: {RETRY_MS}\n\n"
        while True:
            entries = await reader.read(cursor, block_ms)
            if not entries:
                yield ": keepalive\n\n"
                continue
            for entry in entries:
                cursor = entry.id
                yield format_frame(entry)
    except Exception:  # noqa: BLE001 - cortar el stream es la señal de reconexión
        return
    finally:
        await reader.aclose()


__all__ = [
    "EventReader",
    "KEEPALIVE_INTERVAL_MS",
    "SSE_HEADERS",
    "StreamEntry",
    "format_frame",
    "make_event_reader",
    "parse_last_event_id",
    "stream_events",
]
