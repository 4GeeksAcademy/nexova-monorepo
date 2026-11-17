"""
Grafo dedicado `rfp_intake` (LangGraph). No comparte nodos con el grafo CX
del agente de soporte (`services/api/agent/`).

    START → convert_to_markdown → classify ─┬→ discard → END
                                            └→ extract_metadata → orchestrate ─┬→ discard → END
                                                                              └─Send×N→ department_worker
                                                                                  → synthesize → finalize → END
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from rfp_intake.events import TicketCreatedPublisher
from rfp_intake.llm import LlmFn
from rfp_intake.nodes import IntakeNodes, after_classify, fan_out
from rfp_intake.state import IntakeState
from rfp_intake.store import RfpStore
from rfp_intake.trace import AgentName, traced

NODE_NAMES = (
    "convert_to_markdown", "classify", "extract_metadata", "orchestrate",
    "department_worker", "synthesize", "finalize", "discard",
)

# Agente responsable de cada nodo (trazabilidad, ver `rfp_intake/trace.py`).
AGENTS: dict[str, AgentName] = {
    "convert_to_markdown": "conversor_markitdown",
    "classify": "clasificador",
    "extract_metadata": "extractor_metadatos",
    "orchestrate": "orquestador",
    "department_worker": lambda payload: f"worker:{payload.get('department_id')}",
    "synthesize": "sintetizador",
    "finalize": "handoff_parte1",
    "discard": "descarte",
}


def build_rfp_graph(
    llm: LlmFn, store: RfpStore, publisher: TicketCreatedPublisher | None = None
) -> Any:
    nodes = IntakeNodes(llm=llm, store=store, publisher=publisher)
    builder = StateGraph(IntakeState)
    for name in NODE_NAMES:
        builder.add_node(name, traced(getattr(nodes, name), store=store, part="intake", node=name,
                                      agent=AGENTS[name]))

    builder.add_edge(START, "convert_to_markdown")
    builder.add_edge("convert_to_markdown", "classify")
    builder.add_conditional_edges("classify", after_classify, ["extract_metadata", "discard"])
    builder.add_edge("extract_metadata", "orchestrate")
    builder.add_conditional_edges("orchestrate", fan_out, ["department_worker", "discard"])
    builder.add_edge("department_worker", "synthesize")
    builder.add_edge("synthesize", "finalize")
    builder.add_edge("finalize", END)
    builder.add_edge("discard", END)
    return builder.compile()
