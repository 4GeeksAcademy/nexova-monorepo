"use client";

import { useCallback, useEffect, useState } from "react";
import { GENERIC_ERROR_MESSAGE } from "./http-errors";

export type AsyncState<T> =
  | { status: "loading" }
  | { status: "ready"; data: T }
  /** `forbidden`: el usuario no tiene permiso (403); se muestra como aviso, no como fallo. */
  | { status: "error"; message: string; forbidden: boolean };

function describeError(err: unknown): { message: string; forbidden: boolean } {
  const status = typeof err === "object" && err !== null ? (err as { status?: unknown }).status : undefined;
  // Los clientes de `lib/*` ya lanzan errores con texto apto para personas (sin códigos HTTP).
  const message = err instanceof Error && err.message.trim() !== "" ? err.message : GENERIC_ERROR_MESSAGE;
  return { message, forbidden: status === 403 };
}

/**
 * Carga un recurso al montar y permite reintentarlo. `load` debe ser estable
 * (función de módulo), no una función creada en cada render.
 */
export function useAsyncResource<T>(load: () => Promise<T>): { state: AsyncState<T>; reload: () => void } {
  const [state, setState] = useState<AsyncState<T>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    load().then(
      (data) => {
        if (!cancelled) setState({ status: "ready", data });
      },
      (err: unknown) => {
        if (!cancelled) setState({ status: "error", ...describeError(err) });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [load, attempt]);

  const reload = useCallback(() => {
    setState({ status: "loading" });
    setAttempt((n) => n + 1);
  }, []);

  return { state, reload };
}
