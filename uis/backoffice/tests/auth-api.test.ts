import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// `session` toca telemetría y `window.location`: aquí solo importa SI se invoca.
vi.mock("../lib/session", () => ({ handleSessionExpired: vi.fn() }));

import {
  AuthApiError,
  changePassword,
  forgotPassword,
  login,
  me,
} from "../lib/auth-api";
import { clearToken, getToken, setToken } from "../lib/auth-storage";
import { CONNECTION_ERROR_MESSAGE, GENERIC_ERROR_MESSAGE } from "../lib/http-errors";
import { handleSessionExpired } from "../lib/session";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** Captura el fallo de una promesa como `AuthApiError` (falla el test si no hay error). */
async function failure(promise: Promise<unknown>): Promise<AuthApiError> {
  try {
    await promise;
  } catch (error) {
    expect(error).toBeInstanceOf(AuthApiError);
    return error as AuthApiError;
  }
  throw new Error("Se esperaba un AuthApiError y la promesa se resolvió");
}

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  const store = new Map<string, string>();
  vi.stubGlobal("window", {
    localStorage: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, value),
      removeItem: (key: string) => void store.delete(key),
    },
  });
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  vi.mocked(handleSessionExpired).mockClear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("login", () => {
  it("guarda el token y lo devuelve (camino feliz)", async () => {
    fetchMock.mockResolvedValue(json({ access_token: "jwt-123", token_type: "bearer" }));

    await expect(login("ana@example.com", "Sup3r-secreta!")).resolves.toBe("jwt-123");

    expect(getToken()).toBe("jwt-123");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/auth/login");
    expect(JSON.parse(String(init?.body))).toEqual({ email: "ana@example.com", password: "Sup3r-secreta!" });
  });

  it("credenciales incorrectas: error con el mensaje del servidor y sin guardar token (fallo)", async () => {
    fetchMock.mockResolvedValue(json({ detail: "Email o contraseña incorrectos." }, 401));

    const error = await failure(login("ana@example.com", "mala"));

    expect(error.status).toBe(401);
    expect(error.message).toBe("Email o contraseña incorrectos.");
    expect(getToken()).toBeNull();
  });

  it("un 401 de login NO se trata como sesión expirada (límite: no redirige)", async () => {
    fetchMock.mockResolvedValue(json({ detail: "Email o contraseña incorrectos." }, 401));

    await failure(login("ana@example.com", "mala"));

    expect(handleSessionExpired).not.toHaveBeenCalled();
  });

  it("detail en lista (422 de validación) se convierte en errores por campo (límite)", async () => {
    fetchMock.mockResolvedValue(
      json(
        {
          detail: [
            { loc: ["body", "email"], msg: "value is not a valid email address" },
            { loc: ["body", "password"], msg: "String should have at least 8 characters" },
            { loc: [], msg: "sin campo" },
          ],
        },
        422,
      ),
    );

    const error = await failure(login("no-email", "corta"));

    expect(error.status).toBe(422);
    expect(error.message).toBe("Revisa los campos del formulario.");
    expect(error.fieldErrors).toEqual({
      email: "value is not a valid email address",
      password: "String should have at least 8 characters",
    });
  });

  it("red caída: status 0 y mensaje de conexión, nunca «Failed to fetch» (fallo)", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    const error = await failure(login("ana@example.com", "Sup3r-secreta!"));

    expect(error.status).toBe(0);
    expect(error.message).toBe(CONNECTION_ERROR_MESSAGE);
  });

  it("5xx sin JSON: mensaje genérico, sin código HTTP ni texto del servidor (fallo)", async () => {
    fetchMock.mockResolvedValue(new Response("<html>Bad gateway</html>", { status: 502 }));

    const error = await failure(login("ana@example.com", "Sup3r-secreta!"));

    expect(error.status).toBe(502);
    expect(error.message).not.toMatch(/502|html|gateway/i);
  });

  it("200 con cuerpo que no es JSON: error genérico y sin token (fallo)", async () => {
    fetchMock.mockResolvedValue(new Response("no soy json", { status: 200 }));

    const error = await failure(login("ana@example.com", "Sup3r-secreta!"));

    expect(error.message).toBe(GENERIC_ERROR_MESSAGE);
    expect(getToken()).toBeNull();
  });
});

