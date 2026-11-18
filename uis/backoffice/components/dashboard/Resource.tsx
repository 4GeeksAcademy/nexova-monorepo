"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import ErrorNotice from "@/components/ErrorNotice";
import type { AsyncState } from "@/lib/useAsyncResource";

export const CARD = "rounded-2xl border border-stone-300 bg-white/85 p-5 shadow-sm";

const FORBIDDEN_MESSAGE = "Disponible solo para el personal interno de Nexova.";

interface ResourceProps<T> {
  state: AsyncState<T>;
  onRetry: () => void;
  /** Error en línea, sin el aviso grande (para tarjetas de indicador). */
  compact?: boolean;
  children: (data: T) => ReactNode;
}

/** Estado de carga, error o contenido de un recurso; cada bloque del Overview falla por separado. */
export function Resource<T>({ state, onRetry, compact = false, children }: ResourceProps<T>) {
  if (state.status === "loading") {
    return (
      <p className="text-sm text-stone-500" role="status">
        Cargando…
      </p>
    );
  }

  if (state.status === "error") {
    if (state.forbidden) {
      return <p className="text-sm text-stone-500">{FORBIDDEN_MESSAGE}</p>;
    }
    if (compact) {
      return (
        <div role="alert" className="text-sm text-red-800">
          <p>{state.message}</p>
          <button
            type="button"
            onClick={onRetry}
            className="mt-2 rounded-lg border border-red-300 bg-white px-3 py-1 font-medium text-red-700 transition hover:bg-red-100"
          >
            Reintentar
          </button>
        </div>
      );
    }
    return <ErrorNotice message={state.message} title="No se han podido cargar estos datos" onRetry={onRetry} hideHome />;
  }

  return <>{children(state.data)}</>;
}

interface KpiCardProps<T> {
  label: string;
  state: AsyncState<T>;
  onRetry: () => void;
  /** Valor principal y línea de contexto, a partir de los datos cargados. */
  render: (data: T) => { value: string; detail: string };
}

export function KpiCard<T>({ label, state, onRetry, render }: KpiCardProps<T>) {
  return (
    <article className={CARD}>
      <p className="text-xs font-semibold uppercase tracking-wide text-stone-500">{label}</p>
      <div className="mt-3">
        <Resource state={state} onRetry={onRetry} compact>
          {(data) => {
            const { value, detail } = render(data);
            return (
              <>
                <p className="text-3xl font-medium tracking-tight text-stone-900">{value}</p>
                <p className="mt-1 text-sm text-stone-600">{detail}</p>
              </>
            );
          }}
        </Resource>
      </div>
    </article>
  );
}

interface PanelProps {
  eyebrow: string;
  title: string;
  href: string;
  hrefLabel: string;
  children: ReactNode;
}

export function Panel({ eyebrow, title, href, hrefLabel, children }: PanelProps) {
  return (
    <section className={`${CARD} flex flex-col`}>
      <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-teal-700">{eyebrow}</p>
      <h2 className="mt-1 text-base font-medium text-stone-900">{title}</h2>
      <div className="mt-4 flex-1">{children}</div>
      <Link
        href={href}
        prefetch={false}
        className="mt-4 inline-block text-sm font-medium text-teal-700 underline-offset-2 hover:underline"
      >
        {hrefLabel} →
      </Link>
    </section>
  );
}

export const ROW = "rounded-xl border border-stone-200 bg-stone-50 px-3 py-2";

export function EmptyState({ children }: { children: ReactNode }) {
  return <p className="rounded-xl border border-dashed border-stone-300 px-3 py-4 text-sm text-stone-600">{children}</p>;
}
