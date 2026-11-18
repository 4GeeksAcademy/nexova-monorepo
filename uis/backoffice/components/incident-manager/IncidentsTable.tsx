import {
  BRANCH_LABELS,
  CATEGORY_LABELS,
  ORIGIN_LABELS,
  STATUS_LABELS,
  type Status,
  isStatus,
  selectableStatuses,
} from "@/lib/incident-manager-domain";
import type { Incident } from "@/lib/incident-manager";

const dateFormat = new Intl.DateTimeFormat("es-ES", { dateStyle: "medium" });

const STATUS_TONE: Record<Status, string> = {
  open: "border-sky-300 bg-sky-50 text-sky-800",
  in_progress: "border-amber-300 bg-amber-50 text-amber-800",
  resolved: "border-emerald-300 bg-emerald-50 text-emerald-800",
  discarded: "border-stone-300 bg-stone-100 text-stone-600",
};

function formatDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "—" : dateFormat.format(date);
}

interface Props {
  incidents: Incident[];
  pendingIds: ReadonlySet<number>;
  onChangeStatus: (incident: Incident, next: Status) => void;
}

export default function IncidentsTable({ incidents, pendingIds, onChangeStatus }: Props) {
  return (
    <div className="overflow-x-auto rounded-2xl border border-stone-200 bg-white shadow-sm">
      <table className="min-w-full divide-y divide-stone-200 text-left text-sm">
        <thead className="bg-stone-50 text-xs uppercase tracking-wide text-stone-500">
          <tr>
            <th scope="col" className="px-4 py-3">Incidencia</th>
            <th scope="col" className="px-4 py-3">Categoría</th>
            <th scope="col" className="px-4 py-3">Origen</th>
            <th scope="col" className="px-4 py-3">Sede</th>
            <th scope="col" className="px-4 py-3">Creada</th>
            <th scope="col" className="px-4 py-3">Estado</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-stone-100">
          {incidents.map((incident) => {
            const pending = pendingIds.has(incident.id);
            const options = selectableStatuses(incident.status);
            return (
              <tr key={incident.id} className="align-top">
                <td className="max-w-md px-4 py-3">
                  <p className="font-medium text-stone-900">{incident.title}</p>
                  <p className="mt-0.5 line-clamp-2 text-stone-500">{incident.description}</p>
                </td>
                <td className="px-4 py-3 text-stone-700">{CATEGORY_LABELS[incident.category]}</td>
                <td className="px-4 py-3 text-stone-700">{ORIGIN_LABELS[incident.origin]}</td>
                <td className="px-4 py-3 text-stone-700">{BRANCH_LABELS[incident.branch]}</td>
                <td className="whitespace-nowrap px-4 py-3 text-stone-600">{formatDate(incident.created_at)}</td>
                <td className="px-4 py-3">
                  <select
                    aria-label={`Estado de la incidencia ${incident.id}`}
                    value={incident.status}
                    disabled={pending || options.length === 1}
                    onChange={(e) => {
                      if (isStatus(e.target.value)) onChangeStatus(incident, e.target.value);
                    }}
                    className={`rounded-lg border px-3 py-1 text-xs font-medium focus:outline-none disabled:cursor-not-allowed ${STATUS_TONE[incident.status]} ${pending ? "opacity-60" : ""}`}
                  >
                    {options.map((status) => (
                      <option key={status} value={status}>
                        {STATUS_LABELS[status]}
                      </option>
                    ))}
                  </select>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
