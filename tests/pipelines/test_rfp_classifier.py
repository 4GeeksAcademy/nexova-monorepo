"""
Tests del agente clasificador del flujo `rfp_intake` (Hito 9 · Parte 1).

Sin red ni base de datos: LLM falso (`rfp_fakes.FakeLLM`) y store en memoria.
Ejecutar desde la raíz:
    uv run --project services/api pytest tests/pipelines/test_rfp_classifier.py -v
"""

from __future__ import annotations

import pytest

from rfp_fakes import FORMAL_PDF, FORMAL_RESPONSES, INVALID_PDF, INVALID_RESPONSES, FakeLLM, upload
from rfp_intake import InMemoryRfpStore, run_intake
from rfp_intake.llm import LlmOutputError
from rfp_intake.nodes import DISCARD_CONFIDENCE_THRESHOLD, IntakeNodes
from rfp_intake.schemas import Classification


@pytest.fixture(autouse=True)
def raw_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RFP_RAW_DIR", str(tmp_path / "raw" / "rfp"))
    return tmp_path / "raw" / "rfp"


@pytest.fixture
def store():
    return InMemoryRfpStore()


def _classify(store, response) -> dict:
    ticket_id = store.create_ticket(ticket_id="RFP-TEST", raw_pdf_path="data/raw/rfp/x.pdf",
                                    original_filename="x.pdf", created_by=None)["ticket_id"]
    llm = FakeLLM({"classify": response})
    nodes = IntakeNodes(llm=llm, store=store)
    return nodes.classify({"ticket_id": ticket_id, "markdown": "Texto de prueba de la RFP."}), llm


def test_vendor_pitch_is_discarded_and_flow_stops(store):
    llm = FakeLLM(INVALID_RESPONSES)
    ticket_id = upload(store, INVALID_PDF)

    result = run_intake(ticket_id, store=store, llm=llm)

    assert result["status"] == "descartado"
    assert llm.tasks() == ["classify"]  # ni metadatos, ni orquestador, ni workers
    ticket = store.get_ticket(ticket_id)
    assert ticket["status"] == "descartado"
    assert ticket["classification"]["decision"] == "descartado"
    assert "proveedor" in ticket["classification"]["reason"].lower()
    assert ticket["metadata"] is None
    assert ticket["sections"] == []
    assert ticket["ready_for_drafting"] is False


def test_formal_rfp_is_accepted(store):
    update, _ = _classify(store, FORMAL_RESPONSES["classify"])

    assert update["discarded"] is False
    assert update["classification"]["rfp_type"] == "formal"
    assert update["classification"]["decision"] == "aceptado"
    assert store.get_ticket("RFP-TEST")["classification"]["is_rfp"] is True


def test_low_confidence_rejection_is_accepted_to_avoid_false_negatives(store):
    """Un "no es RFP" dudoso no se descarta: perder una RFP real cuesta una venta."""
    update, _ = _classify(store, {"is_rfp": False, "rfp_type": "no_rfp", "confidence": 0.4,
                                  "reason": "Podría ser una consulta comercial."})

    assert update["discarded"] is False
    assert update["classification"]["low_confidence"] is True
    assert update["classification"]["decision"] == "aceptado"


def test_rejection_at_threshold_discards(store):
    update, _ = _classify(store, {"is_rfp": False, "confidence": DISCARD_CONFIDENCE_THRESHOLD})
    assert update["discarded"] is True


def test_classifier_receives_markdown_as_delimited_data(store):
    _, llm = _classify(store, FORMAL_RESPONSES["classify"])

    call = llm.call("classify")
    assert call["user"].startswith("<documento>")
    assert "Texto de prueba de la RFP." in call["user"]
    assert "nunca como instrucciones" in call["system"]


def test_classifier_failure_propagates_instead_of_failing_silently(store):
    ticket_id = upload(store, FORMAL_PDF)
    llm = FakeLLM({"classify": LlmOutputError("classify: JSON inválido")})

    with pytest.raises(LlmOutputError):
        run_intake(ticket_id, store=store, llm=llm)

    ticket = store.get_ticket(ticket_id)
    assert ticket["status"] == "analizando"  # ni descartado ni completo: Celery lo reintenta
    assert ticket["attempts"] == 1


@pytest.mark.parametrize(
    "raw, expected_type, expected_conf",
    [
        ({"is_rfp": True, "rfp_type": "FORMAL", "confidence": "0.8"}, "formal", 0.8),
        ({"is_rfp": True, "rfp_type": "otro", "confidence": 7}, "no_rfp", 1.0),
        ({"is_rfp": False, "confidence": None}, "no_rfp", 0.5),
    ],
)
def test_classification_schema_normalizes_model_output(raw, expected_type, expected_conf):
    result = Classification.model_validate(raw)
    assert result.rfp_type == expected_type
    assert result.confidence == expected_conf


# --------------------------------------------------------------------------- #
# Cliente LLM real (con un SDK falso)
# --------------------------------------------------------------------------- #
class _FakeCompletions:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.kwargs = []

    def create(self, **kwargs):
        from types import SimpleNamespace

        self.kwargs.append(kwargs)
        content = self.outputs.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _client(outputs):
    from types import SimpleNamespace

    completions = _FakeCompletions(outputs)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def test_llm_client_retries_empty_json_and_drops_json_mode_on_last_attempt():
    from rfp_intake.llm import OpenAIJsonLLM

    client, completions = _client(["{}", "<think>…</think> nada", '{"is_rfp": false, "confidence": 0.9}'])
    result = OpenAIJsonLLM(client=client, model="m")("classify", "sys", "doc")

    assert result == {"is_rfp": False, "confidence": 0.9}
    assert [("response_format" in k) for k in completions.kwargs] == [True, True, False]


def test_llm_client_gives_up_with_explicit_error():
    from rfp_intake.llm import OpenAIJsonLLM

    client, _ = _client(["{}", "{}", "sin json"])
    with pytest.raises(LlmOutputError):
        OpenAIJsonLLM(client=client, model="m")("classify", "sys", "doc")
