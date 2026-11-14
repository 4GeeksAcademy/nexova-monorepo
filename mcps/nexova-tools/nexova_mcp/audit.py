"""
Trazabilidad: una línea JSON por invocación de tool (logger `nexova_mcp.audit`).

    {"event": "tool_call", "tool": "update_ticket_status", "client_id": "...",
     "subject": "...", "scopes": [...], "result": "error",
     "error_code": "INVALID_TRANSITION", "duration_ms": 12.3}

Nunca se registran los argumentos (pueden llevar descripciones de clientes)
ni el token. Además, este middleware traduce los errores de validación de
argumentos de FastMCP a `VALIDATION_ERROR` y cualquier excepción no prevista
a `INTERNAL_ERROR`, para que todo error tenga un código estable.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastmcp.exceptions import ValidationError as FastMCPValidationError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from mcpauth import MCPAuth
from pydantic import ValidationError as PydanticValidationError

from nexova_mcp.errors import ErrorCode, NexovaToolError

audit_logger = logging.getLogger("nexova_mcp.audit")


def log_event(**fields: Any) -> None:
    audit_logger.info(json.dumps(fields, ensure_ascii=False, default=str))


def _validation_details(exc: Exception) -> list[dict[str, Any]]:
    cause = exc.__cause__ if isinstance(exc.__cause__, PydanticValidationError) else exc
    if not isinstance(cause, PydanticValidationError):
        return []
    return [
        {"field": ".".join(str(p) for p in err["loc"]), "reason": err["msg"]}
        for err in cause.errors(include_url=False, include_input=False, include_context=False)
    ]


class AuditMiddleware(Middleware):
    def __init__(self, mcp_auth: MCPAuth) -> None:
        self._mcp_auth = mcp_auth

    async def on_call_tool(self, context: MiddlewareContext, call_next):  # type: ignore[override]
        start = time.perf_counter()
        info = self._mcp_auth.auth_info
        entry: dict[str, Any] = {
            "event": "tool_call",
            "tool": getattr(context.message, "name", None),
            "client_id": info.client_id if info else None,
            "subject": info.subject if info else None,
            "scopes": sorted(info.scopes) if info else [],
        }
        try:
            result = await call_next(context)
        except NexovaToolError as exc:
            self._log(entry, start, exc.code)
            raise
        except (FastMCPValidationError, PydanticValidationError) as exc:
            self._log(entry, start, ErrorCode.VALIDATION_ERROR)
            raise NexovaToolError(
                ErrorCode.VALIDATION_ERROR,
                "Los argumentos no cumplen el esquema de la tool.",
                {"errors": _validation_details(exc)},
            ) from exc
        except Exception as exc:
            audit_logger.exception("Fallo no previsto en la tool %s", entry["tool"])
            self._log(entry, start, ErrorCode.INTERNAL_ERROR)
            raise NexovaToolError(ErrorCode.INTERNAL_ERROR, "Error interno del servidor MCP.") from exc
        self._log(entry, start, None)
        return result

    @staticmethod
    def _log(entry: dict[str, Any], start: float, code: ErrorCode | None) -> None:
        log_event(
            **entry,
            result="ok" if code is None else "error",
            error_code=code.value if code else None,
            duration_ms=round((time.perf_counter() - start) * 1000, 1),
        )
