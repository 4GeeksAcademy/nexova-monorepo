"""
Cliente MCP del agente: el único camino del grafo hacia el Incidents Manager y
el inventario (`mcps/nexova-tools`, Streamable HTTP + OAuth).

- Tools cargadas con `langchain-mcp-adapters` (`MultiServerMCPClient`), sin
  reimplementar el protocolo. Se invocan con un `ToolCall` para recibir el
  `structuredContent` (artifact) y no solo el texto.
- OAuth: `ClientCredentialsAuth` pide a Logto un access token
  `client_credentials` para el recurso MCP con los scopes de solo lectura del
  agente (`incidents:read inventory:read`), lo cachea hasta que caduca y lo
  renueva una vez si el servidor responde 401. El token vive solo en este
  objeto: nunca entra en el estado del grafo, los checkpoints ni el trace.
- El grafo es síncrono (FastAPI lo ejecuta en su threadpool), así que
  `call_tool()` ejecuta la corrutina con `asyncio.run` y un timeout total
  (`AGENT_TOOL_TIMEOUT_SECONDS`).
- Los errores de las tools llegan con código estable
  (`{"error": {"code": ...}}`) y se traducen a `ToolStatus`.

Variables de entorno:
  AGENT_MCP_URL               URL del endpoint MCP (por defecto http://localhost:8100/mcp)
  AGENT_MCP_RESOURCE          Recurso OAuth / `aud` del token (por defecto = AGENT_MCP_URL)
  AGENT_MCP_TOKEN_URL         Token endpoint de Logto (https://<tenant>.logto.app/oidc/token)
  AGENT_MCP_CLIENT_ID / AGENT_MCP_CLIENT_SECRET   App M2M del agente en Logto
  AGENT_MCP_SCOPES            Por defecto "incidents:read inventory:read"
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import threading
import time
from collections.abc import Callable, Generator
from functools import lru_cache
from typing import Any
from urllib.parse import quote

import httpx
from langchain_core.tools import ToolException
from langchain_mcp_adapters.client import MultiServerMCPClient

from agent.tools.transport import ToolCallError, ToolStatus, tool_timeout_seconds

SERVER_NAME = "nexova"
DEFAULT_MCP_URL = "http://localhost:8100/mcp"
DEFAULT_SCOPES = "incidents:read inventory:read"
TOKEN_EXPIRY_MARGIN_SECONDS = 30

# Cómo se presenta cada código de error del MCP Server al grafo.
_ERROR_STATUS: dict[str, ToolStatus] = {"NOT_FOUND": "not_found"}

# Firma que reciben `lookup_tickets` / `lookup_inventory`: nombre de tool MCP
# + argumentos → `structuredContent`. Lanza `ToolCallError` si algo falla.
McpToolCaller = Callable[[str, dict[str, Any]], dict[str, Any]]


class ClientCredentialsAuth(httpx.Auth):
    """Bearer OAuth `client_credentials` (RFC 6749 §4.4 + Resource Indicators, RFC 8707)."""

    requires_response_body = True

    def __init__(self, token_url: str, client_id: str, client_secret: str, resource: str, scopes: str) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._resource = resource
        self._scopes = scopes
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def _token_request(self) -> httpx.Request:
        # client_secret_basic (RFC 6749 §2.3.1): id y secreto url-encoded antes del base64.
        credentials = f"{quote(self._client_id, safe='')}:{quote(self._client_secret, safe='')}"
        basic = base64.b64encode(credentials.encode()).decode()
        return httpx.Request(
            "POST",
            self._token_url,
            data={"grant_type": "client_credentials", "resource": self._resource, "scope": self._scopes},
            headers={"Authorization": f"Basic {basic}"},
        )

    def _store(self, response: httpx.Response) -> None:
        if response.status_code != 200:
            raise ToolCallError("error", f"Logto rechazó las credenciales del agente (HTTP {response.status_code})")
        payload = response.json()
        with self._lock:
            self._token = payload["access_token"]
            self._expires_at = time.time() + float(payload.get("expires_in", 300)) - TOKEN_EXPIRY_MARGIN_SECONDS

    def _cached(self) -> str | None:
        with self._lock:
            return self._token if self._token and time.time() < self._expires_at else None

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        if self._cached() is None:
            self._store((yield self._token_request()))
        request.headers["Authorization"] = f"Bearer {self._cached()}"
        response = yield request
        if response.status_code == 401:  # token revocado o rotado: un único reintento
            self._store((yield self._token_request()))
            request.headers["Authorization"] = f"Bearer {self._cached()}"
            yield request


def mcp_error_code(exc: BaseException) -> str | None:
    """Código estable (`NOT_FOUND`, `FORBIDDEN_SCOPE`, ...) de un error de tool MCP.

    Algunos SDK anteponen texto ("Error executing tool x: ..."), así que se
    busca el objeto JSON dentro del mensaje.
    """
    text = str(exc)
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.loads(text[start:])["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return None


def _unwrap(exc: BaseException) -> BaseException:
    """Los task groups de anyio envuelven el fallo real en `ExceptionGroup`."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


