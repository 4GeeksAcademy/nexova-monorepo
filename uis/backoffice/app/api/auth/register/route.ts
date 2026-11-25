import { backendFetch, badRequest } from "@/lib/backend-fetch";
const BACKEND_API_URL = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000";

/**
 * Registro encadenado: POST /auth/register (cuenta externa; `POST /users` es solo admin) seguido de POST /auth/login con las mismas
 * credenciales, tal como especifica AUTH-02. Se hace server-side en un único
 * viaje para que el cliente reciba directamente el token.
 */
export async function POST(request: Request): Promise<Response> {
  let payload: { email?: string; password?: string };
  try {
    payload = await request.json();
  } catch {
    return badRequest("Revisa los datos del formulario e inténtalo de nuevo.");
  }

  const createUserResponse = await backendFetch(`${BACKEND_API_URL}/auth/register`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store",
  });

  if (!createUserResponse.ok) {
    const errorBody = await createUserResponse.text();
    return new Response(errorBody, {
      status: createUserResponse.status,
      headers: {
        "content-type": createUserResponse.headers.get("content-type") ?? "application/json; charset=utf-8",
      },
    });
  }

  const loginResponse = await backendFetch(`${BACKEND_API_URL}/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email: payload.email, password: payload.password }),
    cache: "no-store",
  });

  const loginBody = await loginResponse.text();

  return new Response(loginBody, {
    status: loginResponse.status,
    headers: {
      "content-type": loginResponse.headers.get("content-type") ?? "application/json; charset=utf-8",
    },
  });
}
