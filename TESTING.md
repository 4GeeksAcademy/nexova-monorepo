# TESTING.md — Pruebas unitarias de la API de autenticación (AUTH-088)

Alcance: lógica de negocio de la autenticación de `services/api` (login, registro, `/auth/me`,
recuperación y cambio de contraseña, JWT y tokens de reset) y la lógica de sesión del
frontend del backoffice. **No se prueba serialización HTTP ni el framework**: cada test afirma
lo que el endpoint *decide* (¿se acepta?, ¿se bloquea?, ¿qué estado queda?), no cómo responde.

## 1. Cómo ejecutar

> `uv run pytest` desde la raíz no aplica: no hay `pyproject.toml` en la raíz del monorepo
> (la config raíz está protegida). El proyecto Python es `services/api`, así que se usa `--project`.

```bash
# Suite de autenticación
uv run --project services/api pytest tests/auth -v

# Con cobertura del módulo de autenticación
uv run --project services/api pytest tests/auth \
  --cov=routes.auth --cov=routes.users --cov=security --cov=password_resets --cov-report=term-missing

# Toda la suite del monorepo (comprobar que no hay regresiones)
uv run --project services/api pytest

# Frontend (Vitest, equivalente a Jest: mismo describe/it/expect)
cd uis/backoffice && npm test
```

Aislamiento: `tests/auth/conftest.py` redirige los JSON de usuarios, perfiles y tokens de
reset a `tmp_path`, anula la telemetría y el envío de email y vacía los limitadores. Ningún
test toca `services/api/data/*.json`.

> **Jest vs. Vitest.** El ticket menciona Jest; el backoffice ya usa Vitest (`npm test`), con
> la misma API. Se mantiene Vitest para no duplicar herramientas ni tocar el `package.json` raíz.

## 2. Plan de pruebas (escrito antes de los tests)

Cada unidad tiene como mínimo un caso de **camino feliz (F)**, uno **límite (L)** y uno de
**modo de fallo (X)**.

### POST /auth/login — `tests/auth/test_login.py`
| | Caso | Qué decide la lógica |
|---|---|---|
| F | Credenciales válidas | Devuelve un JWT cuyo `sub` es el id del usuario |
| L | Email con mayúsculas y espacios | Se normaliza y entra igual |
| L | Contraseña vacía | Se rechaza (401), no se rompe |
| L | 5.º fallo vs. 6.º intento | El 6.º se bloquea (429) incluso con la contraseña correcta |
| X | Email inexistente | Mismo 401 y mismo mensaje que una contraseña errónea (sin enumeración) |
| X | Usuario inactivo | 401 «Usuario inactivo.» aunque la contraseña sea correcta |
| X | Login correcto tras fallos | Reinicia el contador de fallos |

### POST /users (registro) — `tests/auth/test_register.py`
| | Caso | Qué decide la lógica |
|---|---|---|
| F | Registro válido | Crea usuario con rol `user`, activo, y su perfil vinculado |
| L | Email duplicado con otra capitalización | 400, no se crea un segundo usuario |
| L | Email con espacios/mayúsculas | Se guarda normalizado |
| L | Contraseña de exactamente 8 caracteres / de 7 | 8 entra, 7 se rechaza |
| L | Contraseña con espacios en los extremos | Se puede iniciar sesión con la misma contraseña con la que se registró |
| X | Email inválido / sin contraseña | 422, nada se persiste |
| X | `role: admin` en el payload | Se ignora: el registro público nunca escala el rol |
| X | La contraseña no se guarda en claro | Solo hay hash bcrypt |

### GET /auth/me — `tests/auth/test_me.py`
| | Caso | Qué decide la lógica |
|---|---|---|
| F | Token válido | Devuelve email, rol y el perfil propio |
| X | Sin cabecera / token malformado | 401 |
| X | Usuario sin perfil | 404 «Perfil no encontrado.» |
| X | Token de un usuario borrado | 401 |

### POST /auth/forgot-password — `tests/auth/test_forgot_password.py`
| | Caso | Qué decide la lógica |
|---|---|---|
| F | Usuario activo | Se crea un token y se envía un email; respuesta genérica |
| L | Email con otra capitalización | Se trata como el mismo usuario |
| X | Email inexistente o usuario inactivo | Respuesta idéntica y **no** se envía email |
| X | El proveedor de email falla | La petición responde 200 igual (no filtra si la cuenta existe) |
| X | Más de 3 peticiones por email | 429 |

