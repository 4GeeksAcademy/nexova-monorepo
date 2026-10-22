"""
Puente hacia `data/pipelines/`: la lógica ETL vive allí y aquí solo se importa.

`data/pipelines` no es un paquete instalable, así que se añade a `sys.path`
(mismo recurso que `incidents_analyzer` en `app/main.py`). No se importa como
`data.pipelines` porque `services/api/data/` (TinyDB) ya ocupa el nombre `data`.
En Docker la carpeta se monta en `/data/pipelines` y la ruta llega por
`PIPELINES_PATH` (ver docker-compose.yml).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_DEFAULT_PIPELINES_PATH = (
    Path(__file__).resolve().parent / ".." / ".." / ".." / "data" / "pipelines"
)
PIPELINES_PATH = Path(os.environ.get("PIPELINES_PATH", _DEFAULT_PIPELINES_PATH)).resolve()

# Al final de `sys.path`: nunca debe tapar un módulo de la propia API.
if str(PIPELINES_PATH) not in sys.path:
    sys.path.append(str(PIPELINES_PATH))

from pipeline import (  # noqa: E402
    fail_manual_run,
    get_latest_run,
    query_weekly_kpis,
    run_weekly_performance_pipeline,
    start_manual_run,
)

__all__ = [
    "fail_manual_run",
    "get_latest_run",
    "query_weekly_kpis",
    "run_weekly_performance_pipeline",
    "start_manual_run",
]
