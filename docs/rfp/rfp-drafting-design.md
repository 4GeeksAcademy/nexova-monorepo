# Generación y evaluación de respuestas a RFP — Hito 9 · Parte 2

La Parte 1 deja cada RFP en `analisis_completo` con un handoff (`rfp_intake.v1`)
que dice qué pide el cliente a cada departamento. La Parte 2 convierte ese
handoff en **un borrador de la propuesta económica por departamento**, lo
**autoevalúa** antes de que lo vea un humano y lo entrega a la Parte 3
(`rfp_drafting.v1`).

## 1. Piezas

| Pieza | Dónde | Responsabilidad |
|---|---|---|
| Grafo | `data/pipelines/rfp_drafting/graph.py` | Ciclo generador-evaluador por departamento, en paralelo |
| Generadores | `data/pipelines/rfp_drafting/generators/{seleccion,capacitacion,soporte}.py` | Un agente por departamento, con su prompt, su estructura de sección y sus consultas RAG |
| Evaluadores | `evaluators.py` + `compliance_rules.py` | Legibilidad, pertinencia y cumplimiento → `EvaluationResult` |
| Base de conocimiento | `knowledge.py` | `retrieve()` de `data/pipelines/rag.py` (Qdrant), con timeout y degradación a "sin contexto" |
| Cola | `services/api/tasks/rfp.py` | `rfp.generate_responses`: se encola sola al terminar `rfp.run_intake` |
| HTTP | `POST /rfp/tickets/{id}/drafts` | Regenerar (202; 409 sin handoff o con generación en curso) |
| UI | `uis/backoffice/app/(rfp)/rfp/[ticketId]` + `components/rfp/DraftCard.tsx` | Borradores, insignias de evaluación, aviso de revisión humana, botón "Regenerar" |
| CLI | `scripts/rfp_drafts.py` | Regenerar un ticket o corrida en seco (`--dry-run`, `--pdf`) |
| Esquema | `services/api/migrations/009_rfp_drafting.sql` | Columnas del ciclo en `rfp_department_section` y handoff en `rfp_ticket` |

## 2. Flujo

```
rfp.run_intake ── analisis_completo ──► rfp.generate_responses(ticket_id)       (o botón / script)

load_handoff ── lee SOLO rfp_ticket.handoff_payload (valida rfp_intake.v1)      ticket → generando_borrador
   └─Send por workstream─► department_cycle (subgrafo, uno por departamento, en paralelo)
        retrieve_knowledge → generate ─┬→ eval_readability ─┐
                                       ├→ eval_relevance  ──┼→ judge ─┬→ generate  (feedback, si quedan iteraciones)
                                       └→ eval_compliance ──┘         └→ fin       (pasa | límite agotado)
finalize ── approval_handoff (rfp_drafting.v1)                                 ticket → en_evaluación | needs_human_review
```

- **Entrada = contrato de la Parte 1.** Sin `ready_for_drafting`, sin
  `contract_version = rfp_intake.v1` o sin workstreams → `HandoffNotReady`
  (409 en la API, sin reintentos en Celery). El PDF y el Markdown no se leen.
- **Límite de iteraciones.** `RFP_MAX_ITERATIONS` (3 por defecto). Si se agota
  sin pasar, la sección queda `needs_human_review` con su último borrador y su
  último `EvaluationResult`, y viaja igual en el handoff. El ticket nunca se
  descarta.

## 3. `EvaluationResult`

Se guarda en `rfp_department_section.evaluation_results` (último) y en
`iteration_history` (todas las vueltas):

```json
{
  "section_id": "RFP-1A2B3C4D:seleccion",
  "department_id": "seleccion",
  "iteration": 2,
  "readability": {"pass": true, "score": 15.4, "details": {"word_count": 455, "gunning_fog": 19.8, "problems": []}},
  "relevance":   {"pass": true, "missing_aspects": [], "covered_aspects": ["..."]},
  "compliance":  {"pass": false, "rule_ids": ["garantia-90-dias", "moneda-sede", "ttc-min-15-dias-habiles", "clientes-anonimizados"],
                  "violations": [{"rule_id": "ttc-min-15-dias-habiles", "message": "...", "evidence": "..."}]},
  "overall_pass": false,
  "feedback_for_generator": "Corrige lo siguiente...\n- [ttc-min-15-dias-habiles] Compromete un plazo de cierre de '10 días hábiles'..."
}
```

| Evaluador | Recibe | Decide |
|---|---|---|
| Legibilidad | Solo el borrador | `py-readability-metrics` (vía `rfp_intake/legibility.py`): 120–900 palabras, Flesch-Kincaid ≤ 18, Gunning Fog ≤ 22, ≤ 32 palabras por frase. Configurable (`RFP_MAX_FK_GRADE`, etc.). Las fórmulas son de inglés: los umbrales se calibraron con borradores reales en español (FK 9–15). |
| Pertinencia | Borrador + `key_aspects` y `requests_to_owner` **de ese departamento** | Juez LLM con un veredicto **por aspecto y con cita del borrador**; un "cubierto" sin cita cuenta como no cubierto. |
| Cumplimiento | Borrador + departamento, sede y moneda (+ nombres de clientes actuales) | Reglas por código del BRIEFING §5, sin opinión del modelo. |

Reglas de cumplimiento (`compliance_rules.py`):

| rule_id | Aplica a | Regla |
|---|---|---|
| `garantia-90-dias` | todas | Mencionar la garantía de satisfacción de 90 días |
| `moneda-sede` | todas | Solo la moneda de la sede (España → EUR, Miami → USD) y que aparezca; sede desconocida = no se puede cotizar |
| `ttc-min-15-dias-habiles` | `seleccion` | Ningún plazo de cierre < 15 días hábiles (convierte semanas y días naturales) |
| `sla-24h` | `soporte` | SLA de respuesta de 24 horas explícito |
| `clientes-anonimizados` | todas | Sin nombres de clientes actuales (salen de la tabla `incident` de Supabase) |

