"""
Informe NIST · Protect — validación del output del modelo antes de ejecutar tools.

El enrutador del agente es un LLM cuyo JSON decide qué tool se llama y con qué
argumentos. Ese JSON es output no confiable (la pregunta del usuario puede
manipularlo), así que pasa por un contrato cerrado (`RouteDecision`) antes de
llegar a las tools; si no lo cumple, decide el respaldo determinista por reglas.

    uv run --project services/api pytest tests/pipelines/test_nist_output_validation.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "services" / "api") not in sys.path:
    sys.path.insert(0, str(ROOT / "services" / "api"))

from agent import routing  # noqa: E402


class FakeLLM:
    def __init__(self, payload: dict[str, Any] | str) -> None:
        self.content = payload if isinstance(payload, str) else json.dumps(payload)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **_: Any) -> Any:
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])


@pytest.fixture()
def router_says(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("AGENT_GUARDRAIL_LOG", str(tmp_path / "guardrails.jsonl"))

    def _set(payload: dict[str, Any] | str) -> Path:
        monkeypatch.setattr(routing, "_rag_llm", lambda: (FakeLLM(payload), "fake-model"))
        return tmp_path / "guardrails.jsonl"

    return _set


QUESTION = "¿Cómo va mi ticket de acceso?"


@pytest.mark.parametrize("payload", [
    {"tickets": True, "ticket_id": "1' OR '1'='1"},                     # SQL en el argumento
    {"tickets": True, "ticket_id": "../../admin/users"},                 # ruta
    {"tickets": True, "ticket_id": "NXV-0000001"},                        # 7 dígitos
    {"tickets": True, "ticket_status": "DELETED"},                         # estado inventado
    {"tickets": True, "ticket_category": "ROOT_ACCESS"},                   # categoría inventada
    {"tickets": True, "client_company": "RetailMax'; DROP TABLE incident;--"},
    {"inventory": True, "product": "<script>alert(1)</script>"},
    {"tickets": True, "action": "update_ticket_status", "new_status": "CLOSED"},  # acción no permitida
    "no es JSON",
])
def test_out_of_contract_router_output_falls_back_to_rules(router_says, payload) -> None:
    log = router_says(payload)
    decision = routing.classify_question(QUESTION)

    assert decision.method == "rules"
    assert decision == routing.classify_with_rules(QUESTION)
    if not isinstance(payload, str):  # JSON válido pero fuera de contrato → evento estructural
        event = json.loads(log.read_text().splitlines()[-1])
        assert event["guard"] == "routing" and event["rule"] == "invalid_router_output"


def test_sql_like_ticket_id_is_not_coerced_into_another_ticket() -> None:
    # Antes: normalize_ticket_id quitaba los no-dígitos y "1' OR 1=1--" se convertía en NXV-000111.
    assert routing.normalize_llm_ticket_id("1' OR 1=1--") == "1' OR 1=1--"
    assert routing.normalize_llm_ticket_id("nxv 482") == "NXV-000482"
    assert routing.normalize_llm_ticket_id("482") == "NXV-000482"


def test_valid_router_output_is_used(router_says) -> None:
    router_says({"tickets": True, "ticket_category": "technical_failure", "client_company": "RetailMax Corp",
                 "rag": False, "inventory": False, "ticket_id": None, "ticket_status": None, "product": None})
    decision = routing.classify_question(QUESTION)
    assert decision.method == "llm" and decision.tickets and decision.ticket_category == "technical_failure"


def test_rules_fallback_never_produces_an_invalid_decision() -> None:
    decision = routing.classify_with_rules("¿Cuánto stock de\tsillas\nergonómicas?")
    assert decision.inventory and decision.product and "\t" not in decision.product
