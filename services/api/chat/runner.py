"""
Productor del chat: ejecuta los turnos del agente y publica sus eventos en el hub.

Hay un `SessionRunner` por sesión y, como mucho, un turno en curso por sesión.
Las conexiones WebSocket nunca llaman al agente: solo envían la entrada del
usuario al runner y reciben los eventos del canal `chat.<session_id>`. Por eso
un supervisor que observa la misma sesión ve los mismos tokens sin duplicar la
llamada al modelo.

El agente corre en un hilo (`asyncio.to_thread`); sus fragmentos se encolan y se
vacían en el event loop (`call_soon_threadsafe` y, al terminar el hilo, una
última pasada), donde se numeran (`sequence`) y se publican. Todo el estado del turno se toca solo desde el event loop.

Interrupción: se levanta el `abort` del turno; desde ese instante el runner
descarta cualquier fragmento de esa generación y el cliente LLM corta el stream
del modelo (`agent.streaming.StreamingLLM`). Cuando el hilo termina, el parcial
se guarda como `interrupted` (con el texto mostrado), se publica
`generation_interrupted` y después arranca un turno nuevo con `new_input`.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from chat import schemas
from chat.hub import Hub, channel_for
from chat.schemas import ChatMessage, ChatMessageOut, ChatSession
from chat.store import ChatStore, utcnow

logger = logging.getLogger(__name__)

AgentStreamFn = Callable[..., Any]

FAILURE_TEXT = (
    "No pude responder ahora mismo: el asistente de soporte no está disponible. "
    "Inténtalo de nuevo en unos minutos."
)
_PREVIOUS_PREFIX = "Pregunta anterior (interrumpida): "
_CORRECTION_PREFIX = "\nCorrección del usuario: "
_WORDS = re.compile(r"\s*\S+\s*|\s+")


def new_message_id() -> str:
    return f"msg_{uuid.uuid4().hex[:12]}"


def compose_after_interrupt(previous: str, correction: str, limit: int = schemas.MAX_TEXT_LENGTH) -> str:
    """Entrada del turno que sigue a una interrupción.

    El agente no lee historial: sin la pregunta anterior, una corrección como
    "en realidad era sobre la factura" llegaría sin contexto. Se recorta la
    pregunta anterior para no pasar de `limit` caracteres."""
    budget = limit - len(_PREVIOUS_PREFIX) - len(_CORRECTION_PREFIX) - len(correction)
    if budget < 20:
        return correction[:limit]
    if len(previous) > budget:
        previous = previous[: budget - 1].rstrip() + "…"
    return f"{_PREVIOUS_PREFIX}{previous}{_CORRECTION_PREFIX}{correction}"


def split_tokens(text: str) -> list[str]:
    """Trocea una respuesta que no se generó en streaming (guard, small talk, fallback)."""
    return _WORDS.findall(text) or [text]


def default_agent_fn(question: str, **kwargs: Any) -> Any:
    from agent.runner import stream_agent

    return stream_agent(question, **kwargs)


def is_generation_aborted(exc: BaseException) -> bool:
    from agent.streaming import GenerationAborted

    return isinstance(exc, GenerationAborted)


@dataclass(eq=False)
class Turn:
    message_id: str
    user_text: str
    question: str
    run_id: str
    abort: threading.Event
    text: str = ""
    sequence: int = 0
    # La generación repitió la llamada al LLM: lo mostrado puede no ser la respuesta final.
    replaced: bool = False
    interrupted: bool = False
    task: asyncio.Task[None] | None = None
    # Fragmentos que llegan del hilo del agente; `deque` admite append/popleft entre hilos.
    pending: deque[dict[str, Any]] = field(default_factory=deque)


class SessionRunner:
    def __init__(self, session_id: str, *, store: ChatStore, hub: Hub, agent_fn: AgentStreamFn) -> None:
        self.session_id = session_id
        self.channel = channel_for(session_id)
        self._store = store
        self._hub = hub
        self._agent_fn = agent_fn
        self._lock = asyncio.Lock()
        self.current: Turn | None = None

    # --- Entradas -----------------------------------------------------------------

    async def submit(self, text: str, *, user: Any, tenant: Any) -> None:
        """Turno del usuario. Si el agente está respondiendo, primero lo interrumpe."""
        async with self._lock:
            previous = await self._abort_current()
            self._start_turn(text, previous_question=previous, user=user, tenant=tenant)

    async def close(self) -> None:
        async with self._lock:
            await self._abort_current()
            self._store.set_session_status(self.session_id, "closed")

    async def wait_idle(self) -> None:
        """Espera a que termine el turno en curso (tests y cierre ordenado)."""
        turn = self.current
        if turn is not None and turn.task is not None:
            await asyncio.shield(turn.task)

    # --- Snapshot -----------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """`session_snapshot` coherente con lo publicado hasta ahora (llamar sin `await` intermedio
        tras suscribirse: así no hay hueco ni duplicado entre el snapshot y la cola)."""
        session = self._store.get_session(self.session_id)
        if session is None:
            raise LookupError(self.session_id)
        current = self.current
        messages: list[ChatMessageOut] = []
        for message in self._store.list_messages(self.session_id):
            out = ChatMessageOut(
                message_id=message.message_id, role=message.role, content=message.content,
                status=message.status, created_at=message.created_at,
            )
            if current is not None and message.message_id == current.message_id:
                out.content, out.sequence = current.text, current.sequence
            messages.append(out)
        return schemas.session_snapshot(session, messages)

    # --- Turnos -------------------------------------------------------------------

    async def _abort_current(self) -> str | None:
        """Aborta el turno en curso y espera a que su hilo termine. Devuelve la pregunta
        interrumpida (o None si no había turno o terminó antes de abortarse)."""
        turn = self.current
        if turn is None or turn.task is None:
            return None
        turn.abort.set()
        await asyncio.shield(turn.task)
        return turn.user_text if turn.interrupted else None

    def _publish(self, event: dict[str, Any]) -> None:
        self._hub.publish(self.channel, event)

    def _start_turn(self, text: str, *, previous_question: str | None, user: Any, tenant: Any) -> None:
        now = utcnow()
        user_id = new_message_id()
        self._store.add_message(ChatMessage(
            message_id=user_id, session_id=self.session_id, role="user",
            content=text, status="completed", created_at=now,
        ))
        self._store.set_session_status(self.session_id, "active")
        self._publish(schemas.user_message(self.session_id, user_id, text))

        question = compose_after_interrupt(previous_question, text) if previous_question else text
        turn = Turn(message_id=new_message_id(), user_text=text, question=question,
                    run_id=str(uuid.uuid4()), abort=threading.Event())
        self._store.add_message(ChatMessage(
            message_id=turn.message_id, session_id=self.session_id, role="assistant",
            content="", status="streaming", created_at=utcnow(), run_id=turn.run_id,
        ))
        self.current = turn
        turn.task = asyncio.create_task(self._run(turn, user=user, tenant=tenant))

    async def _run(self, turn: Turn, *, user: Any, tenant: Any) -> None:
        loop = asyncio.get_running_loop()

        def on_stream(chunk: dict[str, Any]) -> None:  # hilo del agente
            turn.pending.append(chunk)
            loop.call_soon_threadsafe(self._drain, turn)

        try:
            result = await asyncio.to_thread(
                self._agent_fn, turn.question,
                on_stream=on_stream, abort=turn.abort, run_id=turn.run_id,
                conversation_id=self.session_id, user_id=getattr(user, "id", None), tenant=tenant,
            )
        except Exception as exc:
            self._drain(turn)
            if is_generation_aborted(exc):
                self._finish_interrupted(turn)
            else:
                logger.exception("Fallo en el turno %s de la sesión %s", turn.run_id, self.session_id)
                self._finish_failed(turn, "agent_unavailable")
            return
        # La vuelta del hilo puede adelantarse a los callbacks pendientes: primero se publican
        # todos los fragmentos recibidos y después se cierra el turno.
        self._drain(turn)
        answer = getattr(result, "answer", None)
        if getattr(result, "error", None) or not answer:
            self._finish_failed(turn, getattr(result, "error", None) or "agent_unavailable")
            return
        self._finish_completed(turn, answer)

    def _drain(self, turn: Turn) -> None:
        while turn.pending:
            self._on_chunk(turn, turn.pending.popleft())

    def _on_chunk(self, turn: Turn, chunk: dict[str, Any]) -> None:
        # Tras el abort no sale ningún token más de esta generación.
        if turn is not self.current or turn.abort.is_set():
            return
        if chunk.get("reset"):
            if turn.sequence:
                turn.replaced = True
            return
        token = chunk.get("token")
        if not isinstance(token, str) or not token or turn.replaced:
            return
        self._emit_token(turn, token)

    def _emit_token(self, turn: Turn, token: str) -> None:
        turn.sequence += 1
        turn.text += token
        self._publish(schemas.token_chunk(self.session_id, turn.message_id, token, turn.sequence))

    def _finish_completed(self, turn: Turn, answer: str) -> None:
        content: str | None = None
        if turn.sequence == 0:
            for token in split_tokens(answer):
                self._emit_token(turn, token)
        elif turn.replaced or turn.text.strip() != answer.strip():
            content = answer  # el guard de salida (u otro paso) cambió lo que se mostró
        self._store.update_message(turn.message_id, content=answer, status="completed", run_id=turn.run_id)
        self.current = None
        self._publish(schemas.generation_completed(self.session_id, turn.message_id, content))

    def _finish_interrupted(self, turn: Turn) -> None:
        turn.interrupted = True
        self._store.update_message(turn.message_id, content=turn.text, status="interrupted")
        self._store.set_session_status(self.session_id, "interrupted")
        self.current = None
        self._publish(schemas.generation_interrupted(self.session_id, turn.message_id))

    def _finish_failed(self, turn: Turn, code: str) -> None:
        self._publish(schemas.error(code, "El asistente no pudo responder.", turn.message_id))
        if turn.text and not turn.replaced:
            # Lo mostrado se conserva; el mensaje queda interrumpido, no se borra.
            self._finish_interrupted(turn)
            turn.interrupted = False  # no hay corrección que encadenar
            return
        turn.text, turn.sequence, turn.replaced = "", 0, False
        self._finish_completed(turn, FAILURE_TEXT)


class ChatService:
    """Sesiones, runners por sesión y el hub compartido."""

    def __init__(self, store: ChatStore, hub: Hub, agent_fn: AgentStreamFn | None = None) -> None:
        self.store = store
        self.hub = hub
        self._agent_fn = agent_fn or default_agent_fn
        self._runners: dict[str, SessionRunner] = {}

    def create_session(self, user_id: str, client_id: str | None) -> ChatSession:
        session = ChatSession(session_id=str(uuid.uuid4()), user_id=user_id, client_id=client_id,
                              status="active", created_at=utcnow())
        self.store.create_session(session)
        return session

    def runner(self, session_id: str) -> SessionRunner:
        runner = self._runners.get(session_id)
        if runner is None:
            runner = SessionRunner(session_id, store=self.store, hub=self.hub, agent_fn=self._agent_fn)
            self._runners[session_id] = runner
        return runner
