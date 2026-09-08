"""
Schemas Pydantic de request/response para inventario (Hito 5, Nexova).

Separados de los modelos ORM (`models.py`): un endpoint nunca debe devolver
un objeto SQLModel directamente.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

VALID_ASSET_CATEGORIES = [
    "hardware",
    "peripherals",
    "office_supplies",
    "training_materials",
]

VALID_OFFICES = ["Valencia", "Miami"]


class AssetCreate(BaseModel):
    name: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    category: str
    office: str

    model_config = ConfigDict(str_strip_whitespace=True)


class AssetRead(BaseModel):
    id: int
    name: str
    sku: str
    category: str
    office: str
    current_stock: int

    model_config = ConfigDict(from_attributes=True)


class AssetEntryCreate(BaseModel):
    asset_id: int
    quantity: int = Field(gt=0)
    supplier: str = Field(min_length=1)
    office: str

    model_config = ConfigDict(str_strip_whitespace=True)


class AssetEntryRead(BaseModel):
    id: int
    asset_id: int
    quantity: int
    supplier: str
    office: str
    created_at: datetime
    user_uuid: str

    model_config = ConfigDict(from_attributes=True)


class AssetExitCreate(BaseModel):
    asset_id: int
    quantity: int = Field(gt=0)
    exit_type: Literal["allocation", "consumption"]
    assigned_to: str | None = None
    office: str

    model_config = ConfigDict(str_strip_whitespace=True)

    @model_validator(mode="after")
    def validate_assigned_to(self) -> "AssetExitCreate":
        if self.exit_type == "allocation" and not self.assigned_to:
            raise ValueError(
                "'assigned_to' es obligatorio cuando exit_type='allocation'."
            )
        if self.exit_type == "consumption" and self.assigned_to is not None:
            raise ValueError(
                "'assigned_to' debe ser nulo cuando exit_type='consumption'."
            )
        return self


class AssetExitRead(BaseModel):
    id: int
    asset_id: int
    quantity: int
    exit_type: str
    assigned_to: str | None
    office: str
    created_at: datetime
    user_uuid: str
    current_stock: int

    model_config = ConfigDict(from_attributes=True)


class AssetSummary(BaseModel):
    """Datos mínimos del activo embebidos en `OrderRead`."""

    id: int
    name: str
    sku: str

    model_config = ConfigDict(from_attributes=True)


class OrderRead(BaseModel):
    """Fila unificada para `GET /inventory/orders` (entradas + salidas)."""

    order_type: Literal["inbound", "outbound"]
    id: int
    asset: AssetSummary
    quantity: int
    office: str
    created_at: datetime
    user_uuid: str
    supplier: str | None = None
    exit_type: str | None = None
    assigned_to: str | None = None
