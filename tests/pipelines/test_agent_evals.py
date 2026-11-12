"""
Evals del agente LangGraph (`services/api/agent/`). Se ejecutan con:

    uv run --project services/api pytest tests/pipelines/test_agent_evals.py -v

Cada eval corre el grafo, lee el trace JSON que dejó la corrida y comprueba
criterios sobre ese trace (no sobre la ejecución en vivo):

- Evals de flujo (1–6): `retrieve` y `generate_answer` sustituidos por fakes,
  checkpointer SQLite en memoria y traces en `tmp_path`. Sin Qdrant ni LLM.
- Evals de anclaje (7–8): leen traces REALES grabados con
  `scripts/agent_record_traces.py` (Qdrant + modelos de 4Geeks) y verifican
  que la respuesta sigue anclada en `docs/company-knowledge-base/`.

Son adicionales a los tests del RAG (`test_rag.py`), que deben seguir pasando.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
API_DIR = str(ROOT / "services" / "api")
if API_DIR not in sys.path:
    sys.path.append(API_DIR)

from langgraph.graph import END, START, StateGraph  # noqa: E402

from agent import nodes  # noqa: E402
from agent.graph import AgentGraphBuildError, build_graph, compile_graph, sqlite_checkpointer  # noqa: E402
from agent.runner import AgentRunResult, run_agent, run_config  # noqa: E402
from agent.state import AgentState  # noqa: E402
from agent.tracing import load_trace  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "agent_traces"

PRICING_CHUNK = {
    "source_document": "pricing-model",
    "section": "Headhunting ejecutivo",
    "text": "Tarifa estándar: 22% del salario anual bruto del cargo contratado.",
}


class FakeRAG:
    """Sustituye a `retrieve` y `generate_answer` y registra cada llamada."""

    def __init__(self, context: list[dict[str, Any]]) -> None:
        self.context = context
        self.retrieve_calls: list[str] = []
        self.generate_calls: list[tuple[str, list[dict[str, Any]]]] = []

    def retrieve(self, question: str) -> list[dict[str, Any]]:
        self.retrieve_calls.append(question)
        return self.context

    def generate_answer(self, question: str, context: list[dict[str, Any]]) -> str:
        self.generate_calls.append((question, context))
        return "La tarifa estándar es el 22 % del salario anual bruto.\nFuente: Modelo de Tarifas"


@pytest.fixture(autouse=True)
def trace_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "traces"
    monkeypatch.setenv("AGENT_TRACE_DIR", str(directory))
    return directory


def _run(question: str, rag: FakeRAG):
    graph = build_graph(
        retrieve_fn=rag.retrieve,
        generate_fn=rag.generate_answer,
        checkpointer=sqlite_checkpointer(":memory:"),
    )
    result = run_agent(question, graph=graph)
    return graph, result, load_trace(result.run_id)


# --- Evals de flujo (trace generado con fakes) ------------------------------------------


def test_eval_1_in_domain_question_retrieves_before_generating() -> None:
    _, result, trace = _run("¿Cuál es la tarifa del headhunting?", FakeRAG([PRICING_CHUNK]))

    assert trace["status"] == "completed"
    assert trace["nodes"] == [nodes.RECEIVE_QUESTION, nodes.RETRIEVE, nodes.GENERATE]
    assert trace["nodes"].index(nodes.RETRIEVE) < trace["nodes"].index(nodes.GENERATE)
    assert [s["order"] for s in trace["steps"]] == [1, 2, 3]
    assert trace["final_answer"] == result.answer and "22" in trace["final_answer"]


def test_eval_2_no_context_routes_to_honest_answer_without_generating() -> None:
    rag = FakeRAG([])
    _, _, trace = _run("¿Quién ganó el último mundial?", rag)

    assert trace["nodes"] == [nodes.RECEIVE_QUESTION, nodes.RETRIEVE, nodes.NO_INFORMATION]
    assert nodes.GENERATE not in trace["nodes"]
    assert trace["steps"][1]["output"] == {"context": []}
    assert trace["final_answer"] == nodes.NO_INFORMATION_ANSWER
    assert rag.generate_calls == []


def test_eval_3_empty_question_is_rejected_before_retrieval() -> None:
    rag = FakeRAG([PRICING_CHUNK])
    _, result, trace = _run("   ", rag)

    assert trace["nodes"] == [nodes.RECEIVE_QUESTION, nodes.REJECT]
    assert trace["status"] == "rejected"
    assert trace["error"] == result.error == nodes.EMPTY_QUESTION_ERROR
    assert rag.retrieve_calls == []


def test_eval_4_generation_uses_exactly_the_retrieved_context() -> None:
    rag = FakeRAG([PRICING_CHUNK])
    _, _, trace = _run("  ¿Cuál es la tarifa del headhunting?  ", rag)

    # Una sola recuperación (nadie re-envuelve `query()`) y la generación recibe
    # el contexto que el trace atribuye al nodo `retrieve`.
    assert rag.retrieve_calls == ["¿Cuál es la tarifa del headhunting?"]
    assert len(rag.generate_calls) == 1
    question, context = rag.generate_calls[0]
    assert question == "¿Cuál es la tarifa del headhunting?"
    retrieve_step = next(s for s in trace["steps"] if s["node"] == nodes.RETRIEVE)
    assert [(c["source_document"], c["section"]) for c in retrieve_step["output"]["context"]] == [
        (c["source_document"], c["section"]) for c in context
    ]


def test_eval_5_every_transition_is_checkpointed() -> None:
    graph, result, trace = _run("¿Cuál es la tarifa del headhunting?", FakeRAG([PRICING_CHUNK]))

    history = list(graph.get_state_history(run_config(result.run_id)))  # más reciente primero
    pending_nodes = {node for snapshot in history for node in snapshot.next}
    assert set(trace["nodes"]) <= pending_nodes
    assert len(history) >= len(trace["nodes"]) + 1

    latest = graph.get_state(run_config(result.run_id))
    assert latest.next == ()
    assert latest.values["answer"] == trace["final_answer"]
    assert latest.values["context"][0]["source_document"] == "pricing-model"


def test_eval_6_structural_errors_fail_at_compile_time() -> None:
    unknown_target = StateGraph(AgentState)
    unknown_target.add_node(nodes.RECEIVE_QUESTION, nodes.receive_question)
    unknown_target.add_edge(START, nodes.RECEIVE_QUESTION)
    unknown_target.add_edge(nodes.RECEIVE_QUESTION, "nodo_inexistente")
    with pytest.raises(AgentGraphBuildError, match="nodo_inexistente"):
        compile_graph(unknown_target)

    orphan = StateGraph(AgentState)
    orphan.add_node(nodes.RECEIVE_QUESTION, nodes.receive_question)
    orphan.add_node(nodes.NO_INFORMATION, nodes.no_information)  # sin aristas
    orphan.add_edge(START, nodes.RECEIVE_QUESTION)
    orphan.add_edge(nodes.RECEIVE_QUESTION, END)
    with pytest.raises(AgentGraphBuildError, match=nodes.NO_INFORMATION):
        compile_graph(orphan)


def test_failed_node_is_recorded_in_the_trace(trace_dir: Path) -> None:
    def broken_retrieve(question: str) -> list[dict[str, Any]]:
        raise ConnectionError("Qdrant caído")

    graph = build_graph(
        retrieve_fn=broken_retrieve,
        generate_fn=FakeRAG([]).generate_answer,
        checkpointer=sqlite_checkpointer(":memory:"),
    )
    with pytest.raises(ConnectionError):
        run_agent("¿Cuál es la tarifa?", graph=graph)

    (trace_file,) = trace_dir.glob("*.json")
    trace = json.loads(trace_file.read_text(encoding="utf-8"))
    assert trace["status"] == "failed" and trace["error"] == "ConnectionError"
    assert trace["steps"][-1] == {**trace["steps"][-1], "node": nodes.RETRIEVE, "error": "ConnectionError"}


# --- Evals de anclaje (traces reales grabados) ------------------------------------------


def _fixture(name: str) -> dict[str, Any]:
    path = FIXTURES / f"{name}.json"
    if not path.exists():
        pytest.fail(f"Falta {path.name}: grábalo con scripts/agent_record_traces.py")
    return json.loads(path.read_text(encoding="utf-8"))


def _retrieved_sources(trace: dict[str, Any]) -> set[str]:
    step = next(s for s in trace["steps"] if s["node"] == nodes.RETRIEVE)
    return {chunk["source_document"] for chunk in step["output"]["context"]}


def test_eval_7_discount_answer_stays_grounded_in_pricing_policy() -> None:
    trace = _fixture("discount-22")

    assert trace["nodes"] == [nodes.RECEIVE_QUESTION, nodes.RETRIEVE, nodes.GENERATE]
    assert _retrieved_sources(trace) & {"pricing-model", "objection-handling"}
    answer = trace["final_answer"]
    # Política de la base de conocimiento: tarifa estándar del 22 % sin descuentos;
    # cualquier excepción la aprueba Marcos Ibáñez (Director de Ventas).
    assert "22" in answer
    assert "Marcos Ibáñez" in answer
    assert "Fuente:" in answer


def test_eval_8_headhunting_fee_quotes_the_knowledge_base() -> None:
    trace = _fixture("headhunting-fee")

    assert "pricing-model" in _retrieved_sources(trace)
    answer = trace["final_answer"]
    assert "22" in answer
    for installment in ("30", "40"):  # facturación 30 % / 30 % / 40 %
        assert installment in answer


def test_eval_9_out_of_domain_question_is_not_answered_from_thin_air() -> None:
    trace = _fixture("out-of-domain")

    assert trace["nodes"] == [nodes.RECEIVE_QUESTION, nodes.RETRIEVE, nodes.NO_INFORMATION]
    assert trace["final_answer"] == nodes.NO_INFORMATION_ANSWER


def test_run_agent_result_points_to_its_trace() -> None:
    _, result, trace = _run("¿Cuál es la tarifa?", FakeRAG([PRICING_CHUNK]))
    assert isinstance(result, AgentRunResult)
    assert result.trace_path.name == f"{result.run_id}.json"
    assert trace["run_id"] == trace["thread_id"] == result.run_id
