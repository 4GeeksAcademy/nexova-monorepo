import { getToken } from "./auth-storage";
import { CONNECTION_ERROR_MESSAGE, GENERIC_ERROR_MESSAGE, statusMessage, userMessage } from "./http-errors";
import { handleSessionExpired } from "./session";

/**
 * Cliente tipado de Roles y Permisos (`/api/rbac/*` → proxy → API). Toda respuesta se valida antes
 * de usarse. La API es quien autoriza: un rol insuficiente llega como `RbacError` con `status` 403.
 */

const BASE_PATH = "/api/rbac";

export interface Role {
  id: string;
  code: string;
  name: string;
  rank: number;
}

export interface Department {
  id: string;
  code: string;
  name: string;
}

export interface AdminUser {
  id: string;
  email: string;
  is_active: boolean;
  role_id: string;
  department_id: string | null;
}

export interface InternalReport {
  id: string;
  department_id: string;
  title: string;
  body: string;
  created_by: string;
  created_at: string;
}

export class RbacError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

function invalid(): never {
  throw new RbacError(0, GENERIC_ERROR_MESSAGE);
}

export function parseRole(raw: unknown): Role {
  if (!isObject(raw) || typeof raw.id !== "string" || typeof raw.code !== "string" || typeof raw.name !== "string" || typeof raw.rank !== "number") invalid();
  return { id: raw.id, code: raw.code, name: raw.name, rank: raw.rank };
}

export function parseDepartment(raw: unknown): Department {
  if (!isObject(raw) || typeof raw.id !== "string" || typeof raw.code !== "string" || typeof raw.name !== "string") invalid();
  return { id: raw.id, code: raw.code, name: raw.name };
}

export function parseAdminUser(raw: unknown): AdminUser {
  if (!isObject(raw) || typeof raw.id !== "string" || typeof raw.email !== "string" || typeof raw.role_id !== "string") invalid();
  return {
    id: raw.id,
    email: raw.email,
    is_active: raw.is_active !== false,
    role_id: raw.role_id,
    department_id: typeof raw.department_id === "string" ? raw.department_id : null,
  };
}

export function parseReport(raw: unknown): InternalReport {
  if (
    !isObject(raw) ||
    typeof raw.id !== "string" ||
    typeof raw.department_id !== "string" ||
    typeof raw.title !== "string" ||
    typeof raw.body !== "string" ||
    typeof raw.created_by !== "string" ||
    typeof raw.created_at !== "string"
  ) {
    invalid();
  }
  return {
    id: raw.id,
    department_id: raw.department_id,
    title: raw.title,
    body: raw.body,
    created_by: raw.created_by,
    created_at: raw.created_at,
  };
}

function parseList<T>(raw: unknown, parse: (item: unknown) => T): T[] {
  if (!Array.isArray(raw)) invalid();
  return raw.map(parse);
}

async function request(path: string, init?: RequestInit): Promise<unknown> {
  const token = getToken();
  let res: Response;
  try {
    res = await fetch(`${BASE_PATH}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    });
  } catch {
    throw new RbacError(0, CONNECTION_ERROR_MESSAGE);
  }

  if (res.status === 401) {
    handleSessionExpired();
    throw new RbacError(401, statusMessage(401));
  }

  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    if (res.ok) invalid();
  }

  if (!res.ok) {
    const detail = isObject(body) ? body.detail : undefined;
    throw new RbacError(res.status, userMessage(res.status, detail));
  }
  return body;
}

const json = (method: string, payload: unknown): RequestInit => ({ method, body: JSON.stringify(payload) });

// --- roles y departamentos (solo admin) ---
export const listRoles = async (): Promise<Role[]> => parseList(await request("/roles"), parseRole);
export const createRole = async (payload: { code: string; name: string; rank: number }): Promise<Role> =>
  parseRole(await request("/roles", json("POST", payload)));
export const updateRole = async (id: string, payload: { name?: string; rank?: number }): Promise<Role> =>
  parseRole(await request(`/roles/${encodeURIComponent(id)}`, json("PUT", payload)));

export const listDepartments = async (): Promise<Department[]> => parseList(await request("/departments"), parseDepartment);
export const createDepartment = async (payload: { code: string; name: string }): Promise<Department> =>
  parseDepartment(await request("/departments", json("POST", payload)));
export const updateDepartment = async (id: string, payload: { name: string }): Promise<Department> =>
  parseDepartment(await request(`/departments/${encodeURIComponent(id)}`, json("PUT", payload)));

// --- usuarios: rol y departamento se asignan por separado (solo admin) ---
export const listUsers = async (): Promise<AdminUser[]> => parseList(await request("/users"), parseAdminUser);

/** Solo envía lo que cambia: lo que se omite no se toca (rol y departamento son independientes). */
export const assignUser = async (
  userId: string,
  changes: { role_id?: string; department_id?: string | null },
): Promise<AdminUser> => parseAdminUser(await request(`/users/${encodeURIComponent(userId)}/assignment`, json("PATCH", changes)));

// --- informes internos (alcance departamental) ---
export const listReports = async (): Promise<InternalReport[]> => parseList(await request("/internal-reports"), parseReport);
export const createReport = async (payload: { title: string; body: string; department_id?: string }): Promise<InternalReport> =>
  parseReport(await request("/internal-reports", json("POST", payload)));
export const updateReport = async (id: string, payload: { title?: string; body?: string }): Promise<InternalReport> =>
  parseReport(await request(`/internal-reports/${encodeURIComponent(id)}`, json("PUT", payload)));
export const deleteReport = async (id: string): Promise<void> => {
  await request(`/internal-reports/${encodeURIComponent(id)}`, { method: "DELETE" });
};
