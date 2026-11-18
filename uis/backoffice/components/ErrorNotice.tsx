import Link from "next/link";

interface ErrorNoticeProps {
  /** Texto legible para el usuario (nunca un código HTTP ni un mensaje técnico). */
  message: string;
  title?: string;
  /** Si se indica, se muestra el botón «Reintentar». */
  onRetry?: () => void;
  /** Texto del botón de reintento. */
  retryLabel?: string;
  /** Oculta el enlace «Volver al inicio» (p. ej. dentro de un formulario). */
  hideHome?: boolean;
  className?: string;
}

/**
 * Estado de error de una pantalla: explica qué ha pasado y ofrece una salida clara
 * (reintentar, volver al inicio o contactar con soporte).
 */
export default function ErrorNotice({
  message,
  title = "No se ha podido completar la operación",
  onRetry,
  retryLabel = "Reintentar",
  hideHome = false,
  className = "",
}: ErrorNoticeProps) {
  return (
    <div role="alert" className={`rounded-lg border border-red-200 bg-red-50 px-4 py-4 text-sm text-red-800 ${className}`}>
      <p className="font-medium">{title}</p>
      <p className="mt-1">{message}</p>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="rounded-lg border border-red-300 bg-white px-4 py-1.5 font-medium text-red-700 transition hover:bg-red-100"
          >
            {retryLabel}
          </button>
        )}
        {!hideHome && (
          <Link href="/" className="text-red-700 underline-offset-2 hover:underline">
            Volver al inicio
          </Link>
        )}
      </div>
      <p className="mt-3 text-xs text-red-700/80">Si el problema continúa, contacta con el equipo de soporte de Nexova.</p>
    </div>
  );
}
