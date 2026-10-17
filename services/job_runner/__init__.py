"""
Control de estado de jobs en segundo plano (`public.job_runs`).

Lo usa `scripts/nightly_export.py`. Es independiente de la API: solo depende
de SQLAlchemy y `DATABASE_URL`, nunca de FastAPI.
"""

from .runs import (
    COMPLETED,
    FAILED,
    PENDING,
    PROCESSING,
    create_run,
    has_completed_for_date,
    has_processing_lock,
    mark_completed,
    mark_failed,
    mark_processing,
    release_stale_locks,
)

__all__ = [
    "COMPLETED",
    "FAILED",
    "PENDING",
    "PROCESSING",
    "create_run",
    "has_completed_for_date",
    "has_processing_lock",
    "mark_completed",
    "mark_failed",
    "mark_processing",
    "release_stale_locks",
]
