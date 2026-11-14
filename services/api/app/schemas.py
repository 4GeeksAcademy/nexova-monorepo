"""
Schemas Pydantic de respuesta para el dominio `incidents`.

Reflejan exactamente la forma que produce
`incidents_analyzer.analyzer.build_summary()`, para que
`POST /api/incidents/analyze` deje de devolver un `dict` sin tipar.
"""

from __future__ import annotations

import datetime as dt
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Mismos valores que `incidents_analyzer.VALID_CATEGORIES` / `VALID_STATUSES`
# (comprobado en el arranque del router de incidencias).
IncidentCategory = Literal["TECHNICAL", "BILLING", "ACCESS", "HR_QUERY", "COMPLAINT"]
IncidentStatus = Literal["OPEN", "CLOSED", "DISCARDED"]


class InvalidBreakdownItem(BaseModel):
    rule: str
    label: str
    count: int


class CategoryBreakdownItem(BaseModel):
    category: str
    count: int
    percentage: float


class StatusBreakdownItem(BaseModel):
    status: str
    count: int
    percentage: float


class SatisfactionDistributionItem(BaseModel):
    score: int
    label: str
    count: int


class SatisfactionSummary(BaseModel):
    closed_tickets: int
    scored_tickets: int
    average: float
    distribution: list[SatisfactionDistributionItem]


class IncidentAnalysisSummary(BaseModel):
    source_file: str
    total_records: int
    valid_records: int
    invalid_records: int
    invalid_breakdown: list[InvalidBreakdownItem]
    category_breakdown: list[CategoryBreakdownItem]
    status_breakdown: list[StatusBreakdownItem]
    satisfaction: SatisfactionSummary


class IncidentRead(BaseModel):
    """Ticket individual del gestor de incidentes. Nunca incluye `customer_email`."""

    model_config = ConfigDict(from_attributes=True)

    ticket_id: str
    date: date
    client_company: str
    category: str
    description: str
    agent_id: str
    status: str
    satisfaction_score: int | None = None
    updated_at: datetime


class IncidentCreate(BaseModel):
    """Alta de un ticket. El id y el estado (`OPEN`) los asigna el servidor."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    client_company: str = Field(min_length=1, max_length=100)
    category: IncidentCategory
    description: str = Field(min_length=1, max_length=2000)
    agent_id: str = Field(min_length=1, max_length=50)
    # `dt.date` y no `date`: el campo se llama igual que el tipo y, con valor
    # por defecto, el nombre quedaría ligado al `FieldInfo` en la clase.
    date: dt.date | None = Field(default=None, description="Fecha del ticket; por defecto hoy (UTC).")


class IncidentStatusUpdate(BaseModel):
    """Cuerpo de `PATCH /api/incidents/{ticket_id}/status`."""

    model_config = ConfigDict(extra="forbid")

    status: IncidentStatus
