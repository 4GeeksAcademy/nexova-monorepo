"""
Dobles de prueba del flujo `rfp_intake`: LLM falso con respuestas por tarea y
respuestas canónicas para los 3 PDF de muestra del BRIEFING
(`tests/pipelines/fixtures/rfp/`).
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "rfp"
FORMAL_PDF = FIXTURES / "nexova-request-1.pdf"    # Vantex Retail Group (Madrid)
INFORMAL_PDF = FIXTURES / "nexova-request-2.pdf"  # NubeSoft (Miami)
INVALID_PDF = FIXTURES / "nexova-request-3.pdf"   # pitch de HireStream (no es RFP)

Response = dict[str, Any] | Exception | Callable[[str, str], dict[str, Any]]


class FakeLLM:
    """`LlmFn` falso: responde según `task` y registra cada llamada."""

    def __init__(self, responses: dict[str, Response]) -> None:
        self.responses = responses
        self.calls: list[dict[str, str]] = []

    def __call__(self, task: str, system: str, user: str) -> dict[str, Any]:
        self.calls.append({"task": task, "system": system, "user": user})
        if task not in self.responses:
            raise AssertionError(f"Llamada LLM inesperada: {task}")
        response = self.responses[task]
        if isinstance(response, Exception):
            raise response
        if callable(response):
            return response(system, user)
        return copy.deepcopy(response)

    def tasks(self) -> list[str]:
        return [call["task"] for call in self.calls]

    def call(self, task: str) -> dict[str, str]:
        return next(c for c in self.calls if c["task"] == task)


FORMAL_RESPONSES: dict[str, Response] = {
    "classify": {"is_rfp": True, "rfp_type": "formal", "confidence": 0.97,
                 "document_kind": "RFP formal", "reason": "Solicitud de propuesta con alcance y calendario."},
    "metadata": {
        "client_name": "Vantex Retail Group, S.A.",
        "client_location": "Madrid, España",
        "rfp_reference": "VRG-2026-011",
        "services_requested": ["Búsqueda ejecutiva de mandos medios", "Programa de formación en liderazgo"],
        "scope": "Cubrir 5 posiciones de mandos medios y un programa trimestral de liderazgo.",
        "volumes": [
            {"concept": "posiciones de mandos medios", "value": "5",
             "quote": "Búsqueda ejecutiva para 5 posiciones de mandos medios"},
            {"concept": "participantes en formación", "value": "40",
             "quote": "formato trimestral, para 40 participantes"},
            {"concept": "presupuesto", "value": "200.000 EUR", "quote": "presupuesto máximo de 200.000 EUR"},
        ],
        "deadline": "2026-08-18",
        "deadline_text": "Proposal Due Date: August 18, 2026",
        "budget_range": None,
        "client_contact": "Ricardo Núñez, Director de Personas",
    },
    "orchestrate": {
        "departments": [
            {"department_id": "seleccion", "reason": "Búsqueda ejecutiva de 5 mandos medios.",
             "services": ["Búsqueda ejecutiva"],
             "excerpts": ["Búsqueda ejecutiva para 5 posiciones de mandos medios (Retail Operations Manager, Regional Sales Manager, entre otras)",
                          "Primer informe de shortlist esperado en un plazo de 3 semanas desde la firma del contrato"]},
            {"department_id": "capacitacion", "reason": "Programa de liderazgo trimestral.",
             "services": ["Formación en liderazgo"],
             "excerpts": ["Programa de formación en liderazgo, formato trimestral, para 40 participantes"]},
        ],
        "unmapped_services": [],
    },
    "worker:seleccion": {
        "key_aspects": ["5 posiciones de mandos medios en retail", "Shortlist en 3 semanas desde la firma"],
        "requests_to_owner": ["Estimar tiempo de cierre por posición", "Calcular horas de consultoría"],
        "open_questions": ["¿Ubicación de cada posición?"],
        "figures_cited": [{"concept": "posiciones", "value": "5",
                           "quote": "Búsqueda ejecutiva para 5 posiciones de mandos medios"}],
    },
    "worker:capacitacion": {
        "key_aspects": ["Programa trimestral de liderazgo", "40 participantes"],
        "requests_to_owner": ["Proponer programa y modalidad", "Definir duración por trimestre"],
        "open_questions": ["¿Modalidad presencial u online?"],
        "figures_cited": [{"concept": "participantes", "value": "40", "quote": "para 40 participantes"}],
    },
    "synthesize": {
        "summary_for_sales": "Vantex Retail Group (Madrid) pide búsqueda ejecutiva y formación. Propuesta en EUR.",
        "next_steps": ["Pedir a Javier Almeida el tiempo de cierre", "Pedir a Elena Vargas el programa"],
    },
}

INFORMAL_RESPONSES: dict[str, Response] = {
    "classify": {"is_rfp": True, "rfp_type": "informal", "confidence": 0.9,
                 "document_kind": "correo de cliente", "reason": "Pide una propuesta de soporte outsourced."},
    "metadata": {
        "client_name": "NubeSoft",
        "client_location": "Miami, FL, USA",
        "rfp_reference": None,
        "services_requested": ["Equipo de soporte al cliente 24/7"],
        "scope": "Outsourcing de soporte 24/7 con unos 12 agentes.",
        "volumes": [{"concept": "agentes de soporte", "value": "12",
                     "quote": "something like 12 support agents covering us 24/7"}],
        # Fallo real del modelo de 4Geeks: completa "August 23rd" con un año que el correo no dice.
        "deadline": "2025-08-23",
        "deadline_text": "say by August 23rd",
        "budget_range": None,
        "client_contact": "Derek Coleman, Co-founder & CEO",
    },
    "orchestrate": {
        "departments": [
            {"department_id": "soporte", "reason": "Pide un equipo de soporte 24/7.",
             "services": ["Soporte outsourced 24/7"],
             "excerpts": ["We think we need something like 12 support agents covering us 24/7"]},
        ],
        "unmapped_services": [],
    },
    "worker:soporte": {
        "key_aspects": ["Cobertura 24/7", "Alrededor de 12 agentes"],
        "requests_to_owner": ["Dimensionar turnos 24/7", "Confirmar SLA de respuesta"],
        "open_questions": ["¿Volumen de tickets mensual?", "¿Idiomas requeridos?"],
        "figures_cited": [{"concept": "agentes", "value": "12", "quote": "12 support agents"}],
    },
    "synthesize": {"summary_for_sales": "NubeSoft (Miami) pide soporte 24/7. Propuesta en USD.",
                   "next_steps": ["Pedir a Roberto Díaz el dimensionamiento"]},
}

def upload(store: Any, pdf: Path, created_by: str = "ventas@nexova.test") -> str:
    """Registra el PDF como lo haría la API (requiere `RFP_RAW_DIR` apuntando a tmp)."""
    from rfp_intake import register_upload

    ticket = register_upload(pdf.read_bytes(), pdf.name, created_by, store)
    return ticket["ticket_id"]


INVALID_RESPONSES: dict[str, Response] = {
    "classify": {"is_rfp": False, "rfp_type": "no_rfp", "confidence": 0.96,
                 "document_kind": "pitch de proveedor", "reason": "Un proveedor de ATS ofrece su producto a Nexova."},
}
