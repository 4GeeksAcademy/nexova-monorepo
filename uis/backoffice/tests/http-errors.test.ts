import { afterEach, describe, expect, it, vi } from "vitest";
import { BACKEND_UNAVAILABLE_DETAIL, backendFetch, badRequest } from "../lib/backend-fetch";
import {
  CONNECTION_ERROR_MESSAGE,
  GENERIC_ERROR_MESSAGE,
  statusMessage,
  userMessage,
} from "../lib/http-errors";

describe("mensajes de error para el usuario", () => {
  it("nunca menciona códigos HTTP ni jerga técnica", () => {
    for (const status of [0, 400, 401, 403, 404, 409, 429, 500, 502, 503, 504]) {
      const message = statusMessage(status);
      expect(message).not.toMatch(/HTTP|\b[45]\d\d\b|Unexpected token|JSON|undefined|Failed to fetch/i);
    }
  });

  it("distingue sesión caducada, permisos, no encontrado y exceso de peticiones", () => {
    expect(statusMessage(401)).toMatch(/sesión/i);
    expect(statusMessage(403)).toMatch(/permiso/i);
    expect(statusMessage(404)).toMatch(/no se encontró/i);
    expect(statusMessage(429)).toMatch(/espera/i);
    expect(statusMessage(500)).toBe(GENERIC_ERROR_MESSAGE);
  });

  it("muestra el texto del servidor solo en errores de cliente (4xx)", () => {
    expect(userMessage(409, "Stock insuficiente: quedan 3 unidades.")).toBe("Stock insuficiente: quedan 3 unidades.");
    expect(userMessage(400, "Revisa el campo cantidad.")).toBe("Revisa el campo cantidad.");
  });

  it("en 5xx ignora el texto del servidor, incluso si parece legible", () => {
    expect(userMessage(500, 'OperationalError: connection to server at "db.interno" failed')).toBe(GENERIC_ERROR_MESSAGE);
    expect(userMessage(503, "Task backend unavailable.")).toBe(GENERIC_ERROR_MESSAGE);
  });

  it("usa el mensaje por defecto si el detalle no es texto, está vacío o es demasiado largo", () => {
    expect(userMessage(422, undefined)).toBe(statusMessage(422));
    expect(userMessage(422, { loc: ["body"] })).toBe(statusMessage(422));
    expect(userMessage(400, "   ")).toBe(statusMessage(400));
    expect(userMessage(400, "x".repeat(301))).toBe(statusMessage(400));
  });

  it("el mensaje de conexión da una salida (comprobar la red y reintentar)", () => {
    expect(CONNECTION_ERROR_MESSAGE).toMatch(/conexión/i);
    expect(CONNECTION_ERROR_MESSAGE).toMatch(/inténtalo/i);
  });
});

describe("proxy del backend", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("devuelve un 502 JSON legible cuando el backend no responde, en vez de lanzar", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed: ECONNREFUSED 127.0.0.1:8000")));

    const res = await backendFetch("http://127.0.0.1:8000/inventory/products");

    expect(res.status).toBe(502);
    expect(res.headers.get("content-type")).toMatch(/application\/json/);
    const text = await res.text();
    expect(JSON.parse(text)).toEqual({ detail: BACKEND_UNAVAILABLE_DETAIL });
    expect(text).not.toMatch(/ECONNREFUSED|127\.0\.0\.1|fetch failed/);
  });

  it("reenvía la respuesta del backend tal cual cuando responde", async () => {
    const upstream = new Response(JSON.stringify({ ok: true }), { status: 201 });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(upstream));

    const res = await backendFetch("http://127.0.0.1:8000/x");

    expect(res).toBe(upstream);
  });

  it("un cuerpo de petición ilegible se responde con un 400 JSON", async () => {
    const res = badRequest();
    expect(res.status).toBe(400);
    expect((await res.json()).detail).toMatch(/petición/i);
  });
});
