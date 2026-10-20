"""Tarea `reporting.run_weekly_performance_pipeline`: éxito, reintentos, timeout y DLQ."""

from __future__ import annotations

from typing import Any

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from celery.worker.request import Request

from reporting import pipeline_bridge
from tasks import celery_app as celery_module
from tasks import reporting as reporting_tasks
from tasks.base import ObservedRequest, ObservedTask

KWARGS = {"run_id": "api-run", "week_start": "2026-10-12", "lookback_weeks": 2}


class FakePipeline:
    """`pipeline_bridge` en memoria: cada intento falla con lo que diga `outcomes`."""

    def __init__(self, outcomes: list[BaseException | None]) -> None:
        self.outcomes = list(outcomes)
        self.ran_with: list[str] = []
        self.opened: list[str] = []
        self.released: list[str] = []
        self.lock_busy = False

    def start_manual_run(self, week_start: Any, lookback_weeks: int) -> dict[str, Any] | None:
        if self.lock_busy:
            return None
        run_id = f"retry-run-{len(self.opened) + 1}"
        self.opened.append(run_id)
        return {"run_id": run_id}

    def run_weekly_performance_pipeline(self, **kwargs: Any) -> dict[str, Any]:
        self.ran_with.append(kwargs["run_id"])
        outcome = self.outcomes.pop(0)
        if outcome is not None:
            raise outcome
        return {"run_id": kwargs["run_id"], "status": "completed", "rows_read": 7}

    def fail_manual_run(self, run_id: str, error_message: str) -> bool:
        self.released.append(run_id)
        return True


