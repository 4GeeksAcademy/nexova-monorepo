"""
Almacenamiento en memoria del último resultado de análisis.

Para este proyecto (demo / desarrollo) basta con guardar el último
`summary` en un objeto en memoria del proceso. En producción esto se
sustituiría por una tabla en base de datos o un almacenamiento por
sesión/usuario, pero la interfaz (`get_last_summary` / `set_last_summary`)
se mantendría igual.
"""

from __future__ import annotations

from threading import Lock
from typing import Any, Optional

_lock = Lock()
# Un resumen por usuario: el export de una persona nunca devuelve el análisis de
# otra (auditoría OWASP, hallazgo C2).
_last_summaries: dict[str, dict[str, Any]] = {}


def set_last_summary(summary: dict[str, Any], *, owner: str) -> None:
    with _lock:
        _last_summaries[owner] = summary


def get_last_summary(*, owner: str) -> Optional[dict[str, Any]]:
    with _lock:
        return _last_summaries.get(owner)
