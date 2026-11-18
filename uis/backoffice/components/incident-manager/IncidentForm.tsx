"use client";

import { type FormEvent, useState } from "react";
import {
  BRANCHES,
  BRANCH_LABELS,
  CATEGORIES,
  CATEGORY_LABELS,
  DESCRIPTION_MAX_LENGTH,
  EMPTY_FORM,
  ORIGINS,
  ORIGIN_LABELS,
  TITLE_MAX_LENGTH,
  type FieldErrors,
  type IncidentField,
  type IncidentFormValues,
  isBranch,
  isCategory,
  isOrigin,
  shouldHighlightBranch,
  toCreatePayload,
  validateIncidentForm,
} from "@/lib/incident-manager-domain";
import { CONNECTION_ERROR, IncidentManagerError, createIncident } from "@/lib/incident-manager";

const INPUT =
  "mt-1 w-full rounded-md border px-3 py-2 text-sm text-stone-900 focus:outline-none disabled:opacity-60";
const OK_BORDER = "border-stone-300 focus:border-teal-600";
const ERROR_BORDER = "border-red-400 focus:border-red-500";

function FieldError({ id, message }: { id: string; message?: string }) {
  if (!message) return null;
  return (
    <p id={id} role="alert" className="mt-1 text-xs text-red-700">
      {message}
    </p>
  );
}

