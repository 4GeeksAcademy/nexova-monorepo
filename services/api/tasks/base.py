"""
Clase base de las tareas: un log por intento y la DLQ al fallar del todo.

Cada intento deja una línea con `task_id`, intento, estado y duración. Celery
llama a `on_retry` mientras quedan reintentos y a `on_failure` solo cuando ya no
quedan (o el error no se reintenta): es ahí donde la tarea pasa a la DLQ. Un
timeout duro no pasa por `on_failure`; lo cubre `ObservedRequest`.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from billiard.einfo import ExceptionInfo
from celery import Task
from celery.worker.request import Request

from tasks import dead_letters

logger = logging.getLogger("tasks")


class ObservedRequest(Request):
    """Lleva a la DLQ las tareas que mata el límite de tiempo duro.

    El límite duro mata el proceso hijo: Celery marca la tarea como fallida en
    el proceso principal sin llamar a `on_failure` ni reintentar, así que sin
    esto no quedaría fila en la DLQ. Corre en el proceso principal del worker.
    El lock del pipeline, si lo había, lo libera su limpieza de corridas viejas.
    """

    def on_timeout(self, soft: bool, timeout: float) -> None:
        super().on_timeout(soft, timeout)
        if soft:
            return
        attempt = int(self.request_dict.get("retries") or 0) + 1
        error_message = f"Hard time limit ({timeout}s) exceeded"
        logger.error(
            "task_id=%s task=%s attempt=%d status=failure error=TimeLimitExceeded: %s",
            self.id,
            self.name,
            attempt,
            error_message,
        )
        try:
            dead_letters.record_dead_letter(
                task_id=self.id,
                task_name=self.name,
                attempt=attempt,
                error_type="TimeLimitExceeded",
                error_message=error_message,
                traceback=None,
                task_kwargs=dict(self.kwargs or {}),
            )
        except Exception:
            logger.exception("task_id=%s no se pudo registrar en la DLQ", self.id)
        else:
            logger.error("task_id=%s attempt=%d status=dead_lettered", self.id, attempt)


class ObservedTask(Task):
    Request = ObservedRequest

    def attempt(self) -> int:
        """Intento en curso, empezando en 1 (`retries` cuenta solo los reintentos)."""

        return self.request.retries + 1

    def _duration_ms(self) -> float:
        started_at = getattr(self.request, "observed_started_at", None)
        if started_at is None:
            return 0.0
        return (time.monotonic() - started_at) * 1000

    def before_start(self, task_id: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        self.request.observed_started_at = time.monotonic()
        logger.info(
            "task_id=%s task=%s attempt=%d status=started",
            task_id,
            self.name,
            self.attempt(),
        )

    def on_success(
        self, retval: Any, task_id: str, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> None:
        logger.info(
            "task_id=%s task=%s attempt=%d status=success duration_ms=%.1f",
            task_id,
            self.name,
            self.attempt(),
            self._duration_ms(),
        )

    def on_retry(
        self,
        exc: BaseException,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: ExceptionInfo,
    ) -> None:
        logger.warning(
            "task_id=%s task=%s attempt=%d status=retry duration_ms=%.1f error=%s: %s\n%s",
            task_id,
            self.name,
            self.attempt(),
            self._duration_ms(),
            type(exc).__name__,
            exc,
            einfo.traceback,
        )

    def on_failure(
        self,
        exc: BaseException,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: ExceptionInfo,
    ) -> None:
        attempt = self.attempt()
        logger.error(
            "task_id=%s task=%s attempt=%d status=failure duration_ms=%.1f error=%s: %s\n%s",
            task_id,
            self.name,
            attempt,
            self._duration_ms(),
            type(exc).__name__,
            exc,
            einfo.traceback,
        )
        try:
            dead_letters.record_dead_letter(
                task_id=task_id,
                task_name=self.name,
                attempt=attempt,
                error_type=type(exc).__name__,
                error_message=str(exc)[:2000],
                traceback=einfo.traceback,
                task_kwargs=dict(kwargs),
            )
        except Exception:
            # La DLQ no debe tapar el fallo original, que ya está en el log y
            # en el result backend.
            logger.exception("task_id=%s no se pudo registrar en la DLQ", task_id)
        else:
            logger.error("task_id=%s attempt=%d status=dead_lettered", task_id, attempt)
