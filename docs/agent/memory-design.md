# Memoria y auto-mejora del agente — Hito 8 · Parte 1

Ticket #MEM-092. Los 30 agentes de soporte de Roberto Díaz tienen que volver a explicar el mismo procedimiento de escalación en cada sesión, porque el asistente no recuerda nada entre conversaciones. Este cambio da al **mismo agente LangGraph** (`services/api/agent/`) una memoria persistente. El agente propone qué recordar, el usuario decide y cada paso queda auditado.

Código: `services/api/agent/memory/`. Migración: `services/api/migrations/007_agent_memory.sql`. Evals: `tests/pipelines/test_agent_memory_evals.py`. Evidencia real: [`memory-evidence.md`](memory-evidence.md), generada con `scripts/agent_memory_demo.py`.

## 1. Arquitectura elegida

**Memoria semántica de largo plazo, en forma de hechos aprobados y estructurados, compartida por la organización, en Postgres/Supabase.**

Cada hecho es una frase corta y general, por ejemplo: "Los tickets de nómina de clientes de retail se resuelven en 24 horas". Lleva estos metadatos:

- `business_line`: `headhunting`, `support` o `training`;
- `category`: `escalation_procedure`, `client_preference` o `incident_pattern`;
- quién lo aprobó y desde qué propuesta;
- uso (`use_count`, `last_used_at`) y estado (`active`, `superseded` o `expired`).

| Opción | Decisión | Por qué |
|---|---|---|
| Ventana de contexto / system prompt | Descartada | El README lo prohíbe. Además, crecería sin control y no deja rastro. |
| Episódica cruda (historial de chats) | Descartada | Arrastraría lo que el BRIEFING prohíbe guardar: datos de candidatos, salarios y correcciones de un solo ticket. Se guarda el hecho ya generalizado, no la conversación. |
| **Hechos estructurados en Postgres** | **Elegida** | El volumen es pequeño y acotado (como mucho 150 hechos activos), y lo que hace falta es auditar, imponer una sola pendiente, deduplicar y expirar. Todo eso son restricciones relacionales: índices únicos parciales, triggers append-only y transacciones. Supabase ya está en el stack. |
| VectorDB (Qdrant) | Descartada por ahora | Con pocas decenas de hechos filtrados por línea y categoría, la similitud léxica basta y es explicable. Si un día hiciera falta, se usaría una colección propia `nexova_agent_memory`, nunca `nexova_knowledge`. |
| Knowledge graph | Descartada | Lo que hay que recordar no depende de relaciones explícitas (jerarquías o dependencias). |
| Fine-tuning | Descartada | Es caro, lento de actualizar y no permite olvidar un hecho concreto. |

La memoria corto plazo dentro de una corrida sigue en el checkpointer SQLite (`thread_id = run_id`). La propuesta pendiente **no** vive en el checkpoint, sino en Postgres, indexada por `conversation_id`.

**El RAG sigue siendo de solo lectura.** Ningún módulo de `agent/memory/` importa Qdrant. `PostgresMemoryStore` solo toca `agent_memory_proposals`, `agent_memory_entries` y `agent_memory_audit`. Hay un test que lo comprueba: `test_memory_never_writes_to_the_rag_knowledge_base`.

### Una memoria o una por línea de negocio

Hay **un solo store con el metadato `business_line`**. Nexova mezcla tres líneas, pero casi todo el uso real es de soporte, y triplicar tablas o colecciones triplicaría también la consolidación sin aislar mejor. El aislamiento se consigue así:

- la deduplicación y los topes se aplican por `business_line` + `category`;
- el hecho se muestra al modelo etiquetado con su línea, para que no se confunda una preferencia de formación con una de soporte;
- la política bloquea siempre los datos de headhunting que no deben entrar (candidatos y salarios).

### Alcance

La memoria es **compartida por la organización**, porque ese es el problema del ticket: lo que corrige un agente debe servirle al resto. Cada hecho guarda `approved_by`, y cada decisión guarda su autor y su mensaje.

### Idioma

Es **solo español**, el idioma base de Nexova y del agente. Los prompts de generación y del clasificador, la pregunta de confirmación y los avisos están en español.

## 2. Interfaz explícita de lectura y escritura

`MemoryStore` (`store.py`) es el contrato. `AgentMemory` (`service.py`) es la única puerta del grafo hacia la memoria:

