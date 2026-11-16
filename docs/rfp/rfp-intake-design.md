# Recepción y enrutamiento de RFPs — Hito 9 · Parte 1

Flujo agéntico que recibe las RFP que llegan a Ventas (Marcos Ibáñez), decide si
lo son y reparte el análisis solo entre los departamentos que la solicitud
necesita. **Criterio de aceptación:** Ventas abre un ticket procesado y sabe,
sin leer el PDF, qué le toca a cada departamento y a quién pedírselo.

## 1. Piezas y layout

| Pieza | Dónde | Responsabilidad |
|---|---|---|
| UI modo ticket | `uis/backoffice/app/(rfp)/rfp` (+ `[ticketId]`) | Subida del PDF, bandeja con poll cada 3 s, detalle por departamento |
| Proxies | `uis/backoffice/app/api/rfp/tickets/*` | Reenvían a la API con el JWT |
| HTTP | `services/api/rfp/router.py` (mismo proceso FastAPI) | `POST /rfp/tickets` (202), `GET /rfp/tickets`, `GET /rfp/tickets/{id}` |
| Cola | `services/api/tasks/rfp.py` (Celery + Redis) | `rfp.run_intake(ticket_id)`, 3 reintentos, DLQ |
| Grafo | `data/pipelines/rfp_intake/` | Conversión, agentes, persistencia |
| CLI | `scripts/rfp_reprocess.py` | Reproceso manual y corrida en seco |
| Esquema | `services/api/migrations/008_rfp_intake.sql` | `rfp_ticket`, `rfp_metadata`, `rfp_department_section` |

Los routers solo disparan y consultan; el grafo vive en `data/pipelines` y se
importa con `rfp/pipeline_bridge.py` (mismo recurso que `reporting/`).

## 2. Ciclo de vida del ticket

```
POST /rfp/tickets ── valida PDF (.pdf, firma %PDF, ≤ 20 MB)
                  ── guarda data/raw/rfp/<ticket_id>.pdf
                  ── crea rfp_ticket (analizando)
                  ── encola rfp.run_intake(ticket_id) ──► 202 {ticket_id, status, task_id}

worker ── run_intake ── grafo ──► analisis_completo | descartado
```

Estados de la Parte 1: `analizando` → `analisis_completo` | `descartado`. La
columna `status` ya admite el vocabulario completo del BRIEFING (§2.3) para que
las Partes 2–3 continúen el mismo ticket sin migrar.

## 3. Grafo `rfp_intake` (dedicado, sin nodos del grafo CX)

```
START → convert_to_markdown → classify ─┬→ discard → END
                                        └→ extract_metadata → orchestrate ─┬→ discard → END
                                                                          └─Send×N→ department_worker
                                                                              → synthesize → finalize → END
```

| Nodo | Agente | Decide el código |
|---|---|---|
| `convert_to_markdown` | — (MarkItDown) | Limpia artefactos `(cid:127)`; PDF sin texto → descartado |
| `classify` | Clasificador | Descarta solo con `is_rfp=false` y confianza ≥ 0,7 |
| `extract_metadata` | Extractor | Sede → moneda (España→EUR, Miami/EE. UU.→USD); cifras solo con cita literal; fecha solo si el año está en el documento; legibilidad |
| `orchestrate` | Orquestador | Catálogo cerrado de departamentos; extractos verificados; servicios sin departamento → `unmapped_services` |
| `department_worker` ×N | Un worker por departamento activo, en paralelo (`Send`) | Responsable del catálogo; cifras sin cita (o cuyo valor no sale de la cita) → `open_questions` |
| `synthesize` | Synthesizer | `who_to_ask`, conflictos entre workers, payload de handoff; resumen determinista si el LLM falla |

Todos los agentes reciben el documento entre `<documento>` como dato (nunca como
instrucciones) y responden JSON validado con Pydantic (`schemas.py`).
`llm.py` pide `json_object`, trata `{}` como fallo de formato (el modelo de
4Geeks lo devuelve a veces) y el último reintento va sin `json_object`.

## 4. Persistencia (PostgreSQL / Supabase)

- `rfp_ticket`: ticket, Markdown, clasificación, síntesis, `handoff_payload`,
  `ready_for_drafting`, `error_message`, `attempts`.
- `rfp_metadata` (1:1): cliente, `client_hq`, `currency`, servicios, alcance,
  volúmenes con cita, fecha límite, presupuesto, `departments_needed`,
  `unmapped_services`, `readability`.
- `rfp_department_section` (1:N, único por ticket y departamento): `key_aspects`,
  `requests_to_owner`, `open_questions`, `figures_cited`, `excerpts`, y ya las
  columnas de las Partes 2–3 (`draft_content`, `evaluation_results`,
  `approval_status`, `approver`, `approved_at`) a `NULL`.

Las clases SQLModel (`rfp_intake/models.py`) entran en `SQLModel.metadata` al
importar el puente, así que `create_db_and_tables()` crea las tablas si faltan
(como `incident`). `008_rfp_intake.sql` las deja versionadas e idempotentes.
El PDF original vive en `data/raw/rfp/` (gitignored); la BD guarda su ruta.

