"""
Cliente MCP del agente (`agent.tools.mcp_client`) de punta a punta, sin red externa:

- Un MCP Server real (SDK `mcp`, Streamable HTTP stateless) con las mismas
  tools y la misma forma de error que `mcps/nexova-tools`, más un token
  endpoint OAuth falso, levantados con uvicorn en un puerto local.
- El agente usa su cliente de producción: `langchain-mcp-adapters` +
  `ClientCredentialsAuth` (client_credentials con `resource` y `scope`).

Se ejecuta con:

    uv run --project services/api pytest tests/pipelines/test_agent_mcp_client.py -v
"""

from __future__ import annotations

import base64
import json
import socket
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
API_DIR = str(ROOT / "services" / "api")
if API_DIR not in sys.path:
    sys.path.append(API_DIR)

import uvicorn  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402
from mcp.server.fastmcp.exceptions import ToolError  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402
from starlette.routing import Mount, Route  # noqa: E402

from agent.tools.inventory import InventoryQuery, lookup_inventory  # noqa: E402
from agent.tools.mcp_client import ClientCredentialsAuth, NexovaMcpClient  # noqa: E402
from agent.tools.tickets import TicketQuery, lookup_tickets  # noqa: E402

CLIENT_ID, CLIENT_SECRET = "nexova-agent", "test-secret-no-real"
TICKET = {"ticket_id": "NXV-000482", "date": "2024-03-14", "client_company": "FinServ Group",
          "category": "ACCESS", "description": "Role permissions not updated", "agent_id": "AGT-08",
          "status": "OPEN", "satisfaction_score": None, "updated_at": "2026-11-12T10:00:00Z"}


class Ticket(BaseModel):
    ticket_id: str
    date: str
    client_company: str
    category: str
    description: str
    agent_id: str
    status: str
    satisfaction_score: int | None = None
    updated_at: str | None = None


class TicketList(BaseModel):
    count: int
    tickets: list[Ticket]


class Product(BaseModel):
    id: int
    name: str
    sku: str
    category: str
    office: str
    current_stock: int
    min_stock_threshold: int


class InventoryResult(BaseModel):
    count: int
    truncated: bool
    products: list[Product]


def _error(code: str) -> ToolError:
    return ToolError(json.dumps({"error": {"code": code, "message": code}}))


