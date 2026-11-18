"""
nexova_shared — lógica de dominio compartida entre la API y los scripts.

Hoy contiene el dominio del gestor de incidencias (valores permitidos del
BRIEFING y transformación CSV → modelo). El script `scripts/seed_incidents.py`
y la API (`services/api/incident_manager`) importan de aquí para no duplicar
reglas de validación.
"""

from __future__ import annotations

from ._bootstrap import ensure_analyzer_importable

ensure_analyzer_importable()
