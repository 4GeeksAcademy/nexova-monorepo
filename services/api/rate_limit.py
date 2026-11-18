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

import logging
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


# --- Presupuesto de llamadas al LLM (informe NIST · Protect) --------------------
#
# Cada endpoint que dispara un modelo descuenta de un presupuesto por usuario, para
# que un bucle (un script, una pestaña que reintenta, un cliente abusivo) no
# convierta la factura del proveedor en un incidente. Mismo `RateLimiter` en
# memoria que `/auth`: válido para un proceso de uvicorn (Redis si se escala).
#
# - `LLM_RATE_LIMIT_PER_MINUTE` (10) y `LLM_RATE_LIMIT_PER_DAY` (200): `/agent/query`
#   y `/knowledge/query` comparten ambos contadores.
# - `CHAT_TURNS_PER_MINUTE` (10): turnos del chat WebSocket; descuentan también del
#   presupuesto diario, porque cada turno es una corrida del agente.
# - `RFP_UPLOADS_PER_HOUR` (20): subidas de RFP y regeneraciones de borradores
#   (cada una lanza varios agentes en el worker).

security_logger = logging.getLogger("nexova.security")

llm_per_minute = RateLimiter(int(os.environ.get("LLM_RATE_LIMIT_PER_MINUTE", "10")), 60)
llm_per_day = RateLimiter(int(os.environ.get("LLM_RATE_LIMIT_PER_DAY", "200")), 86_400)
chat_turns = RateLimiter(int(os.environ.get("CHAT_TURNS_PER_MINUTE", "10")), 60)
rfp_jobs = RateLimiter(int(os.environ.get("RFP_UPLOADS_PER_HOUR", "20")), 3_600)

# scope → [(limitador, prefijo de clave)]. El prefijo decide qué scopes comparten contador.
LLM_SCOPES: dict[str, tuple[tuple[RateLimiter, str], ...]] = {
    "agent": ((llm_per_minute, "llm"), (llm_per_day, "llm")),
    "knowledge": ((llm_per_minute, "llm"), (llm_per_day, "llm")),
    "chat": ((chat_turns, "chat"), (llm_per_day, "llm")),
    "rfp": ((rfp_jobs, "rfp"),),
}

LLM_RATE_LIMIT_MESSAGE = "Has alcanzado el límite de consultas al asistente. Inténtalo de nuevo más tarde."

_budget_lock = Lock()


def consume_llm_budget(scope: str, user_id: str) -> int:
    """Descuenta una llamada del presupuesto de `user_id`.

    Devuelve 0 si se permite (y la cuenta en todos sus contadores) o los segundos
    que faltan si algún contador está agotado (y entonces no cuenta nada).
    """
    pairs = [(limiter, f"{prefix}:{user_id}") for limiter, prefix in LLM_SCOPES[scope]]
    with _budget_lock:
        wait = max(limiter.retry_after(key) for limiter, key in pairs)
        if not wait:
            for limiter, key in pairs:
                limiter.hit(key)
    if wait:
        # Sin contenido de la petición: solo quién, dónde y cuánto debe esperar (A09 / RGPD 5.1.c).
        security_logger.warning(
            "llm_rate_limited scope=%s user_id=%s retry_after=%s", scope, user_id, wait,
            extra={"event": "llm_rate_limited", "scope": scope, "user_id": user_id, "retry_after": wait},
        )
    return wait


def enforce_llm_budget(scope: str, user_id: str) -> None:
    """429 con `Retry-After` si `user_id` agotó su presupuesto en `scope`."""
    wait = consume_llm_budget(scope, user_id)
    if wait:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=LLM_RATE_LIMIT_MESSAGE,
            headers={"Retry-After": str(wait)},
        )


def all_limiters() -> tuple[RateLimiter, ...]:
    """Todos los limitadores del proceso (los tests los vacían entre casos)."""
    return (login_failures, reset_requests, reset_attempts, password_changes,
            llm_per_minute, llm_per_day, chat_turns, rfp_jobs)