export default function IncidentForm() {
  const [values, setValues] = useState<IncidentFormValues>(EMPTY_FORM);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [createdId, setCreatedId] = useState<number | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  function change(field: IncidentField, value: string) {
    setValues((prev) => {
      if (field === "category") return { ...prev, category: isCategory(value) ? value : "" };
      if (field === "origin") return { ...prev, origin: isOrigin(value) ? value : "" };
      if (field === "branch") return { ...prev, branch: isBranch(value) ? value : "" };
      return { ...prev, [field]: value };
    });
    // El error de un campo se retira en cuanto el usuario lo corrige.
    setErrors((prev) => ({ ...prev, [field]: undefined }));
    setCreatedId(null);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmitting) return;
    setFormError(null);
    setCreatedId(null);

    const clientErrors = validateIncidentForm(values);
    const payload = toCreatePayload(values);
    if (!payload) {
      setErrors(clientErrors);
      setFormError("Revisa los campos marcados antes de enviar.");
      return;
    }

    setErrors({});
    setIsSubmitting(true);
    try {
      const created = await createIncident(payload);
      setValues(EMPTY_FORM);
      setCreatedId(created.id);
    } catch (err) {
      if (err instanceof IncidentManagerError) {
        setErrors(err.fieldErrors);
        setFormError(err.message);
      } else {
        setFormError(CONNECTION_ERROR);
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  const highlightBranch = shouldHighlightBranch(values.origin);
  const border = (field: IncidentField) => (errors[field] ? ERROR_BORDER : OK_BORDER);

  return (
    <form
      onSubmit={handleSubmit}
      noValidate
      aria-busy={isSubmitting}
      className="max-w-2xl space-y-5 rounded-2xl border border-stone-200 bg-white p-6 shadow-sm"
    >
      {createdId !== null && (
        <div role="status" className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
          Incidencia registrada correctamente (n.º {createdId}). Ya aparece en el listado como «Abierta».
        </div>
      )}
      {formError && (
        <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {formError}
        </div>
      )}

      <div>
        <label htmlFor="im-title" className="block text-sm font-medium text-stone-700">
          Título
        </label>
        <input
          id="im-title"
          type="text"
          value={values.title}
          maxLength={TITLE_MAX_LENGTH + 20}
          disabled={isSubmitting}
          aria-invalid={Boolean(errors.title)}
          aria-describedby={errors.title ? "im-title-error" : undefined}
          onChange={(e) => change("title", e.target.value)}
          className={`${INPUT} ${border("title")}`}
          placeholder="Resumen breve de la incidencia"
        />
        <FieldError id="im-title-error" message={errors.title} />
      </div>

      <div>
        <label htmlFor="im-description" className="block text-sm font-medium text-stone-700">
          Descripción
        </label>
        <textarea
          id="im-description"
          rows={5}
          value={values.description}
          maxLength={DESCRIPTION_MAX_LENGTH + 100}
          disabled={isSubmitting}
          aria-invalid={Boolean(errors.description)}
          aria-describedby={errors.description ? "im-description-error" : undefined}
          onChange={(e) => change("description", e.target.value)}
          className={`${INPUT} ${border("description")}`}
          placeholder="Qué ha ocurrido, cuándo y a quién afecta"
        />
        <FieldError id="im-description-error" message={errors.description} />
      </div>

      <div className="grid gap-5 sm:grid-cols-2">
        <div>
          <label htmlFor="im-category" className="block text-sm font-medium text-stone-700">
            Categoría
          </label>
          <select
            id="im-category"
            value={values.category}
            disabled={isSubmitting}
            aria-invalid={Boolean(errors.category)}
            aria-describedby={errors.category ? "im-category-error" : undefined}
            onChange={(e) => change("category", e.target.value)}
            className={`${INPUT} ${border("category")}`}
          >
            <option value="">Selecciona una categoría</option>
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {CATEGORY_LABELS[c]}
              </option>
            ))}
          </select>
          <FieldError id="im-category-error" message={errors.category} />
        </div>

        <div>
          <label htmlFor="im-origin" className="block text-sm font-medium text-stone-700">
            Origen del reporte
          </label>
          <select
            id="im-origin"
            value={values.origin}
            disabled={isSubmitting}
            aria-invalid={Boolean(errors.origin)}
            aria-describedby={errors.origin ? "im-origin-error" : undefined}
            onChange={(e) => change("origin", e.target.value)}
            className={`${INPUT} ${border("origin")}`}
          >
            <option value="">Selecciona un origen</option>
            {ORIGINS.map((o) => (
              <option key={o} value={o}>
                {ORIGIN_LABELS[o]}
              </option>
            ))}
          </select>
          <FieldError id="im-origin-error" message={errors.origin} />
        </div>
      </div>

      <div
        data-highlighted={highlightBranch}
        className={
          highlightBranch
            ? "rounded-xl border-2 border-amber-400 bg-amber-50 p-4"
            : "rounded-xl border-2 border-transparent p-4 -m-4"
        }
      >
        <label htmlFor="im-branch" className="block text-sm font-medium text-stone-700">
          Sede {highlightBranch && <span className="text-amber-700">· estás reportando desde una sede</span>}
        </label>
        <select
          id="im-branch"
          value={values.branch}
          disabled={isSubmitting}
          aria-invalid={Boolean(errors.branch)}
          aria-describedby={errors.branch ? "im-branch-error im-branch-hint" : "im-branch-hint"}
          onChange={(e) => change("branch", e.target.value)}
          className={`${INPUT} ${border("branch")}`}
        >
          <option value="">Selecciona una sede</option>
          {BRANCHES.map((b) => (
            <option key={b} value={b}>
              {BRANCH_LABELS[b]}
            </option>
          ))}
        </select>
        <FieldError id="im-branch-error" message={errors.branch} />
        <p id="im-branch-hint" className="mt-1 text-xs text-stone-500">
          Usa «Central — Sede Valencia» cuando la incidencia no corresponda a una oficina concreta.
        </p>
      </div>

      <button
        type="submit"
        disabled={isSubmitting}
        className="inline-flex items-center gap-2 rounded-full bg-teal-700 px-5 py-2 text-sm font-medium text-white transition hover:bg-teal-800 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {isSubmitting && (
          <span
            aria-hidden="true"
            className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white"
          />
        )}
        {isSubmitting ? "Enviando…" : "Registrar incidencia"}
      </button>
    </form>
  );
}
