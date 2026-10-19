"""Tests unitarios del script nocturno: fecha objetivo, lock, idempotencia, CSV y fallos."""

from __future__ import annotations

import csv
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

TARGET = date(2026, 10, 15)  # jueves

ROWS = [
    {
        "id": "a1",
        "timestamp": datetime(2026, 10, 15, 9, 30, tzinfo=timezone.utc),
        "service": "backoffice",
        "event_type": "inbound_order_created",
        "level": "info",
        "value": 3,
        "message": None,
        "tags": {"office": "valencia", "quantity": 3},
    }
]


@pytest.fixture
def pipeline_ok(script, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout='log\n{\n  "status": "completed",\n  "run_id": "p1"\n}\n', stderr="")

    monkeypatch.setattr(script.subprocess, "run", fake_run)
    monkeypatch.setattr(script, "fetch_telemetry_rows", lambda target_date: ROWS)
    return calls


# --- Fecha objetivo ----------------------------------------------------------


def test_target_date_defaults_to_yesterday_utc(script):
    assert script.resolve_target_date(env={}, today=date(2026, 10, 1)) == date(2026, 9, 30)


def test_target_date_from_env(script):
    assert script.resolve_target_date(env={"TARGET_DATE": "2026-01-15"}) == date(2026, 1, 15)


def test_invalid_target_date_is_rejected(script):
    with pytest.raises(ValueError, match="TARGET_DATE"):
        script.resolve_target_date(env={"TARGET_DATE": "15/01/2026"})


def test_day_window_and_week_start(script):
    start, end = script.day_window(TARGET)
    assert start == datetime(2026, 10, 15, tzinfo=timezone.utc)
    assert end == datetime(2026, 10, 16, tzinfo=timezone.utc)
    assert script.week_start_of(TARGET) == date(2026, 10, 12)
    assert "2026-10-12" in script.pipeline_command(TARGET)


# --- Camino feliz ------------------------------------------------------------


def test_successful_run_exports_csv_and_completes(script, store, pipeline_ok, tmp_path: Path):
    assert script.run(TARGET) == 0
    assert store.statuses() == ["completed"]
    assert len(pipeline_ok) == 1

    with (tmp_path / "telemetry_2026-10-15.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["event_type"] == "inbound_order_created"
    assert rows[0]["timestamp"] == "2026-10-15T09:30:00+00:00"
    assert rows[0]["tags"] == '{"office": "valencia", "quantity": 3}'


def test_existing_csv_is_not_rewritten(script, store, pipeline_ok, tmp_path: Path):
    path = tmp_path / "telemetry_2026-10-15.csv"
    path.write_text("original", encoding="utf-8")

    assert script.run(TARGET) == 0
    assert path.read_text(encoding="utf-8") == "original"
    assert store.statuses() == ["completed"]


# --- Lock e idempotencia -----------------------------------------------------


def test_aborts_when_another_instance_is_processing(script, store, pipeline_ok, tmp_path: Path):
    store.seed("nightly_export", date(2026, 10, 14), "processing")

    assert script.run(TARGET) == 0
    assert store.statuses() == ["processing"]  # no se creó fila nueva
    assert pipeline_ok == []
    assert not (tmp_path / "telemetry_2026-10-15.csv").exists()


def test_losing_the_race_for_processing_aborts(script, store, pipeline_ok, monkeypatch):
    # La comprobación previa no ve el lock, pero otra instancia lo toma justo antes.
    monkeypatch.setattr(script.job_runner, "has_processing_lock", lambda job_name: False)
    store.seed("nightly_export", TARGET, "processing")

    assert script.run(TARGET) == 0
    assert sorted(store.statuses()) == ["failed", "processing"]
    assert pipeline_ok == []


def test_skips_when_day_already_completed(script, store, pipeline_ok, tmp_path: Path):
    store.seed("nightly_export", TARGET, "completed")

    assert script.run(TARGET) == 0
    assert store.statuses() == ["completed"]
    assert pipeline_ok == []
    assert not (tmp_path / "telemetry_2026-10-15.csv").exists()


def test_running_twice_equals_running_once(script, store, pipeline_ok, tmp_path: Path):
    assert script.run(TARGET) == 0
    first_csv = (tmp_path / "telemetry_2026-10-15.csv").read_text(encoding="utf-8")

    assert script.run(TARGET) == 0
    assert store.statuses() == ["completed"]
    assert len(pipeline_ok) == 1
    assert (tmp_path / "telemetry_2026-10-15.csv").read_text(encoding="utf-8") == first_csv


def test_failed_day_is_retried(script, store, pipeline_ok):
    store.seed("nightly_export", TARGET, "failed")

    assert script.run(TARGET) == 0
    assert store.statuses() == ["failed", "completed"]


# --- Fallos: nunca queda `processing` -----------------------------------------


def test_export_failure_marks_failed(script, store, pipeline_ok, monkeypatch, tmp_path: Path):
    def boom(target_date):
        raise ConnectionError("db caída")

    monkeypatch.setattr(script, "fetch_telemetry_rows", boom)

    assert script.run(TARGET) == 1
    [row] = store.rows.values()
    assert row["status"] == "failed"
    assert "ConnectionError: db caída" in row["error_message"]
    assert pipeline_ok == []
    assert list(tmp_path.iterdir()) == []  # ni CSV ni .tmp


def test_pipeline_nonzero_exit_marks_failed(script, store, monkeypatch):
    monkeypatch.setattr(script, "fetch_telemetry_rows", lambda target_date: ROWS)
    monkeypatch.setattr(
        script.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, stdout="", stderr="Traceback\nKeyError: x"),
    )

    assert script.run(TARGET) == 1
    [row] = store.rows.values()
    assert row["status"] == "failed"
    assert "código 1" in row["error_message"]


def test_pipeline_skipped_by_its_own_lock_marks_failed(script, store, monkeypatch):
    monkeypatch.setattr(script, "fetch_telemetry_rows", lambda target_date: ROWS)
    monkeypatch.setattr(
        script.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout='{\n  "status": "skipped"\n}\n', stderr=""),
    )

    assert script.run(TARGET) == 1
    assert store.statuses() == ["failed"]


def test_interrupt_marks_failed_and_propagates(script, store, monkeypatch):
    def interrupted(target_date):
        raise SystemExit(143)

    monkeypatch.setattr(script, "export_csv", interrupted)

    with pytest.raises(SystemExit):
        script.run(TARGET)
    assert store.statuses() == ["failed"]


# --- Resumen del pipeline ----------------------------------------------------


def test_parse_pipeline_summary(script):
    assert script.parse_pipeline_summary('{\n  "status": "completed"\n}') == {"status": "completed"}
    assert script.parse_pipeline_summary('12:00 INFO flow\n{\n  "a": {\n    "b": 1\n  }\n}\n') == {"a": {"b": 1}}
    assert script.parse_pipeline_summary("sin json") is None
