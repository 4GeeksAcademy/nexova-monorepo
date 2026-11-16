import { getToken } from "./auth-storage";
import { handleSessionExpired } from "./session";

/**
 * Cliente de la recepción de RFPs (Hito 9 · Parte 1), vía los proxies de
 * `app/api/rfp`. Toda respuesta se valida antes de usarse: es dato externo.
 */

export type TicketStatus =
  | "analizando"
  | "descartado"
  | "analisis_completo"
  | "generando_borrador"
  | "en_evaluación"
  | "needs_human_review"
  | "esperando_aprobación"
  | "terminado";

export type DepartmentId = "seleccion" | "capacitacion" | "soporte";

export interface RfpTicketSummary {
  ticket_id: string;
  rfp_id: string | null;
  status: TicketStatus;
  original_filename: string;
  client_name: string | null;
  client_hq: string | null;
  currency: string | null;
  departments_needed: DepartmentId[];
  error_message: string | null;
  attempts: number;
  /** Parte 2 terminada: borradores evaluados y handoff listo para la Parte 3. */
  ready_for_approval: boolean;
  created_at: string;
  updated_at: string;
}

export interface Figure {
  concept: string;
  value: string;
  quote: string;
}

export interface Readability {
  word_count: number | null;
  estimated_tokens: number | null;
  flesch_kincaid_grade: number | null;
  gunning_fog: number | null;
  cost_tier: string | null;
  note: string | null;
}

export interface RfpMetadata {
  client_name: string | null;
  client_hq: string;
  client_location: string | null;
  currency: string | null;
  rfp_type: string | null;
  services_requested: string[];
  scope: string | null;
  volumes: Figure[];
  deadline: string | null;
  deadline_text: string | null;
  budget_range: string | null;
  client_contact: string | null;
  departments_needed: DepartmentId[];
  unmapped_services: string[];
  readability: Readability | null;
}

export interface DepartmentSection {
  department_id: DepartmentId;
  owner_name: string;
  key_aspects: string[];
  requests_to_owner: string[];
  open_questions: string[];
  figures_cited: Figure[];
  excerpts: string[];
  section_status: SectionStatus | null;
  draft_content: string | null;
  evaluation: EvaluationResult | null;
  iteration_count: number;
}

export type SectionStatus = "generando" | "evaluando" | "evaluada_ok" | "needs_human_review";

export interface Violation {
  rule_id: string;
  message: string;
  evidence: string | null;
}

/** `EvaluationResult` de la Parte 2 (legibilidad / pertinencia / cumplimiento). */
export interface EvaluationResult {
  iteration: number;
  readability: { pass: boolean; score: number | null; problems: string[] };
  relevance: { pass: boolean; missing_aspects: string[] };
  compliance: { pass: boolean; rule_ids: string[]; violations: Violation[] };
  overall_pass: boolean;
  feedback_for_generator: string;
}

export interface Classification {
  rfp_type: string | null;
  confidence: number | null;
  reason: string;
  decision: string | null;
  low_confidence: boolean;
}

export interface Conflict {
  concept: string;
  values: { source: string; value: string; quote: string }[];
}

export interface Synthesis {
  summary_for_sales: string;
  next_steps: string[];
  conflicts: Conflict[];
}

export interface RfpTicketDetail extends RfpTicketSummary {
  /** `max_iterations` del handoff de la Parte 2 (null hasta que termina). */
  approval_max_iterations: number | null;
  classification: Classification | null;
  metadata: RfpMetadata | null;
  sections: DepartmentSection[];
  synthesis: Synthesis | null;
  ready_for_drafting: boolean;
}

export interface RfpTicketAccepted {
  ticket_id: string;
  status: TicketStatus;
  task_id: string;
}

export class RfpError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** Catálogo del BRIEFING §2.1: a quién pedirle qué. */
export const DEPARTMENTS: Record<DepartmentId, { name: string; owner: string }> = {
  seleccion: { name: "Talent Selection Operations", owner: "Javier Almeida" },
  capacitacion: { name: "Corporate Training", owner: "Elena Vargas" },
  soporte: { name: "Customer Support (outsourcing)", owner: "Roberto Díaz" },
};

const TICKETS_PATH = "/api/rfp/tickets";
const REQUEST_TIMEOUT_MS = 15_000;
const UPLOAD_TIMEOUT_MS = 60_000;

