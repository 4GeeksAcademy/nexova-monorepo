"""
Arbitraje de conflictos entre departamentos (BRIEFING §7). Sin LLM.

Los conflictos se detectan sobre **estado estructurado** del ticket (sede →
moneda, monedas presentes en cada borrador, plazos de cierre en días hábiles,
inicio de la formación, regla `sla-24h`), no por agentes negociando. Cada
trigger tiene un árbitro fijo y una regla de resolución determinista:

| trigger_id               | Sección afectada         | Árbitro fijo   | Ronda < límite    | Límite agotado          |
|--------------------------|--------------------------|----------------|-------------------|-------------------------|
| ttc-vs-training-window   | capacitacion (+seleccion | Marcos Ibáñez  | request_changes   | escala a Marcos (humano)|
|                          | si su TTC < 15 d. háb.)  |                |                   |                         |
| support-sla-missing      | soporte                  | Roberto Díaz   | request_changes   | reject (bloqueada)      |
| currency-mismatch        | la(s) que cotiza(n) mal  | Marcos Ibáñez  | request_changes   | reject                  |

Los agentes (evaluadores de la Parte 2) pueden **señalar** el problema; quien
lo **resuelve** es esta tabla, y la decisión queda a nombre del árbitro.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from rfp_drafting.compliance_rules import (
    DURATION_PATTERN,
    MIN_BUSINESS_DAYS_TO_CLOSE,
    ComplianceContext,
    business_days,
    check_sla,
    detect_currencies,
    sentences,
    time_to_close_business_days,
)
from rfp_intake.departments import CURRENCY_BY_HQ, DEPARTMENTS, SALES_DIRECTOR

TRIGGER_TTC_TRAINING = "ttc-vs-training-window"
TRIGGER_SLA_MISSING = "support-sla-missing"
TRIGGER_CURRENCY = "currency-mismatch"

ACTION_REQUEST_CHANGES = "request_changes"
ACTION_REJECT = "reject"
ACTION_ESCALATE = "escalate"


@dataclass(frozen=True)
class Trigger:
    trigger_id: str
    arbiter: str
    rule: str
    on_limit: str  # acción cuando ya no quedan rondas: reject | escalate


TRIGGERS: dict[str, Trigger] = {
    TRIGGER_TTC_TRAINING: Trigger(
        TRIGGER_TTC_TRAINING, SALES_DIRECTOR,
        "Secuenciar entregas: la formación no empieza antes de un cierre realista "
        f"(≥{MIN_BUSINESS_DAYS_TO_CLOSE} días hábiles en búsqueda ejecutiva).",
        ACTION_ESCALATE,
    ),
    TRIGGER_SLA_MISSING: Trigger(
        TRIGGER_SLA_MISSING, DEPARTMENTS["soporte"].owner,
        "Bloquear la aprobación hasta que el SLA de respuesta de 24 horas sea explícito; revisar "
        "la dotación si el SLA es inviable.",
        ACTION_REJECT,
    ),
    TRIGGER_CURRENCY: Trigger(
        TRIGGER_CURRENCY, SALES_DIRECTOR,
        "Reescribir a la moneda de la sede (España → EUR, Miami/EE. UU. → USD); rechazar si no se "
        "resuelve tras el límite de iteraciones.",
        ACTION_REJECT,
    ),
}


@dataclass(frozen=True)
class Conflict:
    trigger_id: str
    department_id: str            # sección sobre la que actúa la resolución
    message: str                  # instrucción concreta para el generador
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def arbiter(self) -> str:
        return TRIGGERS[self.trigger_id].arbiter

    def as_dict(self) -> dict[str, Any]:
        return {"trigger_id": self.trigger_id, "department_id": self.department_id, "arbiter": self.arbiter,
                "rule": TRIGGERS[self.trigger_id].rule, "message": self.message, "evidence": self.evidence}


@dataclass(frozen=True)
class Resolution:
    action: str | None            # None = sin conflicto: aprobación normal del responsable
    arbiter: str | None
    conflicts: tuple[Conflict, ...]

    @property
    def feedback(self) -> str:
        return "\n".join(f"- [{c.trigger_id}] {c.message} (árbitro: {c.arbiter})" for c in self.conflicts)

    @property
    def summary(self) -> str:
        if not self.action:
            return "Sin conflictos entre departamentos."
        verbs = {ACTION_REQUEST_CHANGES: "pide cambios", ACTION_REJECT: "rechaza",
                 ACTION_ESCALATE: "escala la decisión a"}
        return f"{verbs[self.action]} {self.arbiter}" if self.action == ACTION_ESCALATE else (
            f"{self.arbiter} {verbs[self.action]} por regla")


# --------------------------------------------------------------------------- #
# Hechos estructurados
# --------------------------------------------------------------------------- #
_TRAINING_START = re.compile(r"\b(empez|comenz|inici|arranc|start)\w*", re.IGNORECASE)
_TRAINING_SUBJECT = re.compile(r"formaci[oó]n|programa|capacitaci[oó]n|sesiones|training|curso|cohorte",
                               re.IGNORECASE)
_AFTER_CLOSE = re.compile(
    r"(cuando|tras|despu[eé]s de|una vez)[^.\n]{0,80}(cerrad|cierre|cubiert|incorporad|contratad|seleccionad)",
    re.IGNORECASE,
)


def training_window(draft: str) -> dict[str, Any]:
    """Cuándo arranca la formación según el borrador: tras el cierre (secuenciado) o a N días hábiles."""
    sequenced = False
    start_days: float | None = None
    for sentence in sentences(draft or ""):
        if not (_TRAINING_START.search(sentence) and _TRAINING_SUBJECT.search(sentence)):
            continue
        if _AFTER_CLOSE.search(sentence):
            sequenced = True
            continue
        for match in DURATION_PATTERN.finditer(sentence):
            days = business_days(int(match.group("low")), match.group("unit"))
            start_days = days if start_days is None else min(start_days, days)
    return {"sequenced_after_close": sequenced, "start_business_days": start_days}


def _active_sections(ticket: dict[str, Any]) -> dict[str, dict[str, Any]]:
    handoff = ticket.get("approval_handoff") or {}
    active = {s["department_id"] for s in handoff.get("sections") or []}
    return {s["department_id"]: s for s in ticket.get("sections") or []
            if s["department_id"] in active and s.get("draft_content")}


def structured_facts(ticket: dict[str, Any]) -> dict[str, Any]:
    """Lo que mira el árbitro, en un dict (también va al trace y a la evidencia del arbitraje)."""
    sections = _active_sections(ticket)
    metadata = ticket.get("metadata") or {}
    client_hq = metadata.get("client_hq") or (ticket.get("approval_handoff") or {}).get("client_hq")
    facts: dict[str, Any] = {
        "client_hq": client_hq,
        "expected_currency": CURRENCY_BY_HQ.get(client_hq or ""),
        "active_departments": sorted(sections),
        "currencies": {d: detect_currencies(s["draft_content"]) for d, s in sections.items()},
    }
    if "seleccion" in sections:
        facts["seleccion_ttc_business_days"] = time_to_close_business_days(sections["seleccion"]["draft_content"])
    if "capacitacion" in sections:
        facts["training_window"] = training_window(sections["capacitacion"]["draft_content"])
    if "soporte" in sections:
        ctx = ComplianceContext(department_id="soporte", client_hq=client_hq, currency=facts["expected_currency"])
        facts["soporte_sla_24h"] = not check_sla(sections["soporte"]["draft_content"], ctx)
    return facts


# --------------------------------------------------------------------------- #
# Triggers
# --------------------------------------------------------------------------- #
def _ttc_vs_training(facts: dict[str, Any]) -> list[Conflict]:
    if not {"seleccion", "capacitacion"} <= set(facts["active_departments"]):
        return []
    ttc = facts.get("seleccion_ttc_business_days") or []
    window = facts.get("training_window") or {}
    realistic_close = max([MIN_BUSINESS_DAYS_TO_CLOSE, *([min(ttc)] if ttc else [])])
    evidence = {"seleccion_ttc_business_days": ttc, "training_window": window,
                "realistic_close_business_days": realistic_close}
    conflicts: list[Conflict] = []
    unrealistic_ttc = bool(ttc) and min(ttc) < MIN_BUSINESS_DAYS_TO_CLOSE
    start = window.get("start_business_days")
    training_too_early = start is not None and not window.get("sequenced_after_close") and start < realistic_close
    if unrealistic_ttc:
        conflicts.append(Conflict(
            TRIGGER_TTC_TRAINING, "seleccion",
            f"El tiempo de cierre comprometido (≈{min(ttc):g} días hábiles) no es realista: el mínimo en "
            f"búsqueda ejecutiva es {MIN_BUSINESS_DAYS_TO_CLOSE} días hábiles y la formación depende de él.",
            evidence,
        ))
    if unrealistic_ttc or training_too_early:
        conflicts.append(Conflict(
            TRIGGER_TTC_TRAINING, "capacitacion",
            "La formación de la misma cohorte no puede empezar antes del cierre de las posiciones: "
            f"secuencia el inicio después de un cierre realista (≥{realistic_close:g} días hábiles) "
            "o indica que empieza cuando estén cerradas las posiciones.",
            evidence,
        ))
    return conflicts


def _sla_missing(facts: dict[str, Any]) -> list[Conflict]:
    if "soporte" not in facts["active_departments"] or facts.get("soporte_sla_24h", True):
        return []
    return [Conflict(TRIGGER_SLA_MISSING, "soporte",
                     "La sección de soporte debe comprometer explícitamente el SLA de respuesta de 24 horas.",
                     {"soporte_sla_24h": False})]


def _currency_mismatch(facts: dict[str, Any]) -> list[Conflict]:
    expected = facts.get("expected_currency")
    currencies: dict[str, list[str]] = facts["currencies"]
    used = sorted({c for values in currencies.values() for c in values})
    conflicts = []
    for department_id, found in sorted(currencies.items()):
        if expected:
            wrong = [c for c in found if c != expected]
            if wrong:
                conflicts.append(Conflict(
                    TRIGGER_CURRENCY, department_id,
                    f"Cotiza en {', '.join(wrong)} pero el cliente tiene sede en {facts['client_hq']}: "
                    f"reescribe todas las cifras en {expected}.",
                    {"client_hq": facts["client_hq"], "expected_currency": expected, "found": found,
                     "all_sections": currencies},
                ))
        elif len(used) > 1 and found:
            conflicts.append(Conflict(
                TRIGGER_CURRENCY, department_id,
                "Las secciones cotizan en monedas distintas y la sede del cliente no está confirmada: "
                "no des cifras hasta confirmar la sede (España → EUR, Miami/EE. UU. → USD).",
                {"client_hq": facts["client_hq"], "expected_currency": None, "all_sections": currencies},
            ))
    return conflicts


DETECTORS = (_ttc_vs_training, _sla_missing, _currency_mismatch)


def detect_conflicts(ticket: dict[str, Any]) -> list[Conflict]:
    """Todos los conflictos activos del ticket (cualquier departamento)."""
    facts = structured_facts(ticket)
    return [conflict for detector in DETECTORS for conflict in detector(facts)]


def resolve(conflicts: list[Conflict], *, approval_round: int, max_rounds: int) -> Resolution:
    """Regla determinista: con rondas disponibles se piden cambios; agotadas, se aplica `on_limit`."""
    if not conflicts:
        return Resolution(action=None, arbiter=None, conflicts=())
    ordered = tuple(sorted(conflicts, key=lambda c: list(TRIGGERS).index(c.trigger_id)))
    if approval_round < max_rounds:
        return Resolution(ACTION_REQUEST_CHANGES, ordered[0].arbiter, ordered)
    rejecting = [c for c in ordered if TRIGGERS[c.trigger_id].on_limit == ACTION_REJECT]
    if rejecting:
        return Resolution(ACTION_REJECT, rejecting[0].arbiter, ordered)
    return Resolution(ACTION_ESCALATE, ordered[0].arbiter, ordered)
