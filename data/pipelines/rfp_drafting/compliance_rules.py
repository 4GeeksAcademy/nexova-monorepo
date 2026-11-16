"""
Lineamientos de cumplimiento de Nexova (BRIEFING §5) como reglas por código.

El evaluador de cumplimiento no opina: aplica estas reglas, cada una con un
`rule_id` estable, y devuelve las violaciones con la evidencia encontrada.

| rule_id                   | Aplica a      | BRIEFING §5                                                     |
|---------------------------|---------------|-----------------------------------------------------------------|
| garantia-90-dias          | todas         | Incluir la garantía de satisfacción estándar a 90 días          |
| moneda-sede               | todas         | EUR si la sede es España, USD si es Miami/EE. UU.                |
| ttc-min-15-dias-habiles   | seleccion     | Ningún tiempo de cierre < 15 días hábiles en búsqueda ejecutiva  |
| sla-24h                   | soporte       | Mencionar explícitamente el SLA de respuesta de 24 horas         |
| clientes-anonimizados     | todas         | Sin nombres de clientes actuales como referencia                 |
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

from rfp_drafting.schemas import ComplianceCheck, Violation

RULE_GUARANTEE = "garantia-90-dias"
RULE_CURRENCY = "moneda-sede"
RULE_TTC = "ttc-min-15-dias-habiles"
RULE_SLA = "sla-24h"
RULE_ANONYMIZED = "clientes-anonimizados"

MIN_BUSINESS_DAYS_TO_CLOSE = 15
BUSINESS_DAYS_PER_WEEK = 5

RULE_DESCRIPTIONS: dict[str, str] = {
    RULE_GUARANTEE: "Incluir la garantía de satisfacción estándar de Nexova a 90 días.",
    RULE_CURRENCY: "Cotizar solo en la moneda de la sede del cliente (España → EUR, Miami/EE. UU. → USD).",
    RULE_TTC: "No comprometer un tiempo de cierre menor a 15 días hábiles en búsqueda ejecutiva.",
    RULE_SLA: "Mencionar explícitamente el SLA de respuesta de 24 horas.",
    RULE_ANONYMIZED: "No nombrar clientes actuales: usar 'cliente del sector retail' o similar.",
}

_CORPORATE_SUFFIXES = {"sa", "sl", "ltd", "corp", "inc", "gmbh", "group", "llc", "plc"}


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return re.sub(r"\s+", " ", text)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.;:!?])\s+|\n+", text) if s.strip()]


@dataclass(frozen=True)
class ComplianceContext:
    department_id: str
    client_hq: str | None
    currency: str | None
    known_clients: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# Reglas
# --------------------------------------------------------------------------- #
_GUARANTEE = re.compile(
    r"garant[ií]a[^.\n]{0,120}?(90|noventa)\s*d[ií]as|(90|noventa)\s*d[ií]as[^.\n]{0,120}?garant[ií]a",
    re.IGNORECASE,
)


def check_guarantee(draft: str, ctx: ComplianceContext) -> list[Violation]:
    if _GUARANTEE.search(draft):
        return []
    return [Violation(rule_id=RULE_GUARANTEE,
                      message="Falta la garantía de satisfacción estándar de Nexova a 90 días.")]


_USD_MARKERS = re.compile(r"\bUSD\b|US\$|\$\s?\d|\d\s?\$|\bd[oó]lar(es)?\b", re.IGNORECASE)
_EUR_MARKERS = re.compile(r"\bEUR\b|€|\beuros?\b", re.IGNORECASE)


def check_currency(draft: str, ctx: ComplianceContext) -> list[Violation]:
    if ctx.currency not in ("EUR", "USD"):
        return [Violation(rule_id=RULE_CURRENCY,
                          message=f"Sede del cliente '{ctx.client_hq}' sin moneda asignada: no se puede "
                                  "cotizar hasta confirmar si es España (EUR) o Miami/EE. UU. (USD).")]
    expected, wrong = (_EUR_MARKERS, _USD_MARKERS) if ctx.currency == "EUR" else (_USD_MARKERS, _EUR_MARKERS)
    other = "USD" if ctx.currency == "EUR" else "EUR"
    violations = []
    match = wrong.search(draft)
    if match:
        violations.append(Violation(
            rule_id=RULE_CURRENCY,
            message=f"Aparece {other} ('{match.group(0)}'); la propuesta debe cotizarse solo en {ctx.currency}.",
            evidence=_sentence_with(draft, match.start()),
        ))
    if not expected.search(draft):
        violations.append(Violation(
            rule_id=RULE_CURRENCY,
            message=f"La sección no indica la moneda de la propuesta ({ctx.currency}).",
        ))
    return violations


_CLOSE_CONTEXT = re.compile(
    r"cierre|cerrar|cubrir|cobertura de|contrataci[oó]n|incorporaci[oó]n|time[- ]to[- ]close|"
    r"plazo de (entrega|b[uú]squeda)|completar la b[uú]squeda",
    re.IGNORECASE,
)
_DURATION = re.compile(
    r"(?P<low>\d+)(?:\s*(?:-|–|a|y)\s*(?P<high>\d+))?\s*"
    r"(?P<unit>d[ií]as?\s+h[aá]biles|d[ií]as?\s+laborables|d[ií]as?|semanas?)",
    re.IGNORECASE,
)


def _business_days(value: int, unit: str) -> float:
    unit = _plain(unit)
    if "semana" in unit:
        return value * BUSINESS_DAYS_PER_WEEK
    if "habil" in unit or "laborable" in unit:
        return value
    return value * BUSINESS_DAYS_PER_WEEK / 7  # días naturales


def check_time_to_close(draft: str, ctx: ComplianceContext) -> list[Violation]:
    if ctx.department_id != "seleccion":
        return []
    violations = []
    for sentence in _sentences(draft):
        if not _CLOSE_CONTEXT.search(sentence):
            continue
        for match in _DURATION.finditer(sentence):
            days = _business_days(int(match.group("low")), match.group("unit"))
            if days < MIN_BUSINESS_DAYS_TO_CLOSE:
                violations.append(Violation(
                    rule_id=RULE_TTC,
                    message=f"Compromete un plazo de cierre de '{match.group(0)}' (≈{days:g} días hábiles); "
                            f"el mínimo en búsqueda ejecutiva es {MIN_BUSINESS_DAYS_TO_CLOSE} días hábiles.",
                    evidence=sentence[:300],
                ))
    return violations


_SLA_24H = re.compile(r"24\s*(h\b|horas|hrs?\b)|veinticuatro horas", re.IGNORECASE)
_SLA_CONTEXT = re.compile(r"\bSLA\b|tiempo de respuesta|respuesta", re.IGNORECASE)


def check_sla(draft: str, ctx: ComplianceContext) -> list[Violation]:
    if ctx.department_id != "soporte":
        return []
    if any(_SLA_24H.search(s) and _SLA_CONTEXT.search(s) for s in _sentences(draft)):
        return []
    return [Violation(rule_id=RULE_SLA,
                      message="Falta el SLA de respuesta de 24 horas de forma explícita (p. ej. "
                              "'SLA de respuesta de 24 horas').")]


def _client_patterns(name: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z0-9]+", _plain(name))]
    full = " ".join(words)
    core = " ".join(w for w in words if w not in _CORPORATE_SUFFIXES)
    return [p for p in {full, core, core.replace(" ", "")} if len(p) >= 4]


def check_anonymized(draft: str, ctx: ComplianceContext) -> list[Violation]:
    plain = " ".join(re.findall(r"[a-z0-9]+", _plain(draft)))
    violations = []
    for client in ctx.known_clients:
        if any(re.search(rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])", plain) for p in _client_patterns(client)):
            violations.append(Violation(
                rule_id=RULE_ANONYMIZED,
                message=f"Nombra a un cliente actual ('{client}'); anonimízalo (p. ej. 'un cliente del sector').",
            ))
    return violations


def _sentence_with(text: str, index: int) -> str:
    start = max(text.rfind(".", 0, index), text.rfind("\n", 0, index)) + 1
    end = min([i for i in (text.find(".", index), text.find("\n", index)) if i != -1] or [len(text)])
    return text[start:end + 1].strip()[:300]


# --------------------------------------------------------------------------- #
RULES = (
    (RULE_GUARANTEE, check_guarantee, None),
    (RULE_CURRENCY, check_currency, None),
    (RULE_TTC, check_time_to_close, "seleccion"),
    (RULE_SLA, check_sla, "soporte"),
    (RULE_ANONYMIZED, check_anonymized, None),
)


def rules_for(department_id: str) -> list[str]:
    return [rule_id for rule_id, _, dept in RULES if dept in (None, department_id)]


def evaluate_compliance(draft: str, ctx: ComplianceContext) -> ComplianceCheck:
    violations: list[Violation] = []
    for _, check, dept in RULES:
        if dept in (None, ctx.department_id):
            violations.extend(check(draft, ctx))
    return ComplianceCheck(passed=not violations, rule_ids=rules_for(ctx.department_id), violations=violations)


def normalize_known_clients(names: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({n.strip() for n in names if n and n.strip()}))
