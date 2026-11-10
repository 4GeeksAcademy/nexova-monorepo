"""
Tests unitarios del RAG de Nexova (`data/process/rag_indexing.py` y
`data/pipelines/rag.py`). Sin Qdrant ni LLM reales: todo con stubs en memoria.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
for sub in ("process", "pipelines"):
    path = str(ROOT / "data" / sub)
    if path not in sys.path:
        sys.path.insert(0, path)

import rag  # noqa: E402
import rag_indexing  # noqa: E402

KB_PATH = ROOT / "docs" / "company-knowledge-base"


# --- Stubs ------------------------------------------------------------------------


class FakeQdrant:
    def __init__(self, scores: list[float] | None = None) -> None:
        self.scores = scores or []
        self.points: list = []
        self.collections: set[str] = set()

    def query_points(self, collection_name, query, limit, with_payload):
        points = [
            SimpleNamespace(score=s, payload={"source_document": "pricing-model", "section": f"s{i}", "text": f"texto {i}"})
            for i, s in enumerate(self.scores[:limit])
        ]
        return SimpleNamespace(points=points)

    def collection_exists(self, name):
        return name in self.collections

    def delete_collection(self, name):
        self.collections.discard(name)
        self.points = []

    def create_collection(self, collection_name, vectors_config):
        self.collections.add(collection_name)

    def upsert(self, collection_name, points):
        by_id = {p.id: p for p in self.points}
        by_id.update({p.id: p for p in points})
        self.points = list(by_id.values())


class FakeLLM:
    """Sirve de cliente de embeddings y de chat; registra las llamadas."""

    def __init__(self, answer: str = "Respuesta generada por el modelo.") -> None:
        self.answer = answer
        self.chat_calls: list[dict] = []
        self.embeddings = SimpleNamespace(
            create=lambda model, input: SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3])])
        )
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        self.chat_calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.answer))])


@pytest.fixture(autouse=True)
def model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_MODEL", "embed-model")
    monkeypatch.setenv("GENERATION_MODEL", "chat-model")
    monkeypatch.setattr(rag, "embed", lambda text, client=None: [0.1, 0.2, 0.3])


# --- Chunking e indexación ----------------------------------------------------------


def test_each_source_document_produces_at_least_three_chunks() -> None:
    docs = rag_indexing.load_documents(KB_PATH)
    assert set(docs) == {"service-lines", "pricing-model", "hiring-process-sla", "objection-handling"}
    for source, text in docs.items():
        assert len(rag_indexing.chunk_document(source, text)) >= 3, source


def test_chunks_have_required_payload_and_unique_deterministic_ids() -> None:
    chunks = rag_indexing.build_chunks(rag_indexing.load_documents(KB_PATH))
    assert len({c.point_id for c in chunks}) == len(chunks)
    for chunk in chunks:
        payload = chunk.payload()
        assert payload["company"] == "nexova" and payload["language"] == "es"
        assert payload["section"] and payload["text"]
    assert chunks[0].point_id == rag_indexing.build_chunks(rag_indexing.load_documents(KB_PATH))[0].point_id


def test_chunks_do_not_split_an_objection_from_its_answer() -> None:
    docs = rag_indexing.load_documents(KB_PATH)
    chunks = rag_indexing.chunk_document("objection-handling", docs["objection-handling"])
    assert all("Respuesta" in c.text for c in chunks)


def test_setup_is_idempotent() -> None:
    qdrant, llm = FakeQdrant(), FakeLLM()
    docs = rag_indexing.load_documents(KB_PATH)
    first = rag_indexing.setup(qdrant=qdrant, llm=llm, documents=docs)
    second = rag_indexing.setup(qdrant=qdrant, llm=llm, documents=docs)
    assert first == second == len(qdrant.points)


def test_generation_model_must_differ_from_embedding_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GENERATION_MODEL", "embed-model")
    with pytest.raises(rag_indexing.RagConfigError):
        rag_indexing.generation_model()


# --- retrieve() -----------------------------------------------------------------------


def test_retrieve_excludes_results_below_min_score() -> None:
    results = rag.retrieve("pregunta", k=5, min_score=0.4, client=FakeQdrant([0.9, 0.5, 0.2]))
    assert [r["section"] for r in results] == ["s0", "s1"]  # menos de k
    assert all(isinstance(r, dict) for r in results)


def test_retrieve_returns_empty_when_nothing_passes_threshold() -> None:
    assert rag.retrieve("pregunta", min_score=0.8, client=FakeQdrant([0.3, 0.1])) == []


def test_retrieve_respects_k() -> None:
    assert len(rag.retrieve("pregunta", k=2, min_score=0.0, client=FakeQdrant([0.9, 0.8, 0.7]))) == 2


# --- query() --------------------------------------------------------------------------


def test_query_returns_model_output_not_raw_chunk_text(monkeypatch: pytest.MonkeyPatch) -> None:
    context = [{"source_document": "pricing-model", "section": "Formación corporativa", "text": "TEXTO CRUDO DEL CHUNK"}]
    llm = FakeLLM("Son desde 6.200 USD para grupos de hasta 15 personas.")
    monkeypatch.setattr(rag, "retrieve", lambda question: context)
    monkeypatch.setattr(rag, "get_llm_client", lambda: llm)

    answer = rag.query("¿Cuánto cuesta el programa de 8 semanas?")

    assert answer == "Son desde 6.200 USD para grupos de hasta 15 personas."
    assert "TEXTO CRUDO DEL CHUNK" not in answer
    call = llm.chat_calls[0]
    assert call["model"] == "chat-model"  # generación, no embeddings
    assert "TEXTO CRUDO DEL CHUNK" in call["messages"][1]["content"]


def test_query_still_calls_llm_with_empty_context_so_it_can_admit_no_info(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = FakeLLM("No tengo información suficiente.")
    monkeypatch.setattr(rag, "retrieve", lambda question: [])
    monkeypatch.setattr(rag, "get_llm_client", lambda: llm)

    assert rag.query("¿Capital de Francia?") == "No tengo información suficiente."
    assert "vacío" in llm.chat_calls[0]["messages"][1]["content"]


def test_system_prompt_encodes_business_rules() -> None:
    assert "22" in rag.SYSTEM_PROMPT and "Marcos Ibáñez" in rag.SYSTEM_PROMPT
    assert "6 meses" in rag.SYSTEM_PROMPT and "promedios" in rag.SYSTEM_PROMPT