| Operación | Qué hace |
|---|---|
| `read_relevant(question)` | Devuelve hasta 6 hechos activos afines a la pregunta y anota su uso, que alimenta el TTL. |
| `propose(conversation_id, user_id, candidate, message)` | Aplica la política, comprueba duplicados y el límite diario, y registra la propuesta como `pending`. **No escribe memoria.** |
| `resolve_pending(conversation_id, user_id, message)` | Clasifica el mensaje contra la propuesta pendiente y aplica la decisión. |
| `consolidate()` | Limpieza: caduca pendientes y aplica TTL y topes. |

Los hechos entran al prompt como chunks de contexto con el documento "Memoria aprobada del equipo". Son **datos, no instrucciones**: el prompt dice que, si contradicen un documento oficial del CONTEXTO, prevalece el documento.

## 3. Flujo en el grafo

```
START → receive_question ─┬─(vacía)→ reject → END
                          └→ resolve_pending ─┬─(el mensaje solo decidía)→ memory_ack → END
                                              └→ load_memory → route_question → [tools] → [retrieve]
                                                   → generate (estructurado) → propose_memory → END
                                                   → tool_fallback → END
```

1. **`resolve_pending`**: si la conversación tiene una propuesta pendiente de este mismo usuario, el mensaje se evalúa contra ella antes de nada.
2. **`load_memory`**: lee los hechos aprobados afines.
3. **`generate`**: en **una sola llamada** devuelve `{"respuesta", "propuesta_memoria"}` (`generation.py`). Si no hay datos en ninguna fuente, con memoria también se pasa por aquí (no por `no_information`). Una corrección como "ese cliente siempre pide confirmar por email" no está en ninguna fuente, y solo la generación auto-evalúa si vale la pena recordarla. El `SYSTEM_PROMPT` del RAG ya obliga a decir que no hay información.
4. **`propose_memory`**: si hay propuesta y pasa la política, se registra como `pending` y se añade a la respuesta, en la misma conversación:

   > ¿Quieres que recuerde esto para la próxima vez? «…» (Procedimiento de escalación · Outsourcing de soporte). Responde «sí», «no» o envíame la versión corregida.

`POST /agent/query` recibe `conversation_id` opcional, que se crea si falta, y devuelve `conversation_id`, `memory_proposal` y `memory_decision`. El cliente reenvía el `conversation_id` en el siguiente mensaje.

## 4. Auto-evaluación: qué se propone y qué no

El criterio está en `MEMORY_INSTRUCTIONS` (`generation.py`). Solo se propone un hecho **nuevo o corregido, general y repetible** de las tres categorías. La mayoría de los mensajes no generan propuesta. Además, `policy.py` vuelve a filtrar por código lo que el modelo proponga.

**Deberían generar propuesta** (BRIEFING; eval `test_memorable_interaction_is_proposed_in_the_same_answer_and_not_written`):

1. "En realidad los tickets de nómina para clientes de retail ahora se resuelven en 24 horas, no en 48 como decía el SLA anterior." → `escalation_procedure`.
2. "Ese cliente de finanzas siempre pide que le confirmemos por email antes de cerrar un ticket, aunque el sistema permita cerrarlo directo." → `client_preference`.
3. "Los tickets marcados como 'urgente' de clientes con contrato premium deben ir directo a un agente senior, no a la cola general — eso cambió este mes." → `escalation_procedure`.

**No deberían generar propuesta** (eval `test_non_memorable_interaction_generates_no_proposal`):

1. "¿Cuántos tickets abiertos hay ahora mismo?" es una consulta puntual: el dato vive en el gestor de incidentes.
2. "Perfecto, gracias por la explicación." es un cierre de conversación.
3. "Resume este ticket en dos líneas para el reporte de Laura." es una tarea de un solo uso.

## 5. Qué nunca entra en memoria

Tanto el prompt como `policy.check_fact()`, que se aplica antes de proponer **y** antes de escribir (también a la versión editada), lo impiden:

| Bloqueo | Ejemplos |
|---|---|
| `candidate_data` | CVs, candidatos, entrevistas, finalistas, notas de entrevista. Viven en el ATS y en el RAG de candidatos. |
| `salary_data` | Salario, sueldo, pretensión salarial, banda salarial, oferta económica, contraoferta. |
| `one_off_ticket` | `NXV-000482`, "este ticket", "caso 123": correcciones puntuales sin patrón. |
| `personal_contact` | Emails, teléfonos (9 o más dígitos, para no confundir fechas), DNI, NIE o SSN. |
| `instruction_like` | "Ignora las instrucciones…", "a partir de ahora responde…", "system prompt". |

