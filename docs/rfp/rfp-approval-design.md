# RFP · Parte 3: aprobación humana y cierre del documento

Este documento explica cómo una RFP llega a su documento final en la Parte 3. Primero, cada departamento activo aprueba su sección por separado (*human-in-the-loop*). Si hay conflictos entre departamentos, los resuelve un árbitro fijo. Al final se genera el documento consolidado.

**Código:**

| Pieza | Dónde |
|---|---|
| Grafo de aprobación | `data/pipelines/rfp_approval/` |
| Trazabilidad | `data/pipelines/rfp_intake/trace.py` |
| API | `services/api/rfp/` |
| Tareas Celery | `services/api/tasks/rfp.py` |
| UI | `uis/backoffice/app/(rfp)/rfp/[ticketId]` |

## 1. Flujo y estados

```
Parte 1 ─ analizando → analisis_completo
Parte 2 ─ generando_borrador → en_evaluación | needs_human_review   (approval_handoff rfp_drafting.v1)
Parte 3 ─ esperando_aprobación ──(todas las secciones activas aprobadas)──► terminado
```

**Arranque.**
- Cuando termina la tarea `rfp.generate_responses`, se encola una tarea `rfp.run_approval_branch` por cada departamento activo (`enqueue_approvals`).
- Si la cola falla, el ticket guarda el aviso y la UI ofrece el botón "Abrir aprobaciones".

**Estados del ticket.**
- La Parte 3 solo escribe `esperando_aprobación` y `terminado`, tal como pide el BRIEFING §2.3.
- Durante una revisión, el ticket sigue en `esperando_aprobación`. Lo que cambia es el `section_status` de esa sección (`generando` o `evaluando`).

### Grafo de cada departamento

Cada departamento corre su propio grafo:

```
START → load_section → arbitrate ─┬─ sin conflicto / escalado ──► human_approval ⏸ ─► apply_decision ─┬─► synthesize_final_document → END
                                  ├─ conflicto, quedan rondas ──► revise ─► load_section            ├─► revise (ronda + 1)
                                  └─ conflicto al límite (reject) ► close → END                       └─► close → END
```

| Nodo | Qué hace |
|---|---|
| `load_section` | Arma el paquete de revisión: lo mínimo que el humano necesita para decidir (ver §5) |
| `arbitrate` | Aplica los triggers del BRIEFING §7 por regla (ver §3) |
| `human_approval` | `interrupt()`: único punto de pausa. El checkpoint queda guardado en el hilo del departamento |
| `apply_decision` | `approve` → `aprobada`. `request_changes` (o `reject` con rondas disponibles) → `revise`. `reject` al límite → `close` |
| `revise` | Vuelve al generador de **la Parte 2** con los comentarios. Los tres evaluadores de la Parte 2 reevalúan el borrador (`revision.py`). El borrador nuevo vuelve a pasar por la pausa humana |
| `synthesize_final_document` | Si esta aprobación completa el conjunto, consolida las secciones (`final_document.py`), guarda `rfp_final_document` y pone el ticket en `terminado`. Es idempotente: la última rama en aprobar lo genera |

## 2. Pausa por departamento sin bloquear a los demás

**Un grafo por departamento, cada uno con su hilo** en el checkpointer: `thread_id = rfp-{ticket_id}:{department_id}` (`checkpointer.thread_id_for`).

Así, la pausa de `seleccion` no puede retener a `capacitacion`: son ejecuciones distintas con checkpoints distintos. Lo demuestra `test_department_b_is_approved_while_department_a_stays_interrupted`. En el trace se ve que B termina entre la pausa de A y su reanudación. Los tickets concurrentes tampoco comparten checkpoint (`test_thread_ids_are_namespaced_per_ticket_and_department`).

**Por qué no un solo grafo con `Send` e `interrupt` en paralelo.** En LangGraph, varias interrupciones dentro del mismo *superstep* comparten el checkpoint del hilo. Reanudar una sola obliga a mapear los `interrupt_id`, y el superstep no termina hasta que se resuelven todas. Con un hilo por rama, el paralelismo es real y no depende de ese detalle.

### Checkpointer y reanudación

**Checkpointer.**
- Es Postgres (`langgraph-checkpoint-postgres` sobre la `DATABASE_URL` de Supabase), porque la API y el worker son procesos distintos. Lo usa con psycopg v3; SQLAlchemy sigue con psycopg2.
- `RFP_CHECKPOINTER=sqlite|memory` sirve para desarrollo y tests. La memoria solo se usa en tests.
- Las tablas `checkpoint*` las crea `PostgresSaver.setup()`.

