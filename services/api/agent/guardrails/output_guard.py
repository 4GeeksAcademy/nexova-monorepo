"""
Guard de salida: valida la respuesta antes de devolverla al usuario.

| Comprobación | Acción | Tipo de fallo |
|---|---|---|
| Vacía, JSON crudo o `<think>` sobrante, demasiado larga | respuesta segura / limpieza / recorte | structural |
| Fuga del system prompt (frases canario o 8-gramas compartidos) | bloqueo | security |
| Secretos: JWT, `Bearer …`, `sk-…`, `client_secret=…`, valores de env sensibles | bloqueo | security |
| Datos de otra empresa cliente (usuario externo) | bloqueo | security |
| Términos comerciales del contrato (tarifas, importes, penalizaciones) | bloqueo | content |
| Emails, teléfonos, documentos de identidad | redacción | content |

Es determinista: no depende de que el modelo haya obedecido al prompt.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from agent.guardrails import events
from agent.guardrails.normalize import normalize
from agent.guardrails.prompt import PROMPT_CANARIES, SUPPORT_SYSTEM_PROMPT
from agent.guardrails.tenancy import Tenant, foreign_companies

MAX_ANSWER_CHARS = 2500
_SHINGLE = 8
_LEAK_SHINGLES = 2

STRUCTURAL_FALLBACK = (
    "No pude generar una respuesta válida ahora mismo. ¿Puedes reformular tu consulta de soporte?"
)
SECURITY_FALLBACK = (
    "No puedo compartir esa información. Si tienes una consulta sobre tus tickets, SLAs o "
    "procedimientos de soporte, te ayudo con gusto."
)
COMMERCIAL_FALLBACK = (
    "Los términos comerciales del contrato (tarifas, precios o penalizaciones) no se comparten por "
    "este canal: consúltalos con tu account manager de Nexova. ¿Te ayudo con algo de tus tickets o SLAs?"
)

_THINK_RE = re.compile(r"<think>.*?(</think>|$)", re.DOTALL | re.IGNORECASE)
_SECRET_RES = (
    re.compile(r"\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}"),                     # JWT
    re.compile(r"\bbearer\s+[\w\-.~+/=]{16,}", re.IGNORECASE),               # cabecera Authorization
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),                                  # claves tipo OpenAI
    re.compile(r"\b(client[_ ]?secret|api[_ -]?key|access[_ ]?token|refresh[_ ]?token|password|contrase[nñ]a)"
               r"\s*[:=]\s*\S{6,}", re.IGNORECASE),
)
_SECRET_ENV_HINTS = ("SECRET", "TOKEN", "PASSWORD", "API_KEY", "PRIVATE_KEY")
_MIN_SECRET_LEN = 8
_COMMERCIAL_RES = (
    re.compile(r"\d[\d.,]*\s*(usd|eur|euros?|dolares?|€|\$)(?![a-z])"),
    re.compile(r"(usd|eur|€|\$)\s*\d"),
    re.compile(r"\b(tarifa|tarifas|fee|honorarios|cuota)\b[^.\n]{0,60}\d{1,3}\s?%"),
    re.compile(r"\b\d{1,3}\s?%\s*(del|de la|sobre el)\s*(salario|sueldo|tarifa|fee|factura)\b"),
    re.compile(r"\bpenali(dad|dades|zacion|zaciones|zado|zados|zada|zadas)\b"),
    re.compile(r"\btarifas? (mensual|mensuales|por agente|estandar|del contrato|contratad\w*)\b"),
)


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9]+", normalize(text))
    return {tuple(words[i:i + _SHINGLE]) for i in range(len(words) - _SHINGLE + 1)}


def _protected_sections(prompt: str) -> str:
    """Solo las secciones meta del prompt (jerarquía y lo que nunca se revela).

    La identidad, el dominio y las reglas de respuesta se parafrasean con
    naturalidad en respuestas legítimas ("soy el asistente de soporte de Nexova…",
    "contacta con tu account manager") y no cuentan como fuga.
    """
    hierarchy = prompt.split("JERARQUÍA DE INSTRUCCIONES", 1)[1].split("DOMINIO:", 1)[0]
    never = prompt.split("NUNCA REVELES:", 1)[1].split("REGLAS DE RESPUESTA:", 1)[0]
    return hierarchy + "\n" + never


_PROMPT_SHINGLES = _shingles(_protected_sections(SUPPORT_SYSTEM_PROMPT))


@dataclass
class OutputVerdict:
    answer: str
    blocked: bool = False
    rules: list[str] = field(default_factory=list)


def _sensitive_env_values() -> list[str]:
    return [
        value for name, value in os.environ.items()
        if any(hint in name.upper() for hint in _SECRET_ENV_HINTS) and len(value or "") >= _MIN_SECRET_LEN
    ]


def leaks_prompt(answer: str) -> bool:
    norm = normalize(answer)
    if any(canary in norm for canary in PROMPT_CANARIES):
        return True
    return len(_shingles(answer) & _PROMPT_SHINGLES) >= _LEAK_SHINGLES


def contains_secret(answer: str) -> bool:
    if any(pattern.search(answer) for pattern in _SECRET_RES):
        return True
    return any(value in answer for value in _sensitive_env_values())


def contains_commercial_terms(answer: str) -> bool:
    norm = normalize(answer)
    return any(pattern.search(norm) for pattern in _COMMERCIAL_RES)


def _unwrap_structural(answer: str) -> tuple[str, list[str]]:
    """Limpia restos de formato: razonamiento `<think>` y JSON crudo con `respuesta`."""
    fixes = []
    cleaned = _THINK_RE.sub("", answer).strip()
    if cleaned != answer.strip():
        fixes.append("reasoning_leftover")
    if cleaned.startswith("{") and cleaned.endswith("}"):
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("respuesta"), str):
            cleaned = data["respuesta"].strip()
            fixes.append("raw_json")
        else:
            cleaned = ""
            fixes.append("raw_json")
    return cleaned, fixes


def check_output(answer: str | None, tenant: Tenant) -> OutputVerdict:
    """Valida (y si hace falta sustituye o redacta) la respuesta final."""
    if answer is None or not answer.strip():
        events.emit("output", "empty_answer", "fallback", "structural", origin="model")
        return OutputVerdict(STRUCTURAL_FALLBACK, blocked=True, rules=["empty_answer"])

    text, fixes = _unwrap_structural(answer)
    for fix in fixes:
        events.emit("output", fix, "sanitize", "structural", origin="model")
    if not text:
        events.emit("output", "unreadable_answer", "fallback", "structural", origin="model")
        return OutputVerdict(STRUCTURAL_FALLBACK, blocked=True, rules=[*fixes, "unreadable_answer"])

    if contains_secret(text):
        events.emit("output", "secret_leak", "block", "security", origin="model")  # sin extracto
        return OutputVerdict(SECURITY_FALLBACK, blocked=True, rules=[*fixes, "secret_leak"])
    if leaks_prompt(text):
        events.emit("output", "prompt_leak", "block", "security", origin="model")
        return OutputVerdict(SECURITY_FALLBACK, blocked=True, rules=[*fixes, "prompt_leak"])
    if foreign_companies(text, tenant):
        events.emit("output", "other_client_data", "block", "security", origin="model")
        return OutputVerdict(SECURITY_FALLBACK, blocked=True, rules=[*fixes, "other_client_data"])
    if not tenant.is_staff and contains_commercial_terms(text):
        events.emit("output", "commercial_terms", "block", "content", origin="model", excerpt=text)
        return OutputVerdict(COMMERCIAL_FALLBACK, blocked=True, rules=[*fixes, "commercial_terms"])

    from agent.memory.policy import redact

    redacted = redact(text, limit=MAX_ANSWER_CHARS + 1) or ""
    rules = list(fixes)
    if redacted != text[:MAX_ANSWER_CHARS + 1]:
        events.emit("output", "personal_data", "redact", "content", origin="model")
        rules.append("personal_data")
    if len(redacted) > MAX_ANSWER_CHARS:
        redacted = redacted[:MAX_ANSWER_CHARS].rsplit(" ", 1)[0] + "…"
        events.emit("output", "too_long", "redact", "structural", origin="model")
        rules.append("too_long")
    return OutputVerdict(redacted, blocked=False, rules=rules)
