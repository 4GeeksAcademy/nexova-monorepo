import { clearToken } from "./auth-storage";
import { flushOnHide, track } from "./telemetry";

/**
 * Único punto de "la sesión expiró": limpia el token, emite `session_expired`
 * y redirige a /login. Usado por los 3 clientes de API (auth-api, api,
 * inventory) para no duplicar esta lógica en cada uno.
 *
 * `flushOnHide()` se llama explícitamente (no basta con esperar al listener
 * de `visibilitychange`): un redirect inmediato como este puede destruir la
 * página antes de que el navegador llegue a disparar ese evento, perdiendo
 * el evento en cola. `flushOnHide()` encola el `sendBeacon` de forma
 * síncrona, así queda enviado antes de que naveguemos.
 */
export function handleSessionExpired(): void {
  clearToken();
  track("session_expired", {});
  flushOnHide();
  if (typeof window !== "undefined") {
    window.location.href = "/login";
  }
}
