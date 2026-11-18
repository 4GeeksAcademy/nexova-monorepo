"""
Contratos de entrada y salida de las tools.

Los nombres de campo y los valores de dominio son los de la API de Nexova
(`services/api/incident_manager/schemas.py` y `services/api/schemas.py`):
estados `open|in_progress|resolved|discarded`, ocho categorías, origen y sede, y
productos con `current_stock` frente a `min_stock_threshold`.

La API identifica cada incidencia con un `id` entero; aquí se presenta como la
referencia `NXV-NNNNNN` (id con 6 dígitos), que es la que usan los usuarios y el
agente. `ticket_ref` / `ticket_pk` son la única conversión entre ambas.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, BeforeValidator, Field, computed_field

TICKET_ID_PATTERN = r"^NXV-\d{6}$"


def ticket_ref(pk: int) -> str:
    """`482` → `NXV-000482`."""
    return f"NXV-{pk:06d}"


def ticket_pk(ref: str) -> int:
    """`NXV-000482` → `482`. El patrón de la tool ya garantiza el formato."""
    return int(ref.removeprefix("NXV-"))


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

# Mismos valores que `packages/shared/nexova_shared/incident_domain.py` (este proyecto `uv`
# es independiente y no lo importa; la API los valida igualmente).
TicketStatus = Literal["open", "in_progress", "resolved", "discarded"]
TicketCategory = Literal[
    "technical_failure",
    "process_error",
    "client_complaint",
    "candidate_issue",
    "staff_issue",
    "sla_breach",
    "data_quality",
    "other",
]
TicketOrigin = Literal["customer", "branch", "internal"]
TicketBranch = Literal["central", "valencia_operations", "miami_office", "remote"]

InventoryReadAction = Literal["list", "get"]
InventoryWriteAction = Literal["create", "update", "delete", "adjust_stock"]
InventoryAction = Literal["list", "get", "create", "update", "delete", "adjust_stock"]


class Ticket(BaseModel):
    """Incidencia del gestor de incidencias. Nunca incluye datos personales."""

    ticket_id: str = Field(description="Referencia de la incidencia, formato NXV-NNNNNN.")
    title: str = Field(description="Título breve.")
    description: str
    category: str = Field(description="technical_failure, process_error, client_complaint, candidate_issue, "
                                      "staff_issue, sla_breach, data_quality u other.")
    status: str = Field(description="open, in_progress, resolved o discarded.")
    origin: str = Field(description="customer, branch o internal.")
    branch: str = Field(description="central, valencia_operations, miami_office o remote.")
    client_company: str | None = Field(default=None, description="Empresa cliente asociada, si la hay.")
    created_at: dt.datetime | None = Field(default=None, description="Creación (UTC).")
    updated_at: dt.datetime | None = Field(default=None, description="Última modificación (UTC).")

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "Ticket":
        """Construye el ticket desde la respuesta de la API (`id` entero → `ticket_id` NXV-NNNNNN)."""
        data = {k: v for k, v in payload.items() if k != "id"}
        return cls.model_validate({**data, "ticket_id": ticket_ref(int(payload["id"]))})


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
