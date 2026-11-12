# Agente de soporte con LangGraph — Parte 1: migración y flujo

El RAG de Nexova (`data/pipelines/rag.py`) deja de ser una caja negra: el mismo
comportamiento vive ahora en un grafo de LangGraph con estado, nodos y
transiciones explícitas, checkpoints en SQLite y un trace JSON por corrida.
El RAG no se reescribe: el grafo llama a `retrieve()` y `generate_answer()` del
pipeline a través de `services/api/knowledge/pipeline_bridge.py`.

## Grafo (Parte 1)

> La Parte 2 añade el enrutador y las tools en vivo: ver [Parte 2](#parte-2--herramientas-fuera-del-rag). Las rutas de esta sección siguen existiendo cuando la pregunta solo necesita el RAG.

```mermaid
graph TD;
	__start__([__start__]):::first
	receive_question(receive_question)
	retrieve(retrieve)
	generate(generate)
	no_information(no_information)
	reject(reject)
	__end__([__end__]):::last
	__start__ --> receive_question;
	receive_question -. pregunta vacía .-> reject;
	receive_question -. pregunta válida .-> retrieve;
	retrieve -. con contexto .-> generate;
	retrieve -. sin contexto ≥ min_score .-> no_information;
	generate --> __end__;
	no_information --> __end__;
	reject --> __end__;
```

Diagrama generado con `get_agent_graph().get_graph().draw_mermaid()`. Las etiquetas de las aristas se añadieron a mano.

| Nodo | Responsabilidad | Llama a |
|---|---|---|
| `receive_question` | Normaliza la pregunta (`strip`); si queda vacía marca `error="empty_question"` | — |
| `retrieve` | Recupera los chunks que superan `min_score` (0.30) | `rag.retrieve(question)` |
| `generate` | Genera la respuesta con el contexto **ya recuperado** | `rag.generate_answer(question, context)` |
| `no_information` | Respuesta honesta fija, sin gastar una llamada al LLM | — |
| `reject` | Cierra la corrida con el error de pregunta vacía | — |

Ningún nodo llama a `rag.query()` (recuperación y generación juntas). El eval 4 lo comprueba: en cada corrida hay una sola recuperación, y la generación recibe exactamente el contexto que el trace atribuye a `retrieve`.

## Estado (`services/api/agent/state.py`)

`question`, `context`, `answer` y `error`. **No incluye historial de conversación.** Cada consulta del asistente de ventas es independiente: el RAG tampoco lo usaba y ningún nodo lo lee. Arrastrarlo solo engordaría cada checkpoint. Si la Parte 2 necesita memoria entre turnos, se añadirá con su justificación.

## Compilación

`build_graph()` termina en `compile_graph()`. Esta función compila el grafo y lanza `AgentGraphBuildError` en dos casos:
- **Errores que detecta LangGraph:** una arista hacia un nodo inexistente o un grafo sin punto de entrada.
- **Errores que LangGraph deja pasar:** un nodo inalcanzable desde `START` o sin camino hasta `END`.

La API compila el grafo en su `startup` (`get_agent_graph()`, cacheado), así que un grafo roto impide arrancar en vez de fallar en la primera petición.

## Checkpointing

- **Backend:** `SqliteSaver` (`langgraph-checkpoint-sqlite`), en `services/api/data/agent/checkpoints.sqlite`. La ruta se cambia con `AGENT_CHECKPOINT_PATH`.
- **Identificador:** cada corrida usa `thread_id = run_id`.
- **Inspección:** las transiciones se consultan después con:

```python
from agent.graph import get_agent_graph
from agent.runner import run_config
history = list(get_agent_graph().get_state_history(run_config(run_id)))
```

Una corrida completa deja 5 checkpoints: entrada, `receive_question`, `retrieve`, nodo final y estado terminal.

## Trace

- **Dónde:** `services/api/data/agent/traces/<run_id>.json`. La ruta se cambia con `AGENT_TRACE_DIR`.
- **Contenido:** `run_id`, `question`, `status` (`completed` / `rejected` / `failed`), `nodes` en orden y `final_answer`. Por cada paso: `order`, `node`, `started_at`, `duration_ms`, `output` (lo que añadió al estado) y `error`.
- **Si un nodo falla:** el trace se escribe igual, con `status="failed"` y el nombre del nodo que falló.
- **Lectura:** `agent.tracing.load_trace(run_id)`.
- **Privacidad:** el trace se queda en el servidor y no incluye las puntuaciones de Qdrant. La API solo devuelve `answer` y `run_id`.

Traces reales de ejemplo, que además son los fixtures de los evals de anclaje, en `tests/pipelines/fixtures/agent_traces/`:
- `discount-22.json`
- `headhunting-fee.json`
- `out-of-domain.json`

Se regeneran con:

```bash
QDRANT_URL=http://localhost:6333 uv run --project services/api python scripts/agent_record_traces.py
```

## Endpoint

`POST /agent/query`, con JWT y el body `{"question": "..."}`. Responde `{"answer", "run_id"}`. Convive con `POST /knowledge/query`, que no cambia, y el backoffice sigue usando este último.

| Caso | Respuesta |
|---|---|
| Corrida correcta | `200` |
| Pregunta vacía (la detecta el grafo, queda en el trace) | `422` `"La pregunta está vacía…"` |
| Fallo en cualquier nodo (Qdrant, LLM, checkpoint) | `503` `"Agent unavailable."`, con el detalle solo en el log |
| Sin token | `401` |

## Evals

```bash
uv run --project services/api pytest tests/pipelines/test_agent_evals.py -v
```

- **Evals de flujo:** se ejecutan con fakes de `retrieve`/`generate_answer` y un checkpointer en memoria, sin servicios externos. Cubren el orden `retrieve → generate`, la ruta sin contexto, la pregunta vacía, el contrato de nodos, el checkpointing, los errores estructurales y el trace de un nodo que falla.
- **Evals de anclaje:** leen los traces reales y comprueban dos cosas:
  - la política del 22 % (sin descuento; las excepciones las aprueba Marcos Ibáñez);
  - la facturación 30/30/40 del headhunting, y que la pregunta fuera de dominio va por `no_information`.

El resultado de la última ejecución está en [`eval-results.txt`](eval-results.txt). Los tests del RAG (`test_rag.py`) siguen pasando: los evals del agente los complementan, no los sustituyen.

## Nota sobre el BRIEFING

El enunciado del reto menciona `company-BRIEFING.md`, pero el monorepo no tiene ese archivo. La base de conocimiento de la empresa es `docs/company-knowledge-base/`, la misma que indexa el RAG, y los evals de anclaje se verifican contra ella.

---

## Parte 2 — Herramientas fuera del RAG

El RAG responde políticas y procedimientos, que son conocimiento estable. El estado de un ticket o el stock de un producto son **datos operativos que cambian en tiempo real**. Por eso no se indexan en Qdrant: los consultan **tools de solo lectura** que llaman a los servicios reales en el momento de la pregunta.

### Grafo

```mermaid
graph TD;
	__start__([__start__]) --> receive_question;
	receive_question -. pregunta vacía .-> reject;
	receive_question -. válida .-> route_question;
	route_question -. tickets .-> lookup_tickets;
	route_question -. inventario .-> lookup_inventory;
	route_question -. rag .-> retrieve;
	lookup_tickets -.-> lookup_inventory;
	lookup_tickets -.-> retrieve;
	lookup_inventory -.-> retrieve;
	lookup_tickets & lookup_inventory & retrieve -. hay datos .-> generate;
	lookup_tickets & lookup_inventory & retrieve -. tool falló / no encontrado .-> tool_fallback;
	retrieve -. sin contexto .-> no_information;
	generate & tool_fallback & no_information & reject --> __end__([__end__]);
```

| Nodo | Responsabilidad |
|---|---|
| `route_question` | Decide qué fuentes necesita la pregunta: `rag`, `tickets`, `inventory` o varias. |
| `lookup_tickets` | Tool de tickets: `GET /api/incidents/{id}` o `GET /api/incidents?status=&category=&client_company=`. |
| `lookup_inventory` | Tool de inventario: `GET /inventory/products`, filtrado por nombre o SKU. |
| `generate` | Llama a `generate_answer(question, context)` del RAG, sin cambios. Los datos de las tools entran como chunks "(en vivo)" antes de los del RAG. |
| `tool_fallback` | Respuesta fija y honesta cuando una tool falló o no encontró el recurso. No usa el LLM, así que no puede inventar un estado. |

**Cómo avanza el grafo:** después de `route_question` y de cada fuente, la arista condicional `next_step` salta a la siguiente fuente pendiente. El orden es fijo: tickets, después inventario, después RAG. Cuando no queda ninguna fuente, elige el nodo de respuesta:

| Situación | Nodo |
|---|---|
| Hay datos (chunks del RAG o alguna tool con `ok`) | `generate` |
| No hay datos y alguna tool falló | `tool_fallback` |
| No hay datos y ninguna tool falló | `no_information` |

### Enrutamiento (`agent/routing.py`)

1. **LLM:** el modelo de generación del RAG (`get_llm_client` y `generation_model`, a través del bridge) devuelve un JSON que se valida como `RouteDecision`. La decisión incluye `ticket_id`, filtros y `product`.
2. **Reglas de respaldo:** se usan si el LLM falla, tarda o responde algo inválido. Detectan el patrón `NXV-…` o "ticket N", las palabras "tickets" o "incidencias", y las palabras "stock", "inventario" o "quedan". Si nada coincide, la pregunta va al RAG.
3. **Id explícito:** si la pregunta menciona un id de ticket, ese id manda sobre lo que extrajo el modelo.

El usuario nunca indica la fuente. En el trace, `route.method` dice si decidió el LLM (`llm`) o las reglas (`rules`).

### Contratos de las tools (`agent/tools/`)

| Tool | Entrada (Pydantic) | Salida (Pydantic) |
|---|---|---|
| `lookup_tickets` | `TicketQuery{ticket_id: NXV-\d{6}?, status?, category?, client_company?, limit}` | `TicketToolResult{status, query, tickets: [TicketInfo{ticket_id, date, client_company, category, description, agent_id, status, satisfaction_score, updated_at}], detail, duration_ms}` |
| `lookup_inventory` | `InventoryQuery{product?}` | `InventoryToolResult{status, query, products: [ProductStock{id, name, sku, category, office, current_stock, min_stock_threshold, below_threshold}], detail, duration_ms}` |

- **Una responsabilidad por tool:** cada tool consulta un solo servicio.
- **Solo lectura:** la única salida HTTP es `transport.http_get`, que solo sabe hacer `GET`. El eval T7 lo verifica.
- **Timeout numérico:** `AGENT_TOOL_TIMEOUT_SECONDS`, **4.0 s** por defecto, aplicado en cada petición. En la prueba real contra una IP sin respuesta, la tool cortó a los 4,0 s y el grafo respondió por `tool_fallback`.
- **Sin excepciones:** las tools nunca lanzan. Un timeout, un error de red, un 5xx, un 401/403, un 404 o un JSON inválido se convierten en `status = timeout | error | not_found`.
- **Auth:**
  - `GET /api/incidents*` exige JWT. `/agent/query` reenvía el JWT de quien llama mediante un `ContextVar`, y los scripts usan `INCIDENTS_API_TOKEN`. El token nunca entra en el estado, los checkpoints ni el trace (eval T10).
  - `GET /inventory/products` no tiene auth en la API actual.
- **Destino:** `AGENT_TOOLS_BASE_URL`, `http://localhost:8000` por defecto. Es la propia API, y vale igual dentro del contenedor.

### Datos reales del gestor de incidentes

Antes de esta parte, el gestor de incidentes solo analizaba CSV y guardaba en memoria un resumen agregado. Ahora:

- `POST /api/incidents/analyze` además hace upsert de los tickets **válidos** en Supabase. Usa la misma validación (`incidents_analyzer.validate_record`) y **no guarda emails**.
- La tabla es `incident` (modelo `Incident` en `models.py`, migración de referencia `006_incidents.sql`). La crea `create_db_and_tables()` al arrancar.
- Hay dos endpoints nuevos de solo lectura con JWT: `GET /api/incidents` (filtros y `limit`) y `GET /api/incidents/{ticket_id}` (404 si no existe).
- Los datos se cargaron subiendo `scripts/incidents-COMPANY.csv`: 100 registros, de los que 96 son válidos.

### Trace

Cada trace añade estos campos:
- `route`: la decisión del enrutador;
- `sources_used`: las fuentes en orden, por ejemplo `["tickets", "rag"]`;
- en los pasos `lookup_*`, el `ToolResult` completo: `status`, `detail`, `duration_ms` y los datos.

Traces reales en `tests/pipelines/fixtures/agent_traces/`:

| Fixture | Fuentes | Nodo final |
|---|---|---|
| `ticket-status.json` | `tickets` | `generate` |
| `rag-policy.json` | `rag` | `generate` |
| `ticket-plus-policy.json` | `tickets → rag` | `generate` |
| `stock.json` | `inventory` | `generate` |

### Evals

```bash
uv run --project services/api pytest tests/pipelines/test_agent_tools_evals.py -v
```

**Con un servicio simulado** (`httpx.MockTransport`, que ejecuta las tools reales contra rutas y respuestas iguales a las de la API):

| Eval | Qué comprueba |
|---|---|
| T1 | Pregunta de ticket → tool de tickets, sin RAG |
| T2 | Pregunta de política → RAG, sin tools y sin llamadas HTTP |
| T3 | Timeout → `tool_fallback`, sin estado inventado |
| T4 | Ticket inexistente → "No encontré…" |
| T5 | Pregunta mixta → tickets antes que RAG |
| T6 | Pregunta de stock → tool de inventario |
| T7 | Solo peticiones GET |
| T8 | Si el LLM del router cae, se usan las reglas |
| T10 | El JWT se reenvía, pero no aparece en el trace |

También hay evals para la tool fallida en una pregunta mixta, la caída del inventario, el timeout numérico configurable y las reglas del router.

**Con traces reales (T9):** el estado en vivo del ticket aparece en la respuesta, la pregunta de política usa solo el RAG, la mixta va tickets → RAG, y la de stock usa inventario.