describe("peticiones protegidas", () => {
  it("me() adjunta el token guardado como Bearer (camino feliz)", async () => {
    setToken("jwt-123");
    fetchMock.mockResolvedValue(
      json({
        email: "ana@example.com",
        role: "supervisor",
        role_id: "supervisor",
        rank: 2,
        department_id: "sales",
        department_name: "Sales and Business Development",
        profile: { id: "p", user_id: "u", name: null, phone: null, address: null },
      }),
    );

    const result = await me();

    expect(result.email).toBe("ana@example.com");
    expect([result.role, result.rank, result.department_id]).toEqual(["supervisor", 2, "sales"]);
    const headers = fetchMock.mock.calls[0][1]?.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer jwt-123");
  });

  it("me() rechaza una respuesta sin rango: el rol decide qué se muestra y no se adivina (fallo)", async () => {
    setToken("jwt-123");
    fetchMock.mockResolvedValue(
      json({ email: "ana@example.com", role: "user", profile: { id: "p", user_id: "u", name: null, phone: null, address: null } }),
    );

    const error = await failure(me());

    expect(error.message).toBe(GENERIC_ERROR_MESSAGE);
  });

  it("sin token guardado no envía cabecera Authorization (límite)", async () => {
    clearToken();
    fetchMock.mockResolvedValue(json({ detail: "x" }, 403));

    await failure(me());

    const headers = fetchMock.mock.calls[0][1]?.headers as Record<string, string>;
    expect(headers).not.toHaveProperty("Authorization");
  });

  it("401 en una ruta protegida cierra la sesión y avisa «Sesion expirada» (fallo)", async () => {
    setToken("jwt-caducado");
    fetchMock.mockResolvedValue(json({ detail: "No se pudo validar las credenciales." }, 401));

    const error = await failure(me());

    expect(error.status).toBe(401);
    expect(error.message).toBe("Sesion expirada.");
    expect(handleSessionExpired).toHaveBeenCalledTimes(1);
  });

  it("changePassword: 400 con la contraseña actual incorrecta no cierra la sesión (fallo)", async () => {
    setToken("jwt-123");
    fetchMock.mockResolvedValue(json({ detail: "La contraseña actual es incorrecta." }, 400));

    const error = await failure(changePassword("mala", "Nueva-clave-9"));

    expect(error.status).toBe(400);
    expect(error.message).toBe("La contraseña actual es incorrecta.");
    expect(handleSessionExpired).not.toHaveBeenCalled();
    expect(getToken()).toBe("jwt-123");
  });

  it("changePassword envía los nombres de campo que espera la API (camino feliz)", async () => {
    setToken("jwt-123");
    fetchMock.mockResolvedValue(json({ detail: "Contraseña actualizada correctamente." }));

    await expect(changePassword("vieja-clave-1", "Nueva-clave-9")).resolves.toBe("Contraseña actualizada correctamente.");

    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      current_password: "vieja-clave-1",
      new_password: "Nueva-clave-9",
    });
  });
});

describe("forgotPassword", () => {
  it("devuelve el mensaje genérico del servidor (camino feliz)", async () => {
    fetchMock.mockResolvedValue(json({ detail: "Si esa dirección está registrada, recibirás un enlace en breve." }));

    await expect(forgotPassword("ana@example.com")).resolves.toMatch(/recibirás un enlace/);
  });

  it("429 por exceso de peticiones: muestra el mensaje del servidor (límite 4xx)", async () => {
    fetchMock.mockResolvedValue(json({ detail: "Demasiados intentos. Inténtalo de nuevo más tarde." }, 429));

    const error = await failure(forgotPassword("ana@example.com"));

    expect(error.status).toBe(429);
    expect(error.message).toMatch(/Demasiados intentos/);
  });
});
