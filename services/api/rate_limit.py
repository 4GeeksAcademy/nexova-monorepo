"""
Límite de intentos en memoria para los endpoints de autenticación
(auditoría OWASP, hallazgo H1 — A07 Identification and Authentication Failures).

Ventana deslizante por clave (`login:<ip>:<email>`, `forgot:<email>`…). Basta
para un solo proceso de uvicorn, como el `ChatHub`; con varios workers habría
que moverlo a Redis detrás de la misma interfaz. Configurable por entorno:

- `AUTH_MAX_FAILED_LOGINS` (5) fallos de login por IP+email en
  `AUTH_LOCKOUT_SECONDS` (900).
- `AUTH_MAX_RESET_REQUESTS` (3) peticiones de reset por email o IP en la misma ventana.
"""

from __future__ import annotations

import math
import os
import time
from collections import deque
from threading import Lock

from fastapi import HTTPException, Request, status


class RateLimiter:
    def __init__(self, max_attempts: int, window_seconds: float, clock=time.monotonic) -> None:
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        return hits

    def retry_after(self, key: str) -> int:
        """Segundos hasta el próximo intento permitido; 0 si no está bloqueado."""
        with self._lock:
            now = self._clock()
            hits = self._prune(key, now)
            if len(hits) < self.max_attempts:
                return 0
            return max(1, math.ceil(self.window - (now - hits[0])))

    def hit(self, key: str) -> None:
        with self._lock:
            self._prune(key, self._clock()).append(self._clock())

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def check(self, *keys: str) -> None:
        """429 con `Retry-After` si cualquiera de las claves agotó sus intentos."""
        wait = max((self.retry_after(k) for k in keys), default=0)
        if wait:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Demasiados intentos. Inténtalo de nuevo más tarde.",
                headers={"Retry-After": str(wait)},
            )

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()


_WINDOW = float(os.environ.get("AUTH_LOCKOUT_SECONDS", "900"))
login_failures = RateLimiter(int(os.environ.get("AUTH_MAX_FAILED_LOGINS", "5")), _WINDOW)
reset_requests = RateLimiter(int(os.environ.get("AUTH_MAX_RESET_REQUESTS", "3")), _WINDOW)
reset_attempts = RateLimiter(10, _WINDOW)  # tokens de reset probados por IP
password_changes = RateLimiter(5, _WINDOW)  # contraseña actual incorrecta por usuario


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
