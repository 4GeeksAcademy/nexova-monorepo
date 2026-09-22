"use client";

import { useEffect } from "react";
import { flushOnHide, trackFrontendError } from "@/lib/telemetry";

/**
 * Monta los listeners globales de telemetría una sola vez: flush confiable
 * al ocultar/cerrar la pestaña, y captura de errores JS no manejados.
 */
export default function TelemetryBootstrap() {
  useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.visibilityState === "hidden") {
        flushOnHide();
      }
    };

    const handleError = (event: ErrorEvent) => {
      trackFrontendError(event.message, event.error?.stack);
    };

    const handleUnhandledRejection = (event: PromiseRejectionEvent) => {
      const reason = event.reason;
      const message = reason instanceof Error ? reason.message : String(reason);
      const stack = reason instanceof Error ? reason.stack : undefined;
      trackFrontendError(message, stack);
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    window.addEventListener("error", handleError);
    window.addEventListener("unhandledrejection", handleUnhandledRejection);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.removeEventListener("error", handleError);
      window.removeEventListener("unhandledrejection", handleUnhandledRejection);
    };
  }, []);

  return null;
}