@pytest.fixture
def countdowns(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Espía los `countdown` que pasa la tarea a `retry` (en eager no se esperan)."""

    seen: list[int] = []
    original = ObservedTask.retry

    def spy(self: ObservedTask, *args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["countdown"])
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ObservedTask, "retry", spy)
    return seen


def _pipeline(monkeypatch: pytest.MonkeyPatch, outcomes: list[BaseException | None]) -> FakePipeline:
    fake = FakePipeline(outcomes)
    for name in ("start_manual_run", "run_weekly_performance_pipeline", "fail_manual_run"):
        monkeypatch.setattr(pipeline_bridge, name, getattr(fake, name))
    return fake


def test_first_attempt_uses_the_run_opened_by_the_api(monkeypatch, dlq, countdowns):
    pipeline = _pipeline(monkeypatch, [None])

    result = reporting_tasks.run_weekly_performance_pipeline.apply(kwargs=KWARGS)

    assert result.successful()
    assert result.result == {"run_id": "api-run", "status": "completed", "rows_read": 7}
    assert pipeline.ran_with == ["api-run"]
    assert pipeline.opened == []
    assert countdowns == []
    assert dlq.rows == []


def test_retries_with_exponential_backoff_and_a_new_lock_per_retry(monkeypatch, dlq, countdowns):
    pipeline = _pipeline(monkeypatch, [RuntimeError("boom")] * 4)

    result = reporting_tasks.run_weekly_performance_pipeline.apply(kwargs=KWARGS)

    assert result.failed()
    # Nunca reintenta en el acto: la espera crece en cada intento.
    assert countdowns == [15, 30, 60, 120]  # el último no se usa: ya no quedan reintentos
    assert pipeline.ran_with == ["api-run", "retry-run-1", "retry-run-2", "retry-run-3"]
    assert pipeline.released == pipeline.ran_with


def test_dead_letter_after_max_retries(monkeypatch, dlq):
    _pipeline(monkeypatch, [RuntimeError("supabase caído")] * 4)

    result = reporting_tasks.run_weekly_performance_pipeline.apply(kwargs=KWARGS)

    assert result.failed()
    assert len(dlq.rows) == 1
    row = dlq.rows[0]
    assert row["task_id"] == result.id
    assert row["task_name"] == "reporting.run_weekly_performance_pipeline"
    assert row["attempt"] == reporting_tasks.MAX_RETRIES + 1
    assert row["error_type"] == "RuntimeError"
    assert row["error_message"] == "supabase caído"
    assert "RuntimeError" in row["traceback"]
    # Solo los argumentos ligeros del mensaje.
    assert row["task_kwargs"] == KWARGS


def test_soft_time_limit_is_retried_and_releases_the_lock(monkeypatch, dlq, countdowns):
    pipeline = _pipeline(monkeypatch, [SoftTimeLimitExceeded(), None])

    result = reporting_tasks.run_weekly_performance_pipeline.apply(kwargs=KWARGS)

    assert result.successful()
    assert pipeline.released == ["api-run"]
    assert pipeline.ran_with == ["api-run", "retry-run-1"]
    assert countdowns == [15]
    assert dlq.rows == []


def test_busy_lock_on_retry_counts_as_a_failure(monkeypatch, dlq):
    pipeline = _pipeline(monkeypatch, [RuntimeError("boom")])
    pipeline.lock_busy = True

    result = reporting_tasks.run_weekly_performance_pipeline.apply(kwargs=KWARGS)

    assert result.failed()
    assert pipeline.ran_with == ["api-run"]
    assert dlq.rows[0]["error_type"] == "PipelineLockBusy"


def test_dead_letter_write_failure_does_not_hide_the_original_error(monkeypatch):
    _pipeline(monkeypatch, [RuntimeError("boom")] * 4)

    def broken_dlq(**row: Any) -> None:
        raise RuntimeError("DLQ caída")

    from tasks import dead_letters

    monkeypatch.setattr(dead_letters, "record_dead_letter", broken_dlq)

    result = reporting_tasks.run_weekly_performance_pipeline.apply(kwargs=KWARGS)

    assert result.failed()
    assert str(result.result) == "boom"


def test_time_limits_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("TASK_SOFT_TIME_LIMIT", "1")
    assert celery_module.soft_time_limit_seconds() == 1

    monkeypatch.delenv("TASK_SOFT_TIME_LIMIT")
    assert celery_module.soft_time_limit_seconds() == 540

    for invalid in ("0", "abc"):
        monkeypatch.setenv("TASK_SOFT_TIME_LIMIT", invalid)
        with pytest.raises(RuntimeError):
            celery_module.soft_time_limit_seconds()

    conf = celery_module.app.conf
    assert conf.task_time_limit == conf.task_soft_time_limit + 60
    assert conf.task_acks_late is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.task_track_started is True


# --- Timeout duro (ObservedRequest) -------------------------------------------


def _killed_request(retries: int) -> ObservedRequest:
    """`Request` mínimo: solo los campos que lee `on_timeout` (sin mensaje AMQP)."""

    request = ObservedRequest.__new__(ObservedRequest)
    request.id = "task-hard"
    request.name = "reporting.run_weekly_performance_pipeline"
    request._request_dict = {"retries": retries}
    request._kwargs = KWARGS
    return request


@pytest.fixture
def parent_on_timeout(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """Sustituye el `on_timeout` de Celery (marca el fallo en el backend y hace ACK)."""

    seen: list[bool] = []
    monkeypatch.setattr(Request, "on_timeout", lambda self, soft, timeout: seen.append(soft))
    return seen


def test_hard_time_limit_goes_to_the_dead_letter_queue(dlq, parent_on_timeout):
    _killed_request(retries=2).on_timeout(soft=False, timeout=61)

    assert parent_on_timeout == [False]
    assert dlq.rows == [
        {
            "task_id": "task-hard",
            "task_name": "reporting.run_weekly_performance_pipeline",
            "attempt": 3,
            "error_type": "TimeLimitExceeded",
            "error_message": "Hard time limit (61s) exceeded",
            "traceback": None,
            "task_kwargs": KWARGS,
        }
    ]


def test_soft_time_limit_does_not_dead_letter_from_the_parent(dlq, parent_on_timeout):
    # El blando lo maneja la propia tarea (reintento); el padre solo avisa.
    _killed_request(retries=0).on_timeout(soft=True, timeout=1)

    assert parent_on_timeout == [True]
    assert dlq.rows == []


def test_tasks_use_the_observed_request():
    assert reporting_tasks.run_weekly_performance_pipeline.Request is ObservedRequest
