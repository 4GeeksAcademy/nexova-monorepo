"""
Tool de inventario: consulta en vivo el stock a través del MCP Server
`mcps/nexova-tools` (`query_inventory`, action `list`; el inventario es de
solo lectura por diseño en el servidor).

El MCP Server filtra por nombre o SKU sin distinguir mayúsculas, tildes ni
plurales. Sin término de búsqueda devuelve los primeros productos (útil para
"¿qué está bajo mínimo?").
"""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, computed_field

from agent.tools.mcp_client import McpToolCaller, call_mcp_tool
from agent.tools.transport import ToolCallError, ToolStatus

MAX_PRODUCTS = 20


class InventoryQuery(BaseModel):
    """Entrada: nombre o SKU (parcial) del producto."""

    product: str | None = Field(default=None, min_length=2, max_length=100)


class ProductStock(BaseModel):
    """Salida: los campos del inventario relevantes para soporte."""

    id: int
    name: str
    sku: str
    category: str
    office: str
    current_stock: int
    min_stock_threshold: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def below_threshold(self) -> bool:
        return self.current_stock < self.min_stock_threshold


class InventoryToolResult(BaseModel):
    tool: Literal["inventory"] = "inventory"
    status: ToolStatus
    query: InventoryQuery
    products: list[ProductStock] = Field(default_factory=list)
    detail: str | None = None
    duration_ms: float = 0.0


def lookup_inventory(
    query: InventoryQuery, *, call_tool: McpToolCaller | None = None
) -> InventoryToolResult:
    """Consulta el inventario vía MCP. Nunca lanza: los fallos van en `status`."""
    call = call_tool or call_mcp_tool
    start = time.perf_counter()

    def done(status: ToolStatus, products: list[ProductStock] | None = None, detail: str | None = None):
        return InventoryToolResult(
            status=status,
            query=query,
            products=products or [],
            detail=detail,
            duration_ms=round((time.perf_counter() - start) * 1000, 1),
        )

    arguments: dict = {"action": "list", "limit": MAX_PRODUCTS}
    if query.product:
        arguments["search"] = query.product
    try:
        payload = call("query_inventory", arguments)
        products = [ProductStock.model_validate(item) for item in payload["products"]]
    except ToolCallError as exc:
        return done(exc.status, detail=exc.detail)
    except (ValidationError, TypeError, KeyError):
        return done("error", detail="Respuesta del gestor de inventario con formato inesperado")

    if not products:
        return done("not_found", detail="Ningún producto coincide con la búsqueda")
    return done("ok", products)
