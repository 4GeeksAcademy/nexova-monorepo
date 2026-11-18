/**
 * Dominio del gestor de incidencias de Nexova (BRIEFING): valores permitidos,
 * etiquetas, ciclo de vida y validación del formulario. Todo es puro (sin red
 * ni React) para poder probarlo con vitest.
 *
 * Debe coincidir exactamente con `packages/shared/nexova_shared/incident_domain.py`.
 */

export const BRANCHES = ["central", "valencia_operations", "miami_office", "remote"] as const;
export const CATEGORIES = [
  "technical_failure",
  "process_error",
  "client_complaint",
  "candidate_issue",
  "staff_issue",
  "sla_breach",
  "data_quality",
  "other",
] as const;
export const STATUSES = ["open", "in_progress", "resolved", "discarded"] as const;
export const ORIGINS = ["customer", "branch", "internal"] as const;

export type Branch = (typeof BRANCHES)[number];
export type Category = (typeof CATEGORIES)[number];
export type Status = (typeof STATUSES)[number];
export type Origin = (typeof ORIGINS)[number];

export const TITLE_MAX_LENGTH = 120;
export const DESCRIPTION_MAX_LENGTH = 5000;

// --- Etiquetas (BRIEFING · "Oficinas de Nexova") ---------------------------------

export const BRANCH_LABELS: Record<Branch, string> = {
  central: "Central — Sede Valencia",
  valencia_operations: "Valencia — Operaciones",
  miami_office: "Miami Office",
  remote: "Remoto (empleado sin sede fija)",
};

export const CATEGORY_LABELS: Record<Category, string> = {
  technical_failure: "Fallo técnico",
  process_error: "Error de proceso",
  client_complaint: "Queja de cliente",
  candidate_issue: "Incidencia de candidato",
  staff_issue: "Incidencia de personal",
  sla_breach: "Incumplimiento de SLA",
  data_quality: "Calidad de datos",
  other: "Otra",
};

export const STATUS_LABELS: Record<Status, string> = {
  open: "Abierta",
  in_progress: "En curso",
  resolved: "Resuelta",
  discarded: "Descartada",
};

export const ORIGIN_LABELS: Record<Origin, string> = {
  customer: "Cliente",
  branch: "Sede",
  internal: "Interno",
};

// --- Ciclo de vida ---------------------------------------------------------------

/** open → in_progress | discarded; in_progress → resolved | discarded; el resto son finales. */
export const TRANSITIONS: Record<Status, readonly Status[]> = {
  open: ["in_progress", "discarded"],
  in_progress: ["resolved", "discarded"],
  resolved: [],
  discarded: [],
};

export function isFinalStatus(status: Status): boolean {
  return TRANSITIONS[status].length === 0;
}

/** Estados a los que se puede pasar desde `current`, más el propio `current` (valor actual del selector). */
export function selectableStatuses(current: Status): Status[] {
  return [current, ...TRANSITIONS[current]];
}

// --- Guardas de tipo -------------------------------------------------------------

function includes<T extends string>(list: readonly T[], value: unknown): value is T {
  return typeof value === "string" && (list as readonly string[]).includes(value);
}

export const isBranch = (v: unknown): v is Branch => includes(BRANCHES, v);
export const isCategory = (v: unknown): v is Category => includes(CATEGORIES, v);
export const isStatus = (v: unknown): v is Status => includes(STATUSES, v);
export const isOrigin = (v: unknown): v is Origin => includes(ORIGINS, v);

// --- Formulario ------------------------------------------------------------------

export type IncidentField = "title" | "description" | "category" | "origin" | "branch";

export interface IncidentFormValues {
  title: string;
  description: string;
  category: Category | "";
  origin: Origin | "";
  branch: Branch | "";
}

export type FieldErrors = Partial<Record<IncidentField, string>>;

export const EMPTY_FORM: IncidentFormValues = {
  title: "",
  description: "",
  category: "",
  origin: "",
  branch: "",
};

export interface CreateIncidentPayload {
  title: string;
  description: string;
  category: Category;
  origin: Origin;
  branch: Branch;
}

/** Validación previa al envío (la API vuelve a validar: esto solo ahorra la ida y vuelta). */
export function validateIncidentForm(values: IncidentFormValues): FieldErrors {
  const errors: FieldErrors = {};

  const title = values.title.trim();
  if (!title) errors.title = "Escribe un título breve.";
  else if (title.length > TITLE_MAX_LENGTH) errors.title = `Máximo ${TITLE_MAX_LENGTH} caracteres.`;

  const description = values.description.trim();
  if (!description) errors.description = "Describe la incidencia con detalle.";
  else if (description.length > DESCRIPTION_MAX_LENGTH) {
    errors.description = `Máximo ${DESCRIPTION_MAX_LENGTH} caracteres.`;
  }

  if (!isCategory(values.category)) errors.category = "Elige una categoría.";
  if (!isOrigin(values.origin)) errors.origin = "Indica el origen del reporte.";
  if (!isBranch(values.branch)) errors.branch = "Elige la sede que reporta o gestiona la incidencia.";

  return errors;
}

/** Devuelve el payload de la API o `null` si el formulario no es válido. */
export function toCreatePayload(values: IncidentFormValues): CreateIncidentPayload | null {
  if (Object.keys(validateIncidentForm(values)).length > 0) return null;
  if (!isCategory(values.category) || !isOrigin(values.origin) || !isBranch(values.branch)) {
    return null;
  }
  return {
    title: values.title.trim(),
    description: values.description.trim(),
    category: values.category,
    origin: values.origin,
    branch: values.branch,
  };
}

/** Cuando el origen es "sede", el campo `branch` se resalta para recordar que se reporta desde una sede. */
export function shouldHighlightBranch(origin: IncidentFormValues["origin"]): boolean {
  return origin === "branch";
}
