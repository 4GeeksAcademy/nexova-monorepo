"""
Eventos en tiempo real de RFPs (SSE, Hito 10 · Parte 1).

El worker de Celery (que clasifica el documento) y la API (que sirve el stream
SSE) son procesos distintos, así que el bus entre ambos es un Redis Stream:

- `publish_ticket_created` lo llama el nodo `extract_metadata` del grafo cuando
  el documento ya se clasificó como RFP válida y existen los metadatos.
- `services/api/rfp/events.py` lee el stream y lo emite como SSE. El id de cada
  entrada de Redis es el `id:` del frame SSE, así que `Last-Event-ID` permite
  reanudar sin perder eventos (replay de las últimas `STREAM_MAXLEN` entradas).

Es una capa de comunicación: no llama a ningún modelo. Publicar nunca lanza ni
bloquea el análisis: si Redis no responde, el evento se pierde y el ticket sigue
su curso (la UI sigue pudiendo listarlo).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable

logger = logging.getLogger("rfp.events")

STREAM_KEY = "rfp:events"
STREAM_MAXLEN = 500
DEDUPE_KEY_PREFIX = "rfp:event:created:"
DEDUPE_TTL_SECONDS = 24 * 60 * 60
SOCKET_TIMEOUT_SECONDS = 1.0

EVENT_TICKET_CREATED = "rfp_ticket_created"
# Estado con el que el ticket entra al flujo. El BRIEFING lo llama `analyzing`;
# el sistema de RFPs ya usa `analizando` (ver `TICKET_STATUSES`).
TICKET_CREATED_STATUS = "analizando"

TicketCreatedPublisher = Callable[[dict[str, Any]], None]


def build_ticket_created_payload(
    ticket: dict[str, Any] | None, metadata: dict[str, Any], rfp_id: str | None, ticket_id: str
) -> dict[str, Any]:
    """Solo campos del ticket y sus metadatos, con los mismos nombres que `rfp_ticket`/`rfp_metadata`."""
    ticket = ticket or {}
    created_at = ticket.get("created_at")
    return {
        "ticket_id": ticket_id,
        "rfp_id": rfp_id if rfp_id is not None else ticket.get("rfp_id"),
        "client_name": metadata.get("client_name"),
        "client_hq": metadata.get("client_hq"),
        "services_requested": list(metadata.get("services_requested") or []),
        "status": TICKET_CREATED_STATUS,
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
    }


def _client() -> Any | None:
    url = os.environ.get("REDIS_URL")
    if not url:
        return None
    import redis  # import perezoso: los tests y scripts sin Redis no lo necesitan

    return redis.Redis.from_url(
        url, socket_timeout=SOCKET_TIMEOUT_SECONDS, socket_connect_timeout=SOCKET_TIMEOUT_SECONDS
    )


def publish_ticket_created(payload: dict[str, Any], client: Any | None = None) -> bool:
    """Añade el evento al stream. Devuelve True si se publicó; nunca lanza.

    Dedupe: un reintento de Celery vuelve a ejecutar el nodo, así que solo el
    primer evento de cada `ticket_id` se publica (`SET NX` con TTL).
    """
    try:
        client = client or _client()
        if client is None:
            return False
        ticket_id = payload["ticket_id"]
        if not client.set(f"{DEDUPE_KEY_PREFIX}{ticket_id}", "1", nx=True, ex=DEDUPE_TTL_SECONDS):
            return False
        client.xadd(
            STREAM_KEY,
            {"event": EVENT_TICKET_CREATED, "data": json.dumps(payload, ensure_ascii=False)},
            maxlen=STREAM_MAXLEN,
            approximate=True,
        )
        return True
    except Exception:  # noqa: BLE001 - un fallo de Redis jamás debe tumbar el análisis
        logger.warning("No se pudo publicar %s", EVENT_TICKET_CREATED, exc_info=True)
        return False


__all__ = [
    "DEDUPE_KEY_PREFIX",
    "EVENT_TICKET_CREATED",
    "STREAM_KEY",
    "STREAM_MAXLEN",
    "TICKET_CREATED_STATUS",
    "TicketCreatedPublisher",
    "build_ticket_created_payload",
    "publish_ticket_created",
]
