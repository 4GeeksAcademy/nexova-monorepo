"""Discovery, scopes por tool, flujos completos, errores con código y log de auditoría."""

from __future__ import annotations

import json

import pytest

from conftest import Rpc, make_token, tool_error

pytestmark = pytest.mark.anyio

EXPECTED_TOOLS = {"list_tickets", "get_ticket", "create_ticket", "update_ticket_status", "query_inventory"}


@pytest.fixture()
def rpc(http) -> Rpc:
    return Rpc(http, make_token())


# --- Discovery ---------------------------------------------------------------

async def test_discovery_lists_documented_tools(rpc) -> None:
    init = await rpc.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                         "clientInfo": {"name": "test", "version": "1"}})
    assert "SOLO LECTURA" in init["instructions"]

    tools = {t["name"]: t for t in (await rpc.call("tools/list"))["tools"]}
    assert set(tools) == EXPECTED_TOOLS
    for tool in tools.values():
        assert len(tool["description"]) > 80
        assert "scope" in tool["description"]
        assert tool["inputSchema"]["type"] == "object"
        assert tool["outputSchema"]["type"] == "object"

    assert tools["update_ticket_status"]["inputSchema"]["properties"]["status"]["enum"] == ["open", "in_progress", "resolved", "discarded"]
    assert tools["get_ticket"]["inputSchema"]["properties"]["ticket_id"]["pattern"] == r"^NXV-\d{6}$"
    assert tools["query_inventory"]["annotations"]["readOnlyHint"] is True
    assert tools["create_ticket"]["annotations"]["readOnlyHint"] is False


# --- Tickets -----------------------------------------------------------------

async def test_ticket_full_flow(rpc, fake_api) -> None:
    created = (await rpc.tool("create_ticket", client_company="Globex", title="Factura duplicada", description="Se cobró dos veces", category="process_error", origin="customer", branch="central"))["structuredContent"]
    assert created["status"] == "open"
    ticket_id = created["ticket_id"]
    assert ticket_id == "NXV-000484"  # id 484 de la API presentado como referencia

    started = (await rpc.tool("update_ticket_status", ticket_id=ticket_id, status="in_progress"))["structuredContent"]
    assert started["status"] == "in_progress"
    resolved = (await rpc.tool("update_ticket_status", ticket_id=ticket_id, status="resolved"))["structuredContent"]
    assert resolved["status"] == "resolved"

    fetched = (await rpc.tool("get_ticket", ticket_id=ticket_id))["structuredContent"]
    assert fetched["status"] == "resolved" and fetched["client_company"] == "Globex"

    listed = (await rpc.tool("list_tickets", status="resolved"))["structuredContent"]
    assert ticket_id in [t["ticket_id"] for t in listed["tickets"]]

    patch = [r for r in fake_api.requests if r.method == "PATCH"]
    assert [r.url.path for r in patch] == ["/api/incidents/484/status"] * 2  # ciclo de vida, no PATCH genérico


async def test_invalid_transition_has_its_own_code(rpc) -> None:
    error = tool_error(await rpc.tool("update_ticket_status", ticket_id="NXV-000483", status="open"))
    assert error["code"] == "INVALID_TRANSITION"
    assert error["details"] == {"current_status": "discarded", "requested_status": "open", "allowed": []}


async def test_skipping_a_step_lists_the_allowed_transitions(rpc) -> None:
    error = tool_error(await rpc.tool("update_ticket_status", ticket_id="NXV-000482", status="resolved"))
    assert error["code"] == "INVALID_TRANSITION"
    assert error["details"]["allowed"] == ["in_progress", "discarded"]


async def test_missing_ticket_is_not_found(rpc) -> None:
    assert tool_error(await rpc.tool("get_ticket", ticket_id="NXV-999999"))["code"] == "NOT_FOUND"


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("get_ticket", {"ticket_id": "482"}),
        ("update_ticket_status", {"ticket_id": "NXV-000482", "status": "SOLVED"}),
        ("create_ticket", {"title": "x", "description": "x", "category": "OTHER", "origin": "customer", "branch": "central"}),
        ("create_ticket", {"title": "x", "description": "x", "category": "other", "origin": "customer", "branch": "hq"}),
        ("list_tickets", {"limit": 500}),
    ],
)
async def test_validation_errors_have_their_own_code(rpc, fake_api, tool, arguments) -> None:
    error = tool_error(await rpc.call("tools/call", {"name": tool, "arguments": arguments}))
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"]["errors"]
    assert not [r for r in fake_api.requests if r.url.path != "/auth/login"]


