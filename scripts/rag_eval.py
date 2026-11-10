"""
Mide Recall@3 del retrieval contra `data/eval/test-queries.json` (Qdrant y
embeddings reales; haz antes `scripts/rag_setup.py`).

    uv run --project services/api python scripts/rag_eval.py [--min-score 0.30]

Para cada pregunta con `expected`, el acierto es que el chunk correcto
(source_document + section) esté entre los 3 primeros resultados por score
(sin aplicar umbral). Para las preguntas con `expected: null` informa si
alguna superaría `--min-score` (debería no superarlo). Imprime además la
distribución de scores para elegir el umbral.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "data" / "process"))
load_dotenv(REPO_ROOT / ".env")

import rag_indexing  # noqa: E402

QUERIES_PATH = REPO_ROOT / "data" / "eval" / "test-queries.json"
RECALL_TARGET = 0.80


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-score", type=float, default=float(os.environ.get("RAG_MIN_SCORE", "0.30")))
    args = parser.parse_args()

    queries = json.loads(QUERIES_PATH.read_text(encoding="utf-8"))["queries"]
    qdrant = rag_indexing.get_qdrant_client()
    llm = rag_indexing.get_llm_client()

    hits = answerable = 0
    for item in queries:
        vector = rag_indexing.embed(item["question"], client=llm)
        points = qdrant.query_points(rag_indexing.COLLECTION_NAME, query=vector, limit=3, with_payload=True).points
        top = [(p.payload["source_document"], p.payload["section"], p.score) for p in points]
        expected = item["expected"]
        if expected is None:
            best = top[0][2] if top else 0.0
            status = "OK (no supera umbral)" if best < args.min_score else "FALLO (supera umbral)"
            print(f"[fuera de base] best={best:.3f} {status} :: {item['question']}")
            continue
        answerable += 1
        found = any((src, sec) == (expected["source_document"], expected["section"]) for src, sec, _ in top)
        hits += found
        scores = ", ".join(f"{s:.3f}" for _, _, s in top)
        print(f"[{'OK' if found else 'FALLO'}] scores=[{scores}] :: {item['question']}")

    recall = hits / answerable
    print(f"\nRecall@3 = {hits}/{answerable} = {recall:.0%} (objetivo ≥ {RECALL_TARGET:.0%})")
    return 0 if recall >= RECALL_TARGET else 1


if __name__ == "__main__":
    sys.exit(main())