Cuando la política bloquea, en la auditoría queda el evento `blocked` con el código, la línea y la categoría, **sin el texto**: lo prohibido tampoco debe acabar en el registro. Los mensajes que sí se registran pasan por `redact()`, que enmascara emails, teléfonos y documentos.

## 6. Confirmación del usuario

- **Clasificación explícita.** `classifier.py` envía al LLM de generación la propuesta y el mensaje, y recibe un JSON validado con Pydantic (`PendingDecision`): `approve`, `reject`, `edit`, `unrelated` o `ambiguous`, junto con `edited_fact`, `remaining_question` y `confidence`. Nunca se hace un `"sí" in mensaje`.
- **Descarte por defecto.** Solo `approve` o `edit` con confianza ≥ 0,7 escriben. `unrelated`, `ambiguous`, la confianza baja, un `edit` sin texto, un fallo o timeout del modelo y una propuesta de más de 24 horas acaban en `discarded`. El silencio y la ambigüedad nunca cuentan como aprobación.
- **Una pendiente a la vez.** Hay un índice único parcial `(conversation_id) WHERE status = 'pending'`. Además, `resolve_pending` se ejecuta siempre antes que `propose_memory`, así que la propuesta anterior se cierra antes de que pueda nacer otra.
- **Decidir y seguir preguntando.** Con "Sí, recuérdalo. ¿Y cuál es la garantía de reemplazo?" se aplica la decisión y el flujo continúa con `remaining_question`. La respuesta empieza con el aviso ("Guardado en la memoria del equipo: «…».") y sigue con la respuesta normal. Con `unrelated`, el mensaje completo se responde como pregunta nueva.
- **Solo decide quien la recibió.** Si otro usuario escribe con el mismo `conversation_id`, la propuesta no se toca.

## 7. Registro auditable

- **`agent_memory_proposals`.** Guarda qué se propuso, quién, el mensaje que la originó, el estado final, la etiqueta, la confianza, el mensaje de decisión, el hecho final y cuándo. Un trigger solo deja pasar de `pending` a un estado final. Una vez decidida, la propuesta es inmutable y no se puede borrar.
- **`agent_memory_audit`.** Es append-only: los triggers bloquean UPDATE, DELETE y TRUNCATE. Registra los eventos `proposed`, `blocked`, `approved`, `edited`, `rejected`, `discarded`, `written`, `deduplicated`, `superseded`, `expired` y `evicted`.
- **`agent_memory_entries`.** No admite DELETE: olvidar es cambiar `status`, y queda `status_reason`.
- **Trace JSON de la corrida.** Incluye `conversation_id` y un bloque `memory` (ids de los hechos usados, la decisión, la propuesta y el bloqueo).

La propuesta, la decisión y la escritura van en la misma transacción.

## 8. Consolidación y olvido

Hay funciones puras en `consolidation.py` que se aplican en cada escritura y con `scripts/agent_memory_consolidate.py` (programable en supercronic):

1. **Deduplicación al escribir.** Se usa la huella normalizada y la similitud Jaccard sobre raíces de 5 letras, dentro de la misma línea y categoría.
   - Similitud ≥ 0,85 **con las mismas cifras**: es un duplicado. No se inserta y se refresca el hecho existente. Además, un hecho ya conocido ni siquiera se propone.
   - Similitud ≥ 0,5: es una corrección del mismo hecho (el SLA pasa de 48 h a 24 h). El nuevo reemplaza al anterior, que queda como `superseded` con `supersedes_id`, sin borrarse.
2. **TTL por inactividad.** Un hecho sin uso en 90 días pasa a `expired`. Cada lectura cuenta como uso, así que lo útil se mantiene solo y lo que nadie consulta desaparece.
3. **Topes.** Como máximo 25 hechos activos por línea y categoría, y 150 en total. Al superarlos se expulsa lo menos usado y más antiguo (`evicted`).
4. **Propuestas sin respuesta.** Caducan a las 24 horas: se descartan en el siguiente mensaje de esa conversación o en la limpieza periódica.

**Por qué esta política.** Los procedimientos de soporte cambian por correcciones, no por acumulación, y por eso una corrección reemplaza a la anterior en lugar de sumarse. El TTL por uso, y no por antigüedad, protege las reglas vigentes que se consultan a menudo. Los topes garantizan que la memoria activa nunca crezca sin límite, aunque el histórico auditable sí se conserva.