async def test_blank_optional_fields_count_as_not_provided(rpc, fake_api) -> None:
    """Clientes con formulario (MCP Playground) mandan `""` en los opcionales sin rellenar."""
    created = (await rpc.tool("create_ticket", title="Factura duplicada", description="Se cobró dos veces", category="process_error", origin="customer", branch="central", client_company=""))["structuredContent"]
    assert created["status"] == "open"
    post = next(r for r in fake_api.requests if r.method == "POST" and r.url.path == "/api/incidents")
    assert "client_company" not in json.loads(post.content)  # opcional sin rellenar

    listed = await rpc.tool("list_tickets", status="", category="", client_company="", limit="")
    assert listed["structuredContent"]["count"] == len(fake_api.tickets)

    stock = await rpc.tool("query_inventory", action="", product_id="", search="", office="",
                           below_threshold_only="", limit="")
    assert stock["structuredContent"]["count"] == 2

    # Un valor no vacío sigue validándose.
    assert tool_error(await rpc.tool("create_ticket", title="x", description="x", category="nope",
                                     origin="customer", branch="central"))["code"] == "VALIDATION_ERROR"


# --- Scopes (mínimo privilegio) ----------------------------------------------

@pytest.mark.parametrize(
    ("scope", "tool", "arguments", "required"),
    [
        ("incidents:read inventory:read", "create_ticket",
         {"title": "x", "description": "x", "category": "other", "origin": "customer", "branch": "central"}, "incidents:write"),
        ("incidents:read inventory:read", "update_ticket_status",
         {"ticket_id": "NXV-000482", "status": "in_progress"}, "incidents:write"),
        ("inventory:read", "get_ticket", {"ticket_id": "NXV-000482"}, "incidents:read"),
        ("incidents:read incidents:write", "query_inventory", {"action": "list"}, "inventory:read"),
    ],
)
async def test_each_tool_requires_its_scope(http, fake_api, scope, tool, arguments, required) -> None:
    error = tool_error(await Rpc(http, make_token(scope)).tool(tool, **arguments))
    assert error["code"] == "FORBIDDEN_SCOPE"
    assert error["details"]["required_scope"] == required
    assert fake_api.tickets[482]["status"] == "open"


# --- Inventario (solo lectura) -----------------------------------------------

async def test_inventory_queries(rpc) -> None:
    by_name = (await rpc.tool("query_inventory", search="ratones ergonomicos"))["structuredContent"]
    assert [p["sku"] for p in by_name["products"]] == ["MOU-ERG-01"]
    assert by_name["products"][0]["below_threshold"] is True

    shortage = (await rpc.tool("query_inventory", below_threshold_only=True))["structuredContent"]
    assert shortage["count"] == 1

    one = (await rpc.tool("query_inventory", action="get", product_id=2))["structuredContent"]
    assert one["products"][0]["office"] == "Miami"


@pytest.mark.parametrize("action", ["create", "update", "delete", "adjust_stock"])
async def test_inventory_write_is_explicitly_rejected(rpc, fake_api, action) -> None:
    error = tool_error(await rpc.tool("query_inventory", action=action, product_id=1))
    assert error["code"] == "READ_ONLY_RESOURCE"
    assert error["details"]["allowed_actions"] == ["list", "get"]
    assert all(r.method in ("GET",) or r.url.path == "/auth/login" for r in fake_api.requests)


async def test_upstream_down_is_reported(rpc, fake_api) -> None:
    fake_api.down = True
    assert tool_error(await rpc.tool("query_inventory"))["code"] == "UPSTREAM_UNAVAILABLE"


# --- Auditoría -----------------------------------------------------------------

async def test_one_audit_line_per_invocation(rpc, caplog) -> None:
    caplog.set_level("INFO", logger="nexova_mcp.audit")
    await rpc.tool("get_ticket", ticket_id="NXV-000482")
    await rpc.tool("query_inventory", action="delete", product_id=1)

    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "nexova_mcp.audit"]
    calls = [line for line in lines if line["event"] == "tool_call"]
    assert [(c["tool"], c["result"], c["error_code"]) for c in calls] == [
        ("get_ticket", "ok", None),
        ("query_inventory", "error", "READ_ONLY_RESOURCE"),
    ]
    assert calls[0]["client_id"] == "playground-app"
    assert "incidents:read" in calls[0]["scopes"]
    assert all("token" not in json.dumps(c) for c in calls)


async def test_auth_rejection_is_logged(http, caplog) -> None:
    caplog.set_level("INFO", logger="nexova_mcp.audit")
    await Rpc(http, None).raw("tools/list")
    events = [json.loads(r.getMessage()) for r in caplog.records if r.name == "nexova_mcp.audit"]
    assert events[-1]["event"] == "auth_rejected" and events[-1]["status"] == 401
