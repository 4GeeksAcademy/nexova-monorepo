import { getToken, setToken } from "./auth-storage";
import { handleSessionExpired } from "./session";
import { CONNECTION_ERROR_MESSAGE, GENERIC_ERROR_MESSAGE, statusMessage, userMessage } from "./http-errors";

/** Código del rol (`employee`, `supervisor`, `admin`, `user` externo, heredado `manager` o uno creado por un admin). */
export type Role = string;

export interface ProfileRead {
  id: string;
  user_id: string;
  name: string | null;
  phone: string | null;
  address: string | null;
}

export interface MeRead {
  email: string;
  role: Role;
  role_id: string;
  /** Rango del rol: 0 externo · 1 employee · 2 supervisor · 3 admin. */
  rank: number;
  department_id: string | null;
  department_name: string | null;
  profile: ProfileRead;
}

export interface RegisterPayload {
  email: string;
  password: string;
  name?: string;
  phone?: string;
  address?: string;
}

export interface ProfileUpdatePayload {
  name?: string;
  phone?: string;
  address?: string;
}

interface DetailResponse {
  detail: string;
}

export class AuthApiError extends Error {
  status: number;
  fieldErrors?: Record<string, string>;

  constructor(status: number, message: string, fieldErrors?: Record<string, string>) {
    super(message);
    this.status = status;
    this.fieldErrors = fieldErrors;
  }
}

/** `fetch` que convierte un fallo de red en un error legible en vez de «Failed to fetch». */
async function safeFetch(request: () => Promise<Response>): Promise<Response> {
  try {
    return await request();
  } catch {
    throw new AuthApiError(0, CONNECTION_ERROR_MESSAGE);
  }
}

/** Lee el JSON de una respuesta correcta; si no es JSON válido, mensaje genérico. */
async function parseJsonBody<T>(res: Response): Promise<T> {
  try {
    return (await res.json()) as T;
  } catch {
    throw new AuthApiError(res.status, GENERIC_ERROR_MESSAGE);
  }
}

async function parseErrorDetail(res: Response): Promise<{ message: string; fieldErrors?: Record<string, string> }> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    return { message: statusMessage(res.status) };
  }

  const detail = (body as { detail?: unknown })?.detail;

  if (typeof detail === "string") {
    return { message: userMessage(res.status, detail) };
  }

  if (Array.isArray(detail)) {
    const fieldErrors: Record<string, string> = {};
    for (const issue of detail) {
      const loc = Array.isArray(issue?.loc) ? issue.loc : [];
      const field = loc[loc.length - 1];
      if (typeof field === "string" && typeof issue?.msg === "string") {
        fieldErrors[field] = issue.msg;
      }
    }
    return { message: "Revisa los campos del formulario.", fieldErrors };
  }

  return { message: statusMessage(res.status) };
}

/** Llamada de auth pública (login/registro): via proxy interno, sin token, sin redirect en error. */
async function publicAuthRequest<T>(path: string, body: unknown): Promise<T> {
  const res = await safeFetch(() => fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }));

  if (!res.ok) {
    const { message, fieldErrors } = await parseErrorDetail(res);
    throw new AuthApiError(res.status, message, fieldErrors);
  }

  return parseJsonBody<T>(res);
}

/** Llamada de auth protegida: adjunta el token y limpia sesión + redirige a /login en 401. */
async function protectedAuthRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const token = getToken();
  const res = await safeFetch(() => fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options?.headers ?? {}),
    },
  }));

  if (res.status === 401) {
    handleSessionExpired();
    throw new AuthApiError(401, "Sesion expirada.");
  }

  if (!res.ok) {
    const { message, fieldErrors } = await parseErrorDetail(res);
    throw new AuthApiError(res.status, message, fieldErrors);
  }

  return parseJsonBody<T>(res);
}

export async function login(email: string, password: string): Promise<string> {
  const token = await publicAuthRequest<{ access_token: string; token_type: string }>(
    "/api/auth/login",
    { email, password },
  );
  setToken(token.access_token);
  return token.access_token;
}

export async function register(payload: RegisterPayload): Promise<string> {
  const token = await publicAuthRequest<{ access_token: string; token_type: string }>(
    "/api/auth/register",
    payload,
  );
  setToken(token.access_token);
  return token.access_token;
}

const nullableString = (value: unknown): string | null => (typeof value === "string" ? value : null);

/** Valida la respuesta de `/auth/me` antes de usarla (rol y departamento deciden qué se muestra). */
export function parseMe(raw: unknown): MeRead {
  const data = (raw ?? {}) as Record<string, unknown>;
  if (
    typeof data.email !== "string" ||
    typeof data.role !== "string" ||
    typeof data.rank !== "number" ||
    typeof data.profile !== "object" ||
    data.profile === null
  ) {
    throw new AuthApiError(0, GENERIC_ERROR_MESSAGE);
  }
  return {
    email: data.email,
    role: data.role,
    role_id: typeof data.role_id === "string" ? data.role_id : data.role,
    rank: data.rank,
    department_id: nullableString(data.department_id),
    department_name: nullableString(data.department_name),
    profile: data.profile as ProfileRead,
  };
}

export async function me(): Promise<MeRead> {
  return parseMe(await protectedAuthRequest<unknown>("/api/auth/me"));
}

export async function updateProfile(payload: ProfileUpdatePayload): Promise<ProfileRead> {
  return protectedAuthRequest<ProfileRead>("/api/profiles/me", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function forgotPassword(email: string): Promise<string> {
  const response = await publicAuthRequest<DetailResponse>("/api/auth/forgot-password", { email });
  return response.detail;
}

export async function resetPassword(token: string, newPassword: string): Promise<string> {
  const response = await publicAuthRequest<DetailResponse>("/api/auth/reset-password", {
    token,
    new_password: newPassword,
  });
  return response.detail;
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<string> {
  const response = await protectedAuthRequest<DetailResponse>("/api/auth/change-password", {
    method: "POST",
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
  return response.detail;
}
