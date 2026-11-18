"""
Manejo de errores propio del gestor de incidencias, aislado del resto de la API.

`ManagerRoute` solo afecta a las rutas de este router (no hay handlers globales
nuevos), así que el resto de endpoints sigue respondiendo 422 como hasta ahora:

- Error de validación → 400 con `{"errors": [{"field", "message"}]}`.
- Error de negocio (`ApiProblem`) → su código con el mismo formato.
- Excepción no controlada → 500 con mensaje genérico, nunca la traza.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("incident_manager")

GENERIC_SERVER_ERROR = "Ha ocurrido un error inesperado. Inténtalo de nuevo más tarde."


class ApiProblem(Exception):
    """Error de negocio que se responde con `{"errors": [...]}`."""

    def __init__(self, status_code: int, field: str, message: str, **extra: Any) -> None:
        self.status_code = status_code
        self.field = field
        self.message = message
        self.extra = extra
        super().__init__(message)


def _field_name(loc: tuple[Any, ...]) -> str:
    # loc = ("body", "title") | ("query", "status") | ("path", "incident_id")
    parts = [str(p) for p in loc[1:]] if len(loc) > 1 else [str(p) for p in loc]
    return ".".join(parts) or "body"


def _message(error: dict[str, Any]) -> str:
    kind = error.get("type", "")
    ctx = error.get("ctx") or {}
    if kind == "missing":
        return "Este campo es obligatorio."
    if kind == "string_too_short":
        return "Este campo no puede estar vacío."
    if kind == "string_too_long":
        return f"Máximo {ctx.get('max_length')} caracteres."
    if kind == "literal_error":
        expected = str(ctx.get("expected", "")).replace(" or ", " o ")  # Pydantic lo formatea en inglés
        return f"Valor no permitido. Valores válidos: {expected}."
    if kind == "extra_forbidden":
        return "Campo no reconocido."
    if kind in {"json_invalid", "model_attributes_type", "dict_type"}:
        return "El cuerpo de la petición no es un JSON válido."
    if kind in {"int_parsing", "int_type"}:
        return "Debe ser un número entero."
    if kind in {"string_type", "string_pattern_mismatch"}:
        return "Debe ser un texto válido."
    return "Valor no válido."


def format_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{"field": _field_name(tuple(e.get("loc", ()))), "message": _message(e)} for e in errors]


class ManagerRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError as exc:
                return JSONResponse(
                    status_code=400,
                    content={"errors": format_validation_errors(exc.errors())},
                )
            except ApiProblem as exc:
                content: dict[str, Any] = {
                    "errors": [{"field": exc.field, "message": exc.message}],
                    **exc.extra,
                }
                return JSONResponse(status_code=exc.status_code, content=content)
            except StarletteHTTPException:
                raise  # 401/403/404 del propio framework o de las dependencias
            except Exception:  # noqa: BLE001 — último recurso: no filtrar la traza
                logger.exception("Error no controlado en %s %s", request.method, request.url.path)
                return JSONResponse(status_code=500, content={"detail": GENERIC_SERVER_ERROR})

        return handler
