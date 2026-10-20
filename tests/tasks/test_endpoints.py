"""`POST /reporting/pipeline-runs` (productor) y `GET /tasks/{task_id}`."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from reporting import pipeline_bridge
from reporting import router as reporting_router
from security import get_current_user
from tasks import router as tasks_router

WINDOW = {
    "window_start": datetime(2026, 10, 5, tzinfo=timezone.utc),
    "window_end": datetime(2026, 10, 19, tzinfo=timezone.utc),
}


def _client(authenticated: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(reporting_router.router)
    app.include_router(tasks_router.router)
    if authenticated:
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="user-1")
    return TestClient(app)


@pytest.fixture
def released(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(
        pipeline_bridge,
        "fail_manual_run",
        lambda run_id, message: seen.append(run_id) or True,
    )
    return seen


# --- POST /reporting/pipeline-runs --------------------------------------------


def test_trigger_enqueues_and_returns_202_with_task_id(monkeypatch, released):
    monkeypatch.setattr(
        pipeline_bridge, "start_manual_run", lambda *_: {"run_id": "run-1", **WINDOW}
    )
    sent: list[dict[str, Any]] = []

    def fake_apply_async(kwargs: dict[str, Any]) -> Any:
        sent.append(kwargs)
        return SimpleNamespace(id="task-123")

    monkeypatch.setattr(
        reporting_router.run_weekly_performance_pipeline, "apply_async", fake_apply_async
    )

    response = _client().post(
        "/reporting/pipeline-runs", json={"week_start": "2026-10-12", "lookback_weeks": 2}
    )

    assert response.status_code == 202
    body = response.json()
    assert body["task_id"] == "task-123"
    assert body["run_id"] == "run-1"
    assert body["status"] == "queued"
    # El mensaje solo lleva identificadores y parámetros.
    assert sent == [{"run_id": "run-1", "week_start": "2026-10-12", "lookback_weeks": 2}]
    assert released == []


def test_trigger_returns_409_when_the_lock_is_taken(monkeypatch):
    monkeypatch.setattr(pipeline_bridge, "start_manual_run", lambda *_: None)

    response = _client().post("/reporting/pipeline-runs")

    assert response.status_code == 409


def test_trigger_releases_the_lock_when_the_queue_is_down(monkeypatch, released):
    monkeypatch.setattr(
        pipeline_bridge, "start_manual_run", lambda *_: {"run_id": "run-1", **WINDOW}
    )

    def broker_down(kwargs: dict[str, Any]) -> Any:
        raise ConnectionError("redis caído")

    monkeypatch.setattr(reporting_router.run_weekly_performance_pipeline, "apply_async", broker_down)

    response = _client().post("/reporting/pipeline-runs")

    assert response.status_code == 503
    assert released == ["run-1"]


# --- GET /tasks/{task_id} -----------------------------------------------------


class FakeAsyncResult:
    def __init__(self, state: str, result: Any) -> None:
        self.state = state
        self.result = result


def _fake_backend(monkeypatch: pytest.MonkeyPatch, state: str, result: Any = None) -> None:
    monkeypatch.setattr(
        tasks_router, "AsyncResult", lambda task_id, app: FakeAsyncResult(state, result)
    )


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("PENDING", "pending"),
        ("RECEIVED", "pending"),
        ("STARTED", "started"),
        ("RETRY", "retry"),
        ("SUCCESS", "success"),
        ("FAILURE", "failure"),
        ("REVOKED", "failure"),
    ],
)
def test_task_status_maps_celery_states(monkeypatch, state, expected):
    _fake_backend(monkeypatch, state)
    task_id = str(uuid4())

    response = _client().get(f"/tasks/{task_id}")

    assert response.status_code == 200
    assert response.json()["task_id"] == task_id
    assert response.json()["status"] == expected


def test_task_status_includes_result_on_success(monkeypatch):
    _fake_backend(monkeypatch, "SUCCESS", {"run_id": "run-1", "status": "completed"})

    body = _client().get(f"/tasks/{uuid4()}").json()

    assert body["result"] == {"run_id": "run-1", "status": "completed"}
    assert body["error"] is None


def test_task_status_includes_error_message_on_failure(monkeypatch):
    _fake_backend(monkeypatch, "FAILURE", RuntimeError("supabase caído"))

    body = _client().get(f"/tasks/{uuid4()}").json()

    assert body["status"] == "failure"
    assert body["result"] is None
    assert body["error"] == "RuntimeError: supabase caído"


def test_task_status_returns_503_when_the_backend_is_down(monkeypatch):
    class Unreachable:
        @property
        def state(self) -> str:
            raise ConnectionError("redis caído")

    monkeypatch.setattr(tasks_router, "AsyncResult", lambda task_id, app: Unreachable())

    assert _client().get(f"/tasks/{uuid4()}").status_code == 503


def test_task_status_validates_the_id_and_requires_auth():
    assert _client().get("/tasks/not-a-uuid").status_code == 422
    assert _client(authenticated=False).get(f"/tasks/{uuid4()}").status_code == 401
