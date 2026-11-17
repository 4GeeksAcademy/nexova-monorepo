# Chat en vivo por WebSocket con el agente de soporte — Hito 10 · Parte 2

Rama `feature/websocket-chat` (sale de `feature/sse-notifications`). Solo cambia el **canal** del agente de soporte de primera línea (`agent_id = first_line_support`): su lógica, prompts, tools, memoria y guardrails son los mismos que en `POST /agent/query`.

## Qué hace

En `/knowledge` (menú "Chat de soporte") la respuesta del agente se escribe token a token. El campo de texto sigue activo mientras el agente responde: enviar un mensaje **aborta** la generación en curso, el parcial queda visible como "Interrumpido" y la respuesta redirigida llega como un mensaje nuevo. Si la conexión se cae, el cliente reconecta con backoff, recibe un `session_snapshot` con todo el hilo y sigue el stream donde estaba.

```
navegador ──WS /chat/ws/{session_id}?token=JWT──▶ router (consumidor) ──submit──▶ SessionRunner (productor)
    ▲                                                     ▲                          │ asyncio.to_thread
    │                                                     │ cola por conexión         ▼
    └──────────── eventos JSON ◀── ChatHub canal chat.<session_id> ◀── publish ── stream_agent
                                                                                    (LangGraph stream_mode=custom)
```

## Contrato (BRIEFING §3–4)

Frames JSON `{"event": …, "data": {…}}`.

| Dirección | Evento | `data` |
|---|---|---|
| servidor → cliente | `session_snapshot` | `session_id`, `status`, `messages[]` (`message_id`, `role`, `content`, `status`, `created_at`, `sequence` en el mensaje en curso) |
| servidor → cliente | `user_message` | `session_id`, `message_id`, `text` |
| servidor → cliente | `token_chunk` | `session_id`, `message_id`, `token`, `sequence` (desde 1 por mensaje) |
| servidor → cliente | `generation_interrupted` | `session_id`, `message_id`, `status: "interrupted"` |
| servidor → cliente | `generation_completed` | `session_id`, `message_id`, `content?` |
| servidor → cliente | `error` | `code`, `message`, `message_id?` |
| cliente → servidor | `user_message` | `session_id`, `text` (1–1000 caracteres) |
| cliente → servidor | `interrupt_requested` | `session_id`, `new_input` |
| cliente → servidor | `auth` | `token` (solo como primer frame, si no hay `?token=`) |

Extras aditivos sobre el BRIEFING: `message_id` en `token_chunk` y `user_message`, `content` en `generation_completed` (solo cuando el texto definitivo difiere del mostrado), `status` en el snapshot y el evento `error`. No se mezclan payloads RFP/SSE de la Parte 1.

**ChatSession**: `session_id` (UUID; es también el `conversation_id` que el agente usa para su memoria, sin ids paralelos), `agent_id`, `user_id`, `client_id` (`client_company` del usuario), `status` (`active` / `interrupted` / `closed`), `created_at`. `POST /chat/sessions` la crea; `GET /chat/sessions/{id}` la devuelve con sus mensajes.

**Autenticación**: el mismo JWT que el resto de la API, en `?token=` o en un primer frame `auth` (5 s de plazo). Mientras no hay credencial válida no se procesa ni se envía ningún evento de chat. Cierres: `4401` sin credencial válida, `4404` sesión inexistente, `4403` sesión de otro usuario (salvo personal interno, que entra como observador de solo lectura), `4408` consumidor demasiado lento. El socket se acepta antes de validar solo para poder cerrar con esos códigos: rechazar el handshake llegaría al navegador como un `1006` sin motivo.

## Streaming de tokens

El agente llama al SDK `openai` (cliente genérico de cualquier proveedor compatible: `LLM_BASE_URL`, `GENERATION_MODEL`), no a un modelo de LangChain, así que el modo `messages` de LangGraph no ve tokens. Se usa el modo **`custom`**:

- `agent/streaming.py` construye un grafo hermano (`get_chat_agent_graph`) con el **mismo** `build_graph()` de producción y el mismo checkpointer; solo inyecta `generate_fn` / `structured_generate_fn` que pasan un `StreamingLLM` a las funciones de generación existentes (`secure_generate`, `generate_with_memory`).
- `StreamingLLM.create()` llama al LLM con `stream=True`, escribe cada fragmento con `get_stream_writer()` y devuelve un objeto con la forma de siempre (`choices[0].message.content`).
- Con memoria, el modelo responde JSON (`{"respuesta", "propuesta_memoria"}`): `RespuestaFieldExtractor` emite solo el texto de `respuesta` (escapes y `\uXXXX` incluidos, aunque lleguen partidos). Al terminar, `parse_structured` lee el JSON completo como siempre.
- `agent.runner.stream_agent` recorre el grafo con `stream_mode=["custom", "values"]`. Comparte con `run_agent` el cierre del trace (`_finish_run`); `run_agent` no cambia de comportamiento.
- Respuestas que no pasan por el LLM en streaming (guard de entrada, small talk, `tool_fallback`, sin información) se trocean por palabras al final, así el contrato es siempre el mismo.

