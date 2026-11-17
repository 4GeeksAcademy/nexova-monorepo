"""
Pub/sub del chat: un canal por sesión (`chat.<session_id>`).

El productor (el `SessionRunner` que ejecuta el agente) publica cada evento una
sola vez; cada conexión WebSocket suscrita lo recibe por su propia cola. El
productor no conoce a los consumidores ni espera por ellos: `publish` nunca
bloquea. Si un consumidor no vacía su cola (`max_queue`), se le marca como
desbordado y su conexión se cierra para que el cliente reconecte y rehidrate
con un snapshot, en lugar de frenar a los demás.

En memoria y para un solo proceso (BRIEFING §5). Con varios workers de
uvicorn haría falta un backplane (Redis pub/sub) detrás del mismo protocolo.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_MAX_QUEUE = 1024


def channel_for(session_id: str) -> str:
    return f"chat.{session_id}"


@dataclass(eq=False)
class Subscription:
    channel: str
    queue: asyncio.Queue[dict[str, Any]] = field(repr=False)
    overflowed: bool = False


class Hub(Protocol):
    def subscribe(self, channel: str) -> Subscription: ...
    def unsubscribe(self, subscription: Subscription) -> None: ...
    def publish(self, channel: str, event: dict[str, Any]) -> int: ...


class ChatHub:
    def __init__(self, max_queue: int = DEFAULT_MAX_QUEUE) -> None:
        self._max_queue = max_queue
        self._channels: dict[str, set[Subscription]] = {}

    def subscribe(self, channel: str) -> Subscription:
        subscription = Subscription(channel=channel, queue=asyncio.Queue(maxsize=self._max_queue))
        self._channels.setdefault(channel, set()).add(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        subscribers = self._channels.get(subscription.channel)
        if subscribers is None:
            return
        subscribers.discard(subscription)
        if not subscribers:
            del self._channels[subscription.channel]

    def subscriber_count(self, channel: str) -> int:
        return len(self._channels.get(channel, ()))

    def publish(self, channel: str, event: dict[str, Any]) -> int:
        """Entrega `event` a cada suscriptor del canal; devuelve a cuántos llegó."""
        delivered = 0
        for subscription in list(self._channels.get(channel, ())):
            try:
                subscription.queue.put_nowait(event)
                delivered += 1
            except asyncio.QueueFull:
                subscription.overflowed = True
                self.unsubscribe(subscription)
        return delivered
