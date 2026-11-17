"""
Router: /chat — chat en vivo con el agente de soporte de primera línea.

- `POST /chat/sessions`: crea una ChatSession del usuario autenticado.
- `GET /chat/sessions/{session_id}`: sesión + mensajes (dueño o personal interno).
- `POST /chat/sessions/{session_id}/close`: cierra la sesión (aborta el turno en curso).
- `WS /chat/ws/{session_id}`: canal bidireccional del chat.

Autenticación del WebSocket con el MISMO JWT que el resto de la API: `?token=`
o, si no viene, un primer frame `{"event": "auth", "data": {"token": …}}` en
menos de 5 s. Mientras no hay credencial válida no se procesa ni se envía
ningún evento de chat. El socket se acepta antes de validar solo para poder
cerrar con un código que el navegador ve (4401/4403/4404); rechazar el
handshake llegaría al navegador como un 1006 sin motivo.
"""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from jose import JWTError
from pydantic import ValidationError

from agent.guardrails.tenancy import tenant_for_user
from chat import schemas
from chat.hub import ChatHub, Subscription, channel_for
from chat.runner import ChatService
from chat.schemas import AuthFrame, ChatSession, ChatSessionWithMessages, ChatMessageOut
from chat.store import SqliteChatStore
from models import User
from security import decode_access_token, get_current_user

router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_chat_service() -> ChatService:
    return ChatService(SqliteChatStore(), ChatHub())


def _load_session(service: ChatService, session_id: str, user: User) -> ChatSession:
    session = service.store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sesión de chat no encontrada.")
    if session.user_id != user.id and not tenant_for_user(user).is_staff:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="La sesión pertenece a otro usuario.")
    return session


@router.post("/sessions", response_model=ChatSession, status_code=status.HTTP_201_CREATED)
def create_session(
    current_user: User = Depends(get_current_user),
    service: ChatService = Depends(get_chat_service),
) -> ChatSession:
    return service.create_session(current_user.id, tenant_for_user(current_user).client_company)


@router.get("/sessions/{session_id}", response_model=ChatSessionWithMessages)
def get_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    service: ChatService = Depends(get_chat_service),
) -> ChatSessionWithMessages:
    session = _load_session(service, session_id, current_user)
    snapshot = service.runner(session_id).snapshot()["data"]
    return ChatSessionWithMessages(
        **session.model_dump(), messages=[ChatMessageOut(**m) for m in snapshot["messages"]]
    )


@router.post("/sessions/{session_id}/close", response_model=ChatSession)
async def close_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    service: ChatService = Depends(get_chat_service),
) -> ChatSession:
    session = _load_session(service, session_id, current_user)
    if session.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo quien chatea puede cerrarla.")
    await service.runner(session_id).close()
    closed = service.store.get_session(session_id)
    assert closed is not None
    return closed


# --- WebSocket -----------------------------------------------------------------


def user_from_token(token: str) -> User | None:
    from routes.users import get_user_by_id

    try:
        user_id = decode_access_token(token)
    except JWTError:
        return None
    return get_user_by_id(user_id)


async def _authenticate(websocket: WebSocket) -> User | None:
    token = websocket.query_params.get("token")
    if not token:
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=schemas.AUTH_TIMEOUT_SECONDS)
            token = AuthFrame.model_validate_json(raw).data.token
        except (asyncio.TimeoutError, ValidationError, ValueError, KeyError):
            return None
    return user_from_token(token)


async def _send_events(websocket: WebSocket, subscription: Subscription) -> None:
    while True:
        event = await subscription.queue.get()
        await websocket.send_json(event)
        if subscription.overflowed and subscription.queue.empty():
            await websocket.close(code=schemas.CLOSE_SLOW_CONSUMER)
            return


async def _receive_frames(websocket: WebSocket, service: ChatService, session_id: str,
                          user: User, can_write: bool) -> None:
    runner = service.runner(session_id)
    tenant = tenant_for_user(user)
    while True:
        raw = await websocket.receive_text()
        if len(raw.encode()) > schemas.MAX_FRAME_BYTES:
            await websocket.send_json(schemas.error("frame_too_large", "El mensaje es demasiado grande."))
            continue
        try:
            incoming = schemas.client_frame_adapter.validate_json(raw)
        except ValidationError:
            await websocket.send_json(schemas.error(
                "invalid_frame", f"Frame no válido (texto de 1 a {schemas.MAX_TEXT_LENGTH} caracteres)."
            ))
            continue
        if incoming.data.session_id != session_id:
            await websocket.send_json(schemas.error("session_mismatch", "El session_id no coincide con el socket."))
            continue
        if not can_write:
            await websocket.send_json(schemas.error("read_only", "Esta conexión solo observa la sesión."))
            continue
        session = service.store.get_session(session_id)
        if session is None or session.status == "closed":
            await websocket.send_json(schemas.error("session_closed", "La sesión está cerrada."))
            continue
        text = incoming.data.text if isinstance(incoming, schemas.UserMessageFrame) else incoming.data.new_input
        await runner.submit(text, user=user, tenant=tenant)


@router.websocket("/ws/{session_id}")
async def chat_socket(
    websocket: WebSocket,
    session_id: str,
    service: ChatService = Depends(get_chat_service),
) -> None:
    await websocket.accept()
    user = await _authenticate(websocket)
    if user is None:
        await websocket.close(code=schemas.CLOSE_UNAUTHORIZED, reason="unauthorized")
        return
    session = service.store.get_session(session_id)
    if session is None:
        await websocket.close(code=schemas.CLOSE_NOT_FOUND, reason="session_not_found")
        return
    is_owner = session.user_id == user.id
    if not is_owner and not tenant_for_user(user).is_staff:
        await websocket.close(code=schemas.CLOSE_FORBIDDEN, reason="forbidden")
        return

    runner = service.runner(session_id)
    # Suscribirse y tomar el snapshot sin `await` entre medias: todo evento posterior
    # al snapshot queda en la cola y ninguno anterior se repite.
    subscription = service.hub.subscribe(channel_for(session_id))
    snapshot: dict[str, Any] = runner.snapshot()
    tasks: list[asyncio.Task[None]] = []
    try:
        await websocket.send_json(snapshot)
        tasks = [
            asyncio.create_task(_send_events(websocket, subscription)),
            asyncio.create_task(_receive_frames(websocket, service, session_id, user, can_write=is_owner)),
        ]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, (WebSocketDisconnect, RuntimeError)):
                logger.warning("Conexión de chat %s terminada con %s", session_id, type(exc).__name__)
    except WebSocketDisconnect:
        pass
    finally:
        for task in tasks:
            task.cancel()
        service.hub.unsubscribe(subscription)
        # El turno en curso sigue: otra conexión (o la misma al reconectar) lo recibirá.
