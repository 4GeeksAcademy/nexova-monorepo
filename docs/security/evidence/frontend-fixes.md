# Correcciones del frontend (auditoría OWASP Top 10)

Alcance: `uis/backoffice`, `uis/website/my-app` y `uis/talent-pipeline-tracker`.
Rama `feature/owasp-top10-audit`, 2026-11-17. Evidencia en bruto: `before/` y `after/npm-audit-<app>.txt`.

## 1. Dependencias (A06 · hallazgo C4)

`next` y `eslint-config-next` se fijan a **16.4.6** (versión exacta) en las tres apps. Antes: 16.2.11 en
backoffice y website, 16.2.10 en el tracker, con advisories críticos de RCE/SSRF corregidos en 16.4.6.
Después se ejecutó `npm audit fix` **sin `--force`** (actualiza los transitivos `sharp`, `postcss`, `nanoid`,
`zod`, `brace-expansion`, etc.).

| App | `npm audit` antes (todas / prod) | `npm audit` después (todas / prod) |
|---|---|---|
| backoffice | 27 (3 crit, 17 high, 7 mod) / **5 (1 crit, 4 high)** | 18 (2 crit, 15 high, 1 mod) / **0** |
| website | 23 (1 crit, 16 high, 6 mod) / **5 (1 crit, 4 high)** | 14 high / **0** |
| talent-pipeline-tracker | 25 (1 crit, 17 high, 7 mod) / **6 (1 crit, 4 high, 1 mod)** | 14 high / **0** |

**Producción (`--omit=dev`): 0 advisories en las tres apps.**

Advisories que quedan, todos en dependencias de desarrollo (no van en el bundle ni en `next start`):

- `picomatch` (high, ReDoS GHSA-fc5q-39xc-vjpf, rango `*`: sin versión corregida) y su cadena
  `micromatch` → `fast-glob` → `@next/eslint-plugin-next` / `typescript-eslint` / `eslint-config-next`,
  y `tinyglobby`/`fdir`/`vite`. Solo se ejecuta en `eslint` y `vitest` con patrones del propio repo.
  El "fix" que propone npm es bajar `eslint-config-next` a 14.2.35 (rompe Next 16): no se aplica.
- Solo backoffice: `vitest` 3.2.7 → `tinypool` ≤2.1.1 (critical, prototype pollution → RCE en opciones del
  worker) y `@vitest/mocker` ≤4.1.11 (moderate/critical, path traversal en mocks). Requiere `vitest` 5.x
  (major): queda como pendiente, no se usó `--force`. Riesgo acotado a la máquina de quien corre los tests.

## 2. Cabeceras de seguridad (A05)

`headers()` en `next.config.*` de las tres apps, aplicado a `/:path*` (páginas y route handlers), y
`poweredByHeader: false` (ya no sale `X-Powered-By: Next.js`):

| Cabecera | Valor |
|---|---|
| `Content-Security-Policy` | `default-src 'self'; script-src 'self' 'unsafe-inline'` (+ `'unsafe-eval'` solo en `next dev`); `style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src …; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'` |
| `X-Frame-Options` | `DENY` |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=(), payment=(), usb=(), browsing-topics=()` |

`connect-src` por app (más `ws://localhost:* ws://127.0.0.1:*` en `next dev` para HMR):

- **backoffice:** `'self'` (route handlers proxy `/api/*`), el origen de `NEXT_PUBLIC_CHAT_WS_URL` o, si no
  está definido, el fallback de `lib/chat-socket.ts` (`ws(s)://localhost:8000`, `ws(s)://127.0.0.1:8000`), y el
  origen de `NEXT_PUBLIC_TELEMETRY_ENDPOINT`.
- **talent-pipeline-tracker:** `'self'` + orígenes de `NEXT_PUBLIC_API_URL` y `NEXT_PUBLIC_AUTH_API_URL`.
- **website:** solo `'self'` (no hace llamadas desde el navegador).

Decisiones y límites:

- `'unsafe-inline'` en `script-src` es necesario porque Next inyecta los scripts de hidratación sin nonce
  (y la web usa JSON-LD inline). Quitarlo exige CSP con nonce vía `proxy`/middleware y renderizado dinámico de
  todas las páginas: queda como mejora futura.
