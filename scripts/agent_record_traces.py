"""
Graba traces REALES del agente (Qdrant + modelos de 4Geeks) como fixtures de los evals.

    uv run --project services/api python scripts/agent_record_traces.py

Requiere la colección indexada (`scripts/rag_setup.py`) y las variables de
`.env` (QDRANT_URL, LLM_BASE_URL, LLM_API_KEY, EMBEDDING_MODEL,
GENERATION_MODEL). Ejecuta el grafo de producción —con checkpoints SQLite y
trace JSON— para cada caso y copia el trace a
`tests/pipelines/fixtures/agent_traces/<nombre>.json`, que leen los evals de
anclaje (`tests/pipelines/test_agent_evals.py`). Ese archivo es también el
export del trace que acompaña al PR.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))
load_dotenv(REPO_ROOT / ".env")

from agent.graph import get_agent_graph  # noqa: E402
from agent.runner import run_agent, run_config  # noqa: E402

FIXTURES_DIR = REPO_ROOT / "tests" / "pipelines" / "fixtures" / "agent_traces"

CASES = {
    "discount-22": "¿Pueden hacerme un descuento del 22% al 15%?",
    "headhunting-fee": "¿Cuál es la tarifa del headhunting ejecutivo y cómo se factura?",
    "out-of-domain": "¿Cuál es la capital de Francia y quién ganó el último mundial de fútbol?",
}


def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    graph = get_agent_graph()  # compila (y valida) antes de cualquier corrida
    for name, question in CASES.items():
        result = run_agent(question, graph=graph)
        checkpoints = list(graph.get_state_history(run_config(result.run_id)))
        target = FIXTURES_DIR / f"{name}.json"
        shutil.copyfile(result.trace_path, target)
        trace = json.loads(target.read_text(encoding="utf-8"))
        print(f"\n=== {name} (run_id={result.run_id}) ===")
        print(f"pregunta:     {question}")
        print(f"nodos:        {' → '.join(trace['nodes'])}")
        print(f"checkpoints:  {len(checkpoints)} en SQLite (thread_id={result.run_id})")
        print(f"respuesta:    {result.answer}")
        print(f"trace:        {target.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
