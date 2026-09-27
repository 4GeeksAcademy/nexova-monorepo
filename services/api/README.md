# `services/api` — API centralizada de Nexova (FastAPI)

## Autenticación

Auth JWT stateless. Los usuarios y perfiles se almacenan **solo en TinyDB**
(`data/users.json`, `data/profiles.json`), incluso tras introducir Supabase.

| Método | Ruta                | Descripción                                                          | Protegida |
| ------ | ------------------- | --------------------------------------------------------------------- | --------- |
| `POST` | `/users`            | Registro: crea `User` + `Profile` inicial.                            | No        |
| `GET`  | `/users`            | Lista usuarios.                                                        | Sí        |
| `GET`  | `/users/{id}`       | Obtiene un usuario.                                                    | Sí        |
| `PUT`  | `/users/{id}`       | Actualiza `email`/`role` (propio usuario o admin).                    | Sí        |
| `DELETE` | `/users/{id}`     | Elimina usuario + perfil vinculado (propio usuario o admin).          | Sí        |
| `GET`  | `/profiles/me`      | Perfil del usuario autenticado.                                       | Sí        |
| `PUT`  | `/profiles/me`      | Actualiza `name`/`phone`/`address` propios.                            | Sí        |
| `POST` | `/auth/login`       | Valida credenciales, devuelve `{ access_token, token_type }`.         | No        |
| `GET`  | `/auth/me`          | Devuelve email, role y profile del usuario autenticado.               | Sí        |
| `POST` | `/auth/forgot-password` | Envía (si el email existe y está activo) un link de reset por email. Siempre `200`. | No |
| `POST` | `/auth/reset-password`  | Consume el token del email y fija una nueva contraseña.           | No        |
| `POST` | `/auth/change-password` | Cambia la contraseña de la sesión activa (requiere la actual).    | Sí        |

Variables de entorno (`services/api/.env`, ver `.env.example`):

- `SECRET_KEY` — clave de firma del JWT. Nunca hardcodeada, nunca commiteada.
- `ALGORITHM` — por defecto `HS256`.
- `ACCESS_TOKEN_EXPIRE_MINUTES` — ventana de expiración del token (por defecto `30`).
- `PASSWORD_RESET_TOKEN_EXPIRE_MINUTES` — validez del token de `/auth/forgot-password` (por defecto `30`).
- `RESEND_FROM_EMAIL` — remitente del email de recuperación (por defecto `onboarding@resend.dev`).
- `FRONTEND_URL` — base usada para construir el link `/reset-password?token=...` del email.
- `RESEND_API_KEY` — API key de [Resend](https://resend.com/api-keys). Nunca hardcodeada, nunca commiteada.
- `TELEMETRY_ENDPOINT` — endpoint de ingesta de telemetría (por defecto `http://localhost:8000/telemetry/events`, el propio stub). Se lee desde ya aunque todavía no se usa para redirigir tráfico.

Rutas protegidas fuera de `/users`/`/auth`/`/profiles` (requieren
`Authorization: Bearer <token>`): `POST /suppliers`,
`PATCH /suppliers/{id}/rate`, `PATCH /suppliers/{id}/status`,
`DELETE /suppliers/{id}`, `POST /api/incidents/analyze`,
`GET /api/incidents/results/export`.

## Dominio: `incidents`

Endpoints para el análisis de ficheros de incidentes de soporte (Fase 2 del
reto "Analizador de Incidencias").

| Método | Ruta                              | Descripción                                                       |
| ------ | ---------------------------------- | ------------------------------------------------------------------ |
| `POST` | `/api/incidents/analyze`           | Recibe un CSV (`multipart/form-data`, campo `file`) y devuelve el resumen en JSON. |
| `GET`  | `/api/incidents/results/export`    | Descarga el último análisis como `results.csv`.                    |
| `GET`  | `/api/health`                      | Comprobación de estado del servicio.                               |

La lógica de validación y cálculo de métricas se reutiliza tal cual del
paquete compartido `packages/incidents-analyzer` — la misma que usa
`scripts/analyze.py`. No hay lógica duplicada.

## Cómo ejecutar en local

```bash
cd services/api
cp .env.example .env   # y genera tu propio SECRET_KEY, ver más abajo
uv sync                # o: pip install -r requirements.txt
pip install -e ../../packages/incidents-analyzer

uvicorn app.main:app --reload --port 8000
```

Genera un `SECRET_KEY` aleatorio con:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

La API queda disponible en `http://localhost:8000`. Documentación
interactiva automática en `http://localhost:8000/docs`.

> Nota: si no instalas `incidents-analyzer` con `pip install -e`, la app
> intenta localizarlo automáticamente vía `sys.path` (ver `app/main.py`),
> así que funciona igualmente para pruebas rápidas.

## Errores manejados

| Situación                         | Código HTTP |
| ---------------------------------- | ----------- |
| No se envía fichero                | 400         |
| Extensión distinta de `.csv`       | 400         |
| Fichero vacío                      | 400         |
| Fichero > 50 MB                    | 413         |
| Codificación no UTF-8              | 400         |
| CSV sin cabecera / sin filas       | 400         |
| Export sin análisis previo         | 404         |

## Dominio: `telemetry`

| Método | Ruta                | Descripción                                                                 | Protegida |
| ------ | ------------------- | ---------------------------------------------------------------------------- | --------- |
| `POST` | `/telemetry/events` | Recibe `{ "events": [...] }`, loguea la cuenta y `event_type` de cada uno, y responde `{ "received": N }`. **Sin autenticación** (algunos eventos, ej. `login_failed`, ocurren antes de tener sesión) y **sin persistencia** — es un stub de verificación, ver `docs/telemetry/telemetry-plan.md`. | No |

## Privacidad

El endpoint `/api/incidents/analyze` nunca incluye `customer_email` en la
respuesta JSON, ni el export CSV lo incluye en ninguna fila. Solo se
procesan las columnas necesarias para el cálculo de métricas.

## CORS

Por defecto permite `http://localhost:3000` (el backoffice en Next.js en
desarrollo). Configurable vía la variable de entorno `ALLOWED_ORIGINS`
(lista separada por comas).