### POST /auth/reset-password — `tests/auth/test_reset_password.py`
| | Caso | Qué decide la lógica |
|---|---|---|
| F | Token válido | Cambia la contraseña; la nueva entra y la vieja no |
| L | Token de un solo uso | El segundo uso → 400 |
| L | Token expirado | 400 |
| L | Contraseña nueva de 7 caracteres | 422 y el token **no** se consume |
| X | Token inventado / vacío | 400 |
| X | 10 tokens inválidos desde una IP | 429 |
| X | Sesiones anteriores tras el reset | Sus JWT quedan invalidados |

### POST /auth/change-password — `tests/auth/test_change_password.py`
| | Caso | Qué decide la lógica |
|---|---|---|
| F | Contraseña actual correcta | Cambia; login con la nueva funciona |
| L | Nueva igual a la actual | Se acepta sin romper la sesión del usuario |
| X | Contraseña actual incorrecta | 400 y la contraseña no cambia |
| X | 5 fallos seguidos | 429 incluso con la contraseña correcta |
| X | Sin autenticación | 401 |

### JWT — `tests/auth/test_tokens.py` (funciones puras de `security.py`)
| | Caso | Qué decide la lógica |
|---|---|---|
| F | `create_access_token` → `decode_token_claims` | `sub`, `iat` y `exp` correctos; `exp - iat` = `ACCESS_TOKEN_EXPIRE_MINUTES` |
| L | Token que expira justo ahora / ya expirado | Expirado se rechaza (la regresión que motivó el ticket) |
| L | `token_still_valid` con `password_changed_at` naive vs. aware, `iat` igual al cambio, sin `iat` | Reglas de invalidación sin errores de zona horaria |
| X | Firma manipulada, otra clave, `alg=none`, sin `sub` | `JWTError` |
| X | Cuenta desactivada | `token_still_valid` = False |
| F/X | `hash_password` / `verify_password` | Hash ≠ claro, verifica, rechaza otra contraseña |

### Tokens de reset — `tests/auth/test_reset_tokens.py`
| | Caso | Qué decide la lógica |
|---|---|---|
| F | `create` → `consume` | Devuelve el `user_id` |
| L | Justo antes / en el instante de expiración | Antes sirve; en el límite no |
| X | Reutilizado, inexistente, vacío | `None` |
| X | Persistencia | Solo se guarda el SHA-256, nunca el token en claro |

### Limitador — `tests/auth/test_rate_limiter.py`
Reloj inyectado: bloquea al agotar intentos, `Retry-After` correcto, se libera al terminar la
ventana, `reset` y claves independientes.

### Frontend (Vitest) — `uis/backoffice/tests/`
| | Caso | Archivo |
|---|---|---|
| F | `setToken` → `getToken` → `clearToken` | `auth-storage.test.ts` |
| X | Sin `window` (SSR) → `null` y sin excepciones | `auth-storage.test.ts` |
| F | `login` guarda el token | `auth-api.test.ts` |
| L | `detail` como lista de errores → `fieldErrors` por campo | `auth-api.test.ts` |
| X | Red caída → error de conexión (status 0); 401 protegido → sesión expirada | `auth-api.test.ts` |

## 3. Casos identificados con ayuda de la IA

El agente de IA (Claude Code) analizó la lógica de cada endpoint (`routes/auth.py`,
`routes/users.py`, `security.py`, `password_resets.py`, `rate_limit.py`) buscando casos límite que
el plan inicial no contemplaba. Los adoptados tras revisarlos uno a uno:

| Caso sugerido por la IA | Dónde | Resultado |
|---|---|---|
| Registrarse con una contraseña con espacios en los extremos e iniciar sesión con la misma | `test_register.py` | **Reveló un bug** (ver §4) |
| Rotar emails en `forgot-password` no evita el límite: la clave por IP también cuenta | `test_forgot_password.py` | Pasa; documenta la protección |
| Un token de reset válido también se bloquea cuando la IP ya agotó sus 10 intentos | `test_reset_password.py` | Pasa |
| Una contraseña nueva demasiado corta (422) no «quema» el token de reset | `test_reset_password.py` | Pasa |
| Un token expirado no debe quedar marcado como usado | `test_reset_tokens.py` | Pasa |
| El instante exacto de expiración ya no sirve (`>=`) | `test_reset_tokens.py` | Pasa |
| `password_changed_at` sin zona horaria o en otra zona horaria | `test_tokens.py` | Pasa |
| Token sin `exp`: el decodificador lo acepta (comportamiento actual, documentado en el test) | `test_tokens.py` | Pasa; punto a vigilar |
| Tokens con `alg=none` o firmados con HS512 | `test_tokens.py` | Rechazados |
| El rol se lee de la base en cada petición, no del token | `test_me.py` | Pasa |
| La telemetría de login fallido no contiene el email | `test_login.py` | Pasa |
| Con un email inexistente se ejecuta bcrypt contra el hash señuelo | `test_login.py` | Pasa |

