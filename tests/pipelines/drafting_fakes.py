"""
Dobles de prueba de la Parte 2 (`rfp_drafting`): ticket listo con el handoff
de la Parte 1, borradores canónicos que pasan o fallan, y LLM/RAG falsos.
"""

from __future__ import annotations

from typing import Any

from rfp_fakes import FakeLLM

SELECCION_WS = {
    "department_id": "seleccion",
    "owner": "Javier Almeida",
    "key_aspects": ["5 posiciones de mandos medios en retail", "Primer shortlist en 3 semanas"],
    "requests_to_owner": ["Estimar el tiempo de cierre por posición", "Dimensionar las horas de consultoría"],
    "open_questions": ["¿Perfil detallado de cada posición?"],
    "figures_cited": [{"concept": "posiciones", "value": "5", "quote": "5 posiciones de mandos medios"}],
    "excerpts": ["Búsqueda ejecutiva para 5 posiciones de mandos medios"],
}
CAPACITACION_WS = {
    "department_id": "capacitacion",
    "owner": "Elena Vargas",
    "key_aspects": ["Programa de liderazgo trimestral", "40 participantes"],
    "requests_to_owner": ["Proponer modalidad del programa"],
    "open_questions": [],
    "figures_cited": [],
    "excerpts": ["Programa de formación en liderazgo, formato trimestral, para 40 participantes"],
}
SOPORTE_WS = {
    "department_id": "soporte",
    "owner": "Roberto Díaz",
    "key_aspects": ["12 agentes con cobertura 24/7"],
    "requests_to_owner": ["Dimensionar turnos 24/7"],
    "open_questions": ["¿Volumen de tickets mensual?"],
    "figures_cited": [],
    "excerpts": ["12 support agents covering us 24/7"],
}


def make_ready_ticket(store: Any, workstreams: list[dict[str, Any]], *, client_hq: str = "España",
                      currency: str | None = "EUR", ticket_id: str = "RFP-P2TEST") -> str:
    """Ticket en `analisis_completo` con handoff `rfp_intake.v1`, como lo deja la Parte 1."""
    store.create_ticket(ticket_id=ticket_id, raw_pdf_path=f"data/raw/rfp/{ticket_id}.pdf",
                        original_filename="rfp.pdf", created_by=None)
    for ws in workstreams:
        store.upsert_section(ticket_id, {
            "department_id": ws["department_id"], "owner_name": ws["owner"],
            "key_aspects": ws["key_aspects"], "requests_to_owner": ws["requests_to_owner"],
            "open_questions": ws["open_questions"], "figures_cited": ws["figures_cited"],
            "excerpts": ws["excerpts"],
        })
    handoff = {
        "contract_version": "rfp_intake.v1", "ticket_id": ticket_id, "rfp_id": "VRG-2026-011",
        "client_name": "Vantex Retail Group, S.A.", "client_hq": client_hq, "currency": currency,
        "deadline": "2026-08-18", "services_requested": ["Búsqueda ejecutiva", "Formación"],
        "workstreams": workstreams, "conflicts": [], "unmapped_services": [], "cost_tier": "medio",
    }
    store.complete(ticket_id, {"summary_for_sales": "..."}, handoff)
    return ticket_id


_COMMON_TAIL = (
    "Incluimos la garantía de satisfacción de 90 días de Nexova. "
    "Si en ese plazo el cliente no está satisfecho, revisamos el servicio sin coste. "
    "Los datos que faltan se confirmarán con el cliente en la primera reunión. "
    "Nuestro equipo enviará un informe de avance cada semana. "
    "El responsable del proyecto será el punto de contacto único."
)

GOOD_SELECCION = (
    "## Búsqueda y selección de talento\n\n"
    "Cubriremos las 5 posiciones de mandos medios que pide la RFP. "
    "El proceso incluye búsqueda directa, entrevistas por competencias y verificación de referencias. "
    "Entregaremos el primer shortlist en 3 semanas desde la firma del contrato. "
    "Estimamos el cierre de cada posición entre 30 y 40 días hábiles. "
    "Calculamos unas 60 horas de consultoría por posición. "
    "Los honorarios son el 22 % del salario anual bruto de cada cargo, facturados en EUR. "
    "La factura se divide en tres pagos: inicio, terna final y contratación. "
    "Trabajamos con experiencia previa con un cliente del sector retail. "
    "El perfil detallado de cada posición se validará con el cliente. "
    + _COMMON_TAIL
)

# Igual, pero promete cerrar en 10 días hábiles: viola ttc-min-15-dias-habiles (BRIEFING §5).
BAD_TTC_SELECCION = GOOD_SELECCION.replace(
    "Estimamos el cierre de cada posición entre 30 y 40 días hábiles.",
    "Garantizamos el cierre de cada posición en 10 días hábiles.",
)

GOOD_CAPACITACION = (
    "## Programa de formación corporativa\n\n"
    "Proponemos el programa de liderazgo de 12 semanas para los 40 participantes. "
    "El programa se imparte en modalidad híbrida, con sesiones en vivo y trabajo práctico. "
    "Dividiremos a los participantes en tres grupos de unas 14 personas. "
    "Los grupos de más de 15 personas requieren una cotización personalizada en EUR. "
    "Cada edición trimestral incluye un diagnóstico inicial y un certificado final. "
    "La formación empezará cuando estén cerradas las nuevas posiciones de mandos medios. "
    "La modalidad final se acordará con el cliente antes del inicio. "
    + _COMMON_TAIL
)

GOOD_SOPORTE = (
    "## Outsourcing de soporte al cliente\n\n"
    "Proponemos un equipo de 12 agentes con cobertura 24/7 por turnos rotativos. "
    "Habrá tres turnos de ocho horas con un supervisor de calidad. "
    "Nos comprometemos a un SLA de respuesta de 24 horas para todas las consultas. "
    "Cada mes enviaremos un informe de cumplimiento de SLAs. "
    "La tarifa mensual por agente está entre 1.800 y 2.400 USD según la especialización. "
    "El contrato mínimo es de 6 meses. "
    "El volumen mensual de tickets se confirmará con el cliente. "
    + _COMMON_TAIL
)

PASS_RELEVANCE = {"missing_aspects": [], "covered_aspects": ["todo"]}


class Sequence:
    """Respuestas sucesivas para la misma tarea (una por iteración)."""

    def __init__(self, *responses: dict[str, Any]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, system: str, user: str) -> dict[str, Any]:
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


def section(draft: str) -> dict[str, Any]:
    """Respuesta JSON de un generador a partir de un borrador ya renderizado."""
    title, _, content = draft.partition("\n\n")
    return {"title": title.removeprefix("## ").strip(), "content": content, "assumptions": []}


def fake_llm(**responses: Any) -> FakeLLM:
    """`fake_llm(generate_seleccion=..., relevance_seleccion=...)` → tareas `generate:seleccion`, …"""
    return FakeLLM({k.replace("_", ":", 1): v for k, v in responses.items()})


def no_knowledge(query: str) -> list[dict[str, Any]]:
    return []
