"""
Nexova — API centralizada (FastAPI)

Un único backend para toda la empresa. Cada dominio de negocio se monta
como un router independiente (ver `app/routers/`); este proyecto añade el
dominio `incidents` (análisis de tickets de soporte).

Ejecución local:
    cd services/api
    pip install -r requirements.txt
    pip install -e ../../packages/incidents-analyzer   # lógica compartida
    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import os
import sys

# --- Hacer importable el paquete compartido sin necesidad de `pip install` ---
try:
    import incidents_analyzer  # noqa: F401
except ImportError:
    _here = os.path.dirname(os.path.abspath(__file__))
    _shared_pkg = os.path.join(_here, "..", "..", "..", "packages", "incidents-analyzer")
    sys.path.insert(0, os.path.abspath(_shared_pkg))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import incidents
from database import create_db_and_tables
from models import HealthResponse
from routers import inventory
from routes import auth, profiles, suppliers, users

app = FastAPI(
    title="Nexova API",
    description="API centralizada de Nexova — soporte, operaciones y más.",
    version="0.1.0",
)


@app.on_event("startup")
def on_startup() -> None:
    create_db_and_tables()

# Orígenes permitidos para el frontend (Next.js en desarrollo).
# En producción, sustituir por el dominio real del backoffice.
ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS", "http://localhost:3000"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(profiles.router)
app.include_router(incidents.router)
app.include_router(suppliers.router)
app.include_router(inventory.router)


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")
