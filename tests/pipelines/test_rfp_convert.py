"""
Tests de la ingesta del flujo `rfp_intake`: validación de la subida, PDF →
Markdown con MarkItDown, legibilidad y sede → moneda.

    uv run --project services/api pytest tests/pipelines/test_rfp_convert.py -v
"""

from __future__ import annotations

import pytest

from rfp_fakes import FORMAL_PDF, INFORMAL_PDF, INVALID_PDF
from rfp_intake import InMemoryRfpStore, InvalidUpload, register_upload
from rfp_intake import legibility
from rfp_intake.convert import clean_markdown, pdf_to_markdown
from rfp_intake.legibility import compute_readability, cost_tier
from rfp_intake.nodes import currency_for_hq, parse_deadline, quote_in_document, resolve_client_hq


@pytest.mark.parametrize(
    "pdf, expected",
    [
        (FORMAL_PDF, ["Vantex Retail Group", "5 posiciones de mandos medios", "40 participantes"]),
        (INFORMAL_PDF, ["NubeSoft", "12 support agents", "24/7"]),
        (INVALID_PDF, ["HireStream", "15-minute demo"]),
    ],
)
def test_sample_pdfs_convert_to_clean_markdown(pdf, expected):
    markdown = pdf_to_markdown(pdf)
    for text in expected:
        assert text in markdown
    assert "(cid:" not in markdown


def test_clean_markdown_replaces_pdf_bullet_artifacts():
    assert clean_markdown("(cid:127) Uno\n\n\n\n(cid:127) Dos   \n") == "- Uno\n\n- Dos"


# --------------------------------------------------------------------------- #
# Subida
# --------------------------------------------------------------------------- #
@pytest.fixture
def raw_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RFP_RAW_DIR", str(tmp_path / "raw" / "rfp"))
    return tmp_path / "raw" / "rfp"


def test_upload_saves_pdf_under_raw_dir_and_creates_analyzing_ticket(raw_dir):
    store = InMemoryRfpStore()
    ticket = register_upload(FORMAL_PDF.read_bytes(), "Vantex RFP.pdf", "ventas@nexova.test", store)

    assert ticket["status"] == "analizando"
    assert ticket["raw_pdf_path"] == f"data/raw/rfp/{ticket['ticket_id']}.pdf"
    assert (raw_dir / f"{ticket['ticket_id']}.pdf").read_bytes() == FORMAL_PDF.read_bytes()
    assert ticket["original_filename"] == "Vantex RFP.pdf"


@pytest.mark.parametrize(
    "content, filename, status",
    [
        (b"%PDF-1.4 ...", "rfp.docx", 415),
        (b"", "rfp.pdf", 422),
        (b"hola, no soy un pdf", "rfp.pdf", 415),
    ],
)
def test_upload_rejects_non_pdf_files(raw_dir, content, filename, status):
    store = InMemoryRfpStore()
    with pytest.raises(InvalidUpload) as exc:
        register_upload(content, filename, None, store)
    assert exc.value.status_code == status
    assert store.list_tickets() == []
    assert not raw_dir.exists() or not any(raw_dir.iterdir())


def test_upload_does_not_leave_orphan_pdf_if_ticket_creation_fails(raw_dir):
    class BrokenStore(InMemoryRfpStore):
        def create_ticket(self, **kwargs):
            raise RuntimeError("DB caída")

    with pytest.raises(RuntimeError):
        register_upload(FORMAL_PDF.read_bytes(), "rfp.pdf", None, BrokenStore())
    assert not any(raw_dir.iterdir())


# --------------------------------------------------------------------------- #
# Legibilidad
# --------------------------------------------------------------------------- #
def test_readability_on_formal_rfp():
    if not legibility._ensure_punkt():
        pytest.skip("punkt_tab de NLTK no disponible (sin red)")
    metrics = compute_readability(pdf_to_markdown(FORMAL_PDF))

    assert metrics["word_count"] > 100
    assert metrics["flesch_kincaid_grade"] is not None
    assert metrics["gunning_fog"] is not None
    assert metrics["cost_tier"] in {"bajo", "medio", "alto"}
    assert metrics["note"] is None


def test_readability_on_short_text_reports_reason_instead_of_failing():
    metrics = compute_readability(pdf_to_markdown(INVALID_PDF))

    assert metrics["word_count"] < 100
    assert metrics["flesch_kincaid_grade"] is None
    assert "100" in metrics["note"]
    assert metrics["estimated_tokens"] > 0


def test_readability_without_nltk_data_does_not_fail(monkeypatch):
    monkeypatch.setattr(legibility, "_ensure_punkt", lambda: False)
    metrics = compute_readability("palabra " * 150)
    assert metrics["gunning_fog"] is None
    assert "punkt_tab" in metrics["note"]


def test_cost_tier_grows_with_size_and_complexity():
    assert cost_tier(500, 8.0) == "bajo"
    assert cost_tier(2000, 8.0) == "medio"
    assert cost_tier(500, 17.0) == "alto"


# --------------------------------------------------------------------------- #
# Sede → moneda y otras reglas deterministas
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "location, hq, currency",
    [
        ("Madrid, España", "España", "EUR"),
        ("Valencia", "España", "EUR"),
        ("Miami, FL, USA", "Miami", "USD"),
        ("Estados Unidos", "Miami", "USD"),
        ("Por causa de la expansión", "desconocido", None),  # "usa" dentro de "causa"
        (None, "desconocido", None),
    ],
)
def test_client_hq_and_currency(location, hq, currency):
    assert resolve_client_hq(location) == hq
    assert currency_for_hq(hq) == currency


def test_parse_deadline_never_guesses():
    assert parse_deadline("2026-08-18").isoformat() == "2026-08-18"
    assert parse_deadline("August 23rd") is None
    assert parse_deadline(None) is None
    # El modelo completó "August 23rd" con un año que el documento no dice: se rechaza.
    assert parse_deadline("2025-08-23", "say by August 23rd") is None
    assert parse_deadline("2026-08-18", "Proposal Due Date: August 18, 2026").isoformat() == "2026-08-18"


def test_quote_matching_ignores_bullets_accents_and_line_breaks():
    document = "- Búsqueda ejecutiva para 5 posiciones de mandos medios (Retail Operations Manager, Regional\nSales Manager)"
    assert quote_in_document("busqueda ejecutiva para 5 posiciones", document)
    assert quote_in_document("Regional Sales Manager", document)
    assert not quote_in_document("10 posiciones", document)