// --- Validación ---------------------------------------------------------------

type JsonRecord = Record<string, unknown>;

const STATUSES: readonly TicketStatus[] = [
  "analizando",
  "descartado",
  "analisis_completo",
  "generando_borrador",
  "en_evaluación",
  "needs_human_review",
  "esperando_aprobación",
  "terminado",
];
const DEPARTMENT_IDS: readonly DepartmentId[] = ["seleccion", "capacitacion", "soporte"];
const SECTION_STATUSES: readonly SectionStatus[] = ["generando", "evaluando", "evaluada_ok", "needs_human_review"];

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(row: JsonRecord, key: string): string {
  const value = row[key];
  if (typeof value !== "string") throw new Error(`Campo "${key}" no es texto.`);
  return value;
}

function optStr(row: JsonRecord, key: string): string | null {
  const value = row[key];
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") throw new Error(`Campo "${key}" no es texto.`);
  return value;
}

function optNum(row: JsonRecord, key: string): number | null {
  const value = row[key];
  if (value === null || value === undefined) return null;
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`Campo "${key}" no es un número válido.`);
  }
  return value;
}

function strList(row: JsonRecord, key: string): string[] {
  const value = row[key];
  if (value === null || value === undefined) return [];
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new Error(`Campo "${key}" no es una lista de textos.`);
  }
  return value as string[];
}

function oneOf<T extends string>(value: unknown, key: string, allowed: readonly T[]): T {
  if (typeof value !== "string" || !(allowed as readonly string[]).includes(value)) {
    throw new Error(`Valor inesperado en "${key}".`);
  }
  return value as T;
}

function departments(row: JsonRecord, key: string): DepartmentId[] {
  return strList(row, key).map((value) => oneOf(value, key, DEPARTMENT_IDS));
}

function figures(row: JsonRecord, key: string): Figure[] {
  const value = row[key];
  if (!Array.isArray(value)) return [];
  return value.filter(isRecord).map((item) => ({
    concept: str(item, "concept"),
    value: str(item, "value"),
    quote: str(item, "quote"),
  }));
}

export function parseTicketSummary(raw: unknown): RfpTicketSummary {
  if (!isRecord(raw)) throw new Error("Ticket con estructura inesperada.");
  const attempts = optNum(raw, "attempts");
  return {
    ticket_id: str(raw, "ticket_id"),
    rfp_id: optStr(raw, "rfp_id"),
    status: oneOf(raw.status, "status", STATUSES),
    original_filename: str(raw, "original_filename"),
    client_name: optStr(raw, "client_name"),
    client_hq: optStr(raw, "client_hq"),
    currency: optStr(raw, "currency"),
    departments_needed: departments(raw, "departments_needed"),
    error_message: optStr(raw, "error_message"),
    attempts: attempts ?? 0,
    ready_for_approval: raw.ready_for_approval === true,
    created_at: str(raw, "created_at"),
    updated_at: str(raw, "updated_at"),
  };
}

export function parseTicketList(raw: unknown): RfpTicketSummary[] {
  if (!Array.isArray(raw)) throw new Error("Lista de tickets inesperada.");
  return raw.map(parseTicketSummary);
}

function parseReadability(raw: unknown): Readability | null {
  if (!isRecord(raw)) return null;
  return {
    word_count: optNum(raw, "word_count"),
    estimated_tokens: optNum(raw, "estimated_tokens"),
    flesch_kincaid_grade: optNum(raw, "flesch_kincaid_grade"),
    gunning_fog: optNum(raw, "gunning_fog"),
    cost_tier: optStr(raw, "cost_tier"),
    note: optStr(raw, "note"),
  };
}

function parseMetadata(raw: unknown): RfpMetadata | null {
  if (!isRecord(raw)) return null;
  return {
    client_name: optStr(raw, "client_name"),
    client_hq: str(raw, "client_hq"),
    client_location: optStr(raw, "client_location"),
    currency: optStr(raw, "currency"),
    rfp_type: optStr(raw, "rfp_type"),
    services_requested: strList(raw, "services_requested"),
    scope: optStr(raw, "scope"),
    volumes: figures(raw, "volumes"),
    deadline: optStr(raw, "deadline"),
    deadline_text: optStr(raw, "deadline_text"),
    budget_range: optStr(raw, "budget_range"),
    client_contact: optStr(raw, "client_contact"),
    departments_needed: departments(raw, "departments_needed"),
    unmapped_services: strList(raw, "unmapped_services"),
    readability: parseReadability(raw.readability),
  };
}

