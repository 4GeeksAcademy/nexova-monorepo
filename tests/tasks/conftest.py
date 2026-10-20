"""
Configuración de los tests de la cola de tareas (`services/api/tasks/`).

- Hace importables los módulos de `services/api` (se importan planos, como en
  la imagen Docker) y define las variables que exigen al importarse.
- Las tareas corren en modo eager (en el propio proceso, sin Redis ni worker).
- Bloquea la base de datos en TODOS los tests: el pipeline y la DLQ se
  sustituyen por dobles en memoria.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

API_DIR = Path(__file__).resolve().parents[2] / "services" / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

# Nada de esto se conecta: el engine es perezoso y las tareas corren eager.
os.environ.setdefault("REDIS_URL", "redis://localhost:6399/0")
os.environ.setdefault("DATABASE_URL", "postgresql://blocked:blocked@localhost:1/blocked")
os.environ.setdefault("SECRET_KEY", "test-secret")


def _database_blocked() -> None:
    raise RuntimeError("Los tests unitarios no deben tocar la base de datos.")


class FakeDeadLetters:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def record_dead_letter(self, **row: Any) -> None:
        self.rows.append(row)


@pytest.fixture(autouse=True)
def eager_celery() -> Any:
    from tasks.celery_app import app

    app.conf.task_always_eager = True
    app.conf.task_eager_propagates = False
    yield app
    app.conf.task_always_eager = False


@pytest.fixture(autouse=True)
def no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    from business_performance import db

    from tasks import dead_letters

    monkeypatch.setattr(db, "get_engine", _database_blocked)
    monkeypatch.setattr(dead_letters, "get_engine", _database_blocked)


@pytest.fixture
def dlq(monkeypatch: pytest.MonkeyPatch) -> FakeDeadLetters:
    from tasks import dead_letters

    fake = FakeDeadLetters()
    monkeypatch.setattr(dead_letters, "record_dead_letter", fake.record_dead_letter)
    return fake
