"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { getToken } from "./auth-storage";
import { ChatApiError, createChatSession } from "./chat-api";
import { chatReducer, INITIAL_CHAT_STATE, isGenerating, type ChatState } from "./chat-protocol";
import { connectChatSocket, type ChatConnectionState, type ChatSocket } from "./chat-socket";
import { handleSessionExpired } from "./session";

/**
 * Hook del chat de soporte: crea o reanuda la sesión (su `session_id` se guarda
 * en `sessionStorage` para reengancharse al mismo hilo tras recargar o
 * reconectar), abre el WebSocket y aplica cada evento con `chatReducer`.
 *
 * `send(text)` envía un turno; si el agente está respondiendo, lo interrumpe
 * (`interrupt_requested`) y la respuesta redirigida llega como mensaje nuevo.
 */

const STORAGE_KEY = "nexova.chat.session_id";

function readStoredSession(): string | null {
  if (typeof window === "undefined") return null;
  return window.sessionStorage.getItem(STORAGE_KEY);
}

export interface UseChatSession {
  chat: ChatState;
  connection: ChatConnectionState;
  generating: boolean;
  setupError: string | null;
  send: (text: string) => boolean;
  newChat: () => void;
}

export function useChatSession(): UseChatSession {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [connection, setConnection] = useState<ChatConnectionState>("connecting");
  const [setupError, setSetupError] = useState<string | null>(null);
  const [chat, dispatch] = useReducer(chatReducer, INITIAL_CHAT_STATE);
  const socketRef = useRef<ChatSocket | null>(null);
  const [generation, setGeneration] = useState(0); // fuerza una sesión nueva

  // 1) Sesión: la guardada o una nueva.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const id = readStoredSession() ?? (await createChatSession()).sessionId;
        window.sessionStorage.setItem(STORAGE_KEY, id);
        if (!cancelled) {
          setSetupError(null);
          setSessionId(id);
        }
      } catch (err) {
        if (cancelled || (err instanceof ChatApiError && err.status === 401)) return;
        setSetupError(err instanceof Error ? err.message : "No se pudo iniciar el chat.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [generation]);

  // 2) WebSocket ligado a esa sesión.
  useEffect(() => {
    if (!sessionId) return;
    const controller = new AbortController();
    socketRef.current = connectChatSocket({
      sessionId,
      signal: controller.signal,
      getToken,
      onEvent: dispatch,
      onState: (state) => {
        setConnection(state);
        if (state === "not_found") {
          // La sesión ya no existe en el servidor: empezar una nueva.
          window.sessionStorage.removeItem(STORAGE_KEY);
          setSessionId(null);
          setGeneration((g) => g + 1);
        }
      },
      onUnauthorized: handleSessionExpired,
    });
    return () => {
      controller.abort();
      socketRef.current = null;
    };
  }, [sessionId]);

  const generating = isGenerating(chat);

  const send = useCallback(
    (text: string): boolean => {
      const socket = socketRef.current;
      if (!socket) return false;
      return generating ? socket.sendInterrupt(text) : socket.sendUserMessage(text);
    },
    [generating],
  );

  const newChat = useCallback(() => {
    window.sessionStorage.removeItem(STORAGE_KEY);
    dispatch({ event: "session_snapshot", sessionId: "", status: "active", messages: [] });
    setSessionId(null);
    setConnection("connecting");
    setGeneration((g) => g + 1);
  }, []);

  return { chat, connection, generating, setupError, send, newChat };
}