**`resume_approval`** es el punto de entrada explícito de la reanudación:
- Lee la pausa guardada en el checkpoint y valida la decisión contra ella: el aprobador esperado y las decisiones permitidas en esa ronda.
- Después reanuda exactamente `human_approval` con `Command(resume=…)`. `load_section` y `arbitrate` no se repiten (`test_resume_continues_from_the_interrupt_without_restarting`).
- Si la rama se cayó después de la decisión (por ejemplo, falló el LLM en `revise`), un reintento de Celery continúa desde el último checkpoint sin repetir la decisión.

**Validación de la respuesta humana** (`schemas.ApprovalDecision`). Se valida en la API, en `resume_approval` y otra vez dentro del nodo:

| Comprobación | Detalle |
|---|---|
| Decisión | Solo `approve`, `request_changes` o `reject` |
| Comentario | Obligatorio para pedir cambios o rechazar |
| Campos | No se aceptan campos extra |
| Aprobador | `acting_as` debe coincidir con el aprobador esperado |

**Quién aprueba.** Lo decidió el usuario:
- Cualquier usuario interno registra la decisión **en nombre del** responsable nombrado (BRIEFING §2.1): Javier Almeida, Elena Vargas o Roberto Díaz.
- Si el conflicto se escaló, decide el árbitro fijo.
- Se guardan `approver` (el responsable) y `decided_by_user` (el usuario real del backoffice).
- No hay una escalera jerárquica multinivel.

## 3. Arbitraje: triggers del BRIEFING §7 en estado estructurado

`arbitration.structured_facts()` no lee texto libre ni pregunta a ningún LLM. Calcula estos hechos:

| Hecho | Cómo se obtiene |
|---|---|
| `client_hq` → `expected_currency` | Se mapea la sede a su moneda (`CURRENCY_BY_HQ`) |
| `currencies` por sección | Marcadores EUR/USD de `compliance_rules.detect_currencies` |
| `seleccion_ttc_business_days` | Plazos de cierre normalizados a días hábiles, con el mismo parser que la regla `ttc-min-15-dias-habiles` |
| `training_window` | Si la formación empieza tras el cierre (`sequenced_after_close`) o a los N días hábiles |
| `soporte_sla_24h` | Resultado de la regla `sla-24h` |

Cada trigger tiene un árbitro y una regla de resolución fijos:

| `trigger_id` | Dispara cuando | Árbitro | Quedan rondas | Límite agotado |
|---|---|---|---|---|
| `ttc-vs-training-window` | Ambos departamentos están activos y el TTC es < 15 días hábiles, o la formación arranca antes de un cierre realista sin estar secuenciada | Marcos Ibáñez | `request_changes` a capacitación (y a selección si su TTC < 15) | Escala a Marcos, que decide como humano: aprobar o rechazar |
| `support-sla-missing` | La sección de soporte omite el SLA 24 h | Roberto Díaz | `request_changes` | `reject`: aprobación bloqueada |
| `currency-mismatch` | Una sección cotiza en una moneda distinta a la de la sede, o las secciones discrepan sin sede conocida | Marcos Ibáñez | `request_changes` (reescribir a la moneda de la sede) | `reject` |

Cada activación queda en `rfp_arbitration` (trigger, árbitro, acción, evidencia y ronda) y en el historial de decisiones de la sección, con `by_rule`. Los agentes de la Parte 2 **señalan** el problema (`rule_id`); quien lo **resuelve** es esta tabla.

## 4. Límite de iteraciones

`RFP_MAX_APPROVAL_ROUNDS` (2 por defecto) limita las rondas de cambios por sección. Cuentan tanto las de un humano como las forzadas por el árbitro. Agotado el límite:
- La pausa solo admite `approve` o `reject`. Pedir más cambios devuelve 422 con "no está permitida".
- Un `reject` cierra la sección como `rechazada`.

Se combina con el límite propio de la Parte 2 (`RFP_MAX_ITERATIONS`, 3) y con el `recursion_limit` del grafo (`graph.recursion_limit`).

## 5. Preguntas de diseño

