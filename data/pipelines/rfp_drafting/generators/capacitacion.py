"""Generador de la sección de Corporate Training (Elena Vargas)."""

from rfp_drafting.generators.base import DepartmentGenerator

GENERATOR = DepartmentGenerator(
    department_id="capacitacion",
    section_title="Programa de formación corporativa",
    outline=(
        "Programa(s) de formación aplicables a lo que pide la RFP.",
        "Duración (4, 8 o 12 semanas) y calendario propuesto.",
        "Modalidad (presencial, virtual o híbrida) y justificación.",
        "Número de participantes y organización en grupos.",
        "Inversión (o 'cotización personalizada' cuando corresponda).",
        "Garantía de satisfacción de 90 días.",
    ),
    knowledge_queries=(
        "programas de formación corporativa duración y precio por grupo",
        "formación en liderazgo modalidad presencial virtual híbrida",
    ),
    specific_rules=(
        "Usa solo los formatos de programa de Nexova (4, 8 o 12 semanas).",
        "Grupos de más de 15 personas requieren cotización personalizada: no inventes un precio fijo; "
        "propón cómo dividirlos en grupos y deja el importe a confirmar.",
        "Si la RFP también pide selección, la formación no debe empezar antes de que se cierren las "
        "posiciones (≥ 15 días hábiles): menciónalo al proponer el calendario.",
    ),
)