class FakeAuthorizationAndMcpServer:
    def __init__(self) -> None:
        self.token_requests: list[dict[str, Any]] = []
        self.seen_tokens: list[str | None] = []
        self.issued = 0
        self.slow = False
        self.revoke_current = False

        mcp = FastMCP("nexova-tools-fake", stateless_http=True, json_response=True)

        @mcp.tool()
        def get_ticket(ticket_id: str) -> Ticket:
            if ticket_id != "NXV-000482":
                raise _error("NOT_FOUND")
            return Ticket(**TICKET)

        @mcp.tool()
        def list_tickets(status: str | None = None, category: str | None = None,
                         client_company: str | None = None, limit: int = 10) -> TicketList:
            if status == "CLOSED":  # simula un token sin el scope necesario
                raise _error("FORBIDDEN_SCOPE")
            return TicketList(count=1, tickets=[Ticket(**TICKET)])

        @mcp.tool()
        def query_inventory(action: str = "list", search: str | None = None, limit: int = 20) -> InventoryResult:
            if self.slow:
                time.sleep(2)
            return InventoryResult(count=1, truncated=False, products=[Product(
                id=1, name="Silla ergonómica", sku="CHR-ERG-01", category="office", office="Madrid",
                current_stock=2, min_stock_threshold=4)])

        self.mcp = mcp

    async def token(self, request: Request) -> JSONResponse:
        form = dict(await request.form())
        basic = base64.b64decode(request.headers["authorization"].split()[1]).decode()
        self.token_requests.append({**form, "basic": basic})
        if basic != f"{CLIENT_ID}:{CLIENT_SECRET}":
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        self.issued += 1
        return JSONResponse({"access_token": f"oauth-token-{self.issued}", "token_type": "Bearer", "expires_in": 3600})

    def app(self) -> Starlette:
        mcp_app = self.mcp.streamable_http_app()
        server = self

        class RequireBearer:
            def __init__(self, app):
                self.app = app

            async def __call__(self, scope, receive, send):
                if scope["type"] == "http" and scope["path"].startswith("/mcp"):
                    headers = dict(scope["headers"])
                    token = headers.get(b"authorization", b"").decode().removeprefix("Bearer ") or None
                    server.seen_tokens.append(token)
                    valid = token == f"oauth-token-{server.issued}" and not server.revoke_current
                    if not valid:
                        server.revoke_current = False
                        response = JSONResponse({"error": "invalid_token"}, status_code=401,
                                                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})
                        await response(scope, receive, send)
                        return
                await self.app(scope, receive, send)

        @asynccontextmanager
        async def lifespan(_app):
            async with self.mcp.session_manager.run():
                yield

        app = Starlette(routes=[Route("/oidc/token", self.token, methods=["POST"]), Mount("/", app=mcp_app)],
                        lifespan=lifespan)
        app.add_middleware(RequireBearer)
        return app


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def fake_server() -> Iterator[tuple[FakeAuthorizationAndMcpServer, str]]:
    fake = FakeAuthorizationAndMcpServer()
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(fake.app(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "el servidor MCP de prueba no arrancó"
    yield fake, f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def _client(base: str, *, secret: str = CLIENT_SECRET, timeout: float = 4.0) -> NexovaMcpClient:
    auth = ClientCredentialsAuth(f"{base}/oidc/token", CLIENT_ID, secret,
                                 resource=f"{base}/mcp", scopes="incidents:read inventory:read")
    return NexovaMcpClient(f"{base}/mcp", auth, timeout=timeout)


def test_tickets_and_inventory_through_real_mcp_client(fake_server) -> None:
    fake, base = fake_server
    client = _client(base)

    ticket = lookup_tickets(TicketQuery(ticket_id="NXV-000482"), call_tool=client.call_tool)
    assert ticket.status == "ok" and ticket.tickets[0].status == "OPEN"

    listed = lookup_tickets(TicketQuery(client_company="FinServ"), call_tool=client.call_tool)
    assert listed.status == "ok" and listed.tickets[0].ticket_id == "NXV-000482"

    stock = lookup_inventory(InventoryQuery(product="silla"), call_tool=client.call_tool)
    assert stock.status == "ok" and stock.products[0].below_threshold is True

    # client_credentials con Resource Indicator y solo los scopes de lectura del agente.
    request = fake.token_requests[-1]
    assert request["grant_type"] == "client_credentials"
    assert request["resource"] == f"{base}/mcp"
    assert request["scope"] == "incidents:read inventory:read"


def test_token_is_cached_and_refreshed_once_on_401(fake_server) -> None:
    fake, base = fake_server
    client = _client(base)
    before = len(fake.token_requests)
    client.call_tool("get_ticket", {"ticket_id": "NXV-000482"})
    client.call_tool("get_ticket", {"ticket_id": "NXV-000482"})
    assert len(fake.token_requests) == before + 1  # un único token para varias llamadas

    fake.revoke_current = True  # el servidor deja de aceptar el token actual
    result = lookup_tickets(TicketQuery(ticket_id="NXV-000482"), call_tool=client.call_tool)
    assert result.status == "ok"
    assert len(fake.token_requests) == before + 2


def test_mcp_error_codes_map_to_tool_status(fake_server) -> None:
    _, base = fake_server
    client = _client(base)

    missing = lookup_tickets(TicketQuery(ticket_id="NXV-999999"), call_tool=client.call_tool)
    assert missing.status == "not_found" and missing.detail == "MCP NOT_FOUND"

    forbidden = lookup_tickets(TicketQuery(status="CLOSED"), call_tool=client.call_tool)
    assert forbidden.status == "error" and forbidden.detail == "MCP FORBIDDEN_SCOPE"


def test_bad_credentials_and_unreachable_server_fall_back(fake_server) -> None:
    _, base = fake_server
    rejected = lookup_tickets(TicketQuery(ticket_id="NXV-000482"), call_tool=_client(base, secret="mal").call_tool)
    assert rejected.status == "error"

    # Puerto cerrado: conexión rechazada (en Windows tarda ~2 s en rechazarse).
    down = NexovaMcpClient(f"http://127.0.0.1:{_free_port()}/mcp", None, timeout=8)
    unreachable = lookup_inventory(InventoryQuery(product="silla"), call_tool=down.call_tool)
    assert unreachable.status == "error" and unreachable.detail.startswith("MCP Server no disponible")


def test_slow_mcp_server_times_out(fake_server) -> None:
    fake, base = fake_server
    fake.slow = True
    try:
        result = lookup_inventory(InventoryQuery(product="silla"), call_tool=_client(base, timeout=0.5).call_tool)
    finally:
        fake.slow = False
    assert result.status == "timeout"