## 4. Estados

| Momento | Ticket | Sección |
|---|---|---|
| Arranca | `generando_borrador` | `generando` |
| Primer borrador en evaluación | `en_evaluación` | `evaluando` |
| Falla y quedan iteraciones | `en_evaluación` | `generando` (vuelve a su generador) |
| Todas pasan | `en_evaluación` + `ready_for_approval = true` | `evaluada_ok` |
| Alguna agota el límite | `needs_human_review` + `ready_for_approval = true` | `needs_human_review` |

No se usan estados de la Parte 3. Con todo aprobado por los evaluadores, el
ticket se queda en `en_evaluación` (el último estado que puede escribir la
Parte 2) y `ready_for_approval` marca que el handoff está listo; la UI lo
muestra como "Borradores evaluados".

## 5. Handoff a la Parte 3 (`rfp_ticket.approval_handoff`)

```json
{
  "contract_version": "rfp_drafting.v1",
  "ticket_id": "RFP-1A2B3C4D", "rfp_id": "VRG-2026-011",
  "client_name": "Vantex Retail Group, S.A.", "client_hq": "España", "currency": "EUR", "deadline": "2026-08-18",
  "max_iterations": 3, "needs_human_review": false,
  "sections": [
    {"department_id": "seleccion", "owner": "Javier Almeida", "section_status": "evaluada_ok",
     "needs_human_review": false, "iterations": 3, "draft_content": "## ...", "evaluation_result": {"...": "..."}}
  ],
  "kpis": {"sections": 2, "passed": 2, "needs_human_review": 0, "avg_iterations": 2.5},
  "generated_at": "2026-11-16T20:15:00+00:00"
}
```

La Parte 3 arranca con `WHERE ready_for_approval` y aprueba cada sección por
separado con su responsable (`owner`). `kpis.avg_iterations` es el KPI del
BRIEFING "iteraciones promedio por sección" (ideal < 2).

## 6. Preguntas de diseño

**¿Qué información necesita cada evaluador? ¿La sección o el documento completo?**
Solo la sección. Legibilidad recibe el borrador y nada más. Pertinencia recibe
el borrador y lo que la RFP pide **a ese departamento** (`key_aspects` y
`requests_to_owner` del handoff), no el documento ni las secciones de otros.
Cumplimiento recibe el borrador, el departamento (para saber qué reglas
aplican), la sede/moneda y la lista de clientes actuales. Menos contexto =
menos tokens y menos juicios contaminados por otras secciones.

**¿Cómo se evita que dos evaluadores en paralelo choquen al escribir?**
Cada evaluador devuelve una clave distinta del estado del subgrafo
(`readability`, `relevance`, `compliance`) y ninguno escribe en la base de
datos. `judge` es el único nodo que los combina y persiste, y LangGraph lo
ejecuta cuando los tres han terminado (arista con las tres entradas). Además,
cada departamento corre en su propio subgrafo (`Send`), con su estado aislado:
lo único compartido es su fila de `rfp_department_section`, que solo escribe su
propio ciclo.

**¿Cómo sabe Ventas qué borrador es provisional?**
En la tarjeta del departamento: borde ámbar, estado "Requiere revisión humana",
el aviso "Borrador provisional: requiere revisión humana", la iteración en que
se quedó ("Iteración 3 de 3") y la lista exacta de lo que falló, con su
`rule_id`. El ticket queda en `needs_human_review` en la bandeja, y el handoff
lleva `needs_human_review: true` por sección y a nivel de ticket.

**¿El feedback al generador es específico o genérico?**
Específico y construido por código, no por el modelo: una línea por fallo con
la regla o aspecto, el mensaje y el texto afectado. Por ejemplo:
`[ttc-min-15-dias-habiles] Compromete un plazo de cierre de '10 días hábiles'
(≈10 días hábiles); el mínimo es 15. Texto afectado: "..."` o `[pertinencia]
No respondes a: Dimensionar las horas de consultoría`. El generador recibe ese
feedback junto con su borrador anterior. En la prueba real, Vantex/selección
pasó en la 3.ª iteración y capacitación en la 2.ª, corrigiendo exactamente lo
señalado.

## 7. Ajustes tras probar con el modelo real

- **Juez de pertinencia demasiado estricto:** marcaba como no cubiertos aspectos
  que el borrador sí trataba. Ahora da un veredicto por aspecto con cita del
  borrador y criterios explícitos (las peticiones internas y los datos que
  faltan en la RFP cuentan como cubiertos si el borrador da el resultado o los
  deja como supuesto a confirmar).
- **Fecha límite mal interpretada:** el generador la usaba como fecha de fin de
  la formación; ahora se le pasa como `fecha_limite_entrega_de_la_propuesta`.
- **Formato:** el modelo repetía su propio título y, una vez, devolvió la
  sección anidada como texto JSON; el generador lo desenvuelve y, si no puede,
  repite la petición una vez.

## 8. Cómo probarlo

```bash
uv run --project services/api pytest tests/pipelines -k "rfp" -v
QDRANT_URL=http://localhost:6333 uv run --project services/api python scripts/rfp_drafts.py --dry-run --pdf tests/pipelines/fixtures/rfp/nexova-request-1.pdf
```

Con Docker (tras aplicar la migración 009): `docker compose up --build api worker redis qdrant uis`,
subir una RFP en `/rfp` y abrir su detalle: la Parte 2 arranca sola.
