"""
Streaming de tokens del agente para el chat por WebSocket (`services/api/chat/`).

No cambia la lógica del agente: el grafo del chat (`get_chat_agent_graph`) es el
mismo `build_graph()` de producción (mismos nodos, prompts, memoria, guardrails
y checkpointer); solo se le inyectan funciones de generación que llaman al MISMO
LLM con `stream=True`.

Cómo llegan los tokens al canal: la generación escribe cada fragmento con
`get_stream_writer()` de LangGraph y `agent.runner.stream_agent` recorre el
grafo con `stream_mode=["custom", "values"]`. Los nodos llaman al SDK `openai`
directamente (no a un modelo de LangChain), por eso el modo `messages` no vería
nada y el que corresponde es `custom`.

Abort: cada turno lleva un `threading.Event` (`TurnControl.abort`). El cliente
LLM con streaming lo revisa entre fragmentos; al levantarse cierra el stream
HTTP del modelo y lanza `GenerationAborted`. Es cancelación real de la
generación, no `interrupt()` de LangGraph (no hay pausa de grafo que reanudar).

Generación estructurada (con memoria): el modelo devuelve JSON
`{"respuesta": "...", "propuesta_memoria": ...}`. `RespuestaFieldExtractor`
emite solo el texto del campo `respuesta` a medida que llega; al terminar,
`generate_with_memory` lee el JSON completo como siempre.

Fragmentos que emite el writer: `{"token": "..."}` y `{"reset": True}` cuando
la misma generación repite la llamada al LLM (reintento de formato o respaldo):
lo ya mostrado puede no coincidir con la respuesta final, y el canal enviará el
texto definitivo al terminar.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
from types import SimpleNamespace
from typing import Any

from langgraph.graph.state import CompiledStateGraph


class GenerationAborted(Exception):
    """La generación en curso se abortó a petición del usuario (interrupción)."""


@dataclass
class TurnControl:
    """Control de un turno de chat: señal de abort y llamadas al LLM hechas."""

    abort: threading.Event = field(default_factory=threading.Event)
    llm_calls: int = 0


_current_turn: ContextVar[TurnControl | None] = ContextVar("chat_turn_control", default=None)


@contextmanager
def turn_scope(control: TurnControl) -> Iterator[TurnControl]:
    """Activa `control` para las generaciones que ocurran dentro del bloque."""
    token = _current_turn.set(control)
    try:
        yield control
    finally:
        _current_turn.reset(token)


def current_turn() -> TurnControl | None:
    return _current_turn.get()


def emit(chunk: dict[str, Any]) -> None:
    """Escribe en el stream `custom` de LangGraph; fuera de un stream no hace nada."""
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
    except Exception:  # fuera de un nodo o sin modo `custom`
        return
    writer(chunk)


# --- Extracción incremental del campo "respuesta" ---------------------------------

_SIMPLE_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


class RespuestaFieldExtractor:
    """Devuelve, fragmento a fragmento, el contenido decodificado del string `"respuesta"`.

    Tolera texto antes del objeto (vallas ```json, espacios) y fragmentos partidos
    en cualquier punto (en mitad de la clave, de un escape o de un `\\uXXXX`)."""

    _KEY = '"respuesta"'

    def __init__(self) -> None:
        self._state = "key"
        self._pending = ""  # texto aún no consumido en las fases de búsqueda
        self._escape: str | None = None  # None, "\\" o "\\u…" en curso
        self._high_surrogate: int | None = None

    @property
    def done(self) -> bool:
        return self._state == "done"

    def feed(self, text: str) -> str:
        out: list[str] = []
        data = self._pending + text
        self._pending = ""
        i = 0
        while i < len(data):
            if self._state == "key":
                idx = data.find(self._KEY, i)
                if idx < 0:
                    # Conserva una cola por si la clave llega partida.
                    self._pending = data[max(i, len(data) - len(self._KEY) + 1):]
                    return ""
                i = idx + len(self._KEY)
                self._state = "colon"
            elif self._state == "colon":
                ch = data[i]
                i += 1
                if ch == ":":
                    self._state = "quote"
                elif not ch.isspace():
                    self._state = "key"  # era la palabra dentro de otro valor: seguir buscando
            elif self._state == "quote":
                ch = data[i]
                i += 1
                if ch == '"':
                    self._state = "string"
                elif not ch.isspace():
                    self._state = "done"  # `respuesta` no es un string: nada que emitir
            elif self._state == "string":
                ch = data[i]
                i += 1
                piece = self._string_char(ch)
                if piece:
                    out.append(piece)
            else:  # done
                break
        return "".join(out)

    def _string_char(self, ch: str) -> str:
        if self._escape is None:
            if ch == "\\":
                self._escape = "\\"
                return ""
            if ch == '"':
                self._state = "done"
                return ""
            return self._flush_surrogate() + ch
        if self._escape == "\\":
            if ch == "u":
                self._escape = "\\u"
                return ""
            self._escape = None
            return self._flush_surrogate() + _SIMPLE_ESCAPES.get(ch, ch)
        self._escape += ch  # "\\u" + hex
        if len(self._escape) < 6:
            return ""
        hex_part, self._escape = self._escape[2:], None
        try:
            code = int(hex_part, 16)
        except ValueError:
            return ""
        if 0xD800 <= code <= 0xDBFF:
            prefix = self._flush_surrogate()
            self._high_surrogate = code
            return prefix
        if 0xDC00 <= code <= 0xDFFF and self._high_surrogate is not None:
            high, self._high_surrogate = self._high_surrogate, None
            return chr(0x10000 + ((high - 0xD800) << 10) + (code - 0xDC00))
        return self._flush_surrogate() + chr(code)

    def _flush_surrogate(self) -> str:
        if self._high_surrogate is None:
            return ""
        self._high_surrogate = None
        return "�"


class _TextEmitter:
    """Decide con el primer carácter visible si la salida es JSON (`{`, ```) o texto plano."""

    def __init__(self) -> None:
        self._mode: str | None = None
        self._lead = ""
        self._extractor = RespuestaFieldExtractor()

    def feed(self, delta: str) -> str:
        if self._mode is None:
            self._lead += delta
            stripped = self._lead.lstrip()
            if not stripped:
                return ""
            self._mode = "json" if stripped[0] in "{`" else "text"
            delta, self._lead = stripped, ""
        return self._extractor.feed(delta) if self._mode == "json" else delta


# --- Cliente LLM con streaming ----------------------------------------------------


class StreamingLLM:
    """Envuelve el cliente `openai` con la misma interfaz `chat.completions.create`.

    Cada llamada se hace con `stream=True`: emite el texto visible mientras llega,
    revisa el abort entre fragmentos y devuelve un objeto con la misma forma que
    la respuesta no-stream (`choices[0].message.content`), así las funciones de
    generación existentes lo usan sin cambios."""

    def __init__(self, client: Any, control: TurnControl) -> None:
        self._client = client
        self._control = control
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs: Any) -> Any:
        control = self._control
        control.llm_calls += 1
        if control.llm_calls > 1:
            emit({"reset": True})
        if control.abort.is_set():
            raise GenerationAborted()
        kwargs.pop("stream", None)
        stream = self._client.chat.completions.create(stream=True, **kwargs)
        parts: list[str] = []
        emitter = _TextEmitter()
        try:
            for chunk in stream:
                if control.abort.is_set():
                    raise GenerationAborted()
                choices = getattr(chunk, "choices", None) or []
                delta = getattr(getattr(choices[0], "delta", None), "content", None) if choices else None
                if not delta:
                    continue
                parts.append(delta)
                visible = emitter.feed(delta)
                if visible:
                    emit({"token": visible})
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()  # en un abort, corta la respuesta HTTP del modelo
        content = "".join(parts)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _turn_llm() -> StreamingLLM | None:
    control = current_turn()
    if control is None:
        return None  # fuera del chat: el cliente de siempre, sin streaming
    from knowledge.pipeline_bridge import get_llm_client

    return StreamingLLM(get_llm_client(), control)


def chat_generate(question: str, context: list[dict[str, Any]]) -> str:
    """`secure_generate` (prompt de soporte, contexto aislado) con streaming."""
    from agent.guardrails.generation import secure_generate

    return secure_generate(question, context, llm=_turn_llm())


def chat_structured_generate(question: str, context: list[dict[str, Any]]) -> Any:
    """`secure_structured_generate()` (respuesta + propuesta de memoria) con streaming."""
    from agent.guardrails.generation import secure_structured_generate

    return secure_structured_generate()(question, context, llm=_turn_llm())


@lru_cache(maxsize=1)
def get_chat_agent_graph() -> CompiledStateGraph:
    """El grafo de producción con generación en streaming (mismo checkpointer)."""
    from agent.graph import build_graph, get_agent_graph, memory_enabled
    from agent.guardrails import Guardrails

    memory = None
    if memory_enabled():
        from agent.memory.service import AgentMemory
        from agent.memory.store import PostgresMemoryStore

        memory = AgentMemory(PostgresMemoryStore())
    return build_graph(
        checkpointer=get_agent_graph().checkpointer,
        memory=memory,
        guardrails=Guardrails(),
        generate_fn=chat_generate,
        structured_generate_fn=chat_structured_generate,
    )
