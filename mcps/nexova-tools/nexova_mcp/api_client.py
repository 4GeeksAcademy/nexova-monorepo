"""
Cliente HTTP hacia la API de Nexova (`services/api`).

El MCP Server no reimplementa lógica de negocio: llama a los mismos endpoints
que el backoffice. La API emite su propio JWT (HS256), distinto del token
OAuth del cliente MCP, así que el servidor entra con una cuenta de servicio
(`POST /auth/login`) y vuelve a entrar una vez si recibe un 401.

`InventoryReader` es la única vista del cliente que reciben las tools de
inventario y solo sabe hacer GET: escribir en inventario no es posible ni
por error de programación.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from nexova_mcp.errors import ErrorCode, NexovaToolError


class NexovaApiClient:
    def __init__(
        self,
        base_url: str,
        email: str,
        password: str,
        *,
        timeout: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._email = email
        self._password = password
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)
        self._token: str | None = None
        self._login_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _login(self) -> str:
        async with self._login_lock:
            response = await self._send("POST", "/auth/login", json={"email": self._email, "password": self._password})
            if response.status_code != 200:
                raise NexovaToolError(
                    ErrorCode.UPSTREAM_AUTH_ERROR,
                    "La cuenta de servicio del MCP no puede autenticarse en la API de Nexova.",
                    {"upstream_status": response.status_code},
                )
            self._token = response.json()["access_token"]
            return self._token

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException:
            raise NexovaToolError(ErrorCode.UPSTREAM_UNAVAILABLE, "La API de Nexova no respondió a tiempo.") from None
        except httpx.HTTPError:
            raise NexovaToolError(ErrorCode.UPSTREAM_UNAVAILABLE, "No se pudo conectar con la API de Nexova.") from None

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        token = self._token or await self._login()
        response = await self._send(method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs)
        if response.status_code == 401:  # token de servicio caducado: un único re-login
            token = await self._login()
            response = await self._send(method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs)
        return _parse(response)

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self.request("GET", path, params=params)


class InventoryReader:
    """Vista de solo lectura del cliente para las tools de inventario."""

    def __init__(self, api: NexovaApiClient) -> None:
        self._api = api

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not path.startswith("/inventory/"):
            raise ValueError("InventoryReader solo consulta rutas /inventory/*")
        return await self._api.get(path, params=params)


def _detail(response: httpx.Response) -> Any:
    try:
        return response.json().get("detail")
    except Exception:
        return None


def _parse(response: httpx.Response) -> Any:
    status = response.status_code
    if 200 <= status < 300:
        return response.json()
    detail = _detail(response)
    if status == 404:
        raise NexovaToolError(ErrorCode.NOT_FOUND, str(detail or "Recurso no encontrado."))
    if status == 409 and isinstance(detail, dict) and detail.get("code") == "invalid_transition":
        raise NexovaToolError(
            ErrorCode.INVALID_TRANSITION,
            detail.get("message", "Transición de estado no permitida."),
            {k: detail[k] for k in ("current_status", "requested_status", "allowed") if k in detail},
        )
    if status == 422:
        raise NexovaToolError(ErrorCode.VALIDATION_ERROR, "La API de Nexova rechazó los datos.", {"upstream": detail})
    if status in (401, 403):
        raise NexovaToolError(ErrorCode.UPSTREAM_AUTH_ERROR, "La API de Nexova rechazó la cuenta de servicio.")
    raise NexovaToolError(ErrorCode.UPSTREAM_UNAVAILABLE, "La API de Nexova devolvió un error.", {"upstream_status": status})