Cada test generado se revisó antes de incorporarlo y solo afirma decisiones de negocio, no el
formato del JSON de respuesta (serialización).

## 4. Bugs encontrados por la batería

**Bug 1 — la contraseña con espacios en los extremos no permitía volver a entrar.**
`UserCreate` tiene `str_strip_whitespace=True` y guardaba el hash de la contraseña *recortada*,
pero `LoginRequest`, `ResetPasswordRequest` y `ChangePasswordRequest` no recortaban. Un usuario
registrado con `" clave "` recibía 401 al escribir exactamente lo mismo. Lo detectó
`test_register.py::test_password_with_surrounding_spaces_works_at_login_as_typed`.

*Corrección* (`services/api/models.py`): los tres modelos aplican también `str_strip_whitespace`.
Es compatible con los hashes ya guardados (siempre fueron de la versión recortada). Efecto
secundario conocido: una contraseña fijada antes con espacios por `reset-password` o
`change-password` (sin recortar) dejaría de coincidir; es un caso marginal y se resuelve con
«olvidé mi contraseña».

**Observaciones (sin cambio de código):**
- `iat` tiene resolución de segundos y `token_still_valid` usa `>=`: un token emitido en el mismo
  segundo que un cambio de contraseña sigue siendo válido. Es deliberado (no bloquear a quien
  entra justo después de cambiarla) y está fijado en `test_tokens.py`.
- Por la misma lógica, tras `change-password` la sesión actual (emitida antes) deja de ser válida.
  Se deduce del código; no se ha probado end-to-end con el frontend.
- `consume_reset_token` lee y marca como usado en dos pasos sobre TinyDB: con dos peticiones
  simultáneas podría consumirse dos veces. No se ha reproducido; TinyDB no es seguro para
  concurrencia entre procesos de todos modos.

## 5. Resultados y cobertura

| Suite | Tests | Resultado |
|---|---|---|
| `tests/auth` (pytest) | 107 | 107 passed |
| Toda `tests/` (regresión) | 733 | 733 passed, 2 skipped (línea base antes de AUTH-088: 626 + 2) |
| `uis/backoffice` (Vitest) | 66 | 66 passed (47 previos + 19 nuevos) |
| `tsc --noEmit` (backoffice) | — | sin errores |

Cobertura del módulo de autenticación (`pytest tests/auth --cov=...`):

| Módulo | Cobertura | Líneas sin cubrir |
|---|---|---|
| `routes/auth.py` | **100 %** | — |
| `security.py` | **100 %** | — |
| `password_resets.py` | **100 %** | — |
| `rate_limit.py` | 83 % | presupuesto del LLM (123-142), fuera de auth |
| `routes/users.py` | 56 % | `list/get/update/delete` de usuarios (CRUD, no autenticación; parte cubierta en `tests/security`) |

El objetivo del ticket (≥ 70 % en el módulo de autenticación) se cumple de sobra: los tres
módulos de auth están al 100 %. `routes/users.py` queda fuera del objetivo a propósito: su
parte de autenticación (registro, búsqueda por email, cambio de contraseña) está cubierta; el
resto es gestión de cuentas.

**Prueba de sensibilidad.** Se multiplicó por 1000 la vida del token en
`security.create_access_token` y la suite falló (`test_token_roundtrip_keeps_the_subject_and_lifetime`
y `test_lifetime_follows_the_configured_minutes`); la regresión que motivó el ticket quedaría
detectada. El cambio se revirtió.

**Fuera de alcance:** `mcps/nexova-tools/tests` es otro proyecto `uv` con su propio entorno y no
se ejecuta con `--project services/api`.
