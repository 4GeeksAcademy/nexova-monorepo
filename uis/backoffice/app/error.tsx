"use client";

import { useEffect } from "react";
import ErrorNotice from "@/components/ErrorNotice";
import { trackFrontendError } from "@/lib/telemetry";

/** Error inesperado al pintar una pantalla: mensaje legible y salida (reintentar / inicio / soporte). */
export default function Error({ error, retry }: { error: unknown; retry: () => void }) {
  useEffect(() => {
    // Solo el tipo y el mensaje ya saneado por Next; nunca se muestra al usuario.
    const message = typeof error === "object" && error !== null && "message" in error ? String(error.message) : "render_error";
    trackFrontendError(message);
  }, [error]);

  return (
    <main className="mx-auto max-w-xl px-6 py-16">
      <ErrorNotice
        title="Algo ha ido mal al mostrar esta pantalla"
        message="Ha ocurrido un error inesperado. Puedes volver a intentarlo o regresar al inicio."
        onRetry={retry}
      />
    </main>
  );
}
