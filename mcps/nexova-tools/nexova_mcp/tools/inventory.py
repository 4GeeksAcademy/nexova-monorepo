"""
Tool de inventario: solo lectura por diseño.

Tres barreras, de fuera hacia dentro:
1. No existe el scope `inventory:write`: ningún token puede pedir escritura.
2. `query_inventory` declara en su esquema las acciones de escritura
   (`create`, `update`, `delete`, `adjust_stock`) precisamente para
   rechazarlas de forma explícita con `READ_ONLY_RESOURCE` (y queda en el
   log de auditoría), en vez de fallar con un error genérico.
3. La tool solo recibe un `InventoryReader`, que únicamente sabe hacer GET.
"""

from __future__ import annotations

import unicodedata
from typing import Annotated, get_args

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from mcpauth import MCPAuth
from pydantic import Field

from nexova_mcp.api_client import InventoryReader
from nexova_mcp.auth import SCOPE_INVENTORY_READ, require_scope
from nexova_mcp.errors import ErrorCode, NexovaToolError
from nexova_mcp.models import (
    BlankAsNone,
    InventoryAction,
    InventoryResult,
    InventoryWriteAction,
    Product,
    blank_as,
)

WRITE_ACTIONS = frozenset(get_args(InventoryWriteAction))


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _stem(word: str) -> str:
    """Raíz tolerante a plurales y género: `ratones` → `raton`, `ergonomicos` → `ergonomic`."""
    return word[: max(4, len(word) - 2)] if len(word) > 4 else word


def matches(product: Product, term: str) -> bool:
    """Coincidencia por nombre o SKU sin distinguir mayúsculas, tildes ni plurales."""
    needle = _normalize(term)
    words = [w for w in needle.replace("-", " ").split() if len(w) > 2] or [needle]
    haystack = _normalize(f"{product.name} {product.sku}")
    return all(_stem(word) in haystack for word in words)


def register(mcp: FastMCP, mcp_auth: MCPAuth, inventory: InventoryReader) -> None:
    @mcp.tool(
        name="query_inventory",
        title="Consultar inventario (solo lectura)",
        description=(
            "Consulta el inventario de Nexova: productos con su stock actual frente al umbral mínimo. "
            "action='list' devuelve productos filtrados por búsqueda (nombre o SKU, sin distinguir "
            "mayúsculas ni tildes), oficina y/o escasez; action='get' devuelve un producto por product_id. "
            "El inventario es de SOLO LECTURA: las acciones 'create', 'update', 'delete' y 'adjust_stock' "
            "se rechazan siempre con READ_ONLY_RESOURCE (los movimientos de stock se registran como "
            "órdenes en el backoffice, no por MCP). Requiere el scope 'inventory:read'. "
            "Errores: FORBIDDEN_SCOPE, VALIDATION_ERROR, READ_ONLY_RESOURCE, NOT_FOUND, UPSTREAM_UNAVAILABLE."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
        tags={"inventory"},
    )
    async def query_inventory(
        action: Annotated[
            InventoryAction,
            blank_as("list"),
            Field(description="'list' o 'get'. Las acciones de escritura existen solo para ser rechazadas."),
        ] = "list",
        product_id: Annotated[
            Annotated[int, Field(ge=1)] | None, BlankAsNone, Field(description="Obligatorio con action='get'.")
        ] = None,
        search: Annotated[
            Annotated[str, Field(min_length=2, max_length=100)] | None,
            BlankAsNone,
            Field(description="Nombre o SKU (parcial) del producto."),
        ] = None,
        office: Annotated[
            Annotated[str, Field(min_length=2, max_length=50)] | None, BlankAsNone, Field(description="Filtra por oficina.")
        ] = None,
        below_threshold_only: Annotated[bool, blank_as(False), Field(description="Solo productos en escasez.")] = False,
        limit: Annotated[int, blank_as(20), Field(ge=1, le=100, description="Máximo de productos a devolver.")] = 20,
    ) -> InventoryResult:
        require_scope(mcp_auth, SCOPE_INVENTORY_READ)

        if action in WRITE_ACTIONS:
            raise NexovaToolError(
                ErrorCode.READ_ONLY_RESOURCE,
                f"El inventario es de solo lectura vía MCP: la acción '{action}' no está permitida.",
                {"action": action, "allowed_actions": ["list", "get"]},
            )

        if action == "get":
            if product_id is None:
                raise NexovaToolError(
                    ErrorCode.VALIDATION_ERROR,
                    "action='get' requiere product_id.",
                    {"errors": [{"field": "product_id", "reason": "Field required"}]},
                )
            product = Product.model_validate(await inventory.get(f"/inventory/products/{product_id}"))
            return InventoryResult(count=1, truncated=False, products=[product])

        products = [Product.model_validate(item) for item in await inventory.get("/inventory/products")]
        if search:
            products = [p for p in products if matches(p, search)]
        if office:
            products = [p for p in products if _normalize(p.office) == _normalize(office)]
        if below_threshold_only:
            products = [p for p in products if p.below_threshold]
        return InventoryResult(count=min(len(products), limit), truncated=len(products) > limit, products=products[:limit])
