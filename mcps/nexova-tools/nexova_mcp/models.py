"""
Contratos de entrada y salida de las tools.

Los nombres de campo y los valores de dominio son los de la API de Nexova
(`services/api/app/schemas.py` y `services/api/schemas.py`): ids de ticket
`NXV-NNNNNN`, estados `OPEN|CLOSED|DISCARDED`, cinco categorías fijas y
productos con `current_stock` frente a `min_stock_threshold`.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, BeforeValidator, Field, computed_field

TICKET_ID_PATTERN = r"^NXV-\d{6}$"


def _is_blank(value: Any) -> bool:
    return isinstance(value, str) and not value.strip()


def blank_as(default: Any) -> BeforeValidator:
    """
    Muchos clientes MCP con formularios (p. ej. MCP Playground) envían los
    parámetros opcionales que no se rellenan como `""` en vez de omitirlos.
    Con este validador, `""` (o solo espacios) equivale a no informado y toma
    `default`. No cambia el JSON Schema publicado en el discovery.
    """
    return BeforeValidator(lambda value: default if _is_blank(value) else value)


BlankAsNone = blank_as(None)

TicketStatus = Literal["OPEN", "CLOSED", "DISCARDED"]
TicketCategory = Literal["TECHNICAL", "BILLING", "ACCESS", "HR_QUERY", "COMPLAINT"]

InventoryReadAction = Literal["list", "get"]
InventoryWriteAction = Literal["create", "update", "delete", "adjust_stock"]
InventoryAction = Literal["list", "get", "create", "update", "delete", "adjust_stock"]


class Ticket(BaseModel):
    """Ticket del Incidents Manager. Nunca incluye el email del cliente."""

    ticket_id: str = Field(description="Id del ticket, formato NXV-NNNNNN.")
    date: dt.date = Field(description="Fecha del ticket (ISO 8601).")
    client_company: str = Field(description="Empresa cliente que abrió el ticket.")
    category: str = Field(description="TECHNICAL, BILLING, ACCESS, HR_QUERY o COMPLAINT.")
    description: str
    agent_id: str = Field(description="Agente de soporte asignado.")
    status: str = Field(description="OPEN, CLOSED o DISCARDED.")
    satisfaction_score: int | None = Field(default=None, description="1-5, solo en tickets cerrados con encuesta.")
    updated_at: dt.datetime | None = Field(default=None, description="Última modificación (UTC).")


class TicketList(BaseModel):
    count: int = Field(description="Número de tickets devueltos.")
    tickets: list[Ticket]


class Product(BaseModel):
    """Producto del inventario con su stock actual (entradas − salidas)."""

    id: int = Field(description="Id numérico del producto (asset_id).")
    name: str
    sku: str
    category: str
    office: str = Field(description="Oficina donde está el stock.")
    current_stock: int
    min_stock_threshold: int = Field(description="Por debajo de este valor el producto está en escasez.")
    programme_id: str | None = None

    @computed_field(description="true si current_stock < min_stock_threshold.")  # type: ignore[prop-decorator]
    @property
    def below_threshold(self) -> bool:
        return self.current_stock < self.min_stock_threshold


class InventoryResult(BaseModel):
    count: int = Field(description="Número de productos devueltos.")
    truncated: bool = Field(description="true si había más productos que el límite pedido.")
    products: list[Product]
