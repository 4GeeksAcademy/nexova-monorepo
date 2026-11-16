"""Generador de la sección de Customer Support outsourcing (Roberto Díaz)."""

from rfp_drafting.generators.base import DepartmentGenerator

GENERATOR = DepartmentGenerator(
    department_id="soporte",
    section_title="Outsourcing de soporte al cliente",
    outline=(
        "Dotación de agentes (la que pide la RFP; si es aproximada, dilo).",
        "Turnos y cobertura horaria (p. ej. 24/7 por rotación).",
        "SLA de respuesta comprometido: 24 horas.",
        "Supervisión de calidad y reporting mensual de SLAs.",
        "Tarifa mensual por agente, contrato mínimo y condiciones.",
        "Garantía de satisfacción de 90 días.",
    ),
    knowledge_queries=(
        "tarifa mensual por agente outsourcing de soporte contrato mínimo",
        "outsourcing de equipos de soporte al cliente tamaño mínimo de equipo SLAs",
    ),
    specific_rules=(
        "Menciona de forma explícita el 'SLA de respuesta de 24 horas'. Si propones tiempos de "
        "primera respuesta más cortos, que sean adicionales, nunca en sustitución del SLA de 24 horas.",
        "No cambies el número de agentes que pide el cliente; si es aproximado ('unos 12'), "
        "preséntalo como dimensionamiento a validar.",
    ),
)
