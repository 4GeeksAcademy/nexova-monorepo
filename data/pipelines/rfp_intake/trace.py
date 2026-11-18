"""
Trazabilidad del flujo RFP (Partes 1–3): qué agente hizo qué y en qué orden.

`traced(...)` envuelve un nodo de LangGraph y, en cada ejecución, guarda en
`rfp_trace_event` el agente, un resumen del input y del output y los
timestamps. Si el grafo declara `trace` en su estado (Parte 3), el evento
también se añade al estado (y viaja en el checkpoint).

Un nodo que se pausa con `interrupt()` deja un evento `interrumpido`; al
reanudarse vuelve a ejecutarse y deja otro `ok` con la decisión humana: el
trace muestra la pausa y la reanudación, en orden.

Si guardar el evento falla, el flujo sigue (la trazabilidad no debe tumbar
el pipeline), con un aviso en el log.
"""

from __future__ import annotations

import functools
import logging
import time
from datetime import date, datetime
from typing import Any, Callable

from langgraph.errors import GraphBubbleUp

from rfp_intake.models import utcnow

logger = logging.getLogger("rfp_trace")

STATUS_OK = "ok"
STATUS_INTERRUPTED = "interrumpido"
STATUS_ERROR = "error"

# Campos voluminosos que no se copian enteros al trace (se registra su tamaño).
_BULKY_KEYS = {"markdown", "draft", "draft_content", "previous_draft", "content_markdown", "pdf_path_bytes"}
_MAX_STR = 400
_MAX_ITEMS = 12
_MAX_DEPTH = 4

AgentName = str | Callable[[dict[str, Any]], str]


def summarize(value: Any, depth: int = 0) -> Any:
    """Copia JSON acotada de un valor del estado (para no guardar documentos enteros)."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= _MAX_STR else f"{value[:_MAX_STR]}… ({len(value)} caracteres)"
    if depth >= _MAX_DEPTH:
        return f"<{type(value).__name__}>"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in list(value.items())[:40]:
            key = str(key)
            if key in _BULKY_KEYS and isinstance(item, str):
                out[key] = {"chars": len(item), "preview": item[:160]}
            elif key == "trace":
                continue  # el propio trace no se anida en el trace
            else:
                out[key] = summarize(item, depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        summary = [summarize(item, depth + 1) for item in items[:_MAX_ITEMS]]
        if len(items) > _MAX_ITEMS:
            summary.append(f"… {len(items) - _MAX_ITEMS} más")
        return summary
    if hasattr(value, "model_dump"):
        return summarize(value.model_dump(by_alias=True), depth)
    return repr(value)[:_MAX_STR]


def _as_dict(value: Any) -> dict[str, Any]:
    summary = summarize(value)
    return summary if isinstance(summary, dict) else {"value": summary}


def traced(
    fn: Callable[[dict[str, Any]], dict[str, Any] | None],
    *,
    store: Any,
    part: str,
    node: str,
    agent: AgentName,
    thread_id: Callable[[dict[str, Any]], str | None] | None = None,
    in_state: bool = False,
) -> Callable[[dict[str, Any]], dict[str, Any] | None]:
    """Envuelve `fn(state)` registrando cada ejecución en el store (y en `state['trace']` si `in_state`)."""

    @functools.wraps(fn)
    def wrapper(state: dict[str, Any]) -> dict[str, Any] | None:
        started_at = utcnow()
        clock = time.perf_counter()
        agent_name = agent(state) if callable(agent) else agent
        event = {
            "part": part,
            "thread_id": thread_id(state) if thread_id else None,
            "node": node,
            "agent": agent_name,
            "input": _as_dict(state),
            "started_at": started_at,
        }

        def _finish(status: str, output: Any) -> dict[str, Any]:
            finished = dict(event, status=status, output=_as_dict(output), finished_at=utcnow(),
                            duration_ms=int((time.perf_counter() - clock) * 1000))
            ticket_id = state.get("ticket_id")
            if ticket_id:
                try:
                    store.append_trace(ticket_id, finished)
                except Exception:
                    logger.warning("ticket=%s no se pudo guardar el trace de %s", ticket_id, node, exc_info=True)
            return finished

        try:
            result = fn(state)
        except GraphBubbleUp as pause:
            # `interrupt()` (u otra señal de control de LangGraph): la rama queda en pausa.
            _finish(STATUS_INTERRUPTED, {"interrupt": [getattr(i, "value", None) for i in pause.args[0]]}
                    if pause.args and isinstance(pause.args[0], (list, tuple)) else {"interrupt": True})
            raise
        except Exception as exc:
            # Solo el tipo: el trace se sirve por `GET /rfp/tickets/{id}/trace` y el texto de la
            # excepción puede incluir rutas, URLs o fragmentos del documento. El detalle va al log del worker.
            _finish(STATUS_ERROR, {"error": type(exc).__name__})
            raise

        finished = _finish(STATUS_OK, result or {})
        if in_state:
            entry = {k: _json_ready(v) for k, v in finished.items() if k not in ("input",)}
            result = dict(result or {})
            result["trace"] = [entry]
        return result

    return wrapper


def _json_ready(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (datetime, date)) else value
