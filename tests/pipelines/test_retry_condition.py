"""`retry_unless_time_limit`: Prefect no debe reintentar un timeout del worker.

El caso real (#DEV-55): el `SoftTimeLimitExceeded` de Celery cayó dentro de
`extract_events` (retries=3); Prefect lo reintentó y el flow terminó bien, así
que la tarea nunca llegó a sus reintentos ni a la DLQ.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from billiard.exceptions import SoftTimeLimitExceeded
from prefect import flow, task
from prefect.tasks import Task
from prefect.testing.utilities import prefect_test_harness

import pipeline


def _state(data: object) -> SimpleNamespace:
    return SimpleNamespace(data=data)


def test_time_limit_exceptions_are_not_retried():
    assert pipeline.retry_unless_time_limit(None, None, _state(SoftTimeLimitExceeded())) is False

    class TimeLimitExceeded(Exception):
        pass

    assert pipeline.retry_unless_time_limit(None, None, _state(TimeLimitExceeded())) is False


def test_other_failures_keep_retrying():
    assert pipeline.retry_unless_time_limit(None, None, _state(ConnectionError("pooler"))) is True
    assert pipeline.retry_unless_time_limit(None, None, _state(None)) is True


def test_every_task_with_retries_uses_the_condition():
    retrying = [
        obj
        for obj in vars(pipeline).values()
        if isinstance(obj, Task) and obj.retries
    ]

    assert retrying, "el pipeline debería tener tasks con reintentos"
    for prefect_task in retrying:
        assert prefect_task.retry_condition_fn is pipeline.retry_unless_time_limit, prefect_task.name


def test_soft_time_limit_inside_a_retrying_task_fails_the_flow():
    """Reproduce el caso real con Prefect de verdad (servidor temporal local)."""

    calls: list[int] = []

    @task(retries=3, retry_delay_seconds=0, retry_condition_fn=pipeline.retry_unless_time_limit)
    def flaky_extract() -> str:
        calls.append(1)
        if len(calls) == 1:
            # El límite blando de Celery salta una sola vez.
            raise SoftTimeLimitExceeded()
        return "ok"

    @flow
    def demo_flow() -> str:
        return flaky_extract()

    # El harness levanta y para su propio servidor dentro del test.
    with prefect_test_harness(), pytest.raises(SoftTimeLimitExceeded):
        demo_flow()
    assert len(calls) == 1
