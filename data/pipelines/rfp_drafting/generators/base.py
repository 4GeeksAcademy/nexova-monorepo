"""
Base común de los generadores. Cada departamento tiene su propio generador
(`seleccion.py`, `capacitacion.py`, `soporte.py`) con su prompt, su esquema de
sección y sus consultas a la base de conocimiento; aquí solo vive lo que
comparten: las reglas de empresa del BRIEFING §5 y el formato de entrada/salida.

Entrada: SOLO el handoff de la Parte 1 (cabecera común + workstream del
departamento). Nunca el PDF ni el Markdown de la RFP.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from rfp_drafting.knowledge import format_knowledge
from rfp_drafting.schemas import GeneratedSection
from rfp_intake.departments import DEPARTMENTS
from rfp_intake.llm import LlmFn


def currency_instructions(currency: str | None) -> str:
    if currency == "EUR":
        return (
            "Moneda: EUR (cliente con sede en España). Todas las cifras económicas en EUR. Las tarifas "
            "de referencia internas están en USD: NO las cites en USD ni las conviertas con un tipo de "
            "cambio inventado; expresa los honorarios como porcentaje o como 'importe en EUR a "
            "confirmar'. No escribas 'USD', '$' ni 'dólares'."
        )
    if currency == "USD":
        return (
            "Moneda: USD (cliente con sede en Miami/EE. UU.). Todas las cifras económicas en USD. "
            "No escribas 'EUR', '€' ni 'euros'."
        )
    return (
        "Moneda: SIN DETERMINAR (sede del cliente desconocida). No des cifras económicas; indica que "
        "la moneda se confirmará con el cliente."
    )


COMPANY_RULES = """Lineamientos obligatorios de Nexova (se verificarán automáticamente):
1. Incluye SIEMPRE la garantía de satisfacción estándar de Nexova a 90 días (con las palabras \
"garantía de satisfacción de 90 días"). No la confundas con la garantía de reemplazo de 6 meses \
del modelo de tarifas: si mencionas esta, hazlo aparte.
2. {currency}
3. No nombres clientes actuales de Nexova como referencia: usa "un cliente del sector retail" o similar.
4. No inventes volúmenes, plazos ni precios que no estén en los datos o en la base de \
conocimiento: si falta un dato, decláralo como supuesto "a confirmar con el cliente".
5. Escribe en español, en tono profesional y claro, con frases cortas (máx. ~25 palabras) y \
entre 150 y 450 palabras en total."""


@dataclass(frozen=True)
class DepartmentGenerator:
    department_id: str
    section_title: str
    outline: tuple[str, ...]
    knowledge_queries: tuple[str, ...]
    specific_rules: tuple[str, ...]

    @property
    def owner(self) -> str:
        return DEPARTMENTS[self.department_id].owner

    def system_prompt(self, currency: str | None) -> str:
        dept = DEPARTMENTS[self.department_id]
        outline = "\n".join(f"- {item}" for item in self.outline)
        specific = "\n".join(f"- {rule}" for rule in self.specific_rules)
        return f"""Eres el redactor de la sección "{self.section_title}" de la propuesta económica de \
Nexova Solutions, en nombre del departamento {dept.name} (responsable: {dept.owner}).

Redacta SOLO la sección de tu departamento a partir del análisis de la RFP que recibes \
(metadatos y aspectos clave de la Parte 1) y de los fragmentos de la base de conocimiento.

Estructura de la sección:
{outline}

Reglas de tu departamento:
{specific}

{COMPANY_RULES.format(currency=currency_instructions(currency))}

