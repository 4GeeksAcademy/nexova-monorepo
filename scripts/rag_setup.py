"""
Indexa la base de conocimiento de Nexova en Qdrant (colección `nexova_knowledge`).

    uv run --project services/api python scripts/rag_setup.py

Lee `docs/company-knowledge-base/`, genera los chunks, calcula sus embeddings
con el modelo de embeddings de 4Geeks y recarga la colección (limpiar-y-recargar,
IDs deterministas: ejecutarlo varias veces no duplica puntos). Requiere
QDRANT_URL, LLM_BASE_URL, LLM_API_KEY, EMBEDDING_MODEL y GENERATION_MODEL
(ver `.env.example`).
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "data" / "process"))
load_dotenv(REPO_ROOT / ".env")

import rag_indexing  # noqa: E402


def main() -> None:
    rag_indexing.generation_model()  # falla pronto si falta config o los modelos coinciden
    total = rag_indexing.setup()
    client = rag_indexing.get_qdrant_client()
    count = client.count(rag_indexing.COLLECTION_NAME, exact=True).count
    print(f"Colección '{rag_indexing.COLLECTION_NAME}': {total} chunks indexados ({count} puntos en Qdrant).")


if __name__ == "__main__":
    main()
