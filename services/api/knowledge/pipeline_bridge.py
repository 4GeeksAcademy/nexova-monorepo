"""
Puente hacia `data/process/` y `data/pipelines/`: la lógica RAG vive allí y aquí
solo se importa (mismo patrón que `reporting/pipeline_bridge.py`).

Ninguna de las dos carpetas es un paquete instalable, así que se añaden a
`sys.path`. En Docker se montan en `/data/process` y `/data/pipelines` y las
rutas llegan por `PROCESS_PATH` / `PIPELINES_PATH` (ver docker-compose.yml).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent / ".." / ".." / ".." / "data"
PROCESS_PATH = Path(os.environ.get("PROCESS_PATH", _DATA_DIR / "process")).resolve()
PIPELINES_PATH = Path(os.environ.get("PIPELINES_PATH", _DATA_DIR / "pipelines")).resolve()

# Al final de `sys.path`: nunca debe tapar un módulo de la propia API.
for _path in (PROCESS_PATH, PIPELINES_PATH):
    if str(_path) not in sys.path:
        sys.path.append(str(_path))

# `retrieve` y `generate_answer` por separado los usa el grafo del agente (`agent/`);
# el cliente y el modelo de generación, su enrutador (`agent/routing.py`).
from rag import (  # noqa: E402
    generate_answer,
    generation_model,
    get_llm_client,
    query,
    retrieve,
)

__all__ = ["generate_answer", "generation_model", "get_llm_client", "query", "retrieve"]
