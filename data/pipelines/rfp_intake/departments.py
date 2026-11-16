"""
Catálogo cerrado de departamentos y vocabulario de estados del ticket RFP.

Fuente: BRIEFING del Hito 9, §2.1 (departamentos) y §2.3 (estados). Ningún
agente puede crear departamentos nuevos: lo que no encaja aquí acaba en
`unmapped_services` (ver `nodes.orchestrate`).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Department:
    department_id: str
    name: str
    owner: str
    contributes: str
    # Pistas para recuperar extractos si el orquestador no aporta citas válidas.
    keywords: tuple[str, ...]


DEPARTMENTS: dict[str, Department] = {
    "seleccion": Department(
        department_id="seleccion",
        name="Talent Selection Operations",
        owner="Javier Almeida",
        contributes="Roles a cubrir, tiempo estimado de cierre, horas de consultoría necesarias",
        keywords=(
            "búsqueda", "busqueda", "selección", "seleccion", "headhunting", "reclutamiento",
            "posiciones", "puestos", "vacantes", "roles", "contratar", "hiring", "hire",
            "recruit", "in place", "mandos",
        ),
    ),
    "capacitacion": Department(
        department_id="capacitacion",
        name="Corporate Training",
        owner="Elena Vargas",
        contributes="Programas de formación aplicables, duración, modalidad",
        keywords=(
            "formación", "formacion", "capacitación", "capacitacion", "training", "programa",
            "liderazgo", "participantes", "curso", "taller", "workshop", "onboarding",
        ),
    ),
    "soporte": Department(
        department_id="soporte",
        name="Customer Support (outsourcing)",
        owner="Roberto Díaz",
        contributes="Dotación de agentes, turnos, SLA de respuesta comprometido",
        keywords=(
            "soporte", "support", "agentes", "agents", "24/7", "call center", "atención al cliente",
            "customer service", "helpdesk", "turnos", "shifts", "response time",
        ),
    ),
}

DEPARTMENT_IDS: tuple[str, ...] = tuple(DEPARTMENTS)

# Vocabulario completo del ticket (Partes 1–3). La Parte 1 solo usa los tres primeros.
STATUS_ANALYZING = "analizando"
STATUS_DISCARDED = "descartado"
STATUS_ANALYSIS_COMPLETE = "analisis_completo"
# Parte 2 (generación y evaluación).
STATUS_GENERATING = "generando_borrador"
STATUS_EVALUATING = "en_evaluación"
STATUS_NEEDS_HUMAN_REVIEW = "needs_human_review"

# Estado de cada sección durante el ciclo generador-evaluador.
SECTION_GENERATING = "generando"
SECTION_EVALUATING = "evaluando"
SECTION_PASSED = "evaluada_ok"
SECTION_NEEDS_HUMAN_REVIEW = "needs_human_review"
SECTION_STATUSES: tuple[str, ...] = (
    SECTION_GENERATING,
    SECTION_EVALUATING,
    SECTION_PASSED,
    SECTION_NEEDS_HUMAN_REVIEW,
)
TICKET_STATUSES: tuple[str, ...] = (
    STATUS_ANALYZING,
    STATUS_DISCARDED,
    STATUS_ANALYSIS_COMPLETE,
    "generando_borrador",
    "en_evaluación",
    "needs_human_review",
    "esperando_aprobación",
    "terminado",
)

# Sede del cliente → moneda de la propuesta (BRIEFING §5).
HQ_SPAIN = "España"
HQ_MIAMI = "Miami"
HQ_UNKNOWN = "desconocido"
CURRENCY_BY_HQ: dict[str, str] = {HQ_SPAIN: "EUR", HQ_MIAMI: "USD"}


def department_card(department_id: str) -> dict[str, str]:
    """Datos públicos de un departamento (lo que ve Ventas)."""
    dept = DEPARTMENTS[department_id]
    return {
        "department_id": dept.department_id,
        "name": dept.name,
        "owner": dept.owner,
        "contributes": dept.contributes,
    }
