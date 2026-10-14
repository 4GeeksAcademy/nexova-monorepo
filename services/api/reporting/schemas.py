"""Modelos de respuesta de `/reporting`. El de KPIs es el contrato del CONTEXT §6."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class WeeklyPerformanceEntry(BaseModel):
    office: str
    programme_id: str
    total_material_cost: float
    kits_delivered_count: int
    shortage_events_count: int
    cost_variance_events_count: int
    currency: str


class WeeklyPerformanceReport(BaseModel):
    week_start: date
    entries: list[WeeklyPerformanceEntry]


class PipelineRunRead(BaseModel):
    run_id: str
    pipeline_name: str
    trigger: Literal["schedule", "manual"]
    status: Literal["running", "completed", "failed", "skipped"]
    phase: Literal["extract", "transform", "load"] | None
    window_start: datetime
    window_end: datetime
    started_at: datetime
    finished_at: datetime | None
    rows_read: int
    rows_written: int
    rows_revised: int
    rows_missing_cost: int
    rows_unassigned_programme: int
    rows_invalid_office: int
    rows_duplicates_dropped: int
    error_message: str | None
    prefect_flow_run_id: str | None


class PipelineRunRequest(BaseModel):
    week_start: date | None = Field(
        default=None, description="Lunes de la última semana a recalcular. Por defecto, la actual."
    )
    lookback_weeks: int = Field(default=2, ge=1, le=52)


class PipelineRunAccepted(BaseModel):
    run_id: str
    status: Literal["running"]
    window_start: datetime
    window_end: datetime
