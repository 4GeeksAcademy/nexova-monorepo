"""
Transporte HTTP común de las tools: solo GET, timeout explícito y credenciales
fuera del estado del grafo.

- URL base: `AGENT_TOOLS_BASE_URL` (por defecto `http://localhost:8000`, la
  propia API; vale igual dentro del contenedor `api`).
- Timeout: `AGENT_TOOL_TIMEOUT_SECONDS` (por defecto 4.0 s) en cada petición.
- Auth: el JWT de quien llama a `/agent/query` se reenvía vía `ContextVar`
  (`forwarded_credentials`); fuera de una petición (scripts) se usa
  `INCIDENTS_API_TOKEN`. El token nunca entra en el estado, los checkpoints
  ni el trace.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Literal

import httpx

ToolStatus = Literal["ok", "not_found", "timeout", "error"]

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_TIMEOUT_SECONDS = 4.0

_forwarded_token: ContextVar[str | None] = ContextVar("agent_forwarded_token", default=None)


class ToolCallError(Exception):
    """Fallo de transporte o de servicio, ya clasificado para el `ToolResult`."""

    def __init__(self, status: ToolStatus, detail: str) -> None:
        super().__init__(detail)
        self.status: ToolStatus = status
        self.detail = detail


def tools_base_url() -> str:
    return os.environ.get("AGENT_TOOLS_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def tool_timeout_seconds() -> float:
    raw = os.environ.get("AGENT_TOOL_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_TIMEOUT_SECONDS


@contextmanager
def forwarded_credentials(token: str | None) -> Iterator[None]:
    """Hace disponible el bearer token de la petición a las tools de esta corrida."""
    handle = _forwarded_token.set(token)
    try:
        yield
    finally:
        _forwarded_token.reset(handle)


def _auth_headers() -> dict[str, str]:
    token = _forwarded_token.get() or os.environ.get("INCIDENTS_API_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


def http_get(
    path: str,
    *,
    params: dict[str, Any] | None = None,
    authenticated: bool = False,
    client: httpx.Client | None = None,
) -> Any:
    """GET de solo lectura contra un servicio de Nexova; devuelve el JSON.

    Es el único punto de salida HTTP de las tools y solo sabe hacer GET.
    Traduce timeouts, errores de red y códigos HTTP a `ToolCallError`.
    """
    timeout = tool_timeout_seconds()
    headers = _auth_headers() if authenticated else {}
    clean_params = {k: v for k, v in (params or {}).items() if v is not None}
    owns_client = client is None
    http = client or httpx.Client(base_url=tools_base_url(), timeout=timeout)
    try:
        response = http.get(path, params=clean_params, headers=headers, timeout=timeout)
    except httpx.TimeoutException as exc:
        raise ToolCallError("timeout", f"Sin respuesta en {timeout:g} s") from exc
    except httpx.HTTPError as exc:
        raise ToolCallError("error", f"Servicio no disponible ({type(exc).__name__})") from exc
    finally:
        if owns_client:
            http.close()

    if response.status_code == 404:
        raise ToolCallError("not_found", "Recurso no encontrado")
    if response.status_code in (401, 403):
        raise ToolCallError("error", f"Credenciales rechazadas (HTTP {response.status_code})")
    if response.status_code >= 400:
        raise ToolCallError("error", f"Respuesta HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise ToolCallError("error", "Respuesta no es JSON") from exc
