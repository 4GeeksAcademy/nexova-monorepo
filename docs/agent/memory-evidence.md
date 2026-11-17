# Evidencia: memoria del agente (Hito 8 · Parte 1)

Generado con `scripts/agent_memory_demo.py` el 2026-11-15 10:41 UTC, contra el grafo de producción (modelos de 4Geeks, Qdrant, MCP Server y Supabase).

Antes de empezar se expiraron 1 hechos de corridas anteriores de los usuarios demo (estado `expired`, motivo `demo_reset`, auditado; nada se borra).

## Ciclo aprobado

`conversation_id` = `08357eed-a3be-4f67-9d0c-16e53ac3fb5a`

**1. Usuario (demo-soporte-a):** En realidad los tickets de nómina para clientes de retail ahora se resuelven en 24 horas, no en 48 como decía el SLA anterior.

> Gracias por compartir esa información. La base de conocimiento disponible no cubre plazos de resolución de tickets de nómina para clientes de retail. Esto escapa a lo que puedo confirmar con el material que tengo. Te recomendaría consultar directamente con un account manager senior de Nexova para validarlo. Fuente: Ningún documento del CONTEXTO aborda tickets de nómina.
>
> ---
> ¿Quieres que recuerde esto para la próxima vez? «Los tickets de nómina para clientes de retail se resuelven en 24 horas.» (Patrón de incidentes · Outsourcing de soporte). Responde «sí», «no» o envíame la versión corregida.

Propuesta registrada: `{'id': '8849aba8-d460-464b-94bc-c44e56ebcf09', 'fact': 'Los tickets de nómina para clientes de retail se resuelven en 24 horas.', 'business_line': 'support', 'category': 'incident_pattern'}` · trace `tests/pipelines/fixtures/agent_memory/approved-1-proposal.json`

**2. Usuario (demo-soporte-a), misma conversación:** Sí, guárdalo.

> Guardado en la memoria del equipo: «Los tickets de nómina para clientes de retail se resuelven en 24 horas.».

Decisión: `{'proposal_id': '8849aba8-d460-464b-94bc-c44e56ebcf09', 'outcome': 'approved', 'label': 'approve', 'fact_id': '2b7ae502-c40e-4780-9a1e-50b807a0f603'}` · trace `tests/pipelines/fixtures/agent_memory/approved-2-decision.json`

Hechos activos antes: 0 · después: 1 · nuevos: ['2b7ae502-c40e-4780-9a1e-50b807a0f603']

**3. Otra persona (demo-soporte-b), conversación nueva:** ¿En cuánto tiempo hay que resolver los tickets de nómina de clientes de retail?

> Según la información disponible, los tickets de nómina para clientes de retail se resuelven en un promedio de 24 horas. Este es un dato operativo confirmado por el equipo de soporte, pero no constituye una garantía contractual. Para más detalles sobre plazos específicos en un contrato, consulta con tu account manager.
>
> Fuente: Memoria aprobada del equipo — Sección: Patrón de incidentes · Outsourcing de soporte

trace `tests/pipelines/fixtures/agent_memory/approved-3-later.json`

Auditoría de la conversación (`agent_memory_audit`):

| created_at | event | proposal_id | entry_id | details |
|---|---|---|---|---|
| 2026-11-15 10:41:32 | proposed | 8849aba8-d460-464b-94bc-c44e56ebcf09 | — | {'fact': 'Los tickets de nómina para clientes de retail se resuelven en 24 horas.', 'reason': 'El usuario afirma una regla operativa corregida sobre el SLA de resolución de tickets de nómina para retail, que no estaba en el CONTEXTO.', 'category': 'incident_pattern', 'business_line': 'support'} |
| 2026-11-15 10:41:36 | approved | 8849aba8-d460-464b-94bc-c44e56ebcf09 | — | {'label': 'approve', 'method': 'llm', 'raw_label': 'approve', 'confidence': 1.0} |
| 2026-11-15 10:41:36 | written | 8849aba8-d460-464b-94bc-c44e56ebcf09 | 2b7ae502-c40e-4780-9a1e-50b807a0f603 | {'fact': 'Los tickets de nómina para clientes de retail se resuelven en 24 horas.', 'category': 'incident_pattern', 'business_line': 'support', 'supersedes_id': None} |