**¿Qué pasa si un departamento rechaza su sección tras la interrupción?**
- **Con rondas disponibles:** pedir cambios o rechazar **vuelve al generador de la Parte 2** dentro de la misma ejecución. Pedir cambios corrige el borrador anterior; rechazar lo reescribe desde cero. En ambos casos el comentario va como feedback y la sección se reevalúa antes de volver a la pausa.
- **Al límite:** el rechazo cierra la rama (`rechazada`). El ticket sigue en `esperando_aprobación` y no hay documento final. "Reabrir aprobación" (`POST …/approvals?department_id=`) abre una ejecución nueva de **solo ese** departamento: borra su hilo y empieza en la ronda 0.

**¿Qué información mínima ve el humano para decidir?** El `review_packet`, que es lo que lleva la propia `interrupt`:
- cliente, sede y moneda;
- el borrador;
- si pasó la evaluación y qué violaciones (`rule_id`) o aspectos sin cubrir quedan;
- las preguntas abiertas al cliente;
- la ronda actual;
- los conflictos que afectan a la sección.

Con eso decide sin releer la RFP. La UI lo muestra en la tarjeta de cada sección.

**¿Cómo se detectan los triggers sin depender de texto libre?** Con los hechos de §3, que son los mismos parsers deterministas que usa el evaluador de cumplimiento. La evidencia numérica (días hábiles, monedas encontradas) se guarda con cada arbitraje.

## 6. Trazabilidad

`traced()` envuelve cada nodo de las tres partes y registra en `rfp_trace_event`:
- `part` y `node`;
- `agent` (por ejemplo `clasificador`, `generador:seleccion`, `arbitro_reglas:soporte`, `aprobacion_humana:capacitacion`);
- `thread_id`;
- `status` (`ok`, `interrumpido` o `error`);
- input y output resumidos (los documentos solo con su tamaño y un preview);
- timestamps y duración.

En la Parte 3 el evento también entra en `state["trace"]` y viaja en el checkpoint. Se consulta con `GET /rfp/tickets/{id}/trace` y en el bloque "Trazabilidad" de la UI. Si guardar el trace falla, el flujo sigue y queda un aviso en el log.

## 7. API

| Endpoint | Respuesta |
|---|---|
| `POST /rfp/tickets/{id}/approvals[?department_id=]` | 202, con una tarea por departamento. 409 si no hay handoff de la Parte 2, si el ticket está terminado o si hay una decisión en curso |
| `POST /rfp/tickets/{id}/approvals/{department_id}` con `{decision, acting_as, comment}` | 202 y la sección pasa a `procesando`. Se encola `rfp.resume_approval_branch` solo para esa rama. 409 si no está pendiente; 422 si la decisión o el aprobador no valen |
| `GET /rfp/tickets/{id}/trace` | Eventos de trace en orden |
| `GET /rfp/tickets/{id}/final-document` | Documento final; 404 hasta que todas las secciones activas estén aprobadas |

El detalle del ticket incluye ahora:
- por sección: `approval_status`, `expected_approver`, `allowed_decisions`, `approval_round`, `approver`, `approved_at`, `decided_by_user` y `approval_history`;
- a nivel de ticket: `arbitrations`, `final_document` y `max_approval_rounds`.

## 8. Ruta E2E reproducible

```bash
uv run --project services/api python scripts/rfp_e2e.py --request-changes --trace
```

```bash
uv run --project services/api pytest tests/pipelines/test_rfp_e2e.py tests/pipelines/test_rfp_approval_graph.py -v
```

El script sube `nexova-request-1.pdf` (Vantex, EUR) o, con `--rfp informal`, `nexova-request-2.pdf` (NubeSoft, USD). Lo pasa por las Partes 1 y 2 con el LLM falso de los tests y aprueba cada sección con `resume_approval` programático. Imprime las pausas, las decisiones, los estados del ticket en orden (`analizando → analisis_completo → generando_borrador → en_evaluación → esperando_aprobación → terminado`), el trace y el documento final.

Para operar un ticket real sin Celery: `scripts/rfp_approve.py status|start|resume`.

## 9. Despliegue

- **Migración.** Aplicar `services/api/migrations/010_rfp_approval.sql` a mano en Supabase. Añade columnas a una tabla existente, y `create_all()` no lo hace.
- **Variables.** Opcionales: `RFP_MAX_APPROVAL_ROUNDS` (2), `RFP_CHECKPOINTER` (`postgres`) y `RFP_CHECKPOINT_DATABASE_URL` (por defecto, `DATABASE_URL`).
