"""
Endurecimiento HTTP de la API (auditoría OWASP, A05 Security Misconfiguration).

- `allowed_origins()`: lista de `ALLOWED_ORIGINS` limpia; `*` se descarta porque
  la API usa `allow_credentials=True` (Starlette reflejaría cualquier origen).
  La reutiliza el WebSocket del chat para comprobar `Origin` (CSWSH).
- `security_headers`: middleware con cabeceras defensivas en toda respuesta.
- `docs_enabled()`: Swagger/ReDoc/OpenAPI solo si `ENABLE_API_DOCS` no es
  `false` (por defecto activos en desarrollo; en producción, `false`).
"""

from __future__ import annotations

import logging
import os

from fastapi import Request

logger = logging.getLogger(__name__)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    # La API solo sirve JSON: nada de scripts, frames ni recursos embebidos.
    # Swagger (/docs) carga sus assets de un CDN, por eso se excluye abajo.
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}
_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


def allowed_origins() -> list[str]:
    raw = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000")
    origins = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
    if "*" in origins:
        logger.error("ALLOWED_ORIGINS='*' no se admite con credenciales: se ignora.")
        origins = [o for o in origins if o != "*"]
    return origins


def origin_allowed(origin: str | None) -> bool:
    """Sin cabecera `Origin` (clientes no navegador) se permite; con ella, debe estar en la lista."""
    return origin is None or origin.rstrip("/") in allowed_origins()


def docs_enabled() -> bool:
    return os.environ.get("ENABLE_API_DOCS", "true").strip().lower() not in {"0", "false", "no"}


async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        if name == "Content-Security-Policy" and request.url.path.startswith(_DOCS_PATHS):
            continue
        response.headers.setdefault(name, value)
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response