## Ciclo rechazado

`conversation_id` = `e8646ca0-56bb-4b6f-909f-1909122ecfbf`

**1. Usuario (demo-soporte-a):** Ese cliente de finanzas siempre pide que le confirmemos por email antes de cerrar un ticket, aunque el sistema permita cerrarlo directo.

> Entendido. Para clientes del sector finanzas, la preferencia es confirmar por email antes de cerrar un ticket, aunque el sistema lo permita. Esto no está documentado en nuestra base de conocimiento actual, pero lo tomamos como un dato operativo. Si necesitas ajustar el proceso para ese cliente, coméntalo con el account manager asignado.
>
> Fuente: Memoria aprobada del equipo — Sección: Patrón de incidentes · Outsourcing de soporte
>
> ---
> ¿Quieres que recuerde esto para la próxima vez? «Los clientes de finanzas requieren confirmación por email antes de cerrar un ticket, incluso si el sistema permite el cierre directo.» (Preferencia de cliente · Outsourcing de soporte). Responde «sí», «no» o envíame la versión corregida.

Propuesta registrada: `{'id': '8b459227-5b48-4bf4-ae72-3ac57f295f08', 'fact': 'Los clientes de finanzas requieren confirmación por email antes de cerrar un ticket, incluso si el sistema permite el cierre directo.', 'business_line': 'support', 'category': 'client_preference'}` · trace `tests/pipelines/fixtures/agent_memory/rejected-1-proposal.json`

**2. Usuario (demo-soporte-a), misma conversación:** No, no hace falta que lo recuerdes.

> Entendido, no guardaré esa propuesta en la memoria.

Decisión: `{'proposal_id': '8b459227-5b48-4bf4-ae72-3ac57f295f08', 'outcome': 'rejected', 'label': 'reject', 'fact_id': None}` · trace `tests/pipelines/fixtures/agent_memory/rejected-2-decision.json`

Hechos activos antes: 1 · después: 1 · nuevos: ninguno

**3. Otra persona (demo-soporte-b), conversación nueva:** ¿Hay algo especial que deba hacer antes de cerrar los tickets del cliente de finanzas?

> Según la información disponible, no hay un procedimiento especial documentado para cerrar tickets de clientes de finanzas. Para obtener orientación específica sobre el manejo de tickets de este cliente, te sugiero consultar con un account manager senior o con el equipo de soporte directo. Fuente: No se encontró información específica en el CONTEXTO sobre tickets de clientes de finanzas.

trace `tests/pipelines/fixtures/agent_memory/rejected-3-later.json`

Auditoría de la conversación (`agent_memory_audit`):

| created_at | event | proposal_id | entry_id | details |
|---|---|---|---|---|
| 2026-11-15 10:41:50 | proposed | 8b459227-5b48-4bf4-ae72-3ac57f295f08 | — | {'fact': 'Los clientes de finanzas requieren confirmación por email antes de cerrar un ticket, incluso si el sistema permite el cierre directo.', 'reason': 'El usuario informa una preferencia operativa de una empresa cliente de outsourcing (finanzas) que no está en el contexto actual.', 'category': 'client_preference', 'business_line': 'support'} |
| 2026-11-15 10:41:56 | rejected | 8b459227-5b48-4bf4-ae72-3ac57f295f08 | — | {'label': 'reject', 'method': 'llm', 'raw_label': 'reject', 'confidence': 1.0} |

## Interacciones que no generan propuesta

| Mensaje | ¿Propuesta? |
|---|---|
| ¿Cuántos tickets abiertos hay ahora mismo? | no |
| Perfecto, gracias por la explicación. | no |
| Resume este ticket en dos líneas para el reporte de Laura. | no |
