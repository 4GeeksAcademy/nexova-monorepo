# Agente de soporte con LangGraph — Parte 1: migración y flujo

El RAG de Nexova (`data/pipelines/rag.py`) deja de ser una caja negra: el mismo
comportamiento vive ahora en un grafo de LangGraph con estado, nodos y
transiciones explícitas, checkpoints en SQLite y un trace JSON por corrida.
El RAG no se reescribe: el grafo llama a `retrieve()` y `generate_answer()` del
pipeline a través de `services/api/knowledge/pipeline_bridge.py`.

## Grafo

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
