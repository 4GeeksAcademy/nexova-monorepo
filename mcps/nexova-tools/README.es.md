# `nexova-tools` — MCP Server de Nexova

_[English version](./README.md)_

Servidor MCP que expone el **Incidents Manager** (tickets de soporte) y el **inventario** de Nexova a cualquier cliente MCP autorizado: el agente LangGraph de `services/api`, MCP Playground u otros equipos. Está protegido con **OAuth 2.1 vía [MCP Auth](https://mcp-auth.dev/)** (`mcpauth`, modo resource server), con **Logto** como authorization server.

No reimplementa lógica de negocio. Cada tool llama a los mismos endpoints de `services/api` que usa el backoffice.

## Decisiones de diseño

### Transporte: Streamable HTTP (stateless)

- **Por qué no stdio:** stdio solo sirve a un cliente local que lanza el proceso y no tiene autenticación de red. Este servidor lo consumen varios clientes remotos (el agente en otro contenedor, Playground desde el navegador y futuros partners). Además, la especificación de autorización de MCP (bearer + Protected Resource Metadata, RFC 9728) se define sobre HTTP.
- **Por qué stateless:** cada petición HTTP crea su propio contexto MCP. Así la identidad OAuth (`mcp_auth.auth_info`) es siempre la del token de esa petición y nunca la de una sesión anterior. También permite escalar horizontalmente sin sesiones pegajosas.
- **Respuestas en JSON**, no SSE: ninguna tool emite progreso.

### Autenticación y mínimo privilegio

**Rutas:**
- `GET /.well-known/oauth-protected-resource/mcp` es **pública**. Sirve la metadata del recurso: `resource`, `authorization_servers` (Logto) y `scopes_supported`.
- `/mcp` está **entero** detrás del bearer middleware de MCP Auth, incluidos `initialize`, `tools/list` y `tools/call`. Sin un access token válido no se puede ni descubrir las tools.

**Validación del token:**
- **Issuer:** debe ser el de Logto.
- **Firma:** se comprueba con el JWKS de Logto, que se cachea 5 min.
- **Caducidad.**
- **Audience:** `aud == MCP_RESOURCE_URL` (RFC 8707). Un token emitido para otra API se rechaza.

**Scopes por tool:**

| Tool | Scope | Endpoint de la API | Tipo |
|---|---|---|---|
| `list_tickets` | `incidents:read` | `GET /api/incidents` | lectura |
| `get_ticket` | `incidents:read` | `GET /api/incidents/{ticket_id}` | lectura |
| `create_ticket` | `incidents:write` | `POST /api/incidents` | escritura |
| `update_ticket_status` | `incidents:write` | `PATCH /api/incidents/{ticket_id}/status` | escritura (ciclo de vida) |
| `query_inventory` | `inventory:read` | `GET /inventory/products[/{id}]` | lectura |

- Cada tool comprueba **su** scope. Un token con `inventory:read` no puede leer tickets.
- El agente LangGraph recibe solo `incidents:read inventory:read`.
- **No existe `inventory:write`.**

### Inventario de solo lectura por diseño

El inventario no es de solo lectura porque falte un endpoint. Lo garantizan cuatro barreras:

1. **No existe un scope de escritura** de inventario: ningún cliente puede pedirlo.
2. **Rechazo explícito.** `query_inventory` declara en su esquema las acciones `create`, `update`, `delete` y `adjust_stock` precisamente para rechazarlas con `READ_ONLY_RESOURCE`. Los intentos quedan además en el log de auditoría. Un agente externo ve en el discovery que esas acciones existen y que se rechazan.
3. **Cliente solo GET.** La tool solo recibe un `InventoryReader`, que únicamente sabe hacer `GET /inventory/*`.
4. **Anotación MCP** `readOnlyHint: true`.

### Ciclo de vida de los tickets

Los cambios de estado pasan **solo** por `PATCH /api/incidents/{id}/status`, nunca por una edición genérica:

| Desde | Hacia |
|---|---|
| `OPEN` | `CLOSED`, `DISCARDED` |
| `CLOSED` | `OPEN` (reabrir) |
| `DISCARDED` | — (estado final) |

Cualquier otra transición devuelve `INVALID_TRANSITION`, con el estado actual y las transiciones permitidas. El id (`NXV-NNNNNN`) y el estado inicial (`OPEN`) los asigna la API al crear el ticket.

### Identidad hacia la API

La API de Nexova emite su propio JWT (HS256), que no es un token OAuth. Por eso el MCP entra con una **cuenta de servicio** (`NEXOVA_API_EMAIL` / `NEXOVA_API_PASSWORD`, vía `POST /auth/login`) y repite el login una vez si recibe un 401. La identidad del cliente OAuth (`client_id`, `sub`, scopes) queda en el log de auditoría de cada invocación.

## Códigos de error

### Capa HTTP (MCP Auth): no llega a las tools

| Situación | HTTP | Cabecera `WWW-Authenticate` |
|---|---|---|
| Sin cabecera `Authorization` | 401 | `Bearer error="missing_auth_header", resource_metadata="…"` |
| Token inválido, firma ajena o caducado | 401 | `Bearer error="invalid_token", …` |
| Issuer desconocido | 401 | `Bearer error="invalid_issuer", …` |
| Token para otro recurso (`aud`) | 401 | `Bearer error="invalid_audience", …` |

El parámetro `resource_metadata` apunta a la Protected Resource Metadata, y con él un cliente OAuth descubre Logto sin configuración previa.

### Capa de tools: `isError: true`

El texto de un error de tool es este JSON:

```json
{"error": {"code": "INVALID_TRANSITION", "message": "No se puede pasar de DISCARDED a OPEN. …",
           "details": {"current_status": "DISCARDED", "requested_status": "OPEN", "allowed": []}}}
```

| `code` | Cuándo |
|---|---|
| `FORBIDDEN_SCOPE` | El token es válido pero no tiene el scope de la tool. `details.required_scope` y `details.granted_scopes` dicen cuál falta. |
| `VALIDATION_ERROR` | Los argumentos no cumplen el esquema (id que no es `NXV-NNNNNN`, estado o categoría fuera del enum, límites…). `details.errors` lista el campo y el motivo. |
| `READ_ONLY_RESOURCE` | Intento de escritura sobre inventario. |
| `NOT_FOUND` | El ticket o el producto no existe. |
| `INVALID_TRANSITION` | La transición de estado no está permitida. |
| `UPSTREAM_UNAVAILABLE` | La API de Nexova no responde, tarda más de `MCP_UPSTREAM_TIMEOUT_SECONDS` (5 s por defecto) o devuelve un 5xx. |
| `UPSTREAM_AUTH_ERROR` | La cuenta de servicio no puede entrar en la API. |
| `INTERNAL_ERROR` | Fallo no previsto del servidor. No se exponen detalles. |

## Trazabilidad

El logger `nexova_mcp.audit` escribe **una línea JSON por invocación de tool**:

```json
{"event": "tool_call", "tool": "query_inventory", "client_id": "nexova-agent", "subject": "nexova-agent",
 "scopes": ["incidents:read", "inventory:read"], "result": "error", "error_code": "READ_ONLY_RESOURCE",
 "duration_ms": 1.2}
```

Los rechazos de MCP Auth se registran como `{"event": "auth_rejected", "status": 401, "path": "/mcp", "www_authenticate": "…"}`. Nunca se registran los argumentos ni el token.

## Configuración

| Variable | Ejemplo | Descripción |
|---|---|---|
| `MCP_RESOURCE_URL` | `https://<codespace>-8100.app.github.dev/mcp` | URL pública del endpoint MCP. Es el *API identifier* del recurso en Logto y el `aud` esperado. |
| `MCP_OAUTH_ISSUER` | `https://<tenant>.logto.app/oidc` | Issuer OIDC de Logto. La configuración se descubre en `/.well-known/openid-configuration`. |
| `NEXOVA_API_URL` | `http://api:8000` | API de Nexova. |
| `NEXOVA_API_EMAIL` / `NEXOVA_API_PASSWORD` | — | Cuenta de servicio en la API: un usuario creado con `POST /users`. |
| `MCP_UPSTREAM_TIMEOUT_SECONDS` | `5` | Timeout hacia la API. |
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8100` | |

### Configurar Logto (una vez)

1. **API resource.** En *API resources → Create*, usa como identifier el valor de `MCP_RESOURCE_URL`. En *Permissions*, crea `incidents:read`, `incidents:write` e `inventory:read`.
2. **Agente (M2M).**
   - En *Applications → Machine-to-machine*, crea la app `nexova-agent`.
   - Asígnale un rol M2M que tenga **solo** `incidents:read` e `inventory:read` del recurso anterior.
   - Copia el App ID y el App secret en `AGENT_MCP_CLIENT_ID` / `AGENT_MCP_CLIENT_SECRET` del `.env` raíz.
   - El token endpoint (`AGENT_MCP_TOKEN_URL`) es `https://<tenant>.logto.app/oidc/token`.
3. **Pruebas manuales (Playground).** Crea una segunda app M2M, por ejemplo `nexova-playground`, con un rol que tenga los **tres** permisos. Así se pueden probar también las tools de escritura.

Para obtener un token de prueba:

```bash
curl -s -u "$CLIENT_ID:$CLIENT_SECRET" https://<tenant>.logto.app/oidc/token -d grant_type=client_credentials -d resource="$MCP_RESOURCE_URL" -d scope="incidents:read incidents:write inventory:read"
```

## Ejecutar

Con Docker, desde la raíz del monorepo (servicio `mcp`, puerto 8100):

```bash
docker compose up --build mcp
```

En local:

```bash
cd mcps/nexova-tools && uv sync && uv run python -m nexova_mcp.server
```

Tests (sin red: Logto y la API se sustituyen por dobles; la verificación del token es la de producción salvo el JWKS):

```bash
cd mcps/nexova-tools && uv run pytest
```

## Validación con MCP Playground (Codespaces)

1. En el Codespace, levanta el stack con `scripts/docker-up.sh` o `docker compose up --build`.
2. En la pestaña *Ports*, cambia la visibilidad del puerto **8100** a **Public**. Copia la URL reenviada, por ejemplo `https://<codespace>-8100.app.github.dev`. Con `localhost` no funciona desde Playground.
3. Pon `MCP_RESOURCE_URL=https://<codespace>-8100.app.github.dev/mcp` en `.env`, usa el mismo valor como identifier del API resource en Logto y reinicia el servicio `mcp`.
4. Abre [MCP Playground](https://www.mcpplayground.tech/playground) y conecta a `https://<codespace>-8100.app.github.dev/mcp` (transporte Streamable HTTP):
   - **Sin token:** la conexión falla con 401. Es lo esperado.
   - **Con token:** añade la cabecera `Authorization: Bearer <token de nexova-playground>`. Si el cliente admite el flujo OAuth, descubrirá Logto por la metadata del recurso.
5. Haz un flujo por tool:

   | Paso | Llamada | Resultado esperado |
   |---|---|---|
   | 1 | `create_ticket` | Ticket `OPEN` con id nuevo |
   | 2 | `update_ticket_status` → `CLOSED` | `CLOSED` |
   | 3 | `update_ticket_status` → `DISCARDED` | `INVALID_TRANSITION` |
   | 4 | `get_ticket` / `list_tickets` | El ticket con su estado |
   | 5 | `query_inventory` (`list`, con `search` o `below_threshold_only`) | Productos |
   | 6 | `query_inventory` con `action: "adjust_stock"` | `READ_ONLY_RESOURCE` |

6. **Intento de escritura sobre inventario.** La tool responde `isError: true` y no llega a la API:

   ```json
   {"error": {"code": "READ_ONLY_RESOURCE",
              "message": "El inventario es de solo lectura vía MCP: la acción 'adjust_stock' no está permitida.",
              "details": {"action": "adjust_stock", "allowed_actions": ["list", "get"]}}}
   ```

   La línea de auditoría correspondiente es `"tool": "query_inventory", "result": "error", "error_code": "READ_ONLY_RESOURCE"`. Con el token del agente, que no tiene scope de escritura, `create_ticket` responde `FORBIDDEN_SCOPE`.
