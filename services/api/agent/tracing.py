"""
Trace consultable de cada corrida del agente (log JSON propio, sin servicios externos).

Cada nodo se envuelve con `traced(name, fn)`; durante una corrida (`start_run`)
el wrapper anota en el `RunTracer` activo qué nodo se ejecutó, en qué orden,
cuánto tardó y qué parte del estado produjo (o con qué error falló). Al
terminar, el runner persiste el trace en `<AGENT_TRACE_DIR>/<run_id>.json` y
`load_trace(run_id)` lo recupera después.

El trace queda en el servidor: incluye los chunks recuperados (documento,
sección y texto recortado) pero nunca las puntuaciones de Qdrant, igual que
el RAG, y la API solo devuelve el `run_id`.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_API_DIR = Path(__file__).resolve().parents[1]
DEFAULT_TRACE_DIR = _API_DIR / "data" / "agent" / "traces"
_CHUNK_TEXT_LIMIT = 300

_current_tracer: ContextVar["RunTracer | None"] = ContextVar("agent_run_tracer", default=None)


def trace_dir() -> Path:
    return Path(os.environ.get("AGENT_TRACE_DIR", DEFAULT_TRACE_DIR)).resolve()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _summarize(output: dict[str, Any]) -> dict[str, Any]:
    """Copia serializable de la salida de un nodo, con los chunks recortados."""
    summary = dict(output)
    if "context" in summary:
        summary["context"] = [
            {
                "source_document": chunk.get("source_document"),
                "section": chunk.get("section"),
                "text": (chunk.get("text") or "")[:_CHUNK_TEXT_LIMIT],
            }
            for chunk in summary["context"] or []
        ]
    return summary


class RunTracer:
    def __init__(self, run_id: str, question: str) -> None:
        self.run_id = run_id
        self.question = question
        self.started_at = _now()
        self.steps: list[dict[str, Any]] = []

    def record(
        self,
        node: str,
        *,
        started_at: str,
        duration_ms: float,
        output: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        self.steps.append(
            {
                "order": len(self.steps) + 1,
                "node": node,
                "started_at": started_at,
                "duration_ms": round(duration_ms, 1),
                "output": _summarize(output) if output is not None else None,
                "error": error,
            }
        )

    def to_dict(
        self,
        *,
        status: str,
        final_answer: str | None,
        error: str | None,
        route: dict[str, Any] | None = None,
        sources_used: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "thread_id": self.run_id,
            "question": self.question,
            "started_at": self.started_at,
            "finished_at": _now(),
            "status": status,
            "route": route,
            # Fuentes consultadas, en orden: "tickets" / "inventory" (tools en vivo), "rag".
            "sources_used": sources_used or [],
            "nodes": [step["node"] for step in self.steps],
            "steps": self.steps,
            "final_answer": final_answer,
            "error": error,
        }

    def save(
        self,
        *,
        status: str,
        final_answer: str | None,
        error: str | None,
        route: dict[str, Any] | None = None,
        sources_used: list[str] | None = None,
    ) -> Path:
        directory = trace_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.run_id}.json"
        payload = self.to_dict(
            status=status,
            final_answer=final_answer,
            error=error,
            route=route,
            sources_used=sources_used,
        )
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


@contextmanager
def start_run(run_id: str, question: str) -> Iterator[RunTracer]:
    """Activa un `RunTracer` para los nodos que se ejecuten dentro del bloque."""
    tracer = RunTracer(run_id, question)
    token = _current_tracer.set(tracer)
    try:
        yield tracer
    finally:
        _current_tracer.reset(token)


def traced(
    name: str, fn: Callable[[Any], dict[str, Any]]
) -> Callable[[Any], dict[str, Any]]:
    """Envuelve un nodo para que registre su ejecución en el tracer activo."""

    def wrapper(state: Any) -> dict[str, Any]:
        tracer = _current_tracer.get()
        started_at = _now()
        start = time.perf_counter()
        try:
            output = fn(state)
        except Exception as exc:
            if tracer is not None:
                tracer.record(
                    name,
                    started_at=started_at,
                    duration_ms=(time.perf_counter() - start) * 1000,
                    error=type(exc).__name__,
                )
            raise
        if tracer is not None:
            tracer.record(
                name,
                started_at=started_at,
                duration_ms=(time.perf_counter() - start) * 1000,
                output=output,
            )
        return output

    wrapper.__name__ = name
    wrapper.__doc__ = fn.__doc__
    return wrapper


def load_trace(run_id: str, directory: Path | None = None) -> dict[str, Any]:
    """Lee el trace de una corrida. `run_id` debe ser un UUID (evita path traversal)."""
    safe_id = str(uuid.UUID(run_id))
    path = (directory or trace_dir()) / f"{safe_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))
