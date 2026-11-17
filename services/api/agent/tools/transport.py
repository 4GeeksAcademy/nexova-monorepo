"""
Tipos comunes de las tools del agente y su timeout.

Desde la migración al MCP Server, las tools ya no hacen HTTP directo contra la
API: todo pasa por `agent.tools.mcp_client` (el antiguo `http_get` y el
reenvío del JWT de quien llama se eliminaron). Aquí solo queda:

- `ToolStatus` / `ToolCallError`: cómo se clasifica un fallo para el `ToolResult`.
- `tool_timeout_seconds()`: `AGENT_TOOL_TIMEOUT_SECONDS` (por defecto 4.0 s),
  timeout total de cada llamada a una tool MCP.
"""

from __future__ import annotations

import os
from typing import Literal

ToolStatus = Literal["ok", "not_found", "timeout", "error"]

DEFAULT_TIMEOUT_SECONDS = 4.0


class ToolCallError(Exception):
    """Fallo de transporte o de servicio, ya clasificado para el `ToolResult`."""

    def __init__(self, status: ToolStatus, detail: str) -> None:
        super().__init__(detail)
        self.status: ToolStatus = status
        self.detail = detail


def tool_timeout_seconds() -> float:
    raw = os.environ.get("AGENT_TOOL_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_TIMEOUT_SECONDS
