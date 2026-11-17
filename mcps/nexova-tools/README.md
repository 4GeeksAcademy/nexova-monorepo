# `nexova-tools` — Nexova MCP Server

_[Versión en español](./README.es.md) (full guide, including Logto setup and MCP Playground steps)._

An MCP server that exposes Nexova's **Incidents Manager** (support tickets) and **inventory** to any authorized MCP client: the LangGraph agent in `services/api`, MCP Playground, or other teams. It is protected with **OAuth 2.1 via [MCP Auth](https://mcp-auth.dev/)** (`mcpauth`, resource server mode), with **Logto** as the authorization server. Every tool calls the same `services/api` endpoints that the backoffice uses; no business logic is duplicated.

## Design

### Transport

**Streamable HTTP, stateless, JSON responses.**
- **Why not stdio:** the server has several remote clients, and MCP's authorization spec (bearer tokens plus Protected Resource Metadata, RFC 9728) only applies over HTTP.
- **Why stateless:** each request carries its own OAuth identity, and no sticky sessions are needed.

### Auth

- `GET /.well-known/oauth-protected-resource/mcp` is public.
- All of `/mcp` (`initialize`, `tools/list`, `tools/call`) sits behind MCP Auth's bearer middleware.
- The middleware checks the issuer, the signature against Logto's JWKS (cached), expiry, and `aud == MCP_RESOURCE_URL`.

### Least privilege

| Tool | Scope | API endpoint |
|---|---|---|
| `list_tickets` | `incidents:read` | `GET /api/incidents` |
| `get_ticket` | `incidents:read` | `GET /api/incidents/{ticket_id}` |
| `create_ticket` | `incidents:write` | `POST /api/incidents` |
| `update_ticket_status` | `incidents:write` | `PATCH /api/incidents/{ticket_id}/status` (lifecycle) |
| `query_inventory` | `inventory:read` | `GET /inventory/products[/{id}]` |

The agent only gets `incidents:read inventory:read`.

### Read-only inventory, by design

1. There is no `inventory:write` scope.
2. `query_inventory` declares the write actions (`create`, `update`, `delete`, `adjust_stock`) in its schema so it can explicitly reject them with `READ_ONLY_RESOURCE`. These attempts are audited.
3. The tool only receives a GET-only `InventoryReader`.
4. The tool carries the `readOnlyHint` annotation.

### Ticket lifecycle

The allowed transitions are `OPEN→CLOSED`, `OPEN→DISCARDED` and `CLOSED→OPEN`. `DISCARDED` is final. Any other transition returns `INVALID_TRANSITION`.

## Error codes

**HTTP layer.** MCP Auth returns `401` with `WWW-Authenticate: Bearer error="…", resource_metadata="…"`. The `error` value is one of:
- `missing_auth_header`
- `invalid_token`
- `invalid_issuer`
- `invalid_audience`

**Tool layer.** The result has `isError: true` and its text is `{"error": {"code", "message", "details"}}`. The code is one of:

| Code | Meaning |
|---|---|
| `FORBIDDEN_SCOPE` | The token is valid but lacks this tool's scope. |
| `VALIDATION_ERROR` | The arguments don't match the tool's schema. |
| `READ_ONLY_RESOURCE` | A write was attempted on the inventory. |
| `NOT_FOUND` | The ticket or product doesn't exist. |
| `INVALID_TRANSITION` | The status change isn't allowed. |
| `UPSTREAM_UNAVAILABLE` | The Nexova API is down, timed out or returned a 5xx. |
| `UPSTREAM_AUTH_ERROR` | The service account can't log in to the API. |
| `INTERNAL_ERROR` | An unexpected server error. |

## Audit log

The `nexova_mcp.audit` logger writes one JSON line per tool call, with these fields:
- `tool`
- `client_id`
- `subject`
- `scopes`
- `result`
- `error_code`
- `duration_ms`

Requests rejected by auth are logged as `auth_rejected`. Arguments and tokens are never logged.

## Run

```bash
docker compose up --build mcp
```

```bash
cd mcps/nexova-tools && uv sync && uv run python -m nexova_mcp.server
```

```bash
cd mcps/nexova-tools && uv run pytest
```

**Configuration.** The service reads these variables:
- `MCP_RESOURCE_URL`
- `MCP_OAUTH_ISSUER`
- `NEXOVA_API_URL`
- `NEXOVA_API_EMAIL`
- `NEXOVA_API_PASSWORD`
- `MCP_UPSTREAM_TIMEOUT_SECONDS`
- `MCP_HOST`
- `MCP_PORT`

The Spanish README describes each variable.