## 9. Anti-envenenamiento

Un usuario malicioso podría presentar información falsa como corrección legítima. Las defensas son:

1. **El usuario ve exactamente lo que se guardará.** No hay escrituras silenciosas.
2. **Política por código.** Bloquea instrucciones disfrazadas de hechos, datos personales, candidatos y salarios.
3. **Autoría y trazabilidad.** Cada hecho tiene `approved_by` y su propuesta, y cada decisión queda en la auditoría inmutable, así que se puede saber quién envenenó qué.
4. **Límite de 10 propuestas por usuario y día.** Evita el inundado de la memoria.
5. **Versiones en lugar de sobrescrituras.** Una corrección falsa deja el hecho anterior como `superseded`, recuperable desde la tabla.
6. **Jerarquía de fuentes en el prompt.** La memoria es "dato aportado por el equipo", nunca una instrucción. Si contradice un documento oficial del RAG, prevalece el documento y el agente menciona la discrepancia.
7. **La respuesta no da por buena la corrección.** El prompt pide no presentar como oficial una corrección no verificada.

En producción real la memoria core pasaría por tickets de cambio revisados por un equipo dedicado. Esa gobernanza se omite a propósito, como indica el README.

## 10. Por qué no hace falta multi-agente

- La **auto-evaluación** es un campo más (`propuesta_memoria`) en la salida estructurada de la misma llamada que genera la respuesta, sin una segunda llamada ni un agente evaluador.
- La **decisión** es una clasificación acotada a cinco etiquetas, más cercana a una función que a un agente: no planifica ni usa herramientas.
- La **escritura y la limpieza** son código determinista (`service.py`, `consolidation.py`): no hay nada que razonar.
- No hay subtareas paralelas ni roles con objetivos distintos que coordinar. Dividirlo en agentes añadiría latencia, coste y puntos de fallo sin ganar capacidad.

## 11. Fallos y límites

- Si la base de datos de memoria cae, el agente responde sin memoria y **sin aprobar nada**. Se registra el error y no se devuelve un 503. Lo cubre el eval `test_memory_store_outage_never_breaks_the_answer`.
- La generación y el clasificador piden JSON estricto (`response_format=json_object`; si el proveedor no lo admite, se repite sin él). En la prueba real, el modelo de 4Geeks a veces devolvía `{}` o mezclaba su razonamiento con el JSON. Por eso, si el **formato** falla, se repite la misma llamada una vez (`FORMAT_RETRIES = 1`). No es una segunda evaluación. Si vuelve a fallar, la generación responde con `generate_answer()` sin propuesta y el clasificador descarta.
- Ajustes del prompt tras la prueba real:
  - Con chunks del RAG en el contexto, el modelo no proponía correcciones "no verificadas". El criterio ahora depende solo de lo que afirma el usuario: su confirmación es el control.
  - El modelo citaba la memoria sin que existiera y decía "lo tendremos en cuenta" antes de que el usuario aprobara. Ambas cosas están prohibidas en el prompt.
- La relevancia es léxica: una pregunta formulada con vocabulario muy distinto al del hecho puede no recuperarlo. El siguiente paso sería una colección `nexova_agent_memory` con embeddings.
- `scripts/agent_record_traces.py` usa el grafo de producción, que ahora incluye memoria: sus corridas pueden registrar propuestas en Supabase.

## 12. Evidencia

- **Evals sin red** (`tests/pipelines/test_agent_memory_evals.py`, 44 tests). Cubren:
  - los 3 ejemplos del BRIEFING que sí y los 3 que no;
  - la política;
  - el ciclo aprobado reflejado en otra conversación y el ciclo rechazado con la memoria intacta;
  - la ambigüedad, la confianza baja, el cambio de tema, el fallo del clasificador y la caducidad a 24 h;
  - una sola pendiente;
  - decidir y preguntar a la vez;
  - la edición permitida y la bloqueada;
  - el autor y el límite diario;
  - la caída del store;
  - la deduplicación, el reemplazo, el TTL y los topes;
  - el aislamiento del RAG.
- **Evidencia real** con los modelos de 4Geeks y Supabase: [`memory-evidence.md`](memory-evidence.md), con los dos ciclos completos, las respuestas reales y las filas de `agent_memory_audit`.
