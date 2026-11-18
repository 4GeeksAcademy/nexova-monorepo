"""Contratos Pydantic del gestor de incidencias (sin coerción silenciosa)."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nexova_shared.incident_domain import (
    DESCRIPTION_MAX_LENGTH,
    TITLE_MAX_LENGTH,
    Branch,
    Category,
    Origin,
    Status,
)


class IncidentCreate(BaseModel):
    """Alta de incidencia. El estado inicial es siempre `open`."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=TITLE_MAX_LENGTH)
    description: str = Field(min_length=1, max_length=DESCRIPTION_MAX_LENGTH)
    category: Category
    origin: Origin
    branch: Branch


class IncidentStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Status


class IncidentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str
    category: Category
    status: Status
    origin: Origin
    branch: Branch
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def _as_utc(cls, value: datetime) -> datetime:
        # SQLite devuelve fechas sin zona; en Postgres ya vienen con ella.
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class IncidentSummary(BaseModel):
    """Métricas agregadas; todas las claves existen aunque no haya incidencias."""

    total: int
    by_status: dict[str, int]
    by_category: dict[str, int]
    by_origin: dict[str, int]
    by_branch: dict[str, int]
