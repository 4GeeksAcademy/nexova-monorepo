"""
Persistencia de sesiones y mensajes del chat (lo que permite rehidratar el hilo).

El agente no guarda historial (`agent/state.py`), así que el canal lleva el
suyo: `SqliteChatStore` en `data/agent/chat_sessions.sqlite` (junto a los
checkpoints; `CHAT_DB_PATH` lo cambia) e `InMemoryChatStore` para tests.

Los mensajes nunca se borran ni se sobrescriben en el sitio salvo para
completar el que se está generando: un mensaje `interrupted` conserva el texto
que se mostró y el turno siguiente es un mensaje nuevo.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from chat.schemas import ChatMessage, ChatSession, MessageStatus, SessionStatus

_API_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CHAT_DB_PATH = _API_DIR / "data" / "agent" / "chat_sessions.sqlite"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ChatStore(Protocol):
    def create_session(self, session: ChatSession) -> None: ...
    def get_session(self, session_id: str) -> ChatSession | None: ...
    def set_session_status(self, session_id: str, status: SessionStatus) -> None: ...
    def add_message(self, message: ChatMessage) -> None: ...
    def update_message(self, message_id: str, *, content: str, status: MessageStatus,
                       run_id: str | None = None) -> None: ...
    def list_messages(self, session_id: str) -> list[ChatMessage]: ...


class InMemoryChatStore:
    def __init__(self) -> None:
        self.sessions: dict[str, ChatSession] = {}
        self.messages: list[ChatMessage] = []

    def create_session(self, session: ChatSession) -> None:
        self.sessions[session.session_id] = session

    def get_session(self, session_id: str) -> ChatSession | None:
        return self.sessions.get(session_id)

    def set_session_status(self, session_id: str, status: SessionStatus) -> None:
        session = self.sessions.get(session_id)
        if session is not None:
            self.sessions[session_id] = session.model_copy(update={"status": status})

    def add_message(self, message: ChatMessage) -> None:
        self.messages.append(message)

    def update_message(self, message_id: str, *, content: str, status: MessageStatus,
                       run_id: str | None = None) -> None:
        for i, message in enumerate(self.messages):
            if message.message_id == message_id:
                update: dict[str, object] = {"content": content, "status": status}
                if run_id is not None:
                    update["run_id"] = run_id
                self.messages[i] = message.model_copy(update=update)

    def list_messages(self, session_id: str) -> list[ChatMessage]:
        return [m for m in self.messages if m.session_id == session_id]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id TEXT PRIMARY KEY,
    agent_id   TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    client_id  TEXT,
    status     TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL REFERENCES chat_sessions(session_id),
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    status     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    run_id     TEXT
);
CREATE INDEX IF NOT EXISTS chat_messages_session ON chat_messages(session_id, seq);
"""


class SqliteChatStore:
    """SQLite de la biblioteca estándar; un lock serializa el acceso entre hilos."""

    def __init__(self, path: str | Path | None = None) -> None:
        target = str(path or os.environ.get("CHAT_DB_PATH", DEFAULT_CHAT_DB_PATH))
        if target != ":memory:":
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(target, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)
            # Un reinicio a mitad de una generación deja el mensaje `streaming`: queda interrumpido.
            self._conn.execute("UPDATE chat_messages SET status = 'interrupted' WHERE status = 'streaming'")

    def create_session(self, session: ChatSession) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO chat_sessions VALUES (?, ?, ?, ?, ?, ?)",
                (session.session_id, session.agent_id, session.user_id, session.client_id,
                 session.status, session.created_at.isoformat()),
            )

    def get_session(self, session_id: str) -> ChatSession | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM chat_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return ChatSession.model_validate(dict(row)) if row else None

    def set_session_status(self, session_id: str, status: SessionStatus) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE chat_sessions SET status = ? WHERE session_id = ?", (status, session_id))

    def add_message(self, message: ChatMessage) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO chat_messages (message_id, session_id, role, content, status, created_at, run_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (message.message_id, message.session_id, message.role, message.content,
                 message.status, message.created_at.isoformat(), message.run_id),
            )

    def update_message(self, message_id: str, *, content: str, status: MessageStatus,
                       run_id: str | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE chat_messages SET content = ?, status = ?, run_id = COALESCE(?, run_id)"
                " WHERE message_id = ?",
                (content, status, run_id, message_id),
            )

    def list_messages(self, session_id: str) -> list[ChatMessage]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT message_id, session_id, role, content, status, created_at, run_id"
                " FROM chat_messages WHERE session_id = ? ORDER BY seq",
                (session_id,),
            ).fetchall()
        return [ChatMessage.model_validate(dict(row)) for row in rows]
