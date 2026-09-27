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

import logging
import os
import sys
import time

# --- Hacer importable el paquete compartido sin necesidad de `pip install` ---
try:
    import incidents_analyzer  # noqa: F401
except ImportError:
    _here = os.path.dirname(os.path.abspath(__file__))
    _shared_pkg = os.path.join(_here, "..", "..", "..", "packages", "incidents-analyzer")
    sys.path.insert(0, os.path.abspath(_shared_pkg))

import random

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.routers import incidents
from database import create_db_and_tables
from models import HealthResponse
from routers import inventory
from routes import auth, profiles, suppliers, telemetry, users
from routes.telemetry import log_telemetry_event

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api.timing")

# Muestreo de api_request_completed (respuestas 2xx/3xx) — 100% en local para
# poder verlo en la verificación manual; se baja en producción vía env var.
API_TELEMETRY_SAMPLE_RATE = float(os.environ.get("API_TELEMETRY_SAMPLE_RATE", "1.0"))

# Rutas de creación de órdenes cuyos 422 de validación Pydantic (que nunca
# llegan al handler de la ruta) deben emitir inbound/outbound_order_rejected.
_ORDER_VALIDATION_TELEMETRY = {
    "/inventory/orders/inbound": "inbound_order_rejected",
    "/inventory/orders/outbound": "outbound_order_rejected",
}

app = FastAPI(
    title="Nexova API",
    description="API centralizada de Nexova — soporte, operaciones y más.",
    version="0.1.0",
)


@app.exception_handler(RequestValidationError)
async def order_validation_telemetry_handler(request: Request, exc: RequestValidationError):
    event_type = _ORDER_VALIDATION_TELEMETRY.get(request.url.path)
    if event_type is not None:
        body = exc.body if isinstance(exc.body, dict) else {}
        log_telemetry_event(
            event_type,
            source="backend",
            session_id=None,
            user_id=None,
            request_id=None,
            properties={
                "office": body.get("office"),
                "product_id": body.get("asset_id"),
                "rejection_reason": "validation_error",
            }
            if event_type == "inbound_order_rejected"
            else {
                "office": body.get("office"),
                "product_id": body.get("asset_id"),
                "requested_quantity": body.get("quantity"),
                "rejection_reason": "validation_error",
            },
        )
    return await request_validation_exception_handler(request, exc)


@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        duration = (time.perf_counter() - start) * 1000
        logger.info(f"{request.method} {request.url.path} → 500 | {duration:.1f}ms")
        log_telemetry_event(
            "api_request_failed",
            source="backend",
            session_id=None,
            user_id=None,
            request_id=None,
            properties={
                "method": request.method,
                "route": request.url.path,
                "status_code": 500,
                "error_type": type(exc).__name__,
            },
        )
        raise

    duration = (time.perf_counter() - start) * 1000  # ms

    logger.info(
        f"{request.method} {request.url.path} → {response.status_code} | {duration:.1f}ms"
    )

    route_template = request.scope.get("route")
    route_path = route_template.path if route_template is not None else request.url.path

    if response.status_code >= 500:
        log_telemetry_event(
            "api_request_failed",
            source="backend",
            session_id=None,
            user_id=None,
            request_id=None,
            properties={
                "method": request.method,
                "route": route_path,
                "status_code": response.status_code,
                "error_type": "HTTPException",
            },
        )
    elif random.random() < API_TELEMETRY_SAMPLE_RATE:
        log_telemetry_event(
            "api_request_completed",
            source="backend",
            session_id=None,
            user_id=None,
            request_id=None,
            properties={
                "method": request.method,
                "route": route_path,
                "status_code": response.status_code,
                "duration_ms": round(duration, 1),
            },
        )

    return response


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
app.include_router(telemetry.router)


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")
