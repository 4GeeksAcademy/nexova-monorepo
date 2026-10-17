"""
Configuración de los tests del script nocturno (`scripts/nightly_export.py`).

- Carga el script como módulo (`scripts/` no es un paquete).
- Bloquea la base de datos en TODOS los tests: `job_runner` se sustituye por
  un almacén en memoria y cualquier `get_engine()` real falla en el acto.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "nightly_export.py"


def _database_blocked() -> None:
    raise RuntimeError("Los tests unitarios no deben tocar la base de datos.")


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("nightly_export", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["nightly_export"] = module
    spec.loader.exec_module(module)
    return module


class FakeJobRuns:
    """`job_runs` en memoria con la misma semántica de lock que el índice parcial."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def seed(self, job_name: str, target_date: date, status: str) -> str:
        run_id = f"seed-{len(self.rows)}"
        self.rows[run_id] = {"job_name": job_name, "target_date": target_date, "status": status}
        return run_id

    def statuses(self) -> list[str]:
        return [row["status"] for row in self.rows.values()]

    def release_stale_locks(self, job_name: str) -> int:
        return 0

    def has_processing_lock(self, job_name: str) -> bool:
        return any(r["job_name"] == job_name and r["status"] == "processing" for r in self.rows.values())

    def has_completed_for_date(self, job_name: str, target_date: date) -> bool:
        return any(
            r["job_name"] == job_name and r["target_date"] == target_date and r["status"] == "completed"
            for r in self.rows.values()
        )

    def create_run(self, job_name: str, target_date: date) -> str:
        run_id = f"run-{len(self.rows)}"
        self.rows[run_id] = {"job_name": job_name, "target_date": target_date, "status": "pending"}
        return run_id

    def mark_processing(self, run_id: str) -> bool:
        if self.has_processing_lock(self.rows[run_id]["job_name"]):
            self.mark_failed(run_id, "lock")
            return False
        self.rows[run_id]["status"] = "processing"
        return True

    def mark_completed(self, run_id: str) -> None:
        self.rows[run_id]["status"] = "completed"

    def mark_failed(self, run_id: str, error_message: str) -> None:
        self.rows[run_id].update(status="failed", error_message=error_message)


@pytest.fixture
def script() -> ModuleType:
    return _load_script()


@pytest.fixture
def store(script: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeJobRuns:
    fake = FakeJobRuns()
    for name in (
        "release_stale_locks",
        "has_processing_lock",
        "has_completed_for_date",
        "create_run",
        "mark_processing",
        "mark_completed",
        "mark_failed",
    ):
        monkeypatch.setattr(script.job_runner, name, getattr(fake, name))
    monkeypatch.setattr(script, "get_engine", _database_blocked)
    monkeypatch.setattr(script, "RAW_DIR", tmp_path)
    return fake
