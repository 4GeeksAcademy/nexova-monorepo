"""
Tool de inventario: consulta en vivo el stock del gestor de inventario (solo lectura).

    GET /inventory/products   (sin auth en la API actual)

Devuelve los productos cuyo nombre o SKU contiene el término buscado (sin
distinguir mayúsculas ni tildes), con su stock actual frente al umbral mínimo.
Sin término, devuelve los primeros productos (útil para "¿qué está bajo mínimo?").
"""

from __future__ import annotations

import time
import unicodedata
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError, computed_field

from agent.tools.transport import ToolCallError, ToolStatus, http_get

MAX_PRODUCTS = 20


class InventoryQuery(BaseModel):
    """Entrada: nombre o SKU (parcial) del producto."""

    product: str | None = Field(default=None, min_length=2, max_length=100)


class ProductStock(BaseModel):
    """Salida: los campos de `GET /inventory/products` relevantes para soporte."""

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


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _stem(word: str) -> str:
    """Raíz tolerante a plurales y género: `ratones` → `raton`, `ergonomicos` → `ergonomic`."""
    return word[: max(4, len(word) - 2)] if len(word) > 4 else word


def _matches(product: ProductStock, term: str) -> bool:
    needle = _normalize(term)
    words = [w for w in needle.replace("-", " ").split() if len(w) > 2] or [needle]
    haystack = _normalize(f"{product.name} {product.sku}")
    return all(_stem(word) in haystack for word in words)


def lookup_inventory(
    query: InventoryQuery, *, client: httpx.Client | None = None
) -> InventoryToolResult:
    """Consulta el gestor de inventario. Nunca lanza: los fallos van en `status`."""
    start = time.perf_counter()

    def done(status: ToolStatus, products: list[ProductStock] | None = None, detail: str | None = None):
        return InventoryToolResult(
            status=status,
            query=query,
            products=products or [],
            detail=detail,
            duration_ms=round((time.perf_counter() - start) * 1000, 1),
        )

    try:
        payload = http_get("/inventory/products", client=client)
        products = [ProductStock.model_validate(item) for item in payload]
    except ToolCallError as exc:
        return done(exc.status, detail=exc.detail)
    except (ValidationError, TypeError):
        return done("error", detail="Respuesta del gestor de inventario con formato inesperado")

    if query.product:
        products = [p for p in products if _matches(p, query.product)]
    if not products:
        return done("not_found", detail="Ningún producto coincide con la búsqueda")
    return done("ok", products[:MAX_PRODUCTS])
