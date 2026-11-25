"use client";

import { useState, type FormEvent } from "react";
import { FIELD_INPUT, FIELD_LABEL, SUBMIT_BUTTON } from "@/components/account/form-styles";
import { CARD, EmptyState, Resource } from "@/components/dashboard/Resource";
import { useAuth } from "@/lib/AuthContext";
import { can, isInternal, seesAllDepartments } from "@/lib/permissions";
import {
  RbacError,
  createReport,
  deleteReport,
  listDepartments,
  listReports,
  updateReport,
  type Department,
  type InternalReport,
} from "@/lib/rbac";
import { useAsyncResource } from "@/lib/useAsyncResource";

const BUTTON_SECONDARY =
  "rounded-lg border border-stone-300 bg-white px-3 py-1.5 text-sm text-stone-700 transition hover:border-teal-600 hover:text-teal-700 disabled:cursor-not-allowed disabled:opacity-50";
const BUTTON_DANGER =
  "rounded-lg border border-red-300 bg-white px-3 py-1.5 text-sm text-red-700 transition hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50";
const ERROR_LINE = "rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700";

const loadReports = listReports;
const loadDepartments = listDepartments;
const dateFormat = new Intl.DateTimeFormat("es-ES", { dateStyle: "medium" });

function describe(err: unknown): string {
  return err instanceof RbacError ? err.message : "No se pudo completar la operación. Inténtalo de nuevo.";
}

function formatDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "—" : dateFormat.format(date);
}

export default function InternalReportsPage() {
  const { user } = useAuth();

  if (!isInternal(user)) {
    return (
      <section className="px-6 py-12">
        <div className="mx-auto w-full max-w-3xl">
          <p role="alert" className={ERROR_LINE}>Los informes internos son solo para el personal de Nexova.</p>
        </div>
      </section>
    );
  }
  return <Reports />;
}

function Reports() {
  const { user } = useAuth();
  const reports = useAsyncResource(loadReports);
  const departments = useAsyncResource(loadDepartments);
  const showAll = seesAllDepartments(user);

  return (
    <section className="px-6 py-12">
      <div className="mx-auto w-full max-w-4xl space-y-8">
        <header>
          <p className="text-xs font-semibold uppercase tracking-widest text-teal-700">Informes internos</p>
          <h1 className="mt-2 text-3xl font-medium tracking-tight text-stone-900">
            {showAll ? "Informes de todos los departamentos" : `Informes de ${user?.department_name ?? "tu departamento"}`}
          </h1>
          <p className="mt-3 text-base leading-7 text-stone-600">
            {showAll
              ? "Como administrador ves todos los departamentos."
              : "Solo ves los informes de tu departamento; el resto te devuelve acceso denegado."}
          </p>
        </header>

        {can(user, "create") && <NewReport showDepartment={showAll} onCreated={reports.reload} />}

        <Resource state={reports.state} onRetry={reports.reload}>
          {(items) =>
            items.length === 0 ? (
              <EmptyState>Todavía no hay informes en este departamento.</EmptyState>
            ) : (
              <ul className="space-y-4">
                {items.map((report) => (
                  <ReportCard
                    key={report.id}
                    report={report}
                    departmentName={
                      departments.state.status === "ready"
                        ? nameOf(departments.state.data, report.department_id)
                        : report.department_id === user?.department_id
                          ? (user.department_name ?? report.department_id)
                          : report.department_id
                    }
                    onChanged={reports.reload}
                  />
                ))}
              </ul>
            )
          }
        </Resource>
      </div>
    </section>
  );
}

function nameOf(departments: Department[], id: string): string {
  return departments.find((d) => d.id === id)?.name ?? id;
}

function NewReport({ showDepartment, onCreated }: { showDepartment: boolean; onCreated: () => void }) {
  const departments = useAsyncResource(loadDepartments);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [departmentId, setDepartmentId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await createReport({ title: title.trim(), body: body.trim(), ...(showDepartment && departmentId ? { department_id: departmentId } : {}) });
      setTitle("");
      setBody("");
      onCreated();
    } catch (err) {
      setError(describe(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={onSubmit} className={`${CARD} space-y-4`} aria-label="Nuevo informe">
      <h2 className="text-lg font-medium text-stone-900">Nuevo informe</h2>
      <label className={FIELD_LABEL}>Título
        <input required maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} className={FIELD_INPUT} />
      </label>
      <label className={FIELD_LABEL}>Contenido
        <textarea required rows={4} maxLength={10000} value={body} onChange={(e) => setBody(e.target.value)} className={FIELD_INPUT} />
      </label>
      {showDepartment && departments.state.status === "ready" && (
        <label className={FIELD_LABEL}>Departamento
          <select required value={departmentId} onChange={(e) => setDepartmentId(e.target.value)} className={FIELD_INPUT}>
            <option value="">Elige un departamento</option>
            {departments.state.data.map((d) => (
              <option key={d.id} value={d.id}>{d.name}</option>
            ))}
          </select>
        </label>
      )}
      <button type="submit" disabled={busy} className={SUBMIT_BUTTON}>{busy ? "Guardando…" : "Crear informe"}</button>
      {error && <p role="alert" className={ERROR_LINE}>{error}</p>}
    </form>
  );
}

function ReportCard({ report, departmentName, onChanged }: { report: InternalReport; departmentName: string; onChanged: () => void }) {
  const { user } = useAuth();
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(report.title);
  const [body, setBody] = useState(report.body);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      setEditing(false);
      onChanged();
    } catch (err) {
      setError(describe(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className={CARD}>
      <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-teal-700">{departmentName} · {formatDate(report.created_at)}</p>
      {editing ? (
        <div className="mt-2 space-y-3">
          <input aria-label="Título" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} className={FIELD_INPUT} />
          <textarea aria-label="Contenido" rows={4} value={body} maxLength={10000} onChange={(e) => setBody(e.target.value)} className={FIELD_INPUT} />
          <div className="flex gap-2">
            <button type="button" disabled={busy || !title.trim() || !body.trim()} className={BUTTON_SECONDARY}
              onClick={() => run(() => updateReport(report.id, { title: title.trim(), body: body.trim() }))}>Guardar</button>
            <button type="button" className={BUTTON_SECONDARY} onClick={() => setEditing(false)}>Cancelar</button>
          </div>
        </div>
      ) : (
        <>
          <h3 className="mt-1 text-base font-medium text-stone-900">{report.title}</h3>
          <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-stone-700">{report.body}</p>
        </>
      )}
      {!editing && (can(user, "update") || can(user, "delete")) && (
        <div className="mt-4 flex gap-2">
          {can(user, "update") && <button type="button" className={BUTTON_SECONDARY} onClick={() => setEditing(true)}>Editar</button>}
          {can(user, "delete") && (
            <button type="button" disabled={busy} className={BUTTON_DANGER}
              onClick={() => { if (window.confirm("¿Eliminar este informe? No se puede deshacer.")) void run(() => deleteReport(report.id)); }}>
              Eliminar
            </button>
          )}
        </div>
      )}
      {error && <p role="alert" className={`${ERROR_LINE} mt-3`}>{error}</p>}
    </li>
  );
}
