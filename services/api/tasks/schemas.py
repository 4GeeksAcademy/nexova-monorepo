"""Modelos de respuesta de `/tasks`."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

TaskStatus = Literal["pending", "started", "retry", "success", "failure"]


class TaskStatusResponse(BaseModel):
    task_id: str
    status: TaskStatus
    # Lo que devolvió la tarea; solo con `status = success`.
    result: Any | None = None
    # Último error (`Tipo: mensaje`); con `status = retry` o `failure`.
    error: str | None = None
