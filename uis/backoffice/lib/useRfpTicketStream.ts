"use client";

import { useEffect, useRef, useState } from "react";
import { getToken } from "./auth-storage";
import { connectRfpStream, type StreamState } from "./rfp-stream";
import { handleSessionExpired } from "./session";
import type { RfpTicketCreatedEvent } from "./sse";

/**
 * Suscribe el componente al stream SSE de RFPs. `onTicketCreated` se llama una
 * sola vez por `ticket_id` aunque el servidor reenvíe el evento al reconectar.
 */
export function useRfpTicketStream(onTicketCreated: (event: RfpTicketCreatedEvent) => void): StreamState {
  const [state, setState] = useState<StreamState>("connecting");
  const handlerRef = useRef(onTicketCreated);
  const seenRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    handlerRef.current = onTicketCreated;
  }, [onTicketCreated]);

  useEffect(() => {
    const controller = new AbortController();
    void connectRfpStream({
      signal: controller.signal,
      getToken,
      onUnauthorized: handleSessionExpired,
      onState: setState,
      onEvent: (event) => {
        if (seenRef.current.has(event.ticket_id)) return;
        seenRef.current.add(event.ticket_id);
        handlerRef.current(event);
      },
    });
    return () => controller.abort();
  }, []);

  return state;
}
