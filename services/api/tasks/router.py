"""
Router: /tasks

Estado de una tarea encolada, leído del result backend (Redis). Lo consulta el
cliente con el `task_id` que devolvió el `202` del endpoint que la encoló.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from celery.result import AsyncResult
from fastapi import APIRouter, Depends, HTTPException, status

from models import User
from security import get_current_user
from tasks.celery_app import app as celery_app
from tasks.schemas import TaskStatus, TaskStatusResponse

router = APIRouter(prefix="/tasks", tags=["tasks"])
logger = logging.getLogger(__name__)

# Estados de Celery → estado público. RECEIVED (recibida por un worker pero sin
# empezar) sigue siendo `pending`; REVOKED/REJECTED son fallos definitivos.
_STATUS_BY_STATE: dict[str, TaskStatus] = {
    "PENDING": "pending",
    "RECEIVED": "pending",
    "STARTED": "started",
    "RETRY": "retry",
    "SUCCESS": "success",
    "FAILURE": "failure",
    "REVOKED": "failure",
    "REJECTED": "failure",
}


@router.get("/{task_id}", response_model=TaskStatusResponse)
def get_task_status(
    task_id: UUID, current_user: User = Depends(get_current_user)
) -> dict[str, Any]:
    """Estado actual de la tarea y su resultado cuando termina.

    Celery no distingue un `task_id` desconocido de uno en cola: ambos son
    `pending` (y también uno cuyo resultado ya caducó, a las 24 h).
    """

    result = AsyncResult(str(task_id), app=celery_app)
    try:
        state = result.state
        payload = result.result
    except Exception:
        logger.exception("Could not read task %s from the result backend", task_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Task backend unavailable.",
        )

    task_status = _STATUS_BY_STATE.get(state, "pending")
    response: dict[str, Any] = {
        "task_id": str(task_id),
        "status": task_status,
        "result": None,
        "error": None,
    }
    if task_status == "success":
        response["result"] = payload
    elif task_status in ("retry", "failure") and isinstance(payload, BaseException):
        # Solo el mensaje: el traceback queda en el log del worker y en la DLQ.
        response["error"] = f"{type(payload).__name__}: {payload}"
    return response