function parseSection(raw: unknown): DepartmentSection {
  if (!isRecord(raw)) throw new Error("Sección de departamento inválida.");
  return {
    department_id: oneOf(raw.department_id, "department_id", DEPARTMENT_IDS),
    owner_name: str(raw, "owner_name"),
    key_aspects: strList(raw, "key_aspects"),
    requests_to_owner: strList(raw, "requests_to_owner"),
    open_questions: strList(raw, "open_questions"),
    figures_cited: figures(raw, "figures_cited"),
    excerpts: strList(raw, "excerpts"),
    section_status:
      raw.section_status === null || raw.section_status === undefined
        ? null
        : oneOf(raw.section_status, "section_status", SECTION_STATUSES),
    draft_content: optStr(raw, "draft_content"),
    evaluation: parseEvaluation(raw.evaluation_results),
    iteration_count: optNum(raw, "iteration_count") ?? 0,
  };
}

function bool(row: JsonRecord, key: string): boolean {
  const value = row[key];
  if (typeof value !== "boolean") throw new Error(`Campo "${key}" no es booleano.`);
  return value;
}

function record(row: JsonRecord, key: string): JsonRecord {
  const value = row[key];
  if (!isRecord(value)) throw new Error(`Campo "${key}" no es un objeto.`);
  return value;
}

export function parseEvaluation(raw: unknown): EvaluationResult | null {
  if (!isRecord(raw)) return null;
  const readability = record(raw, "readability");
  const relevance = record(raw, "relevance");
  const compliance = record(raw, "compliance");
  const details = isRecord(readability.details) ? readability.details : {};
  const violations = Array.isArray(compliance.violations) ? compliance.violations.filter(isRecord) : [];
  return {
    iteration: optNum(raw, "iteration") ?? 0,
    readability: {
      pass: bool(readability, "pass"),
      score: optNum(readability, "score"),
      problems: strList(details, "problems"),
    },
    relevance: { pass: bool(relevance, "pass"), missing_aspects: strList(relevance, "missing_aspects") },
    compliance: {
      pass: bool(compliance, "pass"),
      rule_ids: strList(compliance, "rule_ids"),
      violations: violations.map((v) => ({
        rule_id: str(v, "rule_id"),
        message: str(v, "message"),
        evidence: optStr(v, "evidence"),
      })),
    },
    overall_pass: bool(raw, "overall_pass"),
    feedback_for_generator: optStr(raw, "feedback_for_generator") ?? "",
  };
}

function parseClassification(raw: unknown): Classification | null {
  if (!isRecord(raw)) return null;
  return {
    rfp_type: optStr(raw, "rfp_type"),
    confidence: optNum(raw, "confidence"),
    reason: optStr(raw, "reason") ?? "",
    decision: optStr(raw, "decision"),
    low_confidence: raw.low_confidence === true,
  };
}

function parseSynthesis(raw: unknown): Synthesis | null {
  if (!isRecord(raw)) return null;
  const conflicts = Array.isArray(raw.conflicts) ? raw.conflicts.filter(isRecord) : [];
  return {
    summary_for_sales: optStr(raw, "summary_for_sales") ?? "",
    next_steps: strList(raw, "next_steps"),
    conflicts: conflicts.map((conflict) => ({
      concept: str(conflict, "concept"),
      values: (Array.isArray(conflict.values) ? conflict.values.filter(isRecord) : []).map((v) => ({
        source: str(v, "source"),
        value: str(v, "value"),
        quote: str(v, "quote"),
      })),
    })),
  };
}

export function parseTicketDetail(raw: unknown): RfpTicketDetail {
  if (!isRecord(raw)) throw new Error("Ticket con estructura inesperada.");
  const sections = Array.isArray(raw.sections) ? raw.sections.map(parseSection) : [];
  return {
    ...parseTicketSummary(raw),
    classification: parseClassification(raw.classification),
    metadata: parseMetadata(raw.metadata),
    sections,
    synthesis: parseSynthesis(raw.synthesis),
    ready_for_drafting: raw.ready_for_drafting === true,
    approval_max_iterations: isRecord(raw.approval_handoff) ? optNum(raw.approval_handoff, "max_iterations") : null,
  };
}

