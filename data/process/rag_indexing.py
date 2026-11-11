"""
RAG de Nexova — preparación de datos e indexación (Fase 1).

Responsabilidades de este módulo (y solo estas):

- `load_documents()`: leer el corpus de `docs/company-knowledge-base/`.
- `chunk_document()`: dividir cada documento en chunks semánticos.
- `embed()`: vector de un texto con el modelo de EMBEDDINGS (nunca el de generación).
- `setup()`: crear la colección `nexova_knowledge` y cargarla (idempotente).

Recuperación y generación viven en `data/pipelines/rag.py`.

Chunking: los documentos fuente no tienen subtítulos (solo un `#` de título),
así que se parte por bloques separados por línea en blanco: una línea de
servicio, un bloque de tarifas, un grupo de ítems del SLA o un par
objeción → respuesta. Un bloque nunca se corta por la mitad; los bloques de
introducción muy cortos se unen al siguiente.

Idempotencia (estrategia limpiar-y-recargar): `setup()` borra y recrea la
colección, y además los IDs son deterministas (uuid5 de documento + índice),
de modo que volver a ejecutarlo nunca duplica puntos.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

logger = logging.getLogger(__name__)

COMPANY = "nexova"
LANGUAGE = "es"
COLLECTION_NAME = "nexova_knowledge"

# Archivo del corpus → valor de `source_document` (contrato del BRIEFING).
SOURCE_DOCUMENTS: dict[str, str] = {
    "nexova-service-lines.es.md": "service-lines",
    "nexova-pricing-model.es.md": "pricing-model",
    "nexova-hiring-process-sla.es.md": "hiring-process-sla",
    "nexova-objection-handling.es.md": "objection-handling",
}

_DEFAULT_KB_PATH = Path(__file__).resolve().parents[2] / "docs" / "company-knowledge-base"
MIN_BLOCK_CHARS = 80  # bloques más cortos (introducciones) se unen al siguiente
SECTION_MAX_CHARS = 80

# Namespace fijo para los IDs deterministas de los puntos.
_POINT_NAMESPACE = uuid.UUID("6f1d6a52-0c0e-4a5e-9a3c-2f6c1f0b7a11")


class RagConfigError(RuntimeError):
    """Falta configuración obligatoria (variables de entorno de modelos / Qdrant)."""


@dataclass(frozen=True)
class Chunk:
    source_document: str
    section: str
    chunk_index: int
    text: str

    def payload(self) -> dict[str, Any]:
        return {
            "company": COMPANY,
            "source_document": self.source_document,
            "section": self.section,
            "language": LANGUAGE,
            "chunk_index": self.chunk_index,
            "text": self.text,
        }

    @property
    def point_id(self) -> str:
        return str(uuid.uuid5(_POINT_NAMESPACE, f"{self.source_document}:{self.chunk_index}"))


# --------------------------------------------------------------------------- #
# Configuración y clientes
# --------------------------------------------------------------------------- #
def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RagConfigError(f"Falta la variable de entorno {name}.")
    return value


def knowledge_base_path() -> Path:
    return Path(os.environ.get("KNOWLEDGE_BASE_PATH", _DEFAULT_KB_PATH)).resolve()


def get_qdrant_client() -> QdrantClient:
    return QdrantClient(url=os.environ.get("QDRANT_URL", "http://localhost:6333"))


def get_llm_client() -> OpenAI:
    """Cliente del endpoint de 4Geeks (compatible con OpenAI). Lo usan embed() y query()."""
    return OpenAI(base_url=_require_env("LLM_BASE_URL"), api_key=_require_env("LLM_API_KEY"))


def embedding_model() -> str:
    return _require_env("EMBEDDING_MODEL")


def generation_model() -> str:
    """Modelo de generación; debe ser distinto del de embeddings."""
    model = _require_env("GENERATION_MODEL")
    if model == os.environ.get("EMBEDDING_MODEL", "").strip():
        raise RagConfigError("GENERATION_MODEL y EMBEDDING_MODEL deben ser modelos distintos.")
    return model


# --------------------------------------------------------------------------- #
# Lectura y chunking
# --------------------------------------------------------------------------- #
def load_documents(path: Path | None = None) -> dict[str, str]:
    """Devuelve `{source_document: texto}` para los documentos del corpus."""
    base = path or knowledge_base_path()
    documents: dict[str, str] = {}
    for filename, source in SOURCE_DOCUMENTS.items():
        file = base / filename
        if not file.is_file():
            raise FileNotFoundError(f"No se encuentra el documento fuente: {file}")
        documents[source] = file.read_text(encoding="utf-8")
    return documents


def _section_title(block: str) -> str:
    """Primera frase del bloque, sin numeración ni comillas, como `section`."""
    first_line = " ".join(block.split())
    quoted = re.match(r"^[\"“](.+?)[\"”]", first_line)  # objeción citada
    if quoted:
        first_line = quoted.group(1)
    first_line = re.sub(r"^\d+\.\s+", "", first_line)  # "1. Headhunting…" → "Headhunting…"
    first_line = first_line.lstrip("-• ").strip("\"“”")
    sentence = re.split(r"[.:?!](?:\s|$)", first_line, maxsplit=1)[0].strip("\"“” ")
    if len(sentence) > SECTION_MAX_CHARS:
        sentence = sentence[: SECTION_MAX_CHARS - 1].rstrip() + "…"
    return sentence or "General"


def chunk_document(source_document: str, text: str) -> list[Chunk]:
    """Divide un documento en chunks semánticos autocontenidos.

    Un chunk = un bloque separado por línea en blanco, con el título del
    documento como primera línea (da contexto al embedding). Los bloques más
    cortos que `MIN_BLOCK_CHARS` (introducciones) se unen al bloque siguiente;
    la `section` se toma del bloque principal, no de la introducción.
    """
    lines = text.strip().splitlines()
    title = ""
    if lines and lines[0].startswith("#"):
        title = lines[0].lstrip("# ").strip()
        text = "\n".join(lines[1:])

    blocks = [b.strip("\n") for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]

    chunks: list[Chunk] = []
    intro = ""
    for position, block in enumerate(blocks):
        is_last = position == len(blocks) - 1
        if len(" ".join(block.split())) < MIN_BLOCK_CHARS and not is_last:
            intro = f"{intro}\n{block}".strip()
            continue
        intro_text, intro = intro, ""
        body = f"{intro_text}\n{block}".strip() if intro_text else block
        if title:
            body = f"{title}\n{body}"
        # Lista de ítems ("- ...") precedida de un encabezado: la sección es el encabezado.
        section_source = intro_text if intro_text and block.lstrip().startswith("-") else block
        chunks.append(Chunk(source_document, _section_title(section_source), len(chunks), body))
    return chunks


def build_chunks(documents: dict[str, str] | None = None) -> list[Chunk]:
    docs = documents if documents is not None else load_documents()
    chunks: list[Chunk] = []
    for source, text in docs.items():
        chunks.extend(chunk_document(source, text))
    return chunks


# --------------------------------------------------------------------------- #
# Embeddings e indexación
# --------------------------------------------------------------------------- #
def embed(text: str, *, client: OpenAI | None = None) -> list[float]:
    """Vector de `text` con el modelo de embeddings (misma función al indexar y consultar)."""
    llm = client or get_llm_client()
    response = llm.embeddings.create(model=embedding_model(), input=text.strip())
    return list(response.data[0].embedding)


def setup(
    *,
    qdrant: QdrantClient | None = None,
    llm: OpenAI | None = None,
    documents: dict[str, str] | None = None,
) -> int:
    """Indexa el corpus en `nexova_knowledge`. Devuelve el número de chunks cargados."""
    qdrant = qdrant or get_qdrant_client()
    llm = llm or get_llm_client()
    chunks = build_chunks(documents)
    if not chunks:
        raise ValueError("El corpus no produjo ningún chunk.")

    vectors = [embed(chunk.text, client=llm) for chunk in chunks]
    dimension = len(vectors[0])

    # Limpiar-y-recargar: la colección queda exactamente con el corpus actual.
    if qdrant.collection_exists(COLLECTION_NAME):
        qdrant.delete_collection(COLLECTION_NAME)
    qdrant.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
    )
    qdrant.upsert(
        collection_name=COLLECTION_NAME,
        points=[
            PointStruct(id=chunk.point_id, vector=vector, payload=chunk.payload())
            for chunk, vector in zip(chunks, vectors, strict=True)
        ],
    )
    logger.info("Colección %s: %d chunks (dim=%d)", COLLECTION_NAME, len(chunks), dimension)
    return len(chunks)
