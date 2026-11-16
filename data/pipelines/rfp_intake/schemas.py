"""
Contratos Pydantic de la salida de cada agente.

Toda respuesta del modelo se valida aquí antes de tocar el estado o la base de
datos: un campo con forma inesperada se normaliza o se descarta, nunca se
persiste tal cual.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if item is not None and str(item).strip()]


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dict_list(value: Any) -> list[Any]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _confidence(value: Any) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.5


def _rfp_type(value: Any) -> str:
    value = str(value or "").strip().lower()
    return value if value in ("formal", "informal", "no_rfp") else "no_rfp"


def _text(value: Any) -> str:
    return str(value or "").strip()


StrList = Annotated[list[str], BeforeValidator(_str_list)]
OptStr = Annotated[str | None, BeforeValidator(_opt_str)]
Text = Annotated[str, BeforeValidator(_text)]


class Classification(BaseModel):
    is_rfp: bool
    rfp_type: Annotated[Literal["formal", "informal", "no_rfp"], BeforeValidator(_rfp_type)] = "no_rfp"
    confidence: Annotated[float, BeforeValidator(_confidence)] = 0.5
    reason: Text = ""
    document_kind: Text = ""


class Figure(BaseModel):
    """Cifra citada del documento: `quote` debe aparecer literalmente en la RFP."""

    concept: Text
    value: Text
    quote: Text


FigureList = Annotated[list[Figure], BeforeValidator(_dict_list)]


class ExtractedMetadata(BaseModel):
    client_name: OptStr = None
    client_location: OptStr = None
    rfp_reference: OptStr = None
    services_requested: StrList = Field(default_factory=list)
    scope: OptStr = None
    volumes: FigureList = Field(default_factory=list)
    deadline: OptStr = None
    deadline_text: OptStr = None
    budget_range: OptStr = None
    client_contact: OptStr = None


class DepartmentAssignment(BaseModel):
    department_id: Annotated[str, BeforeValidator(lambda v: _text(v).lower())]
    reason: Text = ""
    services: StrList = Field(default_factory=list)
    excerpts: StrList = Field(default_factory=list)


class OrchestratorPlan(BaseModel):
    departments: Annotated[list[DepartmentAssignment], BeforeValidator(_dict_list)] = Field(
        default_factory=list
    )
    unmapped_services: StrList = Field(default_factory=list)


class WorkerOutput(BaseModel):
    key_aspects: StrList = Field(default_factory=list)
    requests_to_owner: StrList = Field(default_factory=list)
    open_questions: StrList = Field(default_factory=list)
    figures_cited: FigureList = Field(default_factory=list)


class SynthesisOutput(BaseModel):
    summary_for_sales: Text = ""
    next_steps: StrList = Field(default_factory=list)