class NexovaMcpClient:
    def __init__(self, url: str, auth: httpx.Auth | None, *, timeout: float | None = None) -> None:
        self._url = url
        self._auth = auth
        self._timeout = timeout

    def _client(self) -> MultiServerMCPClient:
        connection: dict[str, Any] = {"transport": "streamable_http", "url": self._url}
        if self._auth is not None:
            connection["auth"] = self._auth
        return MultiServerMCPClient({SERVER_NAME: connection}, handle_tool_errors=False)

    async def _acall(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tools = {tool.name: tool for tool in await self._client().get_tools(server_name=SERVER_NAME)}
        if name not in tools:
            raise ToolCallError("error", f"El MCP Server no expone la tool '{name}'")
        message = await tools[name].ainvoke({"type": "tool_call", "id": name, "name": name, "args": arguments})
        artifact = getattr(message, "artifact", None) or {}
        structured = artifact.get("structured_content")
        if not isinstance(structured, dict):
            raise ToolCallError("error", "Respuesta del MCP Server sin datos estructurados")
        return structured

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        timeout = self._timeout or tool_timeout_seconds()
        try:
            return asyncio.run(asyncio.wait_for(self._acall(name, arguments), timeout))
        except ToolCallError:
            raise
        except BaseException as raw:  # noqa: BLE001 — se clasifica y nunca rompe el grafo
            exc = _unwrap(raw)
            if isinstance(exc, ToolCallError):
                raise exc from None
            if isinstance(exc, (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException)):
                raise ToolCallError("timeout", f"Sin respuesta en {timeout:g} s") from exc
            if isinstance(exc, ToolException):
                code = mcp_error_code(exc) or "TOOL_ERROR"
                raise ToolCallError(_ERROR_STATUS.get(code, "error"), f"MCP {code}") from exc
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise ToolCallError("error", f"MCP Server no disponible ({type(exc).__name__})") from exc


@lru_cache(maxsize=1)
def default_mcp_client() -> NexovaMcpClient:
    url = os.environ.get("AGENT_MCP_URL", DEFAULT_MCP_URL).rstrip("/")
    token_url = os.environ.get("AGENT_MCP_TOKEN_URL")
    client_id = os.environ.get("AGENT_MCP_CLIENT_ID")
    client_secret = os.environ.get("AGENT_MCP_CLIENT_SECRET")
    auth = None
    if token_url and client_id and client_secret:
        auth = ClientCredentialsAuth(
            token_url,
            client_id,
            client_secret,
            resource=os.environ.get("AGENT_MCP_RESOURCE", url).rstrip("/"),
            scopes=os.environ.get("AGENT_MCP_SCOPES", DEFAULT_SCOPES),
        )
    # Sin credenciales el servidor responderá 401 y las tools irán a `tool_fallback`.
    return NexovaMcpClient(url, auth)


def call_mcp_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """`McpToolCaller` por defecto: el cliente configurado por entorno."""
    return default_mcp_client().call_tool(name, arguments)