## 5. Contrato de handoff hacia la Parte 2

Sin segundo API: campo en BD. Al terminar, `rfp_ticket.ready_for_drafting = true`
y `rfp_ticket.handoff_payload` contiene:

```json
{
  "contract_version": "rfp_intake.v1",
  "ticket_id": "RFP-1A2B3C4D",
  "rfp_id": "VRG-2026-011",
  "client_name": "Vantex Retail Group, S.A.",
  "client_hq": "España",
  "currency": "EUR",
  "deadline": "2026-08-18",
  "services_requested": ["..."],
  "workstreams": [
    {"department_id": "seleccion", "owner": "Javier Almeida",
     "key_aspects": ["..."], "requests_to_owner": ["..."], "open_questions": ["..."],
     "figures_cited": [{"concept": "...", "value": "5", "quote": "..."}], "excerpts": ["..."]}
  ],
  "conflicts": [],
  "unmapped_services": [],
  "cost_tier": "medio"
}
```

La Parte 2 arranca con
`SELECT ticket_id, handoff_payload FROM rfp_ticket WHERE ready_for_drafting AND status = 'analisis_completo'`
sin re-parsear el PDF, y escribe sus borradores en las filas de
`rfp_department_section` que ya existen.

## 6. Preguntas de diseño

**¿Qué pasa si una RFP menciona un departamento que no existe?** El catálogo
(`departments.py`) es cerrado. Si el orquestador propone otro `department_id` o
el cliente pide un servicio que Nexova no presta, se guarda en
`unmapped_services` (visible en la UI y en el handoff) y no se crea ningún
worker. Si no queda ningún departamento aplicable, el ticket se descarta con
`decided_by = "orchestrator"`. Nunca se activan los tres por defecto.

**¿Qué necesita cada worker del estado compartido?** Solo los metadatos
compartidos (cliente, sede, moneda, servicios, volúmenes citados, plazo,
presupuesto) y los extractos de su departamento, verificados como literales del
documento (si el orquestador no aporta ninguno válido, párrafos por palabras
clave). Nunca el documento completo. Si falta una cifra, va a `open_questions`;
una cifra sin cita literal, o cuyo valor no aparece en la cita, se elimina y se
convierte en pregunta. Una fecha cuyo año no está en el documento no se guarda
(el modelo completaba "August 23rd" con 2025).

**¿Cómo se decide que un documento "no es una RFP"? ¿Y los falsos negativos?**
El clasificador distingue RFP formal, informal (correo pidiendo propuesta) y
`no_rfp` (proveedores que nos venden algo, spam, CVs...). Solo se descarta con
confianza ≥ 0,7: un falso negativo pierde una venta, mientras que un falso
positivo solo cuesta un análisis que Ventas descarta a mano. Por debajo del
umbral se acepta con `low_confidence = true`. Un ticket descartado conserva el
motivo y su PDF en `data/raw/rfp/`, y puede reprocesarse.

**¿Qué pasa si dos workers devuelven información contradictoria?**
`detect_conflicts` compara las cifras citadas por concepto entre workers (y
frente a los metadatos). Si discrepan, se registran en `synthesis.conflicts` y
en el handoff con `resolution = "pendiente_ventas"`: se señalan, no se
resuelven por consenso. El arbitraje con árbitro fijo es de la Parte 3.

**¿Dónde corre el trabajo async y cómo sigue siendo verdad el ticket si el job
falla?** En el worker de Celery (Redis), igual que #DEV-55; la API solo encola el
`ticket_id`. Cada nodo persiste su paso de forma idempotente (upserts; las
secciones se rehacen en `orchestrate`), así que un reintento re-ejecuta el grafo
entero sin duplicar. En cada fallo el ticket guarda `error_message` (visible en
la UI) y sigue en `analizando`; tras 3 reintentos (15/30/60 s) la tarea va a la
DLQ y el mensaje indica `scripts/rfp_reprocess.py <ticket_id>`. Una tarea por
ticket: un fallo no bloquea a los demás. Si Redis está caído al subir, el ticket
existe con el error y la API responde 503.

## 7. Legibilidad y costo

`legibility.py` calcula Flesch-Kincaid (grado), Gunning Fog, palabras y tokens
estimados (~4 caracteres/token) sobre el Markdown, y los resume en `cost_tier`
(bajo/medio/alto). `py-readability-metrics` exige ≥ 100 palabras y el
tokenizador `punkt_tab` de NLTK (se descarga la primera vez); si falta
cualquiera, se guardan solo las cifras de tamaño con el motivo en `note`.

## 8. Cómo probarlo

```bash
uv run --project services/api pytest tests/pipelines -k rfp -v
uv run --project services/api python scripts/rfp_reprocess.py --dry-run --pdf tests/pipelines/fixtures/rfp/nexova-request-1.pdf
```

Con Docker: `docker compose up --build api worker redis backoffice` y subir los
PDF de `tests/pipelines/fixtures/rfp/` en `http://localhost:3001/rfp`.
