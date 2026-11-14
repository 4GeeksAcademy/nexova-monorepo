"""
MCP Server de Nexova (transporte Streamable HTTP, OAuth con MCP Auth).

Rutas:
- `GET /.well-known/oauth-protected-resource[/<path del recurso>]`: Protected
  Resource Metadata (RFC 9728), pública, para que el cliente descubra Logto.
- `/mcp`: endpoint MCP. Todo él (initialize, tools/list, tools/call) va detrás
  del bearer middleware de MCP Auth.
- `GET /health`: sonda sin datos.

Modo stateless: cada petición HTTP crea su propio contexto MCP, de modo que la
identidad (`mcp_auth.auth_info`) es siempre la del token de esa petición.

Arranque: `uv run python -m nexova_mcp.server`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastmcp import FastMCP
from jwt import PyJWK
from mcpauth import MCPAuth
from mcpauth.config import AuthServerConfig
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nexova_mcp import audit
from nexova_mcp.api_client import InventoryReader, NexovaApiClient
from nexova_mcp.auth import build_jwt_verifier, build_mcp_auth, fetch_logto_config
from nexova_mcp.config import Settings
from nexova_mcp.tools import inventory, tickets

INSTRUCTIONS = """\
Servidor MCP de Nexova. Expone el Incidents Manager (tickets de soporte) y el inventario.

- Tickets: list_tickets y get_ticket (scope incidents:read); create_ticket y
  update_ticket_status (scope incidents:write). Los ids tienen formato NXV-NNNNNN.
  Ciclo de vida: OPEN→CLOSED, OPEN→DISCARDED, CLOSED→OPEN; DISCARDED es final.
- Inventario: query_inventory (scope inventory:read), SOLO LECTURA. Cualquier
  acción de escritura se rechaza con READ_ONLY_RESOURCE.

Errores de tool: JSON {"error": {"code", "message", "details"}} con code en
FORBIDDEN_SCOPE, VALIDATION_ERROR, READ_ONLY_RESOURCE, NOT_FOUND,
INVALID_TRANSITION, UPSTREAM_UNAVAILABLE, UPSTREAM_AUTH_ERROR, INTERNAL_ERROR.
Sin token OAuth válido el servidor responde HTTP 401 antes de llegar a las tools.
"""


class AuthRejectionLogger:
    """Registra en el log de auditoría los 401/403 que devuelve MCP Auth."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start" and message["status"] in (401, 403):
                headers = dict(message.get("headers") or [])
                audit.log_event(
                    event="auth_rejected",
                    status=message["status"],
                    path=scope.get("path"),
                    www_authenticate=headers.get(b"www-authenticate", b"").decode("latin-1"),
                )
            await send(message)

        await self.app(scope, receive, send_wrapper)


def build_mcp(mcp_auth: MCPAuth, api: NexovaApiClient) -> FastMCP:
    mcp = FastMCP(name="nexova-tools", instructions=INSTRUCTIONS, middleware=[audit.AuditMiddleware(mcp_auth)])
    tickets.register(mcp, mcp_auth, api)
    inventory.register(mcp, mcp_auth, InventoryReader(api))
    return mcp


def create_app(
    settings: Settings,
    *,
    auth_server: AuthServerConfig | None = None,
    jwk_source: PyJWK | None = None,
    api_transport: httpx.AsyncBaseTransport | None = None,
) -> Starlette:
    """
    Construye la app ASGI. En producción descubre Logto y valida los JWT con
    su JWKS; los tests inyectan la metadata de Logto, la clave pública de
    prueba (`jwk_source`) y un transporte HTTP falso hacia la API.
    """
    auth_server = auth_server or fetch_logto_config(settings.oauth_issuer)
    mcp_auth = build_mcp_auth(settings.resource_url, auth_server)
    api = NexovaApiClient(
        settings.api_url,
        settings.api_email,
        settings.api_password,
        timeout=settings.upstream_timeout_seconds,
        transport=api_transport,
    )
    mcp = build_mcp(mcp_auth, api)

    bearer = mcp_auth.bearer_auth_middleware(
        build_jwt_verifier(auth_server, jwk_source),
        resource=settings.resource_url,
        audience=settings.resource_url,
    )
    mcp_app = mcp.http_app(
        path="/mcp",
        stateless_http=True,
        json_response=True,
        middleware=[Middleware(AuthRejectionLogger), Middleware(bearer)],
    )

    @asynccontextmanager
    async def lifespan(app: Starlette):
        async with mcp_app.lifespan(app):
            yield
        await api.aclose()

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return Starlette(
        routes=[
            *mcp_auth.resource_metadata_router().routes,
            Route("/health", health),
            Mount("/", app=mcp_app),
        ],
        # CORS por fuera del bearer: el preflight OPTIONS de un cliente web
        # (MCP Playground) no lleva token y no debe recibir un 401.
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                allow_headers=["*"],
                expose_headers=["WWW-Authenticate", "Mcp-Session-Id", "Mcp-Protocol-Version"],
            )
        ],
        lifespan=lifespan,
    )


def main(factory: Callable[[Settings], Starlette] = create_app) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    uvicorn.run(factory(settings), host=settings.host, port=settings.port, proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
