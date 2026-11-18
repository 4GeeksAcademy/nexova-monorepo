import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clearToken, getToken, setToken } from "../lib/auth-storage";

/** `localStorage` en memoria: el entorno de Vitest es `node` (sin jsdom). */
function installWindow(): Map<string, string> {
  const store = new Map<string, string>();
  vi.stubGlobal("window", {
    localStorage: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, value),
      removeItem: (key: string) => void store.delete(key),
    },
  });
  return store;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("auth-storage con navegador", () => {
  let store: Map<string, string>;

  beforeEach(() => {
    store = installWindow();
  });

  it("guarda, lee y borra el token (camino feliz)", () => {
    expect(getToken()).toBeNull();

    setToken("jwt-123");
    expect(getToken()).toBe("jwt-123");

    clearToken();
    expect(getToken()).toBeNull();
  });

  it("usa la clave nexova_auth_token, que es la que leen otros módulos", () => {
    setToken("jwt-123");

    expect(store.get("nexova_auth_token")).toBe("jwt-123");
  });

  it("un token nuevo reemplaza al anterior (límite: re-login)", () => {
    setToken("viejo");
    setToken("nuevo");

    expect(getToken()).toBe("nuevo");
    expect(store.size).toBe(1);
  });

  it("borrar sin token guardado no falla (límite)", () => {
    expect(() => clearToken()).not.toThrow();
    expect(getToken()).toBeNull();
  });
});

describe("auth-storage sin navegador (SSR)", () => {
  it("getToken devuelve null y set/clear no hacen nada ni lanzan (fallo)", () => {
    // Sin `window`, como durante el render en el servidor de Next.
    expect(typeof window).toBe("undefined");

    expect(getToken()).toBeNull();
    expect(() => setToken("jwt")).not.toThrow();
    expect(() => clearToken()).not.toThrow();
  });
});
