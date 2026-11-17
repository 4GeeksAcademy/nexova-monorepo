"""
Tarea del flujo de RFPs: ejecuta el grafo `rfp_intake` sobre un ticket.

La encola `POST /rfp/tickets` (mensaje ligero: solo `ticket_id`); el PDF ya
está en `data/raw/rfp/` y el ticket en `analizando`. Cada ticket es una tarea
independiente: si una falla, las demás siguen.

Fallos: 3 reintentos con backoff 15/30/60 s, re-ejecutando el grafo desde el
principio (todas sus escrituras son idempotentes). En cada fallo el ticket
guarda `error_message`, así que la UI nunca muestra un `analizando` mudo; al
agotar los reintentos la tarea va a la DLQ (`ObservedTask.on_failure`) y el
ticket se reprocesa con `scripts/rfp_reprocess.py <ticket_id>`.
"""

from __future__ import annotations

import logging
from typing import Any

from rfp import pipeline_bridge
from tasks.base import ObservedTask
from tasks.celery_app import app

MAX_RETRIES = 3
RETRY_BASE_COUNTDOWN_SECONDS = 15

logger = logging.getLogger("tasks")


def retry_countdown(retries: int) -> int:
    return RETRY_BASE_COUNTDOWN_SECONDS * 2**retries


def _record_error(ticket_id: str, message: str) -> None:
    """Deja el fallo visible en el ticket. Nunca lanza."""
    try:
        pipeline_bridge.get_store().record_error(ticket_id, message)
    except Exception:
        logger.exception("ticket=%s no se pudo registrar el error", ticket_id)


def _retry_or_fail(task: ObservedTask, ticket_id: str, exc: Exception, phase: str, script: str) -> None:
    """Deja el fallo visible en el ticket y reintenta; sin reintentos, relanza (→ DLQ)."""
    error = f"{type(exc).__name__}: {exc}"
    if task.request.retries < task.max_retries:
        countdown = retry_countdown(task.request.retries)
        _record_error(
            ticket_id,
            f"{phase}: intento {task.attempt()} de {task.max_retries + 1} falló ({error}); "
            f"reintento en {countdown} s.",
        )
        raise task.retry(exc=exc, countdown=countdown)
    _record_error(
        ticket_id,
        f"{phase} falló tras {task.attempt()} intentos ({error}). Reprocesar con {script} {ticket_id}.",
    )
    raise exc


def enqueue_drafting(ticket_id: str) -> str | None:
    """Encola la Parte 2. Si la cola falla, el ticket queda en `analisis_completo` con el aviso."""
    try:
        return generate_rfp_responses.apply_async(kwargs={"ticket_id": ticket_id}).id
    except Exception:
        logger.exception("ticket=%s no se pudo encolar la generación de borradores", ticket_id)
        _record_error(ticket_id, "Análisis completo, pero no se pudo encolar la generación de borradores: "
                                 "usa 'Regenerar borradores'.")
        return None


@app.task(bind=True, base=ObservedTask, name="rfp.run_intake", max_retries=MAX_RETRIES)
def run_rfp_intake(self: ObservedTask, ticket_id: str) -> dict[str, Any]:
    logger.info("task_id=%s attempt=%d ticket=%s", self.request.id, self.attempt(), ticket_id)
    try:
        result = pipeline_bridge.run_intake(ticket_id, store=pipeline_bridge.get_store())
    except pipeline_bridge.TicketNotFound:
        # Sin ticket no hay nada que reintentar.
        logger.error("task_id=%s ticket=%s no existe", self.request.id, ticket_id)
        raise
    except Exception as exc:
        _retry_or_fail(self, ticket_id, exc, "El análisis", "scripts/rfp_reprocess.py")
    # Parte 2 automática: el handoff de enrutamiento está listo.
    if result.get("status") == "analisis_completo":
        result["drafting_task_id"] = enqueue_drafting(ticket_id)
    return result


@app.task(bind=True, base=ObservedTask, name="rfp.generate_responses", max_retries=MAX_RETRIES)
def generate_rfp_responses(self: ObservedTask, ticket_id: str) -> dict[str, Any]:
    """Parte 2: borrador + evaluación por departamento a partir del handoff de la Parte 1."""
    logger.info("task_id=%s attempt=%d ticket=%s drafting", self.request.id, self.attempt(), ticket_id)
    try:
        return pipeline_bridge.run_drafting(ticket_id, store=pipeline_bridge.get_store())
    except (pipeline_bridge.TicketNotFound, pipeline_bridge.HandoffNotReady):
        # Sin handoff válido de la Parte 1 no hay nada que reintentar.
        logger.error("task_id=%s ticket=%s sin handoff de la Parte 1", self.request.id, ticket_id)
        raise
    except Exception as exc:
        _retry_or_fail(self, ticket_id, exc, "La generación de borradores", "scripts/rfp_drafts.py")
