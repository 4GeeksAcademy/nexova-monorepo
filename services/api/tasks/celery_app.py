"""
Instancia de Celery compartida por la API (productor) y el worker (consumidor).

Redis hace de broker y de result backend; la URL llega por `REDIS_URL`, la
misma en la API, el worker y Flower (ver docker-compose.yml).

Worker (proceso aparte de FastAPI, desde `services/api`):
    celery -A tasks.celery_app worker -l INFO -E

La configuración implementa las tres reglas del patrón productor/consumidor:
mensajes ligeros (solo ids y fechas, ver `tasks/reporting.py`), ACK solo tras
terminar y timeout en toda tarea.
"""

from __future__ import annotations

import os

from celery import Celery
from dotenv import load_dotenv

load_dotenv()

DEFAULT_SOFT_TIME_LIMIT_SECONDS = 540
# El límite duro mata el proceso sin pasar por `on_failure` (no habría fila en
# la DLQ): va siempre por detrás del blando, que sí se maneja como un fallo.
HARD_TIME_LIMIT_MARGIN_SECONDS = 60


def _redis_url() -> str:
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        raise RuntimeError(
            "REDIS_URL no está definida: créala en services/api/.env o en el entorno "
            "(p. ej. redis://localhost:6379/0)."
        )
    return redis_url


def soft_time_limit_seconds() -> int:
    """Timeout blando por tarea (`TASK_SOFT_TIME_LIMIT`, por defecto 540 s)."""

    raw = os.environ.get("TASK_SOFT_TIME_LIMIT", str(DEFAULT_SOFT_TIME_LIMIT_SECONDS))
    try:
        seconds = int(raw)
    except ValueError:
        raise RuntimeError(f"TASK_SOFT_TIME_LIMIT debe ser un entero de segundos: {raw!r}")
    if seconds <= 0:
        raise RuntimeError(f"TASK_SOFT_TIME_LIMIT debe ser mayor que 0: {seconds}")
    return seconds


REDIS_URL = _redis_url()
SOFT_TIME_LIMIT_SECONDS = soft_time_limit_seconds()

app = Celery("nexova", broker=REDIS_URL, backend=REDIS_URL, include=["tasks.reporting"])

app.conf.update(
    # --- ACK solo tras éxito -------------------------------------------------
    # El mensaje se confirma al terminar la tarea, no al recibirla: si el worker
    # muere a mitad, Redis lo vuelve a entregar.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Cada proceso reserva un mensaje cada vez: los demás siguen en la cola.
    worker_prefetch_multiplier=1,
    # --- Timeout -------------------------------------------------------------
    # Por defecto, por debajo de los 15 min tras los que el pipeline da por
    # muerta una corrida `running` (STALE_RUN_MINUTES).
    task_soft_time_limit=SOFT_TIME_LIMIT_SECONDS,
    task_time_limit=SOFT_TIME_LIMIT_SECONDS + HARD_TIME_LIMIT_MARGIN_SECONDS,
    # Un mensaje sin ACK vuelve a la cola pasado este tiempo: debe superar al
    # límite duro, o Redis lo entregaría dos veces mientras aún corre.
    broker_transport_options={"visibility_timeout": 3600},
    # --- Estados y resultados ------------------------------------------------
    # Sin esto Celery nunca publica `STARTED` (pasa de PENDING a SUCCESS).
    task_track_started=True,
    result_expires=86400,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
    # Publicar desde la API: con Redis caído, fallar en ~1-2 s (y responder 503)
    # en vez de los ~20 s que tarda kombu con su política por defecto.
    broker_connection_timeout=1,
    task_publish_retry_policy={
        "max_retries": 1,
        "interval_start": 0,
        "interval_step": 0.5,
        "interval_max": 0.5,
    },
    # El result backend se reintenta aparte (~20 intentos de 1 s por defecto) y
    # también al encolar: se acota el tiempo total.
    result_backend_transport_options={"retry_policy": {"timeout": 3.0}},
    # Eventos para Flower (también se activan con `worker -E`).
    worker_send_task_events=True,
    task_send_sent_event=True,
)
