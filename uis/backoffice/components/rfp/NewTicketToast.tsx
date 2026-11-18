import Link from "next/link";

export interface NewTicketNotice {
  ticket_id: string;
  client_name: string | null;
  client_hq: string | null;
  services_requested: string[];
}

interface NewTicketToastProps {
  notices: NewTicketNotice[];
  onDismiss: (ticketId: string) => void;
}

/** Aviso de RFP nueva (SSE). Teal/sky para distinguirse de los banners ámbar (en curso) y rojos (error). */
export default function NewTicketToast({ notices, onDismiss }: NewTicketToastProps) {
  if (notices.length === 0) return null;
  return (
    <div role="status" aria-live="polite" className="fixed bottom-6 right-6 z-50 flex w-80 flex-col gap-3">
      {notices.map((notice) => (
        <div
          key={notice.ticket_id}
          className="rounded-xl border border-teal-300 bg-white p-4 shadow-lg ring-1 ring-teal-100"
        >
          <div className="flex items-start justify-between gap-3">
            <p className="flex items-center gap-2 font-mono text-xs uppercase tracking-wider text-teal-700">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-teal-600" aria-hidden />
              Nueva RFP
            </p>
            <button
              type="button"
              onClick={() => onDismiss(notice.ticket_id)}
              aria-label={`Cerrar aviso de ${notice.ticket_id}`}
              className="text-stone-400 hover:text-stone-700"
            >
              ×
            </button>
          </div>
          <p className="mt-2 text-sm font-medium text-stone-900">
            {notice.client_name ?? "Cliente sin identificar"}
            {notice.client_hq ? <span className="font-normal text-stone-500"> · {notice.client_hq}</span> : null}
          </p>
          {notice.services_requested.length > 0 && (
            <p className="mt-1 line-clamp-2 text-xs text-stone-600">{notice.services_requested.join(", ")}</p>
          )}
          <Link
            href={`/rfp/${notice.ticket_id}`}
            prefetch={false}
            className="mt-3 inline-block font-mono text-xs text-teal-700 underline-offset-2 hover:underline"
          >
            {notice.ticket_id} →
          </Link>
        </div>
      ))}
    </div>
  );
}
