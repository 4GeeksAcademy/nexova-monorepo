"""
Harness de protección del agente de soporte de primera línea (Hito 8 · Parte 2,
ticket #SEC-114).

Capas independientes, cada una para un tipo de fallo distinto:

| Capa | Módulo | Fallo que contiene |
|---|---|---|
| System prompt seguro + separación de roles | `prompt.py`, `generation.py` | seguridad |
| Guard de entrada (reglas + LLM acotado) | `input_guard.py`, `scope_classifier.py` | seguridad / contenido |
| Aislamiento de contenido externo (RAG, tools, memoria) | `isolation.py` | seguridad |
| Tenancy por código (tickets de la propia empresa) | `tenancy.py` | seguridad |
| Guard de salida | `output_guard.py` | estructural / contenido / seguridad |
| Observabilidad (log JSONL + trace + resumen) | `events.py` | — |

`Guardrails` agrupa las dependencias inyectables (clasificador de alcance y
small talk) para `build_graph(guardrails=...)`; los tests usan fakes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from agent.guardrails.input_guard import InputVerdict
from agent.guardrails.tenancy import INTERNAL_TENANT, Tenant

ScopeFn = Callable[[str], InputVerdict]
SmallTalkFn = Callable[[str], str]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Guardrails:
    """Dependencias del harness. `None` = implementación real (LLM de 4Geeks)."""

    scope_fn: ScopeFn | None = None
    small_talk_fn: SmallTalkFn | None = None
    use_llm_scope: bool = True
    clock: Callable[[], datetime] = field(default=_utcnow)


__all__ = ["Guardrails", "INTERNAL_TENANT", "InputVerdict", "Tenant"]
