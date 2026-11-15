"""
Política de contenido de la memoria (BRIEFING de Nexova), aplicada por código.

El prompt de generación ya pide no proponer nada prohibido, pero un modelo
puede equivocarse o ser manipulado. Este filtro determinista se aplica dos
veces: antes de registrar una propuesta y antes de escribir un hecho
(incluida la versión editada por el usuario). Si bloquea, no se guarda nada.

Nunca entra en memoria:
- datos de candidatos de los procesos de selección (CVs, calificaciones,
  notas de entrevista): viven en el ATS / RAG de candidatos;
- información salarial de negociaciones de headhunting;
- correcciones puntuales de un solo ticket (sin patrón repetible);
- datos personales de contacto (emails, teléfonos, documentos de identidad);
- texto con forma de instrucción para el modelo (anti-envenenamiento).

`redact()` enmascara datos de contacto en los mensajes que se guardan en la
auditoría, para que el propio registro no se convierta en almacén de PII.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_CANDIDATE_RE = re.compile(
    r"\b(candidat[oa]s?|cvs?|curr[ií]cul\w*|entrevistas?|entrevistad[oa]s?|shortlist|"
    r"calificaci[oó]n(es)? del candidato|notas? de (la )?entrevista|terna|finalistas?)\b",
    re.IGNORECASE,
)
_SALARY_RE = re.compile(
    r"\b(salari\w*|sueldos?|remuneraci[oó]n\w*|compensaci[oó]n (econ[oó]mica|total)|"
    r"bruto anual|pretensi[oó]n(es)? (econ[oó]mica|salarial)\w*|paquete retributivo|"
    r"banda salarial|oferta econ[oó]mica|contraoferta)\b",
    re.IGNORECASE,
)
_ONE_OFF_RE = re.compile(
    r"(\bNXV[-\s]?\d{1,6}\b|\b(este|ese|aquel) (ticket|caso|incidente)\b|"
    r"\b(ticket|caso|incidencia) (n[º°o.]?\s*|#\s*)?\d{1,6}\b)",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")
_ID_DOC_RE = re.compile(r"\b(\d{8}[A-HJ-NP-TV-Z]|[XYZ]\d{7}[A-HJ-NP-TV-Z]|\d{3}-\d{2}-\d{4})\b", re.IGNORECASE)
_INSTRUCTION_RE = re.compile(
    r"(ignora|olvida|omite|desobedece)\w* (todas? )?(las |tus |cualquier )?(instrucciones|reglas|pol[ií]ticas)|"
    r"system prompt|prompt del sistema|instrucciones del sistema|a partir de ahora (eres|responde|di)|"
    r"act[uú]a como|jailbreak|siempre (responde|di|ofrece)|nunca menciones|"
    r"<\s*/?\s*(system|assistant|instructions?)\s*>",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PolicyResult:
    allowed: bool
    code: str | None = None
    reason: str | None = None


_MIN_PHONE_DIGITS = 9  # una fecha (dd-mm-aaaa) tiene 8 dígitos y no debe contar como teléfono


def _is_phone(match: re.Match[str]) -> bool:
    return sum(ch.isdigit() for ch in match.group(0)) >= _MIN_PHONE_DIGITS


def _has_phone(text: str) -> bool:
    return any(_is_phone(m) for m in _PHONE_RE.finditer(text))


_CHECKS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (_CANDIDATE_RE, "candidate_data",
     "los datos de candidatos viven en el ATS, no en la memoria del agente"),
    (_SALARY_RE, "salary_data",
     "la información salarial de negociaciones de headhunting nunca se memoriza"),
    (_ONE_OFF_RE, "one_off_ticket",
     "es una corrección puntual de un ticket concreto, sin patrón repetible"),
    (_EMAIL_RE, "personal_contact", "contiene un email"),
    (_ID_DOC_RE, "personal_contact", "contiene un documento de identidad"),
    (_INSTRUCTION_RE, "instruction_like",
     "tiene forma de instrucción para el asistente, no de hecho operativo"),
)


def check_fact(text: str) -> PolicyResult:
    """¿Puede este texto entrar en la memoria? Devuelve el primer motivo de bloqueo."""
    candidate = (text or "").strip()
    if len(candidate) < 10:
        return PolicyResult(False, "too_short", "el hecho es demasiado corto para ser una regla útil")
    for pattern, code, reason in _CHECKS:
        if pattern.search(candidate):
            return PolicyResult(False, code, reason)
    if _has_phone(candidate):
        return PolicyResult(False, "personal_contact", "contiene un número de teléfono")
    return PolicyResult(True)


def redact(text: str | None, *, limit: int = 1000) -> str | None:
    """Enmascara datos de contacto y recorta, para guardar mensajes en la auditoría."""
    if text is None:
        return None
    masked = _EMAIL_RE.sub("[email]", text)
    masked = _ID_DOC_RE.sub("[documento]", masked)
    masked = _PHONE_RE.sub(lambda m: "[teléfono]" if _is_phone(m) else m.group(0), masked)
    return masked[:limit]
