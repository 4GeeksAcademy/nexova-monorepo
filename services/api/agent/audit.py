"""
Registro de decisiones del agente de soporte (informe NIST · Detect; README:
"qué acción tomó cada agente y por qué").

Una línea JSONL append-only por corrida (`AGENT_DECISION_LOG`, por defecto
`<AGENT_TRACE_DIR>/../agent_decisions.jsonl`, fuera de git) con:

- quién (`user_id`, empresa cliente, si es personal interno) y cuándo;
- **qué** hizo: fuentes elegidas, tools llamadas con su `status`, si usó memoria,
  si el guard de salida bloqueó, y el resultado (`answered`, `blocked`,
  `redirected`, `tool_fallback`, `rejected`, `failed`, `interrupted`);
- **por qué**: el veredicto del guard de entrada (categoría y regla) y el método
  del enrutador (`llm` o `rules`), resumidos en `reason`;
- latencia y nodos recorridos (el detalle por nodo sigue en el trace del `run_id`).

Minimización (RGPD art. 5.1.c): ni la pregunta ni la respuesta, solo su longitud y
un hash para correlacionar con el trace, que se purga según la política de
retención. Nunca lanza: la observabilidad no puede tumbar una respuesta.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("nexova.agent.decisions")
_lock = threading.Lock()
_TOOL_SOURCES = ("tickets", "inventory")


def log_path() -> Path:
    explicit = os.environ.get("AGENT_DECISION_LOG")
    if explicit:
        return Path(explicit).resolve()
    from agent.tracing import trace_dir

    return (trace_dir().parent / "agent_decisions.jsonl").resolve()


def _fingerprint(text: str | None) -> str | None:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] if text else None


def _duration_ms(started_at: str | None) -> float | None:
    if not started_at:
        return None
    try:
        start = datetime.fromisoformat(started_at)
    except ValueError:
        return None
    return round((datetime.now(UTC) - start).total_seconds() * 1000, 1)


def _outcome(status: str, state: dict[str, Any]) -> str:
    if status in ("failed", "interrupted"):
        return status
    guard = state.get("guard") or {}
    if guard.get("action") == "block":
        return "blocked"
    if guard.get("action") == "redirect":
        return "redirected"
    if state.get("error"):
        return "rejected"
    if any(r.get("status") != "ok" for r in state.get("tool_results") or []):
        return "tool_fallback"
    return "answered"


def _reason(outcome: str, guard: dict[str, Any], route: dict[str, Any] | None,
            sources: list[str], tools: list[dict[str, Any]], state: dict[str, Any]) -> str:
    if outcome in ("blocked", "redirected"):
        return f"guard de entrada: {guard.get('category')} ({guard.get('rule') or 'sin regla'})"
    if outcome == "rejected":
        return f"pregunta rechazada: {state.get('error')}"
    if outcome in ("failed", "interrupted"):
        return f"corrida {outcome}"
    method = (route or {}).get("method", "?")
    parts = [f"enrutador {method} → {', '.join(sources) or 'sin fuentes'}"]
    failed = [f"{t['tool']}={t['status']}" for t in tools if t["status"] != "ok"]
    if failed:
        parts.append(f"respuesta honesta sin datos ({', '.join(failed)})")
    if state.get("output_blocked"):
        parts.append(f"guard de salida: {', '.join(state.get('output_rules') or [])}")
    return "; ".join(parts)


def build_record(
    state: dict[str, Any],
    *,
    status: str,
    run_id: str,
    conversation_id: str | None,
    question: str | None,
    answer: str | None,
    started_at: str | None,
    nodes: list[str],
) -> dict[str, Any]:
    guard = state.get("guard") or {}
    route = state.get("route")
    sources = list(state.get("sources_used") or [])
    tool_sources = [s for s in sources if s in _TOOL_SOURCES]
    tools = [{"tool": src, "status": result.get("status")}
             for src, result in zip(tool_sources, state.get("tool_results") or [])]
    tenant = state.get("tenant") or {}
    outcome = _outcome(status, state)
    return {
        "ts": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "conversation_id": conversation_id,
        "user_id": state.get("user_id"),
        "tenant": {"client_company": tenant.get("client_company"), "is_staff": tenant.get("is_staff")},
        "status": status,
        "outcome": outcome,
        "reason": _reason(outcome, guard, route, sources, tools, state),
        "guard_input": ({k: guard.get(k) for k in ("category", "action", "rule", "failure_type", "method")}
                        if guard else None),
        "route": ({k: route.get(k) for k in ("rag", "tickets", "inventory", "method")} if route else None),
        "sources_used": sources,
        "tools": tools,
        "memory": {
            "facts_used": len(state.get("memory_facts") or []),
            "decision": (state.get("memory_decision") or {}).get("outcome"),
            "proposal_registered": bool(state.get("memory_proposal")),
        },
        "output_blocked": bool(state.get("output_blocked")),
        "output_rules": list(state.get("output_rules") or []),
        "nodes": nodes,
        "latency_ms": _duration_ms(started_at),
        "question_sha256": _fingerprint(question),
        "question_chars": len(question or ""),
        "answer_chars": len(answer or ""),
    }


def record_decision(state: dict[str, Any] | None, *, status: str, run_id: str,
                    conversation_id: str | None, tracer: Any, answer: str | None = None,
                    initial: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Escribe la línea de la corrida. Devuelve el registro (o None si no se pudo construir)."""
    try:
        merged = {**(initial or {}), **(state or {})}
        record = build_record(
            merged, status=status, run_id=run_id, conversation_id=conversation_id,
            question=getattr(tracer, "question", None), answer=answer,
            started_at=getattr(tracer, "started_at", None),
            nodes=[step["node"] for step in getattr(tracer, "steps", [])],
        )
    except Exception:  # noqa: BLE001 - nunca rompe la corrida
        logger.exception("No se pudo construir el registro de decisión de %s", run_id)
        return None
    logger.info("agent_decision run_id=%s outcome=%s reason=%s", run_id, record["outcome"], record["reason"])
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock, path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.error("No se pudo escribir el registro de decisiones (%s)", type(exc).__name__)
    return record


def read_decisions(limit: int = 50, path: Path | None = None) -> list[dict[str, Any]]:
    """Últimas `limit` decisiones, de la más reciente a la más antigua."""
    path = path or log_path()
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        tail = deque(handle, maxlen=limit)
    records = []
    for line in reversed(tail):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records