## Interrupción (abort, no HITL)

1. `interrupt_requested` (o un `user_message` mientras hay respuesta en curso, que es lo que envía la UI).
2. El runner levanta el `threading.Event` del turno. Desde ese instante descarta cualquier fragmento de esa generación, y `StreamingLLM` deja de leer y **cierra el stream HTTP del modelo** (`GenerationAborted`). El runner espera a que el hilo termine.
3. El parcial se guarda como mensaje `interrupted` con el texto ya mostrado (no se borra ni se sobrescribe), la sesión pasa a `interrupted` y se publica `generation_interrupted`. El trace de esa corrida queda con `status = "interrupted"`.
4. Se guarda y publica el `user_message` con `new_input`, la sesión vuelve a `active` y arranca un **turno nuevo** (otro `run_id`, otro `message_id`, `sequence` desde 1).

No se usa `interrupt()` de LangGraph: no hay pausa de grafo que reanudar. Como el agente no lee historial, el turno que sigue a una interrupción recibe "Pregunta anterior (interrumpida): … / Corrección del usuario: …" (máx. 1000 caracteres), para que "en realidad era sobre la factura" tenga contexto. Es solo texto del usuario dentro de `<mensaje_usuario>`: no cambia prompts ni guardrails.

**Guard de salida.** `check_output` sigue validando la respuesta completa. Si la cambia, `generation_completed.content` trae el texto definitivo, la UI sustituye la burbuja y solo se persiste el texto validado. Riesgo aceptado: el texto bloqueado se vio un instante. La alternativa (retener y validar por frases) añadía latencia y bastante código.

## Pub/sub y reconexión

- **Productor**: un `SessionRunner` por sesión (`chat/runner.py`), con un único turno en curso. Corre el agente en un hilo y publica cada evento una vez en el canal `chat.<session_id>`.
- **Consumidores**: cada conexión WebSocket tiene su cola en `ChatHub` (`chat/hub.py`). `publish` nunca bloquea: un consumidor que no vacía su cola se desconecta (`4408`) y vuelve con un snapshot, sin frenar a los demás.
- **Rehidratación**: el historial vive en SQLite (`data/agent/chat_sessions.sqlite`, `CHAT_DB_PATH`). Al conectar, la conexión se suscribe y toma el snapshot sin ningún `await` entre medias: todo lo posterior queda en su cola y nada anterior se repite. El snapshot incluye el mensaje en curso con su texto parcial y su `sequence`, y el cliente no aplica tokens hasta recibirlo. Si el servidor se reinicia a mitad de una respuesta, ese mensaje queda `interrupted`.
- **Cliente** (`uis/backoffice/lib/chat-socket.ts`): backoff exponencial de 1 a 30 s con jitter (`backoffDelayMs` de la Parte 1), que se reinicia al llegar a "en vivo". `4401` cierra la sesión del backoffice; `4403`/`4404` no reintentan, y con `4404` la página crea una conversación nueva.
- **URL del WebSocket**: `NEXT_PUBLIC_CHAT_WS_URL` si está definida; si no, `ws(s)://<host de la página>:8000`, que es el puerto que publica `docker-compose.yml`. Los route handlers de Next no hacen WebSocket: solo `POST /api/chat/sessions` pasa por proxy.

Límite: el hub está en memoria y sirve para **un proceso** de uvicorn (BRIEFING §5). Con varios workers, el siguiente paso es un backplane Redis pub/sub detrás del mismo protocolo `Hub`.

## Preguntas de diseño

**¿Por qué WebSockets y no lo de la Parte 1?** El usuario tiene que poder hablar mientras el servidor sigue enviando. La interrupción llega a mitad del stream y tiene que poder abortarlo en el mismo canal, con un orden claro entre "estos tokens", "aborto" y "turno nuevo". SSE es solo servidor → navegador. Habría que abrir un POST aparte por cada interrupción, sin ordenación respecto al stream ni estado de conexión compartido, y reconstruir a mano a qué generación corresponde cada señal.

**Varios clientes en la misma sesión (p. ej. un supervisor):** el agente lo invoca solo el `SessionRunner` de la sesión, una vez por turno. Cada evento se publica una vez en `chat.<session_id>` y el hub lo reparte a todas las conexiones suscritas. Un observador que entra a mitad de respuesta recibe el snapshot con el parcial y su `sequence`, y desde ahí los mismos tokens que el resto. Solo quien chatea puede escribir; el supervisor (personal interno) entra en solo lectura.

