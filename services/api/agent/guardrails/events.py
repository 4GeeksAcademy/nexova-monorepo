"""
Observabilidad mínima del harness: cada vez que un guardrail bloquea,
redirige, neutraliza o redacta algo queda un evento con su tipo de fallo
(`structural`, `content` o `security`).

Cada evento va a tres sitios:
1. `logger.warning` (log del servidor);
2. un JSONL append-only (`AGENT_GUARDRAIL_LOG`, por defecto
   `<AGENT_TRACE_DIR>/../guardrail_events.jsonl`);
3. el trace de la corrida (`trace["guardrails"]`), vía el colector activo.

Nunca se guarda el texto completo del usuario ni ningún secreto: solo la
regla que saltó y, como mucho, un extracto corto ya redactado.

`summarize()` agrega los conteos (por guard, regla, acción y tipo de fallo)
para `GET /agent/guardrails/summary` y `scripts/agent_guardrails_report.py`.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger("agent.guardrails")

Guard = Literal["input", "scope", "isolation", "tenancy", "output", "generation", "routing", "tool"]
GuardAction = Literal["block", "redirect", "sanitize", "redact", "retry", "fallback"]
FailureType = Literal["structural", "content", "security"]
FAILURE_TYPES: tuple[FailureType, ...] = ("structural", "content", "security")
_EXCERPT_LIMIT = 120
_lock = threading.Lock()


@dataclass(frozen=True)
class GuardrailEvent:
    guard: Guard
    rule: str
    action: GuardAction
    failure_type: FailureType
    origin: str = "user"
    category: str | None = None
    excerpt: str | None = None
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    run_id: str | None = None
    conversation_id: str | None = None
    user_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Collector:
    run_id: str | None
    conversation_id: str | None
    user_id: str | None
    events: list[GuardrailEvent] = field(default_factory=list)


_current: ContextVar[_Collector | None] = ContextVar("guardrail_collector", default=None)


def log_path() -> Path:
    explicit = os.environ.get("AGENT_GUARDRAIL_LOG")
    if explicit:
        return Path(explicit).resolve()
    from agent.tracing import trace_dir

    return trace_dir().parent / "guardrail_events.jsonl"


def _excerpt(text: str | None) -> str | None:
    if not text:
        return None
    from agent.memory.policy import redact

    return redact(" ".join(text.split()), limit=_EXCERPT_LIMIT)


def emit(
    guard: Guard,
    rule: str,
    action: GuardAction,
    failure_type: FailureType,
    *,
    origin: str = "user",
    category: str | None = None,
    excerpt: str | None = None,
) -> GuardrailEvent:
    """Registra un evento. Nunca lanza: la observabilidad no puede tumbar una respuesta."""
    collector = _current.get()
    event = GuardrailEvent(
        guard=guard,
        rule=rule,
        action=action,
        failure_type=failure_type,
        origin=origin,
        category=category,
        excerpt=_excerpt(excerpt),
        run_id=collector.run_id if collector else None,
        conversation_id=collector.conversation_id if collector else None,
        user_id=collector.user_id if collector else None,
    )
    if collector is not None:
        collector.events.append(event)
    logger.warning(
        "guardrail guard=%s rule=%s action=%s failure_type=%s origin=%s run_id=%s",
        guard, rule, action, failure_type, origin, event.run_id,
    )
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event.to_dict(), ensure_ascii=False)
        with _lock, path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as exc:
        logger.error("No se pudo escribir el log de guardrails (%s)", type(exc).__name__)
    return event


@contextmanager
def collect_events(run_id: str, conversation_id: str | None, user_id: str | None) -> Iterator[_Collector]:
    """Activa el colector de la corrida: los eventos emitidos dentro acaban en su trace."""
    collector = _Collector(run_id, conversation_id, user_id)
    token = _current.set(collector)
    try:
        yield collector
    finally:
        _current.reset(token)


def read_events(path: Path | None = None) -> list[dict[str, Any]]:
    target = path or log_path()
    if not target.exists():
        return []
    events = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # una línea corrupta no invalida el resto del log
    return events


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def summarize(
    events: Iterable[dict[str, Any]],
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Conteos de activaciones por guard, regla, acción y tipo de fallo."""
    since = since.replace(tzinfo=timezone.utc) if since and since.tzinfo is None else since
    until = until.replace(tzinfo=timezone.utc) if until and until.tzinfo is None else until
    selected = []
    for event in events:
        ts = _parse_ts(event.get("ts"))
        if since and (ts is None or ts < since):
            continue
        if until and (ts is None or ts > until):
            continue
        if conversation_id and event.get("conversation_id") != conversation_id:
            continue
        selected.append(event)

    by_type = Counter(e.get("failure_type") for e in selected)
    return {
        "total": len(selected),
        "runs_affected": len({e.get("run_id") for e in selected if e.get("run_id")}),
        "by_failure_type": {ft: by_type.get(ft, 0) for ft in FAILURE_TYPES},
        "by_guard": dict(Counter(e.get("guard") for e in selected).most_common()),
        "by_action": dict(Counter(e.get("action") for e in selected).most_common()),
        "by_rule": dict(Counter(f"{e.get('guard')}:{e.get('rule')}" for e in selected).most_common()),
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
        "conversation_id": conversation_id,
    }
