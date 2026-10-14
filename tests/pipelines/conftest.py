"""
Configuración de los tests del pipeline de desempeño de negocio.

- Hace importable `data/pipelines` (`pipeline` y `business_performance`), que
  no es un paquete instalable.
- Bloquea la base de datos en TODOS los tests: si alguno intentara conectarse
  a Postgres/Supabase, falla en el acto. Los tests son unitarios y trabajan
  solo con datos en memoria.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PIPELINES_DIR = Path(__file__).resolve().parents[2] / "data" / "pipelines"
if str(PIPELINES_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINES_DIR))


def _database_blocked() -> None:
    raise RuntimeError("Los tests unitarios no deben tocar la base de datos.")


@pytest.fixture(autouse=True)
def no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    from business_performance import db, extract, load, queries, runs, silence

    for module in (db, extract, load, queries, runs, silence):
        monkeypatch.setattr(module, "get_engine", _database_blocked)