**Abort del stream vs `interrupt()` HITL:** no se usa `interrupt()`. El abort es cancelación cooperativa del stream del modelo (evento + cierre de la respuesta HTTP del LLM), no una pausa del grafo. El mensaje parcial se conserva como `interrupted` con lo que se mostró, y la siguiente respuesta es un turno nuevo, con su propio `message_id` y su propia corrida del agente, que refleja `new_input`.

## Dónde está el código

| Pieza | Archivo |
|---|---|
| Streaming del agente (sin cambiar su lógica) | `services/api/agent/streaming.py`, `stream_agent` en `services/api/agent/runner.py` |
| Contrato | `services/api/chat/schemas.py` |
| Historial | `services/api/chat/store.py` |
| Pub/sub | `services/api/chat/hub.py` |
| Productor por sesión | `services/api/chat/runner.py` |
| REST + WebSocket | `services/api/chat/router.py` |
| Cliente | `uis/backoffice/lib/chat-protocol.ts`, `lib/chat-socket.ts`, `lib/chat-api.ts`, `lib/useChatSession.ts` |
| UI | `components/chat/ChatPanel.tsx`, `app/(knowledge)/knowledge/page.tsx`, proxy `app/api/chat/sessions/route.ts` |
| Tests | `tests/pipelines/test_chat_ws.py`, `uis/backoffice/tests/chat.test.ts` |

## Cómo probarlo

### Automático

```bash
uv run --project services/api pytest tests/pipelines/test_chat_ws.py -v
cd uis/backoffice && npm test
```

`test_chat_ws.py` (21 tests, sin LLM ni base de datos) cubre:
- **Autenticación**: `?token=`, primer frame, JWT real, `4401`/`4403`/`4404`.
- **Contrato de eventos.**
- **Abort medible**: ningún `token_chunk` del mensaje original tras `generation_interrupted`, stream cerrado y solo 2 tokens emitidos.
- **Parcial y turno nuevo**: el parcial queda `interrupted` y el turno nuevo tiene otro `message_id` y una pregunta con `new_input`.
- **Reconexión** a mitad de respuesta: el snapshot trae el historial y el parcial, y siguen los tokens `sequence` 3 y 4.
- **Reinicio del servidor** con un mensaje a medias.
- **Supervisor** con los mismos eventos y una sola llamada al agente.
- **Guard** que reemplaza el texto.
- **Extractor de `respuesta`.**
- **`stream_agent` con un grafo LangGraph real** y un cliente OpenAI falso: tokens por el modo `custom` y abort que cierra el stream.

### Manual

Con `docker compose up` (api, qdrant, mcp, backoffice) y las claves del LLM en `.env`. El proveedor de `LLM_BASE_URL` tiene que soportar `stream=True`.

1. En `/knowledge`, pregunta algo largo ("Explícame paso a paso cómo escalar un ticket crítico"). El texto se escribe en vivo.
2. Mientras responde, escribe "en realidad mi pregunta era sobre la factura" y pulsa "Interrumpir y enviar". El parcial queda con la etiqueta "Interrumpido" y debajo aparece la respuesta nueva sobre la factura.
3. Con una respuesta en curso, ejecuta `docker compose restart api`. Verás "Reconectando…", luego "Restaurando conversación…" y el hilo vuelve igual; el mensaje que estaba a medias aparece como interrumpido. Recargar la página tampoco pierde el hilo (el `session_id` está en `sessionStorage`).
4. Cliente de consola (sustituye `$TOKEN` y `$SID`, este último de `POST /chat/sessions`):

```bash
uv run --project services/api python - <<'EOF'
import asyncio, json, os, websockets

async def main():
    url = f"ws://localhost:8000/chat/ws/{os.environ['SID']}?token={os.environ['TOKEN']}"
    async with websockets.connect(url) as ws:
        print(json.loads(await ws.recv())["event"])  # session_snapshot
        await ws.send(json.dumps({"event": "user_message", "data": {"session_id": os.environ["SID"], "text": "Explícame cómo escalar un ticket crítico"}}))
        for _ in range(8):
            print(await ws.recv())
        await ws.send(json.dumps({"event": "interrupt_requested", "data": {"session_id": os.environ["SID"], "new_input": "en realidad era sobre la factura"}}))
        while True:
            frame = json.loads(await ws.recv())
            print(frame["event"], frame["data"].get("token", ""))
            if frame["event"] == "generation_completed":
                break

asyncio.run(main())
EOF
```

Sin token, o con el token de otro usuario, el socket se cierra con `4401`/`4403` sin enviar ningún evento de chat.
