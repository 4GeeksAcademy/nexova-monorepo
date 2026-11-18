"use client";

/**
 * Último recurso: error en el layout raíz. Renderiza su propio documento y no recibe los
 * estilos globales, por eso los estilos van en línea.
 */
export default function GlobalError({ retry }: { error: unknown; retry: () => void }) {
  return (
    <html lang="es">
      <body style={{ fontFamily: "system-ui, sans-serif", margin: 0, background: "#f8fafc", color: "#0f172a" }}>
        <main role="alert" style={{ maxWidth: 520, margin: "0 auto", padding: "64px 24px" }}>
          <h1 style={{ fontSize: 22, margin: 0 }}>No se ha podido cargar la aplicación</h1>
          <p style={{ marginTop: 12, lineHeight: 1.5 }}>
            Ha ocurrido un error inesperado. Puedes volver a intentarlo o regresar al inicio; si el problema continúa,
            contacta con el equipo de soporte de Nexova.
          </p>
          <div style={{ marginTop: 20, display: "flex", gap: 16, alignItems: "center" }}>
            <button
              type="button"
              onClick={() => retry()}
              style={{ padding: "8px 18px", borderRadius: 999, border: "1px solid #94a3b8", background: "#fff", cursor: "pointer" }}
            >
              Reintentar
            </button>
            {/* `<a>` y no `Link` a propósito: el router puede ser lo que ha fallado, así que se recarga el documento. */}
            {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
            <a href="/" style={{ color: "#334155" }}>
              Volver al inicio
            </a>
          </div>
        </main>
      </body>
    </html>
  );
}
