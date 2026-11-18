"""
Gestor de incidencias centralizado (README + BRIEFING de Nexova).

Módulo independiente del analizador de incidencias (`app/`): tabla propia
(`incident_records`), router propio y sin dependencias con el agente, el MCP
ni RFP. Convive con el módulo anterior bajo `ROUTER_PREFIX`.
"""

from __future__ import annotations

from . import _shared  # noqa: F401  (debe ir antes de cualquier import de nexova_shared)
