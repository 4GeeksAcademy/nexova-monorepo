"""
Schemas Pydantic de respuesta para el dominio `incidents`.

Reflejan exactamente la forma que produce
`incidents_analyzer.analyzer.build_summary()`, para que
`POST /api/incidents/analyze` deje de devolver un `dict` sin tipar.
"""

from __future__ import annotations

from pydantic import BaseModel


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
