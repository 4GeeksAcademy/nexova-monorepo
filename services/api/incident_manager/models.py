"""
Modelo del gestor de incidencias (tabla `incident_records`).

Los valores permitidos viven en `nexova_shared.incident_domain` (BRIEFING) y
se aplican también en la base de datos con `CHECK`, de modo que ni un script ni
un insert manual puedan guardar un valor fuera de contrato.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime
from sqlmodel import Field, SQLModel

from nexova_shared.incident_domain import (
    BRANCHES,
    CATEGORIES,
    DESCRIPTION_MAX_LENGTH,
    ORIGINS,
    STATUSES,
    TITLE_MAX_LENGTH,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


class ManagedIncident(SQLModel, table=True):
    __tablename__ = "incident_records"
    __table_args__ = (
        CheckConstraint(_in("status", STATUSES), name="ck_incident_records_status"),
        CheckConstraint(_in("category", CATEGORIES), name="ck_incident_records_category"),
        CheckConstraint(_in("origin", ORIGINS), name="ck_incident_records_origin"),
        CheckConstraint(_in("branch", BRANCHES), name="ck_incident_records_branch"),
        CheckConstraint("length(trim(title)) > 0", name="ck_incident_records_title"),
        CheckConstraint("length(trim(description)) > 0", name="ck_incident_records_description"),
    )

    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(max_length=TITLE_MAX_LENGTH)
    description: str = Field(max_length=DESCRIPTION_MAX_LENGTH)
    category: str = Field(index=True)
    status: str = Field(default="open", index=True)
    origin: str = Field(index=True)
    branch: str = Field(index=True)
    # Empresa cliente (opcional): conserva el aislamiento por cliente del agente
    # (auditoría OWASP C2) y alimenta la lista de clientes de RFP. Fuera de los
    # campos del README; nunca es obligatoria.
    client_company: str | None = Field(default=None, index=True, max_length=100)
    created_at: datetime = Field(
        default_factory=_utcnow, sa_type=DateTime(timezone=True), nullable=False
    )
    updated_at: datetime = Field(
        default_factory=_utcnow, sa_type=DateTime(timezone=True), nullable=False
    )


class IncidentSeedLog(SQLModel, table=True):
    """Claves del CSV ya cargadas: hace idempotente `scripts/seed_incidents.py`.

    El BRIEFING pide usar `ticket_id` solo para detectar duplicados y no
    almacenarlo en el modelo, por eso vive en una tabla aparte.
    """

    __tablename__ = "incident_seed_log"

    source_key: str = Field(primary_key=True)
    incident_id: int = Field(foreign_key="incident_records.id", index=True)
