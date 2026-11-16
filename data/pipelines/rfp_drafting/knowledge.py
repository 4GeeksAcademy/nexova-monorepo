"""
Acceso de los generadores a la base de conocimiento de Nexova (RAG, Hito 7).

Reutiliza `retrieve()` de `data/pipelines/rag.py` (Qdrant + embeddings de
4Geeks) sobre `docs/company-knowledge-base`: tarifas, SLA del proceso de
contratación y líneas de servicio. Es una mejora, no un requisito: si Qdrant
no responde o tarda, el generador redacta sin ese contexto (y la evaluación de
cumplimiento sigue aplicándose igual).
"""

from __future__ import annotations

import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("rfp_drafting")

KnowledgeFn = Callable[[str], list[dict[str, Any]]]

KNOWLEDGE_TIMEOUT_SECONDS = float(os.environ.get("RFP_KNOWLEDGE_TIMEOUT_SECONDS", "10"))
MAX_CHUNKS = 6
_DEFAULT_PROCESS_PATH = Path(__file__).resolve().parents[2] / "process"


def _rag_retrieve() -> Callable[..., list[dict[str, Any]]]:
    # `rag.py` importa `rag_indexing` de data/process (en Docker, /data/process).
    process_path = str(Path(os.environ.get("PROCESS_PATH", _DEFAULT_PROCESS_PATH)).resolve())
    if process_path not in sys.path:
        sys.path.append(process_path)
    from rag import retrieve

    return retrieve


def rag_knowledge(query: str) -> list[dict[str, Any]]:
    """Fragmentos de la base de conocimiento para `query`; `[]` si el RAG no está disponible."""
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(lambda: _rag_retrieve()(query))
        return list(future.result(timeout=KNOWLEDGE_TIMEOUT_SECONDS))
    except FutureTimeout:
        logger.warning("RAG sin respuesta en %.0f s para %r: se genera sin contexto", KNOWLEDGE_TIMEOUT_SECONDS, query)
    except Exception:
        logger.warning("RAG no disponible para %r: se genera sin contexto", query, exc_info=True)
    finally:
        pool.shutdown(wait=False)
    return []


def no_knowledge(query: str) -> list[dict[str, Any]]:
    return []


def gather_knowledge(queries: tuple[str, ...], knowledge_fn: KnowledgeFn) -> list[dict[str, Any]]:
    """Une los fragmentos de varias consultas sin duplicados (por documento y sección)."""
    seen: set[tuple[str, str, str]] = set()
    chunks: list[dict[str, Any]] = []
    for query in queries:
        for chunk in knowledge_fn(query):
            key = (str(chunk.get("source_document")), str(chunk.get("section")), str(chunk.get("text"))[:80])
            if key not in seen and chunk.get("text"):
                seen.add(key)
                chunks.append({k: chunk.get(k) for k in ("source_document", "section", "text")})
    return chunks[:MAX_CHUNKS]


def format_knowledge(chunks: list[dict[str, Any]]) -> str:
    if not chunks:
        return "(sin fragmentos de la base de conocimiento: no inventes tarifas; usa 'a confirmar')"
    return "\n\n".join(
        f"[{i}] {c.get('source_document')} — {c.get('section')}\n{c.get('text')}" for i, c in enumerate(chunks, 1)
    )