function parseAccepted(raw: unknown): RfpTicketAccepted {
  if (!isRecord(raw)) throw new Error("Respuesta de subida inesperada.");
  return {
    ticket_id: str(raw, "ticket_id"),
    status: oneOf(raw.status, "status", STATUSES),
    task_id: str(raw, "task_id"),
  };
}

// --- Peticiones ---------------------------------------------------------------

async function readErrorDetail(res: Response): Promise<string> {
  try {
    const body: unknown = await res.json();
    if (isRecord(body) && typeof body.detail === "string") return body.detail;
  } catch {
    // el cuerpo no era JSON, seguimos con el mensaje genérico
  }
  return `Error inesperado (HTTP ${res.status}).`;
}

async function request<T>(
  url: string,
  parse: (raw: unknown) => T,
  init: RequestInit = {},
  timeoutMs = REQUEST_TIMEOUT_MS
): Promise<T | null> {
  const token = getToken();
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  let res: Response;
  try {
    res = await fetch(url, {
      ...init,
      signal: controller.signal,
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
  } catch {
    throw new RfpError(0, "No se pudo conectar con el servidor. Inténtalo de nuevo en unos minutos.");
  } finally {
    clearTimeout(timeoutId);
  }

  if (res.status === 401) {
    handleSessionExpired();
    throw new RfpError(401, "Tu sesión expiró.");
  }
  if (res.status === 404) return null;
  if (!res.ok) throw new RfpError(res.status, await readErrorDetail(res));

  try {
    return parse(await res.json());
  } catch (err) {
    throw new RfpError(res.status, err instanceof Error ? err.message : "Respuesta inválida.");
  }
}

export async function fetchRfpTickets(): Promise<RfpTicketSummary[]> {
  return (await request(TICKETS_PATH, parseTicketList)) ?? [];
}

/** Detalle del ticket, o `null` si no existe. */
export function fetchRfpTicket(ticketId: string): Promise<RfpTicketDetail | null> {
  return request(`${TICKETS_PATH}/${encodeURIComponent(ticketId)}`, parseTicketDetail);
}

/** Sube la RFP (PDF). El backend responde 202 al instante y analiza en segundo plano. */
export async function uploadRfp(file: File): Promise<RfpTicketAccepted> {
  const body = new FormData();
  body.append("file", file);
  const accepted = await request(TICKETS_PATH, parseAccepted, { method: "POST", body }, UPLOAD_TIMEOUT_MS);
  if (!accepted) throw new RfpError(404, "Servicio de RFPs no disponible.");
  return accepted;
}

/** (Re)lanza la Parte 2 (borradores + evaluación). El backend responde 202 al instante. */
export async function regenerateDrafts(ticketId: string): Promise<RfpTicketAccepted> {
  const accepted = await request(
    `${TICKETS_PATH}/${encodeURIComponent(ticketId)}/drafts`,
    parseAccepted,
    { method: "POST" }
  );
  if (!accepted) throw new RfpError(404, "El ticket no existe.");
  return accepted;
}

/** ¿Sigue trabajando el flujo sobre este ticket? (la UI hace poll mientras tanto). */
export function isTicketBusy(ticket: { status: TicketStatus; ready_for_approval: boolean }): boolean {
  if (ticket.status === "analizando" || ticket.status === "generando_borrador") return true;
  return ticket.status === "en_evaluación" && !ticket.ready_for_approval;
}

export const SECTION_STATUS_LABELS: Record<SectionStatus, string> = {
  generando: "Generando",
  evaluando: "Evaluando",
  evaluada_ok: "Evaluación superada",
  needs_human_review: "Requiere revisión humana",
};

export const STATUS_LABELS: Record<TicketStatus, string> = {
  analizando: "Analizando",
  descartado: "Descartado",
  analisis_completo: "Análisis completo",
  generando_borrador: "Generando borrador",
  "en_evaluación": "En evaluación",
  needs_human_review: "Revisión humana",
  "esperando_aprobación": "Esperando aprobación",
  terminado: "Terminado",
};
