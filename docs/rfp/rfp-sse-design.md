# Notificaciones en tiempo real de RFPs (SSE) — Hito 10 · Parte 1

Rama `feature/sse-notifications`. Capa de comunicación: **no hay ninguna llamada a un modelo** en este cambio.

## Qué hace

Cuando un documento se clasifica como RFP válida y se guardan sus metadatos, el dashboard de Ventas (`/rfp`) muestra el ticket nuevo sin recargar: fila resaltada con badge "Nuevo", aviso (toast) con cliente, sede y servicios, e indicador de conexión ("En vivo" / "Reconectando…").

```
worker Celery ──XADD──▶ Redis Stream rfp:events ──XREAD──▶ GET /rfp/events (SSE) ──▶ proxy Next ──▶ fetch+ReadableStream
(extract_metadata)       (últimas ~500 entradas)            (una lectura por cliente)  /api/rfp/events      (Bearer JWT)
```

## Contrato

```
id: 1763391120000-0
event: rfp_ticket_created
data: {"ticket_id":"RFP-1A2B3C4D","rfp_id":"VRG-2026-011","client_name":"NubeSoft","client_hq":"Miami","services_requested":["Equipo de soporte al cliente 24/7"],"status":"analizando","created_at":"2026-07-24T14:32:00+00:00"}
```

- Solo campos del ticket y de `rfp_metadata`, con los nombres que ya usa el sistema de RFPs (sin sobre `{event, data}`).
- **`status` es `analizando`**, el valor que ya usa el backend (`TICKET_STATUSES`) y el backoffice; es el `analyzing` del BRIEFING. No se inventa un vocabulario paralelo.
- Keep-alive: línea de comentario `: keepalive` cada 15 s; primer frame `retry: 3000`.
- Acceso: **mismo JWT** que el resto de `/rfp` (`require_internal_user`): sin token → 401; usuario externo (`client_company`) → 403.

## Momento del disparo

El ticket se crea al subir el PDF (API), pero "RFP válida" y sus metadatos (`client_name`, `client_hq`, `services_requested`, `rfp_id`) existen en el **worker**, al final de `extract_metadata`. Se publica ahí, desde `rfp_intake/events.py`. Un documento descartado por el clasificador no emite nada. Consecuencia conocida: si más tarde el orquestador descarta el ticket por no pedir servicios de ningún departamento, el aviso ya salió; la bandeja lo mostrará como `descartado`.

El publicador nunca lanza ni bloquea el análisis (timeout de socket de 1 s) y, sin `REDIS_URL`, no hace nada. Un reintento de Celery no repite el aviso (`SET NX` por `ticket_id`, TTL 24 h).

## Estrategia de recuperación elegida: `Last-Event-ID` + replay corto

Cada evento es una entrada de un Redis Stream recortado a ~500; su id es el `id:` del frame. Al reconectar, el cliente manda `Last-Event-ID` y el servidor reanuda desde esa entrada. El cliente además **deduplica por `ticket_id`** (hook `useRfpTicketStream` + upsert de la fila), así que un replay nunca duplica la UI. Sin `Last-Event-ID` válido solo se reciben eventos posteriores a la conexión; la carga inicial de la bandeja ya trae la lista.

Límites: si la caída supera la ventana retenida (≈500 RFPs), lo anterior se recupera con la lista de tickets (recargar o el siguiente poll). El JWT (30 min) se valida al conectar; al reconectar con un token caducado el servidor responde 401 y el cliente cierra sesión.

Reconexión: backoff exponencial 1 s → 30 s con jitter, que se reinicia al recibir tráfico. Si no llega ningún byte en 45 s (3 keep-alives) la conexión se da por muerta y se reabre.

## Preguntas de diseño

**¿Conexiones independientes o capa compartida? ¿Y con 50 personas?** Cada conexión SSE es independiente a nivel HTTP, pero comparten una capa intermedia: el Redis Stream. El worker publica una sola vez y cada cliente lee con su propio cursor, así que no hay estado por cliente en el worker y el fan-out no depende de qué instancia de la API atienda a quién (funciona con varias réplicas). Con 50 personas son 50 conexiones largas y 50 `XREAD` bloqueantes; el coste es un socket por persona más una conexión Redis por stream, razonable para un equipo comercial. Si creciera a cientos, el siguiente paso sería un único lector por proceso de API que reparta a sus clientes en memoria. Cada pestaña abre su propia conexión.

**¿Qué estrategia de recuperación y cómo se evitan duplicados?** `Last-Event-ID` con replay corto en servidor (Redis Stream) más deduplicación por `ticket_id` en la UI. Ver arriba.

**¿Por qué SSE y no WebSockets? ¿Cuándo dejaría de serlo?** La notificación es unidireccional (servidor → navegador), sobre HTTP normal, con reconexión y `Last-Event-ID` definidos por el propio protocolo, y atraviesa proxies sin configuración especial. WebSocket aportaría un canal bidireccional que aquí no se necesita. Dejaría de ser suficiente si la persona tuviera que reaccionar al ticket desde el mismo canal (aprobar, comentar, ver quién más lo está mirando, chat): ahí hace falta tráfico cliente → servidor de baja latencia, y es el caso de la Parte 2 (chat por WebSocket). Hoy las acciones ya van por los endpoints REST de aprobación.

## Dónde está el código

| Pieza | Archivo |
|---|---|
| Publicador + contrato | `data/pipelines/rfp_intake/events.py` |
| Enganche | `data/pipelines/rfp_intake/nodes.py` (`extract_metadata`), `graph.py`, `intake.py` |
| Stream SSE | `services/api/rfp/events.py`, `GET /rfp/events` en `services/api/rfp/router.py` |
| Proxy sin buffer | `uis/backoffice/app/api/rfp/events/route.ts` |
| Cliente (`fetch` + `ReadableStream`) | `uis/backoffice/lib/sse.ts`, `lib/rfp-stream.ts`, `lib/useRfpTicketStream.ts` |
| UI | `app/(rfp)/rfp/page.tsx`, `components/rfp/NewTicketToast.tsx` |
| Tests | `tests/pipelines/test_rfp_sse.py`, `uis/backoffice/tests/rfp-stream.test.ts` |

## Cómo probarlo

1. `uv run --project services/api pytest tests/pipelines/test_rfp_sse.py -v` y `cd uis/backoffice && npm test`.
2. Con `docker compose up` (api, worker, redis, backoffice): abrir `/rfp` en dos ventanas y subir un PDF en una; la otra muestra la fila y el aviso sin recargar.
3. `curl -N -H "Authorization: Bearer $TOKEN" http://localhost:8000/rfp/events` → frames `event: rfp_ticket_created`. Sin token → 401.
4. Reconexión: parar la API (`docker compose stop api`) → "Reconectando…" con esperas crecientes; subir un PDF por otro camino o esperar a que el worker clasifique uno; `docker compose start api` → el ticket aparece una sola vez.
