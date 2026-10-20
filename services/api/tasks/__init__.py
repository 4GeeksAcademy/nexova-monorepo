"""
Cola de tareas asíncronas (Celery + Redis), ticket #DEV-55.

- `celery_app`: instancia de Celery y su configuración (broker y backend Redis).
- `base`: clase base de las tareas (logs por intento y escritura en la DLQ).
- `reporting`: tareas del dominio de reporting (pipeline semanal).
- `router`: `GET /tasks/{task_id}` para la API.

El worker importa solo `celery_app` y las tareas, nunca `router`: no necesita
FastAPI ni la configuración de autenticación.
"""
