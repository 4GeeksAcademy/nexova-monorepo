"""
Fixtures del MCP Server, sin red:

- Authorization server falso: metadata de Logto escrita a mano y JWT RS256
  firmados con una clave generada en el test. La verificación usa la misma
  ruta de MCP Auth (bearer middleware + issuer + audience), solo cambia la
  función que comprueba la firma.
- API de Nexova falsa con `httpx.MockTransport` (incluye el ciclo de vida de
  tickets y la regla de transiciones de la API real).
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import PyJWK
from mcpauth.config import AuthorizationServerMetadata, AuthServerConfig, AuthServerType

from nexova_mcp.config import Settings
from nexova_mcp.server import create_app

# Fixtures async con el plugin de anyio: setup y teardown en la misma tarea
# (el lifespan de la app abre cancel scopes que deben cerrarse donde se abrieron).
pytestmark = pytest.mark.anyio

ISSUER = "https://tenant.logto.test/oidc"
RESOURCE = "https://mcp.nexova.test/mcp"
SERVICE_TOKEN = "service-jwt"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

ALL_SCOPES = "incidents:read incidents:write inventory:read"


def make_token(
    scope: str = ALL_SCOPES,
    *,
    client_id: str = "playground-app",
    aud: str = RESOURCE,
    iss: str = ISSUER,
    exp_offset: int = 300,
    key: Any = _KEY,
) -> str:
    now = int(time.time())
    claims = {"iss": iss, "aud": aud, "sub": f"user-{client_id}", "client_id": client_id,
              "scope": scope, "iat": now, "exp": now + exp_offset}
    return jwt.encode(claims, key, algorithm="RS256")


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


# La clave pública de prueba ocupa el lugar del JWKS de Logto; el resto de la
# verificación (issuer, firma, caducidad, audience, middleware) es el de producción.
TEST_JWK = PyJWK.from_dict({**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(_KEY.public_key())), "alg": "RS256"})


AUTH_SERVER = AuthServerConfig(
    type=AuthServerType.OIDC,
    metadata=AuthorizationServerMetadata(
        issuer=ISSUER,
        authorization_endpoint=f"{ISSUER}/auth",
        token_endpoint=f"{ISSUER}/token",
        jwks_uri=f"{ISSUER}/jwks",
        response_types_supported=["code"],
        grant_types_supported=["authorization_code", "client_credentials"],
        code_challenge_methods_supported=["S256"],
    ),
)


class FakeNexovaApi:
    """Responde como `services/api` para las rutas que usa el MCP."""

    def __init__(self) -> None:
        self.tickets: dict[str, dict[str, Any]] = {
            "NXV-000482": self._ticket("NXV-000482", "OPEN"),
            "NXV-000483": self._ticket("NXV-000483", "DISCARDED"),
        }
        self.products = [
            {"id": 1, "name": "Ratón ergonómico", "sku": "MOU-ERG-01", "category": "peripherals",
             "office": "Valencia", "current_stock": 3, "min_stock_threshold": 5, "programme_id": None},
            {"id": 2, "name": "Portátil 14", "sku": "LAP-14", "category": "laptops",
             "office": "Miami", "current_stock": 12, "min_stock_threshold": 4, "programme_id": "onboarding"},
        ]
        self.requests: list[httpx.Request] = []
        self.down = False

    @staticmethod
    def _ticket(ticket_id: str, status: str) -> dict[str, Any]:
        return {"ticket_id": ticket_id, "date": "2026-11-01", "client_company": "Acme Corp",
                "category": "TECHNICAL", "description": "VPN caída", "agent_id": "AG-01",
                "status": status, "satisfaction_score": None, "updated_at": "2026-11-01T10:00:00Z"}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("down", request=request)
        path, method = request.url.path, request.method
        if path == "/auth/login" and method == "POST":
            return httpx.Response(200, json={"access_token": SERVICE_TOKEN, "token_type": "bearer"})
        if request.headers.get("authorization") != f"Bearer {SERVICE_TOKEN}":
            return httpx.Response(401, json={"detail": "Not authenticated"})

        if path == "/api/incidents" and method == "GET":
            status = request.url.params.get("status")
            items = [t for t in self.tickets.values() if not status or t["status"] == status]
            return httpx.Response(200, json=items)
        if path == "/api/incidents" and method == "POST":
            body = json.loads(request.content)
            ticket_id = f"NXV-{len(self.tickets) + 482:06d}"
            self.tickets[ticket_id] = {**self._ticket(ticket_id, "OPEN"), **body}
            return httpx.Response(201, json=self.tickets[ticket_id])
        if path.startswith("/api/incidents/") and path.endswith("/status") and method == "PATCH":
            ticket_id = path.split("/")[3]
            ticket = self.tickets.get(ticket_id)
            if ticket is None:
                return httpx.Response(404, json={"detail": f"No existe el ticket '{ticket_id}'."})
            target = json.loads(request.content)["status"]
            allowed = {"OPEN": ["CLOSED", "DISCARDED"], "CLOSED": ["OPEN"], "DISCARDED": []}[ticket["status"]]
            if target not in allowed:
                return httpx.Response(409, json={"detail": {
                    "code": "invalid_transition", "message": f"No se puede pasar de {ticket['status']} a {target}.",
                    "current_status": ticket["status"], "requested_status": target, "allowed": allowed}})
            ticket["status"] = target
            return httpx.Response(200, json=ticket)
        if path.startswith("/api/incidents/") and method == "GET":
            ticket = self.tickets.get(path.rsplit("/", 1)[1])
            return httpx.Response(200, json=ticket) if ticket else httpx.Response(404, json={"detail": "No existe"})

        if path == "/inventory/products" and method == "GET":
            return httpx.Response(200, json=self.products)
        if path.startswith("/inventory/products/") and method == "GET":
            pid = int(path.rsplit("/", 1)[1])
            match = [p for p in self.products if p["id"] == pid]
            return httpx.Response(200, json=match[0]) if match else httpx.Response(404, json={"detail": "Asset not found"})
        return httpx.Response(405, json={"detail": "Method not allowed"})


@pytest.fixture()
def fake_api() -> FakeNexovaApi:
    return FakeNexovaApi()


@pytest.fixture()
async def http(fake_api: FakeNexovaApi) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        resource_url=RESOURCE, oauth_issuer=ISSUER, api_url="http://api.test",
        api_email="mcp-service@nexova.test", api_password="not-a-secret",
    )
    app = create_app(settings, auth_server=AUTH_SERVER, jwk_source=TEST_JWK,
                     api_transport=httpx.MockTransport(fake_api.handler))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://mcp.nexova.test") as client:
            yield client


class Rpc:
    """Cliente JSON-RPC mínimo sobre Streamable HTTP (modo stateless + JSON)."""

    def __init__(self, http: httpx.AsyncClient, token: str | None) -> None:
        self.http = http
        self.token = token
        self._id = 0

    async def raw(self, method: str, params: dict[str, Any] | None = None) -> httpx.Response:
        self._id += 1
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
                   "MCP-Protocol-Version": "2025-06-18"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}
        return await self.http.post("/mcp", headers=headers, json=body)

    async def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self.raw(method, params)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert "error" not in payload, payload
        return payload["result"]

    async def tool(self, name: str, **arguments: Any) -> dict[str, Any]:
        return await self.call("tools/call", {"name": name, "arguments": arguments})


def tool_error(result: dict[str, Any]) -> dict[str, Any]:
    assert result.get("isError") is True, result
    return json.loads(result["content"][0]["text"])["error"]
