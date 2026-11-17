"""
Limpieza periódica de la memoria del agente (ver docs/agent/memory-design.md).

    uv run --project services/api python scripts/agent_memory_consolidate.py

- Descarta (con auditoría) las propuestas pendientes de más de 24 h.
- Expira los hechos sin uso en 90 días y aplica los topes por línea de negocio
  + categoría y global.

La consolidación también corre en cada escritura; este script cubre la memoria
que nadie toca. Se puede programar en `services/scheduler/crontab` (supercronic).
Requiere `DATABASE_URL` y la migración `services/api/migrations/007_agent_memory.sql`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))
load_dotenv(REPO_ROOT / ".env")

from agent.memory.service import AgentMemory  # noqa: E402
from agent.memory.store import PostgresMemoryStore  # noqa: E402


def main() -> None:
    summary = AgentMemory(PostgresMemoryStore()).consolidate()
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
