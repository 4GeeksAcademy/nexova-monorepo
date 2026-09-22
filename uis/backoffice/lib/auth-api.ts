import { getToken, setToken } from "./auth-storage";
import { handleSessionExpired } from "./session";

export type Role = "admin" | "manager" | "user";

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

async function parseErrorDetail(res: Response): Promise<{ message: string; fieldErrors?: Record<string, string> }> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    return { message: `Error inesperado (HTTP ${res.status}).` };
  }

  const detail = (body as { detail?: unknown })?.detail;

  if (typeof detail === "string") {
    return { message: detail };
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

  return { message: `Error inesperado (HTTP ${res.status}).` };
}

/** Llamada de auth pública (login/registro): via proxy interno, sin token, sin redirect en error. */
async function publicAuthRequest<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const { message, fieldErrors } = await parseErrorDetail(res);
    throw new AuthApiError(res.status, message, fieldErrors);
  }

  return res.json();
}

/** Llamada de auth protegida: adjunta el token y limpia sesión + redirige a /login en 401. */
async function protectedAuthRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const token = getToken();
  const res = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options?.headers ?? {}),
    },
  });

  if (res.status === 401) {
    handleSessionExpired();
    throw new AuthApiError(401, "Sesion expirada.");
  }

  if (!res.ok) {
    const { message, fieldErrors } = await parseErrorDetail(res);
    throw new AuthApiError(res.status, message, fieldErrors);
  }

  return res.json();
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

export async function me(): Promise<MeRead> {
  return protectedAuthRequest<MeRead>("/api/auth/me");
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
