"""OAuth (MCP Auth): metadata pública y nada de `/mcp` sin un token válido."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from conftest import ISSUER, RESOURCE, Rpc, make_token

pytestmark = pytest.mark.anyio


async def test_protected_resource_metadata_is_public(http) -> None:
    response = await http.get("/.well-known/oauth-protected-resource/mcp")
    assert response.status_code == 200
    body = response.json()
    assert body["resource"] == RESOURCE
    assert body["authorization_servers"] == [ISSUER]
    assert set(body["scopes_supported"]) == {"incidents:read", "incidents:write", "inventory:read"}
    assert "inventory:write" not in body["scopes_supported"]


@pytest.mark.parametrize("method", ["initialize", "tools/list", "tools/call"])
async def test_no_token_is_401_with_resource_metadata(http, method) -> None:
    response = await Rpc(http, token=None).raw(method, {"name": "get_ticket", "arguments": {}})
    assert response.status_code == 401
    header = response.headers["www-authenticate"]
    assert header.startswith("Bearer")
    assert 'resource_metadata="https://mcp.nexova.test/.well-known/oauth-protected-resource/mcp"' in header


@pytest.mark.parametrize(
    ("token", "expected_error"),
    [
        (make_token(exp_offset=-3600), "invalid_token"),  # caducado
        (make_token(key=rsa.generate_private_key(public_exponent=65537, key_size=2048)), "invalid_token"),  # firma ajena
        (make_token(aud="https://otro-recurso.test"), "invalid_audience"),
        (make_token(iss="https://evil.test/oidc"), "invalid_issuer"),
        ("no-es-un-jwt", "invalid_token"),
    ],
)
async def test_invalid_tokens_are_rejected_before_tools(http, fake_api, token, expected_error) -> None:
    response = await Rpc(http, token=token).raw("tools/list")
    assert response.status_code == 401
    assert f'error="{expected_error}"' in response.headers["www-authenticate"]
    assert fake_api.requests == []  # nunca se llegó a la API


async def test_cors_preflight_does_not_require_token(http) -> None:
    origin = "https://www.mcpplayground.tech"
    preflight = await http.options(
        "/mcp",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "authorization,content-type"},
    )
    assert preflight.status_code == 200

    # El 401 real expone WWW-Authenticate al navegador para que descubra Logto.
    rejected = await http.post("/mcp", headers={"Origin": origin}, json={})
    assert rejected.status_code == 401
    assert "WWW-Authenticate" in rejected.headers["access-control-expose-headers"]


async def test_identity_is_per_request(http, caplog) -> None:
    """Modo stateless: cada petición usa la identidad de su propio token."""
    caplog.set_level("INFO", logger="nexova_mcp.audit")
    await Rpc(http, make_token(client_id="agent-a")).tool("get_ticket", ticket_id="NXV-000482")
    await Rpc(http, make_token(client_id="agent-b")).tool("get_ticket", ticket_id="NXV-000482")
    clients = [r.getMessage() for r in caplog.records if '"tool_call"' in r.getMessage()]
    assert '"client_id": "agent-a"' in clients[0]
    assert '"client_id": "agent-b"' in clients[1]
