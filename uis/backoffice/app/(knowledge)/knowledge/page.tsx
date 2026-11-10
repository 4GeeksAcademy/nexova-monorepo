"use client";

import { useState, type FormEvent } from "react";
import { askKnowledgeBase, KnowledgeError, MAX_QUESTION_LENGTH } from "@/lib/knowledge";

/**
 * Asistente de ventas de Nexova: el SDR escribe una pregunta y ve la respuesta
 * generada a partir de la base de conocimiento (servicios, tarifas, SLA y
 * objeciones). Distingue cargando, error y respuesta: un fallo de la API nunca
 * se muestra como una respuesta vacía.
 */

const EXAMPLES = [
  "¿Cuánto cuesta un programa de formación de 8 semanas?",
  "¿Qué pasa si la terna no me convence?",
  "El 22% me parece caro, ¿qué respondo?",
];

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "answer"; question: string; answer: string };

export default function KnowledgePage() {
  const [question, setQuestion] = useState("");
  const [state, setState] = useState<State>({ kind: "idle" });

  const trimmed = question.trim();
  const canSubmit = trimmed.length >= 3 && state.kind !== "loading";

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) return;
    setState({ kind: "loading" });
    try {
      const answer = await askKnowledgeBase(trimmed);
      setState({ kind: "answer", question: trimmed, answer });
    } catch (err) {
      if (err instanceof KnowledgeError && err.status === 401) return; // redirige al login
      setState({
        kind: "error",
        message: err instanceof Error ? err.message : "No se pudo obtener la respuesta.",
      });
    }
  }

  return (
    <main className="mx-auto w-full max-w-3xl px-6 py-10">
      <h1 className="text-2xl font-semibold text-stone-900">Asistente de ventas</h1>
      <p className="mt-1 text-sm text-stone-600">
        Pregunta lo que necesites antes o durante una llamada con un prospecto.
      </p>

      <form onSubmit={submit} className="mt-6 space-y-3">
        <label htmlFor="question" className="sr-only">
          Tu pregunta
        </label>
        <textarea
          id="question"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          maxLength={MAX_QUESTION_LENGTH}
          rows={3}
          placeholder="Ej.: ¿Cuánto tarda una búsqueda de un perfil ejecutivo?"
          className="w-full rounded-2xl border border-stone-300 bg-white/85 p-4 text-sm text-stone-900 shadow-sm outline-none focus:border-teal-600"
        />
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="submit"
            disabled={!canSubmit}
            className="rounded-full bg-teal-700 px-5 py-2 text-sm font-medium text-white transition hover:bg-teal-800 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {state.kind === "loading" ? "Consultando…" : "Preguntar"}
          </button>
          {EXAMPLES.map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => setQuestion(example)}
              className="rounded-full border border-stone-300 bg-white px-3 py-1.5 text-xs text-stone-700 transition hover:border-teal-600 hover:text-teal-700"
            >
              {example}
            </button>
          ))}
        </div>
      </form>

      <div className="mt-8" aria-live="polite">
        {state.kind === "loading" && (
          <p className="text-sm text-stone-600">Buscando en la base de conocimiento…</p>
        )}
        {state.kind === "error" && (
          <div
            role="alert"
            className="rounded-2xl border border-red-300 bg-red-50 p-4 text-sm text-red-800"
          >
            {state.message}
          </div>
        )}
        {state.kind === "answer" && (
          <section className="rounded-2xl border border-stone-300 bg-white/85 p-6 shadow-sm">
            <p className="text-xs font-semibold uppercase tracking-wider text-stone-500">
              {state.question}
            </p>
            <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-stone-900">
              {state.answer}
            </p>
          </section>
        )}
      </div>
    </main>
  );
}
