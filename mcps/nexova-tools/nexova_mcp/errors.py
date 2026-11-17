"""
Códigos de error estables de las tools.

Los fallos de autenticación (sin token, token inválido, issuer/audience
incorrectos) nunca llegan aquí: los corta MCP Auth en la capa HTTP con un 401.
Todo lo demás vuelve como resultado MCP con `isError: true` y un texto JSON:

    {"error": {"code": "FORBIDDEN_SCOPE", "message": "...", "details": {...}}}
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from fastmcp.exceptions import ToolError


class ErrorCode(str, Enum):
    FORBIDDEN_SCOPE = "FORBIDDEN_SCOPE"  # el token no tiene el scope que exige la tool
    VALIDATION_ERROR = "VALIDATION_ERROR"  # argumentos que no cumplen el esquema
    READ_ONLY_RESOURCE = "READ_ONLY_RESOURCE"  # intento de escritura sobre inventario
    NOT_FOUND = "NOT_FOUND"  # ticket o producto inexistente
    INVALID_TRANSITION = "INVALID_TRANSITION"  # transición de estado no permitida
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"  # la API de Nexova no responde o falla
    UPSTREAM_AUTH_ERROR = "UPSTREAM_AUTH_ERROR"  # la cuenta de servicio no puede entrar
    INTERNAL_ERROR = "INTERNAL_ERROR"  # fallo no previsto del propio servidor


class NexovaToolError(ToolError):
    """Error de tool con código estable. FastMCP lo devuelve como `isError`."""

    def __init__(self, code: ErrorCode, message: str, details: dict[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        payload: dict[str, Any] = {"code": code.value, "message": message}
        if self.details:
            payload["details"] = self.details
        super().__init__(json.dumps({"error": payload}, ensure_ascii=False))
