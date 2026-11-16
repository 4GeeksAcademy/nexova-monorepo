"""
Contratos de la Parte 2: salida de los generadores y `EvaluationResult`.

`EvaluationResult` sigue la forma que pide el ticket (legibilidad / pertinencia /
cumplimiento estructurados + `overall_pass` + `feedback_for_generator`). Se
persiste tal cual en `rfp_department_section.evaluation_results`.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field


def _text(value: Any) -> str:
    return str(value or "").strip()


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if item is not None and str(item).strip()]


Text = Annotated[str, BeforeValidator(_text)]
StrList = Annotated[list[str], BeforeValidator(_str_list)]


class GeneratedSection(BaseModel):
    """Salida JSON de un generador de departamento."""

    title: Text = ""
    content: Text
    assumptions: StrList = Field(default_factory=list)


class ReadabilityCheck(BaseModel):
    passed: bool = Field(serialization_alias="pass")
    score: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class RelevanceCheck(BaseModel):
    passed: bool = Field(serialization_alias="pass")
    missing_aspects: list[str] = Field(default_factory=list)
    covered_aspects: list[str] = Field(default_factory=list)


class Violation(BaseModel):
    rule_id: str
    message: str
    evidence: str | None = None


class ComplianceCheck(BaseModel):
    passed: bool = Field(serialization_alias="pass")
    rule_ids: list[str] = Field(default_factory=list)  # reglas evaluadas
    violations: list[Violation] = Field(default_factory=list)


class EvaluationResult(BaseModel):
    section_id: str
    department_id: str
    iteration: int
    readability: ReadabilityCheck
    relevance: RelevanceCheck
    compliance: ComplianceCheck
    overall_pass: bool
    feedback_for_generator: str

    def to_record(self) -> dict[str, Any]:
        """Forma persistida/expuesta: claves `pass` como pide el contrato."""
        return self.model_dump(by_alias=True)


class AspectVerdict(BaseModel):
    aspect: Text
    covered: bool
    evidence: Text = ""


def _verdicts(value: Any) -> list[Any]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


class RelevanceJudgement(BaseModel):
    """Salida JSON del juez de pertinencia: un veredicto con evidencia por aspecto.

    Acepta también la forma resumida `missing_aspects` / `covered_aspects`.
    """

    aspects: Annotated[list[AspectVerdict], BeforeValidator(_verdicts)] = Field(default_factory=list)
    missing_aspects: StrList = Field(default_factory=list)
    covered_aspects: StrList = Field(default_factory=list)

    def missing(self) -> list[str]:
        if self.aspects:
            # Un "cubierto" sin cita del borrador no se acepta: el juez debe señalar dónde.
            return [v.aspect for v in self.aspects if not v.covered or not v.evidence]
        return self.missing_aspects

    def covered(self) -> list[str]:
        if self.aspects:
            return [v.aspect for v in self.aspects if v.covered and v.evidence]
        return self.covered_aspects