- Las URLs se leen en `next build` (`headers()` se serializa en el manifiesto), igual que las `NEXT_PUBLIC_*`,
  que también se incrustan en el build. Si cambian en el despliegue hay que reconstruir la imagen. En
  `docker-compose.yml` `NEXT_PUBLIC_TELEMETRY_ENDPOINT` solo se pasa como variable de runtime: si el build de la
  imagen no la recibe, la telemetría quedará desactivada y fuera de la CSP, coherente entre sí.
- Si el backoffice se sirve en un host distinto de localhost sin `NEXT_PUBLIC_CHAT_WS_URL`, el fallback
  `ws(s)://<host>:8000` quedará bloqueado por la CSP: en despliegue hay que definir `NEXT_PUBLIC_CHAT_WS_URL`.
- No se añade `upgrade-insecure-requests` ni HSTS: romperían las llamadas `http://` a la API en local; HSTS
  corresponde al proxy TLS.

Verificación con `next start` (build sin variables `NEXT_PUBLIC_*`):

```text
== curl -sI http://localhost:3101/login            (backoffice)
HTTP/1.1 200 OK
Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self' ws://localhost:8000 wss://localhost:8000 ws://127.0.0.1:8000 wss://127.0.0.1:8000; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=(), browsing-topics=()
== curl -sI http://localhost:3101/api/auth/me      (route handler)
HTTP/1.1 401 Unauthorized
(mismas 5 cabeceras)
== curl -sI http://localhost:3102/                 (website)
HTTP/1.1 200 OK
Content-Security-Policy: … connect-src 'self'; … frame-ancestors 'none'
(mismas 4 cabeceras restantes)
== curl -sI http://localhost:3103/login            (talent-pipeline-tracker)
HTTP/1.1 200 OK
Content-Security-Policy: … connect-src 'self'; … frame-ancestors 'none'
(mismas 4 cabeceras restantes)
```

Ninguna respuesta incluye `X-Powered-By`. Las tres páginas se cargaron en un navegador sin errores de consola
ni violaciones de CSP. No se probó el flujo completo con la API levantada (login, chat por WebSocket,
telemetría) ni `next dev` con HMR.

## 3. Proxy de inventario (A03/A10)

`uis/backoffice/app/api/inventory/products/[id]/route.ts`: el `id` se pasa por `encodeURIComponent` antes de
construir la URL del backend, como en el resto de rutas dinámicas. Evita que un `id` como `../../users/me` o
`1?x=` reescriba la ruta o la query que llega a la API.

## 4. JWT fuera de la URL del WebSocket del chat (A02/A07)

`uis/backoffice/lib/chat-socket.ts` ya no manda el JWT en `?token=` (quedaba en logs de proxies/servidor e
historial). La URL es `…/chat/ws/<session_id>` y, en cuanto abre el socket, el cliente envía como primer frame
`{"event":"auth","data":{"token":"…"}}` (`authFrame` en `lib/chat-protocol.ts`), que el servidor
(`services/api/chat/router.py`) ya acepta durante 5 s. Cada reconexión vuelve a autenticarse.
Tests en `uis/backoffice/tests/chat.test.ts`: la URL no contiene el token, el primer frame enviado es `auth`,
nada se envía antes de `open`, la reconexión re-autentica y `chatSocketUrl` codifica el `session_id`.

Pendiente en backend (fuera de este carril): `_authenticate` sigue aceptando `?token=` como alternativa;
cuando no queden clientes antiguos conviene retirarlo.

## 5. Validación

| App | `npm run lint` | `npm run build` | `npm test` |
|---|---|---|---|
| backoffice | 3 errores **previos** (`react-hooks/set-state-in-effect` en `RequireAuth.tsx`, `AuthContext.tsx`, `OutboundOrderForm.tsx`) + 2 warnings | OK | 21/21 OK |
| website | OK | OK | — |
| talent-pipeline-tracker | 2 errores **previos** (`react-hooks/set-state-in-effect` en `lib/AuthContext.tsx` y `candidates/[id]/page.tsx`) + 3 warnings | OK | — |

Los errores de lint no los introduce este cambio: `eslint-plugin-react-hooks` sigue en 7.1.1 antes y después,
el diff de `eslint-config-next` 16.2.11 → 16.4.6 no toca esas reglas y `memory-bank/progress.md` ya registra los
3 del backoffice. Ninguno está en archivos tocados aquí.
