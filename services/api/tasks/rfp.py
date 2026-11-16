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


def enqueue_approvals(ticket_id: str, department_ids: list[str] | None = None) -> dict[str, str | None]:
    """Parte 3: una tarea por departamento activo, para que cada rama avance y se pause sola."""
    if department_ids is None:
        ticket = pipeline_bridge.get_store().get_ticket(ticket_id)
        department_ids = pipeline_bridge.active_departments(ticket) if ticket else []
    task_ids: dict[str, str | None] = {}
    for department_id in department_ids:
        try:
            task_ids[department_id] = run_approval_branch.apply_async(
                kwargs={"ticket_id": ticket_id, "department_id": department_id}).id
        except Exception:
            logger.exception("ticket=%s dept=%s no se pudo encolar la aprobación", ticket_id, department_id)
            _record_error(ticket_id, f"Borradores listos, pero no se pudo abrir la aprobación de "
                                     f"'{department_id}': usa 'Abrir aprobaciones'.")
            task_ids[department_id] = None
    return task_ids


@app.task(bind=True, base=ObservedTask, name="rfp.generate_responses", max_retries=MAX_RETRIES)
def generate_rfp_responses(self: ObservedTask, ticket_id: str) -> dict[str, Any]:
    """Parte 2: borrador + evaluación por departamento a partir del handoff de la Parte 1."""
    logger.info("task_id=%s attempt=%d ticket=%s drafting", self.request.id, self.attempt(), ticket_id)
    try:
        result = pipeline_bridge.run_drafting(ticket_id, store=pipeline_bridge.get_store())
    except (pipeline_bridge.TicketNotFound, pipeline_bridge.HandoffNotReady):
        # Sin handoff válido de la Parte 1 no hay nada que reintentar.
        logger.error("task_id=%s ticket=%s sin handoff de la Parte 1", self.request.id, ticket_id)
        raise
    except Exception as exc:
        _retry_or_fail(self, ticket_id, exc, "La generación de borradores", "scripts/rfp_drafts.py")
    # Parte 3 automática: cada sección entra en su pausa de aprobación humana.
    result["approval_task_ids"] = enqueue_approvals(ticket_id, list(result.get("sections") or {}))
    return result


@app.task(bind=True, base=ObservedTask, name="rfp.run_approval_branch", max_retries=MAX_RETRIES)
def run_approval_branch(self: ObservedTask, ticket_id: str, department_id: str) -> dict[str, Any]:
    """Parte 3: abre la rama de un departamento y la ejecuta hasta su pausa humana (`interrupt`)."""
    logger.info("task_id=%s attempt=%d ticket=%s dept=%s approval", self.request.id, self.attempt(), ticket_id,
                department_id)
    try:
        return pipeline_bridge.run_start_approval(ticket_id, department_id, pipeline_bridge.get_store())
    except (pipeline_bridge.TicketNotFound, pipeline_bridge.ApprovalNotReady):
        logger.error("task_id=%s ticket=%s sin handoff de la Parte 2", self.request.id, ticket_id)
        raise
    except Exception as exc:
        _retry_or_fail(self, ticket_id, exc, f"La apertura de la aprobación de '{department_id}'",
                       f"scripts/rfp_approve.py start --department {department_id}")


@app.task(bind=True, base=ObservedTask, name="rfp.resume_approval_branch", max_retries=MAX_RETRIES)
def resume_approval_branch(self: ObservedTask, ticket_id: str, department_id: str,
                           decision: dict[str, Any]) -> dict[str, Any]:
    """Parte 3: reanuda la rama en su pausa con la decisión humana ya validada por la API.

    En un reintento la decisión puede haber entrado ya al grafo: `resume_approval`
    continúa entonces desde el último checkpoint en vez de repetirla.
    """
    logger.info("task_id=%s attempt=%d ticket=%s dept=%s resume decision=%s", self.request.id, self.attempt(),
                ticket_id, department_id, decision.get("decision"))
    store = pipeline_bridge.get_store()
    try:
        return pipeline_bridge.run_resume_approval(ticket_id, department_id, decision, store)
    except (pipeline_bridge.TicketNotFound, pipeline_bridge.ApprovalNotPending, pipeline_bridge.InvalidDecision) as exc:
        logger.error("task_id=%s ticket=%s dept=%s decisión no aplicable", self.request.id, ticket_id,
                     department_id, exc_info=True)
        _release_decision(ticket_id, department_id, f"La decisión no se pudo aplicar: {exc}")
        raise
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            _release_decision(ticket_id, department_id, None)
        _retry_or_fail(self, ticket_id, exc, f"La reanudación de la aprobación de '{department_id}'",
                       f"scripts/rfp_approve.py resume --department {department_id}")


def _release_decision(ticket_id: str, department_id: str, message: str | None) -> None:
    """Si la rama sigue en pausa, la sección vuelve a `pendiente` para poder decidir de nuevo. Nunca lanza."""
    try:
        if pipeline_bridge.run_branch_paused(ticket_id, department_id, pipeline_bridge.get_store()):
            pipeline_bridge.get_store().set_approval_status(ticket_id, department_id, "pendiente")
        if message:
            _record_error(ticket_id, message)
    except Exception:
        logger.exception("ticket=%s dept=%s no se pudo liberar la decisión", ticket_id, department_id)
