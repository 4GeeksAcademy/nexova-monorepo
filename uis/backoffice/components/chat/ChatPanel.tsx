"use client";

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { isAwaitingFirstToken, MAX_MESSAGE_LENGTH, type ChatMessage } from "@/lib/chat-protocol";
import type { ChatConnectionState } from "@/lib/chat-socket";
import { useChatSession } from "@/lib/useChatSession";

/**
 * Chat en vivo con el agente de soporte de primera línea (WebSocket).
 * La respuesta se escribe token a token; mientras el agente responde, el campo
 * de texto sigue activo y enviar interrumpe la respuesta en curso: el parcial
 * queda visible como "Interrumpido" y la respuesta redirigida llega como un
 * mensaje nuevo.
 */

const EXAMPLES = [
  "¿Cuál es el estado de mi ticket?",
  "¿Cuánto tarda en resolverse una incidencia crítica?",
  "¿Cómo escalo un ticket que no avanza?",
];

const CONNECTION_LABEL: Record<ChatConnectionState, { text: string; tone: string }> = {
  connecting: { text: "Conectando…", tone: "bg-stone-100 text-stone-600" },
  rehydrating: { text: "Restaurando conversación…", tone: "bg-amber-50 text-amber-800" },
  live: { text: "En vivo", tone: "bg-teal-50 text-teal-800" },
  reconnecting: { text: "Reconectando…", tone: "bg-amber-50 text-amber-800" },
  unauthorized: { text: "Sesión expirada", tone: "bg-red-50 text-red-800" },
  forbidden: { text: "Sin acceso a esta conversación", tone: "bg-red-50 text-red-800" },
  not_found: { text: "Creando conversación…", tone: "bg-stone-100 text-stone-600" },
};

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  const streaming = message.status === "streaming";
  return (
    <li className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-relaxed shadow-sm ${
          isUser
            ? "bg-teal-700 text-white"
            : message.status === "interrupted"
              ? "border border-dashed border-stone-300 bg-stone-50 text-stone-600"
              : "border border-stone-200 bg-white text-stone-900"
        }`}
      >
        {message.content ? (
          <p className="whitespace-pre-wrap">
            {message.content}
            {streaming && <span className="ml-0.5 inline-block animate-pulse" aria-hidden>▍</span>}
          </p>
        ) : (
          <p className="italic text-stone-500">{streaming ? "Escribiendo…" : message.status === "interrupted" ? "Interrumpido antes de responder" : "(sin texto)"}</p>
        )}
        {message.status === "interrupted" && (
          <span className="mt-2 inline-block rounded-full bg-stone-200 px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide text-stone-600">
            Interrumpido
          </span>
        )}
      </div>
    </li>
  );
}

export default function ChatPanel() {
  const { chat, connection, generating, setupError, send, newChat } = useChatSession();
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement | null>(null);

  const lastContent = chat.messages.at(-1)?.content ?? "";
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [chat.messages.length, lastContent]);

  const trimmed = draft.trim();
  const canSend = connection === "live" && trimmed.length > 0 && chat.sessionStatus !== "closed";

  function submit(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    if (!canSend) return;
    if (send(trimmed)) setDraft("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  const label = CONNECTION_LABEL[connection];

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-col px-6 py-10">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-stone-900">Chat de soporte</h1>
          <p className="mt-1 text-sm text-stone-600">
            Agente de soporte de primera línea. Puedes escribir mientras responde para corregir el rumbo.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`rounded-full px-3 py-1 text-xs font-medium ${label.tone}`} role="status">
            {label.text}
          </span>
          <button
            type="button"
            onClick={newChat}
            className="rounded-full border border-stone-300 bg-white px-3 py-1 text-xs text-stone-700 transition hover:border-teal-600 hover:text-teal-700"
          >
            Nueva conversación
          </button>
        </div>
      </div>

      {setupError && (
        <div role="alert" className="mt-6 rounded-2xl border border-red-300 bg-red-50 p-4 text-sm text-red-800">
          {setupError}
        </div>
      )}

      <section
        aria-label="Conversación"
        aria-live="polite"
        className="mt-6 min-h-[18rem] rounded-2xl border border-stone-200 bg-stone-50/60 p-4"
      >
        {chat.messages.length === 0 ? (
          <p className="py-16 text-center text-sm text-stone-500">
            Escribe tu consulta para empezar la conversación.
          </p>
        ) : (
          <ul className="space-y-3">
            {chat.messages.map((message) => (
              <MessageBubble key={message.messageId} message={message} />
            ))}
            {isAwaitingFirstToken(chat) && (
              <li className="flex justify-start">
                <div className="rounded-2xl border border-stone-200 bg-white px-4 py-3 text-sm italic text-stone-500 shadow-sm">
                  El agente está pensando…
                </div>
              </li>
            )}
          </ul>
        )}
        <div ref={endRef} />
      </section>

      {chat.error && (
        <div role="alert" className="mt-3 rounded-2xl border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          {chat.error.message}
        </div>
      )}

      <form onSubmit={submit} className="mt-4 space-y-3">
        <label htmlFor="chat-input" className="sr-only">
          Tu mensaje
        </label>
        <textarea
          id="chat-input"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          maxLength={MAX_MESSAGE_LENGTH}
          rows={3}
          placeholder={
            generating
              ? "El agente está respondiendo: escribe para interrumpirlo y redirigirlo…"
              : "Ej.: ¿Cuál es el estado del ticket TCK-1042?"
          }
          className="w-full rounded-2xl border border-stone-300 bg-white/85 p-4 text-sm text-stone-900 shadow-sm outline-none focus:border-teal-600"
        />
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="submit"
            disabled={!canSend}
            className={`rounded-full px-5 py-2 text-sm font-medium text-white transition disabled:cursor-not-allowed disabled:opacity-50 ${
              generating ? "bg-amber-600 hover:bg-amber-700" : "bg-teal-700 hover:bg-teal-800"
            }`}
          >
            {generating ? "Interrumpir y enviar" : "Enviar"}
          </button>
          {!generating &&
            chat.messages.length === 0 &&
            EXAMPLES.map((example) => (
              <button
                key={example}
                type="button"
                onClick={() => setDraft(example)}
                className="rounded-full border border-stone-300 bg-white px-3 py-1.5 text-xs text-stone-700 transition hover:border-teal-600 hover:text-teal-700"
              >
                {example}
              </button>
            ))}
        </div>
      </form>
    </main>
  );
}
