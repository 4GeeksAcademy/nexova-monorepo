"""
Caché en memoria con TTL para respuestas de lectura costosas.

Un único diccionario de proceso, sin dependencias externas. Vive mientras
vive el proceso de la API (se pierde en cada reinicio y no se comparte
entre workers/réplicas) — trade-off aceptado dado el volumen actual del
proyecto. Ver CACHING_REPORT.md para el razonamiento por endpoint.
"""

from __future__ import annotations

import time
from typing import Any, Callable

_store: dict[str, tuple[float, Any]] = {}


def get_or_set(key: str, ttl_seconds: float, compute: Callable[[], Any]) -> Any:
    now = time.monotonic()
    cached = _store.get(key)
    if cached is not None:
        expires_at, value = cached
        if now < expires_at:
            return value

    value = compute()
    _store[key] = (now + ttl_seconds, value)
    return value


def invalidate(prefix: str) -> None:
    for key in [k for k in _store if k.startswith(prefix)]:
        del _store[key]
