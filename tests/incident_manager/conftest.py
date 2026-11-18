"""
Configuración de los tests del gestor de incidencias (`services/api/incident_manager`).

Monta solo su router sobre un SQLite en memoria (nunca Supabase). La
autenticación se sustituye por un usuario fijo salvo en los tests de acceso.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for path in (
    ROOT / "services" / "api",
    ROOT / "packages" / "shared",
    ROOT / "packages" / "incidents-analyzer",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DATABASE_URL", "postgresql://blocked:blocked@localhost:1/blocked")
os.environ.setdefault("SECRET_KEY", "test-secret")


@pytest.fixture()
def engine():
    from incident_manager.models import IncidentSeedLog, ManagedIncident
    from sqlalchemy.pool import StaticPool
    from sqlmodel import SQLModel, create_engine

    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(
        eng, tables=[ManagedIncident.__table__, IncidentSeedLog.__table__]
    )
    return eng


@pytest.fixture()
def make_client(engine) -> Callable[..., object]:
    from database import get_db
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from incident_manager.router import router
    from models import User
    from security import get_current_user
    from sqlmodel import Session

    def _db() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    def _make(*, role: str | None = "manager", broken_db: bool = False) -> TestClient:
        app = FastAPI()
        app.include_router(router)
        if broken_db:

            def _boom() -> Iterator[Session]:
                raise RuntimeError("password=supersecreta host=db.interno")
                yield  # pragma: no cover

            app.dependency_overrides[get_db] = _boom
        else:
            app.dependency_overrides[get_db] = _db
        if role is not None:
            app.dependency_overrides[get_current_user] = lambda: User(
                id="user-test", email="tester@example.com", hashed_password="x", role=role
            )
        return TestClient(app, raise_server_exceptions=False)

    return _make


VALID_PAYLOAD = {
    "title": "Fallo en el ATS",
    "description": "El ATS no carga las candidaturas nuevas desde esta mañana.",
    "category": "technical_failure",
    "origin": "branch",
    "branch": "valencia_operations",
}


@pytest.fixture()
def payload() -> dict[str, str]:
    return dict(VALID_PAYLOAD)
