"use client";

import Link from "next/link";

/** Error inesperado al pintar una pantalla: mensaje legible y una salida clara. */
export default function Error({ retry }: { error: unknown; retry: () => void }) {
  return (
    <main role="alert" className="mx-auto max-w-xl px-6 py-20">
      <h1 className="text-2xl font-semibold text-slate-900">Algo ha ido mal al mostrar esta página</h1>
      <p className="mt-3 text-slate-600">
        Ha ocurrido un error inesperado. Puedes volver a intentarlo o regresar al inicio. Si el problema continúa,
        contacta con el equipo de soporte de Nexova.
      </p>
      <div className="mt-6 flex flex-wrap items-center gap-4">
        <button
          type="button"
          onClick={() => retry()}
          className="rounded-full border border-slate-300 bg-white px-5 py-2 text-sm font-medium text-slate-800 transition hover:bg-slate-50"
        >
          Reintentar
        </button>
        <Link href="/" className="text-sm font-medium text-slate-700 underline-offset-2 hover:underline">
          Volver al inicio
        </Link>
      </div>
    </main>
  );
}
