"""
Contrato del chat por WebSocket con el agente de soporte de primera línea.

Nombres de entidad y de campos del BRIEFING de la Parte 2 (§3–4). Todos los
frames son JSON `{"event": <nombre>, "data": {...}}`.

Servidor → cliente:
    session_snapshot        {session_id, status, messages: [ChatMessageOut]}
    user_message            {session_id, message_id, text}
    token_chunk             {session_id, message_id, token, sequence}
    generation_interrupted  {session_id, message_id, status: "interrupted"}
    generation_completed    {session_id, message_id, content?}
    error                   {code, message, message_id?}

Cliente → servidor:
    auth                    {token}            (solo como primer frame si no hay ?token=)
    user_message            {session_id, text}
    interrupt_requested     {session_id, new_input}

Extras sobre el BRIEFING (aditivos): `message_id` en `token_chunk` y en
`user_message`, `content` en `generation_completed` (solo cuando el guard de
salida u otra corrección cambió el texto ya mostrado), `status` en el snapshot
y el evento `error`. No se usan ni se mezclan los payloads RFP/SSE de la Parte 1.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

AGENT_ID = "first_line_support"
MAX_TEXT_LENGTH = 1000  # igual que `AgentQueryRequest.question`
MAX_FRAME_BYTES = 8 * 1024

SessionStatus = Literal["active", "interrupted", "closed"]
MessageStatus = Literal["streaming", "completed", "interrupted"]
MessageRole = Literal["user", "assistant"]

# Cierres del WebSocket (rango 4000–4999 de aplicación).
CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_NOT_FOUND = 4404
CLOSE_SLOW_CONSUMER = 4408
AUTH_TIMEOUT_SECONDS = 5.0


class ChatSession(BaseModel):
    """ChatSession del BRIEFING. `session_id` es también el `conversation_id` del agente."""

    session_id: str
    agent_id: Literal["first_line_support"] = AGENT_ID
    user_id: str
    client_id: str | None
    status: SessionStatus
    created_at: datetime


class ChatMessage(BaseModel):
    message_id: str
    session_id: str
    role: MessageRole
    content: str
    status: MessageStatus
    created_at: datetime
    run_id: str | None = None


class ChatMessageOut(BaseModel):
    """Mensaje tal como viaja en `session_snapshot` (sin `run_id`: el trace se queda en el servidor)."""

    message_id: str
    role: MessageRole
    content: str
    status: MessageStatus
    created_at: datetime
    # Último `sequence` aplicado, solo en el mensaje que se está generando.
    sequence: int | None = None


class ChatSessionWithMessages(ChatSession):
    messages: list[ChatMessageOut]


# --- Frames entrantes (cliente → servidor) -------------------------------------

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TEXT_LENGTH)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthData(_Strict):
    token: Annotated[str, StringConstraints(min_length=1, max_length=4096)]


class UserMessageData(_Strict):
    session_id: str
    text: Text


class InterruptData(_Strict):
    session_id: str
    new_input: Text


class AuthFrame(_Strict):
    event: Literal["auth"]
    data: AuthData


class UserMessageFrame(_Strict):
    event: Literal["user_message"]
    data: UserMessageData


class InterruptFrame(_Strict):
    event: Literal["interrupt_requested"]
    data: InterruptData


ClientFrame = Annotated[UserMessageFrame | InterruptFrame, Field(discriminator="event")]
client_frame_adapter: TypeAdapter[UserMessageFrame | InterruptFrame] = TypeAdapter(ClientFrame)


# --- Frames salientes (servidor → cliente) -------------------------------------


def frame(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"event": event, "data": data}


def token_chunk(session_id: str, message_id: str, token: str, sequence: int) -> dict[str, Any]:
    return frame("token_chunk", {"session_id": session_id, "message_id": message_id,
                                 "token": token, "sequence": sequence})


def user_message(session_id: str, message_id: str, text: str) -> dict[str, Any]:
    return frame("user_message", {"session_id": session_id, "message_id": message_id, "text": text})


def generation_interrupted(session_id: str, message_id: str) -> dict[str, Any]:
    return frame("generation_interrupted",
                 {"session_id": session_id, "message_id": message_id, "status": "interrupted"})


def generation_completed(session_id: str, message_id: str, content: str | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {"session_id": session_id, "message_id": message_id}
    if content is not None:
        data["content"] = content
    return frame("generation_completed", data)


def session_snapshot(session: ChatSession, messages: list[ChatMessageOut]) -> dict[str, Any]:
    return frame("session_snapshot", {
        "session_id": session.session_id,
        "status": session.status,
        "messages": [m.model_dump(mode="json", exclude_none=True) for m in messages],
    })


def error(code: str, message: str, message_id: str | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {"code": code, "message": message}
    if message_id is not None:
        data["message_id"] = message_id
    return frame("error", data)
