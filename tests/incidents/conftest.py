"""
Configuración de los tests del gestor de incidentes (`services/api/app/`).

- Hace importables `services/api` (módulos planos, como en Docker) y el
  paquete compartido `packages/incidents-analyzer`.
- Monta solo el router de incidencias sobre un SQLite en memoria: nunca se
  conecta a Supabase. La autenticación se sustituye por un usuario fijo,
  salvo en el test que comprueba el 401.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT / "services" / "api", ROOT / "packages" / "incidents-analyzer"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DATABASE_URL", "postgresql://blocked:blocked@localhost:1/blocked")
os.environ.setdefault("SECRET_KEY", "test-secret")


@pytest.fixture()
def engine():
    from sqlalchemy.pool import StaticPool
    from sqlmodel import SQLModel, create_engine

    from models import Incident

    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(eng, tables=[Incident.__table__])
    return eng


@pytest.fixture()
def seed(engine):
    """Inserta tickets de ejemplo con el formato real (`NXV-NNNNNN`)."""
    from sqlmodel import Session

    from models import Incident

    def _seed(*rows: tuple[str, str]) -> None:
        with Session(engine) as session:
            for ticket_id, status in rows:
                session.add(
                    Incident(
                        ticket_id=ticket_id,
                        date=date(2026, 11, 1),
                        client_company="Acme Corp",
                        category="TECHNICAL",
                        description="No funciona el acceso VPN",
                        agent_id="AG-01",
                        status=status,
                        updated_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
                    )
                )
            session.commit()

    return _seed


@pytest.fixture()
def make_client(engine):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlmodel import Session

    from app.routers import incidents
    from database import get_db
    from models import User
    from security import get_current_user

    def _db() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    def _make(*, authenticated: bool = True) -> TestClient:
        app = FastAPI()
        app.include_router(incidents.router)
        app.dependency_overrides[get_db] = _db
        if authenticated:
            app.dependency_overrides[get_current_user] = lambda: User(
                id="user-test", email="tester@example.com", hashed_password="x"
            )
        return TestClient(app)

    return _make
