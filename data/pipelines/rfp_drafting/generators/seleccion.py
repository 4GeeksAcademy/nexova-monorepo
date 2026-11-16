"""Generador de la sección de Talent Selection Operations (Javier Almeida)."""

from rfp_drafting.generators.base import DepartmentGenerator

GENERATOR = DepartmentGenerator(
    department_id="seleccion",
    section_title="Búsqueda y selección de talento",
    outline=(
        "Roles a cubrir (solo los de la RFP; si no detalla todos, indícalo como supuesto).",
        "Metodología: búsqueda directa, evaluación por competencias, verificación de referencias.",
        "Tiempo estimado de cierre por posición y calendario (shortlist / terna final).",
        "Horas de consultoría estimadas.",
        "Honorarios y calendario de facturación.",
        "Garantía de satisfacción de 90 días.",
    ),
    knowledge_queries=(
        "tarifa headhunting ejecutivo porcentaje del salario y facturación",
        "SLA del proceso de contratación tiempos de entrega de la terna",
    ),
    specific_rules=(
        "El tiempo de cierre de una búsqueda ejecutiva NUNCA puede ser menor a 15 días hábiles: no "
        "prometas plazos más cortos aunque el cliente los pida; si el cliente pide algo menor, "
        "explica el plazo realista.",
        "Si el cliente pide un primer shortlist en un plazo concreto, distingue shortlist de cierre.",
        "Los honorarios se expresan como porcentaje del salario anual bruto del cargo.",
    ),
)
