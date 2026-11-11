"""
RAG de Nexova — recuperación y generación (Fase 2).

- `retrieve()`: embebe la pregunta, busca en Qdrant y descarta lo que no supere
  `min_score` (puede devolver menos de k, o ninguno).
- `generate_answer()`: arma el prompt (voz de vendedor de Nexova) y llama al
  LLM de GENERACIÓN.
- `query()`: `retrieve()` + `generate_answer()`. Es la única función que deben
  llamar consumidores externos (API, UI); un agente posterior puede llamar a
  las dos primeras por separado.

El indexado (`setup`, `embed`) vive en `data/process/rag_indexing.py`.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from openai import OpenAI
from qdrant_client import QdrantClient

# `data/process` no es un paquete instalable: se añade a `sys.path` (en Docker
# llega por PROCESS_PATH, como PIPELINES_PATH).
_PROCESS_DIR = Path(
    os.environ.get("PROCESS_PATH", Path(__file__).resolve().parents[1] / "process")
).resolve()
if str(_PROCESS_DIR) not in sys.path:
    sys.path.append(str(_PROCESS_DIR))

from rag_indexing import (  # noqa: E402
    COLLECTION_NAME,
    embed,
    generation_model,
    get_llm_client,
    get_qdrant_client,
)

logger = logging.getLogger(__name__)

DEFAULT_K = 5
# Umbral de similitud coseno; se afina con scripts/rag_eval.py (ver docs/rag/rag-design.md).
DEFAULT_MIN_SCORE = float(os.environ.get("RAG_MIN_SCORE", "0.30"))

SOURCE_LABELS = {
    "service-lines": "Líneas de Servicio",
    "pricing-model": "Modelo de Tarifas",
    "hiring-process-sla": "SLA del Proceso de Contratación",
    "objection-handling": "Manejo de Objeciones",
}

SYSTEM_PROMPT = """\
Eres el asistente de ventas de Nexova, una consultora B2B de recursos humanos \
(headhunting ejecutivo, outsourcing de soporte al cliente y formación corporativa). \
Ayudas a SDRs y account managers antes o durante una llamada con un prospecto. \
Responde en español, con la voz de un vendedor de Nexova: seguro, claro y orientado a \
avanzar la conversación, pero sin presionar ni exagerar.

Reglas obligatorias:
1. Usa SOLO la información del CONTEXTO. Nunca inventes tarifas, porcentajes, montos, \
plazos, políticas ni condiciones comerciales. Cita las cifras exactamente como aparecen.
2. Los tiempos de entrega son promedios, no garantías. La única garantía contractual es \
la de reemplazo gratuito de 6 meses (por causas atribuibles al proceso de selección), \
salvo que el contrato especifique otra cosa.
3. Nunca ofrezcas ni sugieras un descuento sobre la tarifa estándar del 22 %. Si el \
usuario pregunta por un descuento o una condición no cubierta en el CONTEXTO (por ejemplo, \
bajar del 22 % al 15 %), indica que esa condición requiere aprobación de Marcos Ibáñez \
(Director de Ventas) y no propongas alternativas inventadas.
4. Sobre empresas competidoras o conflictos de interés, mantén la transparencia que indica \
el CONTEXTO; nunca ocultes información al prospecto.
5. Si el CONTEXTO está vacío o no contiene lo necesario para responder, di explícitamente \
que la base de conocimiento no tiene información suficiente sobre eso y sugiere consultar \
con un account manager senior. No rellenes con conocimiento propio.
6. No prometas acciones que el CONTEXTO no describa (por ejemplo, "coordinar una llamada" \
o "gestionar un descuento"): limítate a indicar quién debe aprobar o a quién consultar.
7. Sé conciso (máximo ~150 palabras). Termina con una línea "Fuente:" que liste los \
documentos y secciones del CONTEXTO que usaste, sin numeración. Si no usaste ninguno, \
no escribas la línea "Fuente:".
"""


def retrieve(
    query: str,
    *,
    k: int = DEFAULT_K,
    min_score: float = DEFAULT_MIN_SCORE,
    client: QdrantClient | None = None,
    llm: OpenAI | None = None,
) -> list[dict[str, Any]]:
    """Payloads de los chunks más cercanos a `query` con score >= `min_score`.

    Devuelve dicts (no objetos del SDK de Qdrant) y puede devolver menos de `k`
    elementos, o una lista vacía si nada supera el umbral.
    """
    qdrant = client or get_qdrant_client()
    vector = embed(query, client=llm)
    response = qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=vector,
        limit=k,
        with_payload=True,
    )
    results: list[dict[str, Any]] = []
    for point in response.points:
        # Scores solo en el log del servidor (depuración), nunca hacia el cliente.
        logger.info(
            "RAG score=%.3f source=%s section=%r",
            point.score,
            (point.payload or {}).get("source_document"),
            (point.payload or {}).get("section"),
        )
        if point.score >= min_score:
            results.append(dict(point.payload or {}))
    return results


def _format_context(context: list[dict[str, Any]]) -> str:
    if not context:
        return "(vacío: ningún fragmento superó el umbral de similitud)"
    parts = []
    for i, chunk in enumerate(context, start=1):
        label = SOURCE_LABELS.get(chunk.get("source_document", ""), chunk.get("source_document"))
        parts.append(f"[{i}] Documento: {label} — Sección: {chunk.get('section')}\n{chunk.get('text')}")
    return "\n\n".join(parts)


def generate_answer(
    question: str,
    context: list[dict[str, Any]],
    *,
    llm: OpenAI | None = None,
) -> str:
    """Genera la respuesta final con el LLM de generación a partir del contexto."""
    client = llm or get_llm_client()
    completion = client.chat.completions.create(
        model=generation_model(),
        temperature=0.2,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"CONTEXTO:\n{_format_context(context)}\n\nPREGUNTA:\n{question}",
            },
        ],
    )
    return (completion.choices[0].message.content or "").strip()


def query(question: str) -> str:
    """Respuesta generada a partir del contexto recuperado (punto de entrada único)."""
    context = retrieve(question)
    return generate_answer(question, context)
