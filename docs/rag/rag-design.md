# Diseño del RAG de Nexova (Hito 7)

Asistente de ventas para SDR y account managers: responde preguntas sobre líneas de servicio, tarifas, SLA y objeciones con la voz de un vendedor de Nexova. La respuesta **siempre** la genera un LLM a partir del contexto recuperado; la API nunca devuelve chunks, payloads ni puntuaciones.

## 1. Proceso RAG de extremo a extremo

```
docs/company-knowledge-base/*.md
   │  setup()  (scripts/rag_setup.py)
   ▼
chunk_document() ──► embed(chunk) ──► Qdrant `nexova_knowledge` (coseno)
                                              ▲
Pregunta (UI) ─► POST /knowledge/query (JWT) ─┤
   │                                          │
   ▼                                          │
query() = retrieve(question) ── embed(question) + búsqueda top-k + filtro min_score
        + generate_answer(question, contexto) ──► LLM de generación ──► { "answer": "..." }
```

1. **Indexación** (`data/process/rag_indexing.py`, `setup()`): lee los 4 documentos, los divide en chunks, calcula un vector por chunk con `embed()` y recarga la colección.
2. **Recuperación** (`data/pipelines/rag.py`, `retrieve()`): embebe la pregunta con la **misma** `embed()`, pide los `k=5` vecinos más cercanos y descarta los de score `< min_score`. Devuelve payloads (dicts), pueden ser menos de `k` o ninguno.
3. **Generación** (`generate_answer()`): prompt de sistema con la voz y las reglas de negocio + contexto numerado con documento y sección + pregunta. Si no hay contexto, el modelo debe decir que no hay información suficiente. La respuesta termina con una línea `Fuente:` (trazabilidad).
4. **Entrada única** (`query()`): `retrieve()` + `generate_answer()`. El router (`services/api/knowledge/router.py`) solo hace HTTP; importa `query` mediante `pipeline_bridge`. Un agente posterior puede llamar a `retrieve()` y `generate_answer()` por separado.

## 2. Estrategia de chunking

Los documentos fuente no tienen subtítulos (solo un `#` de título), por lo que **no se fragmenta por encabezados ni por tamaño fijo**. Se parte por **bloques separados por línea en blanco**, que en este corpus coinciden con las unidades semánticas:

| Documento | Unidad de chunk | Chunks |
|---|---|---|
| `service-lines` | cada línea de servicio (la introducción se une a la primera) + párrafo de contratación independiente | 4 |
| `pricing-model` | un bloque de tarifas por línea (headhunting, outsourcing, formación) | 3 |
| `hiring-process-sla` | tabla de plazos por perfil, aviso "promedios, no garantía", escalado a las 4 semanas, restricción geográfica | 4 |
| `objection-handling` | cada par objeción → respuesta | 4 |

Total: **15 chunks** (≥3 por documento). Un bloque nunca se corta por la mitad, así una condición (p. ej. "no se debe ofrecer descuento sin aprobación") queda junto a su regla. Los bloques de introducción muy cortos (< 80 caracteres) se unen al siguiente. Cada chunk lleva como primera línea el título del documento para dar contexto al embedding. `section` es la primera frase del bloque (o la objeción citada), truncada a 80 caracteres.

Payload por punto: `company="nexova"`, `source_document`, `section`, `language="es"`, `chunk_index`, `text`.

Idempotencia: estrategia **limpiar-y-recargar** (se borra y recrea la colección) con **IDs deterministas** (`uuid5` de `source_document:chunk_index`); ejecutar `setup()` varias veces nunca duplica puntos.

## 3. Prácticas de embeddings

- **Dos modelos, dos trabajos**: `EMBEDDING_MODEL` (solo `embed()`) y `GENERATION_MODEL` (solo `generate_answer()`), ambos proporcionados por 4Geeks a través de una API compatible con OpenAI (`LLM_BASE_URL`, `LLM_API_KEY`). `generation_model()` falla si ambos IDs coinciden.
- **Misma `embed()`** al indexar y al consultar. Preprocesado: solo `strip()` del texto; no se normaliza más para no alterar cifras ni porcentajes.
- **Dimensión del vector**: la lee `setup()` del primer embedding (depende del modelo elegido).
- **Métrica**: distancia coseno en Qdrant.
- **`min_score` = 0.30** (`RAG_MIN_SCORE`). Con `pplx-embed-v1-0.6b` y coseno, el chunk correcto de cada pregunta del eval queda con score 0.57–0.77, salvo la pregunta de descuento ("del 22% al 15%"), que acierta con 0.37. La pregunta fuera de la base obtiene 0.02. Cualquier umbral entre ~0.10 y 0.36 separa ambos casos; se elige 0.30 para no perder consultas formuladas de forma indirecta (como la del descuento, que debe llegar al chunk de objeciones) manteniendo margen sobre el ruido. Los chunks que pasan el umbral pero no son relevantes (scores 0.30–0.55 en posiciones 2 y 3) los ignora el prompt. Revisar el umbral si se cambia el modelo de embeddings.
- **Modelos usados** (gateway `https://llm.4geeks.ai`, IDs sin el prefijo `litellm/`): embeddings `madrid-spain/openrouter/perplexity/pplx-embed-v1-0.6b` (1024 dimensiones, floats); generación `madrid-spain/openrouter/deepseek/deepseek-v4-flash`. `z-ai/glm-5.3-flash` figura en la lista pero el gateway lo rechaza con "Invalid model name" al generar, y `mimo-v2.5` devolvió contenido vacío en la prueba.

## 4. Reglas de negocio y su cobertura

| Regla (BRIEFING) | Cómo se garantiza |
|---|---|
| Nunca ofrecer descuento sobre el 22 % | Regla 3 del prompt de sistema (remite a Marcos Ibáñez) + pregunta de eval "del 22% al 15%" |
| Plazos como promedios; solo la garantía de 6 meses es contractual | Regla 2 del prompt + chunk del SLA |
| Transparencia con competidores | Regla 4 del prompt + chunk de objeciones |
| Sin contexto suficiente → decirlo | `min_score` + regla 5 del prompt |
| Faithfulness (cifras idénticas a los chunks) | Regla 1 del prompt, `temperature=0.2` |

El cumplimiento de las reglas depende del prompt (no hay filtro determinista sobre la salida); se vigila con el eval y con revisión manual de las respuestas.

## 5. Evaluación

`data/eval/test-queries.json` (12 preguntas con chunk esperado + 1 fuera de la base; 3 son objeciones reales). `scripts/rag_eval.py` mide **Recall@3** (objetivo ≥ 80 %) con Qdrant y embeddings reales. **Resultado: 12/12 = 100 %.** Tests unitarios (sin servicios externos) en `tests/pipelines/test_rag.py`.

## 6. Puesta en marcha

```bash
cp .env.example .env   # rellenar LLM_BASE_URL, LLM_API_KEY, EMBEDDING_MODEL, GENERATION_MODEL
docker compose up -d qdrant
QDRANT_URL=http://localhost:6333 uv run --project services/api python scripts/rag_setup.py
QDRANT_URL=http://localhost:6333 uv run --project services/api python scripts/rag_eval.py
docker compose up --build api uis
```