Responde SOLO con JSON:
{{"title": "{self.section_title}", "content": "sección en Markdown (sin el título)", \
"assumptions": ["supuesto a confirmar con el cliente"]}}"""

    def user_prompt(
        self, *, header: dict[str, Any], workstream: dict[str, Any], knowledge: list[dict[str, Any]],
        previous_draft: str | None, feedback: str | None, iteration: int,
        revision: dict[str, Any] | None = None,
    ) -> str:
        analysis = {
            "cliente": header.get("client_name"),
            "sede": header.get("client_hq"),
            "moneda": header.get("currency"),
            # Es el plazo para ENTREGAR la propuesta, no una fecha de ejecución del servicio.
            "fecha_limite_entrega_de_la_propuesta": header.get("deadline"),
            "servicios_solicitados": header.get("services_requested"),
            "aspectos_clave": workstream.get("key_aspects"),
            "peticiones_al_responsable": workstream.get("requests_to_owner"),
            "preguntas_abiertas": workstream.get("open_questions"),
            "cifras_citadas_de_la_rfp": workstream.get("figures_cited"),
        }
        parts = [
            "Análisis de la RFP (handoff de la Parte 1):",
            json.dumps(analysis, ensure_ascii=False, indent=1, default=str),
            "",
            "Base de conocimiento de Nexova:",
            format_knowledge(knowledge),
            "",
            "Responde a CADA aspecto clave y a CADA petición al responsable: con contenido concreto o "
            "como supuesto a confirmar con el cliente. La fecha límite es la de entrega de la "
            "propuesta: no la uses como fecha de ejecución del servicio.",
        ]
        if revision:
            # Parte 3: el responsable (o el árbitro fijo) pidió cambios en la aprobación humana.
            parts += ["", f"Iteración {iteration}. Revisión pedida en la aprobación humana por "
                          f"{revision.get('reviewer') or 'el responsable'}."]
            if revision.get("previous_draft"):
                parts += ["Borrador anterior (corrígelo):", revision["previous_draft"]]
            else:
                parts += ["El borrador anterior fue rechazado: redacta la sección desde cero."]
            parts += ["", "Comentarios de la revisión (corrige TODO lo indicado):", revision.get("comments") or ""]
        elif iteration > 1 and previous_draft:
            parts += [
                "",
                f"Iteración {iteration}. Tu borrador anterior NO pasó la evaluación.",
                "Borrador anterior:",
                previous_draft,
                "",
                "Feedback del evaluador (corrige TODO lo indicado):",
                feedback or "",
            ]
        return "\n".join(parts)

    def render(self, section: GeneratedSection) -> str:
        """Borrador final en Markdown: título, cuerpo y supuestos a confirmar."""
        # El modelo a veces repite un título propio al principio del contenido.
        body = re.sub(r"\A\s*#{1,6}[^\n]*\n+", "", section.content.strip()).strip()
        if section.assumptions:
            body += "\n\n**Supuestos a confirmar con el cliente:**\n" + "\n".join(
                f"- {a}" for a in section.assumptions
            )
        return f"## {section.title or self.section_title}\n\n{body}"

    def generate(
        self, llm: LlmFn, *, header: dict[str, Any], workstream: dict[str, Any],
        knowledge: list[dict[str, Any]], previous_draft: str | None = None,
        feedback: str | None = None, iteration: int = 1, revision: dict[str, Any] | None = None,
    ) -> str:
        """`revision` (Parte 3): `{previous_draft, comments, reviewer}` de la aprobación humana."""
        system = self.system_prompt(header.get("currency"))
        user = self.user_prompt(header=header, workstream=workstream, knowledge=knowledge,
                                previous_draft=previous_draft, feedback=feedback, iteration=iteration,
                                revision=revision)
        raw = llm(f"generate:{self.department_id}", system, user)
        try:
            return self.render(GeneratedSection.model_validate(unwrap_section(raw)))
        except ValidationError:
            # Una sola repetición pidiendo el formato exacto; si vuelve a fallar, el error sube (Celery).
            raw = llm(f"generate:{self.department_id}", system,
                      user + '\n\nTu respuesta anterior no tenía el formato pedido. Devuelve SOLO '
                             '{"title": ..., "content": ..., "assumptions": [...]}.')
            return self.render(GeneratedSection.model_validate(unwrap_section(raw)))


def unwrap_section(raw: Any) -> Any:
    """Busca el objeto de la sección si el modelo lo devolvió anidado o como texto JSON."""
    if isinstance(raw, dict) and isinstance(raw.get("content"), str) and (
        "title" in raw or "assumptions" in raw or not raw.get("content", "").lstrip().startswith("{")
    ):
        return raw
    stack: list[Any] = [raw]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if isinstance(item.get("content"), str) and ("title" in item or "assumptions" in item):
                return item
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, str) and item.lstrip().startswith("{"):
            try:
                stack.append(json.loads(item))
            except ValueError:
                continue
    return raw
