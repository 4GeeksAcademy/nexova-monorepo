"""Transformación CSV → modelo y script de seed (README · Seed)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "scripts" / "incidents-COMPANY.csv"


@pytest.fixture(scope="module")
def seed_module():
    spec = importlib.util.spec_from_file_location(
        "seed_incidents", ROOT / "scripts" / "seed_incidents.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # los dataclasses con anotaciones diferidas lo necesitan
    spec.loader.exec_module(module)
    return module


def _record(**overrides):
    base = {
        "ticket_id": "NXV-000001",
        "date": "2024-01-18",
        "client_company": "FinServ Group",
        "category": "ACCESS",
        "description": "Role permissions not updated after department change",
        "agent_id": "AGT-08",
        "status": "CLOSED",
        "customer_email": "a@example.com",
        "satisfaction_score": "5",
    }
    return {**base, **overrides}


# --- transform_csv_row ------------------------------------------------------


def test_transform_applies_briefing_mappings():
    from nexova_shared.incident_csv import transform_csv_row

    row, failed = transform_csv_row(_record())
    assert failed == []
    assert row.status == "resolved"  # CLOSED → resolved
    assert row.category == "technical_failure"  # ACCESS → technical_failure
    assert row.origin == "customer" and row.branch == "central"
    assert row.created_at.isoformat() == "2024-01-18T00:00:00+00:00"
    assert row.description == "Role permissions not updated after department change"
    assert row.source_key == "NXV-000001"


@pytest.mark.parametrize(
    ("csv_status", "model_status"),
    [("OPEN", "open"), ("CLOSED", "resolved"), ("DISCARDED", "discarded")],
)
def test_status_map(csv_status, model_status):
    from nexova_shared.incident_csv import transform_csv_row

    row, _ = transform_csv_row(_record(status=csv_status))
    assert row.status == model_status


@pytest.mark.parametrize(
    ("csv_category", "model_category"),
    [
        ("TECHNICAL", "technical_failure"),
        ("BILLING", "process_error"),
        ("ACCESS", "technical_failure"),
        ("HR_QUERY", "process_error"),
        ("COMPLAINT", "client_complaint"),
    ],
)
def test_category_map(csv_category, model_category):
    from nexova_shared.incident_csv import transform_csv_row

    row, _ = transform_csv_row(_record(category=csv_category))
    assert row.category == model_category


def test_title_is_first_120_chars_trimmed_but_description_is_literal():
    from nexova_shared.incident_csv import transform_csv_row

    long_text = ("palabra " * 40).strip()  # 319 caracteres
    row, _ = transform_csv_row(_record(description=long_text))
    assert len(row.title) <= 120 and row.title == long_text[:120].strip()
    assert row.description == long_text


def test_invalid_rows_are_rejected_with_reasons():
    from nexova_shared.incident_csv import transform_csv_row

    assert transform_csv_row(_record(client_company=""))[1] == ["missing_client_company"]
    assert "invalid_category" in transform_csv_row(_record(category=""))[1]
    assert "invalid_date" in transform_csv_row(_record(date="18/01/2024"))[1]
    assert "closed_without_score" in transform_csv_row(_record(satisfaction_score=""))[1]
    row, failed = transform_csv_row(_record(category=""))
    assert row is None and failed


def test_source_key_falls_back_to_title_and_created_at():
    from nexova_shared.incident_csv import transform_csv_row

    row, _ = transform_csv_row(_record(ticket_id=""))
    assert row.source_key.startswith("Role permissions")
    assert "2024-01-18" in row.source_key


# --- script de seed ---------------------------------------------------------


def test_seed_loads_96_valid_rows_and_matches_briefing(seed_module, engine):
    report = seed_module.run_seed(CSV, engine)

    assert report.total_rows == 100
    assert report.inserted == 96
    assert len(report.invalid) == 4
    assert report.by_status == {"open": 27, "resolved": 56, "discarded": 13}
    assert report.by_category == {
        "technical_failure": 49,
        "process_error": 35,
        "client_complaint": 12,
    }
    assert report.matches_briefing


def test_seed_is_idempotent(seed_module, engine):
    first = seed_module.run_seed(CSV, engine)
    second = seed_module.run_seed(CSV, engine)

    assert first.inserted == 96
    assert second.inserted == 0
    assert second.already_present == 96
    assert second.seeded_total == 96


def test_seeded_rows_follow_briefing_fields(seed_module, engine):
    from incident_manager.models import ManagedIncident
    from sqlmodel import Session, select

    seed_module.run_seed(CSV, engine)
    with Session(engine) as session:
        rows = session.exec(select(ManagedIncident)).all()
    assert {r.origin for r in rows} == {"customer"}
    assert {r.branch for r in rows} == {"central"}
    assert all(r.created_at == r.updated_at for r in rows)
    assert all(r.title and r.title == r.title.strip() and len(r.title) <= 120 for r in rows)


def test_invalid_rows_are_not_inserted_and_never_leak_email(seed_module, engine, capsys):
    from incident_manager.models import ManagedIncident
    from sqlmodel import Session, select

    report = seed_module.run_seed(CSV, engine)
    seed_module.print_report(report)
    out = capsys.readouterr().out

    assert "Inválidas (no insertadas):   4" in out
    assert "@" not in out  # nunca se imprime customer_email
    with Session(engine) as session:
        assert len(session.exec(select(ManagedIncident)).all()) == 96


def test_seed_summary_endpoint_matches_expected_totals(seed_module, engine, make_client):
    seed_module.run_seed(CSV, engine)
    body = make_client().get("/api/incidents/summary").json()

    assert body["total"] == 96
    assert body["by_status"] == {"open": 27, "in_progress": 0, "resolved": 56, "discarded": 13}
    assert body["by_category"]["technical_failure"] == 49
    assert body["by_category"]["process_error"] == 35
    assert body["by_category"]["client_complaint"] == 12
    assert body["by_origin"]["customer"] == 96 and body["by_branch"]["central"] == 96


def test_cli_refuses_remote_database_without_confirmation(seed_module):
    with pytest.raises(SystemExit):
        seed_module.main(["--database-url", "postgresql://u:p@db.example.com/x"])


# --- restricciones en base de datos ----------------------------------------


def test_database_check_constraints_reject_values_outside_the_briefing(engine):
    from datetime import datetime, timezone

    from incident_manager.models import ManagedIncident
    from sqlalchemy.exc import IntegrityError
    from sqlmodel import Session

    base = {
        "title": "t",
        "description": "d",
        "category": "other",
        "status": "open",
        "origin": "internal",
        "branch": "central",
        "created_at": datetime.now(timezone.utc),
    }
    for column, bad in (
        ("branch", "headquarters"),
        ("status", "closed"),
        ("origin", "partner"),
        ("category", "TECHNICAL"),
        ("title", "   "),
    ):
        with Session(engine) as session:
            session.add(ManagedIncident(**{**base, column: bad}))
            with pytest.raises(IntegrityError):
                session.commit()


# --- empresa cliente --------------------------------------------------------


def test_transform_keeps_the_client_company():
    from nexova_shared.incident_csv import transform_csv_row

    row, _ = transform_csv_row(_record(client_company="  FinServ Group "))
    assert row.client_company == "FinServ Group"


def test_seed_stores_the_client_company_of_every_row(seed_module, engine):
    from incident_manager.models import ManagedIncident
    from sqlmodel import Session, select

    seed_module.run_seed(CSV, engine)
    with Session(engine) as session:
        companies = {r.client_company for r in session.exec(select(ManagedIncident)).all()}
    assert None not in companies and len(companies) == 12  # las 12 empresas cliente del CSV


def test_seed_backfills_company_on_rows_loaded_without_it(seed_module, engine):
    from incident_manager.models import ManagedIncident
    from sqlmodel import Session, select

    seed_module.run_seed(CSV, engine)
    with Session(engine) as session:  # simula la base cargada antes de existir la columna
        for incident in session.exec(select(ManagedIncident)).all():
            incident.client_company = None
            session.add(incident)
        session.commit()

    report = seed_module.run_seed(CSV, engine)

    assert report.inserted == 0 and report.backfilled == 96
    with Session(engine) as session:
        assert all(r.client_company for r in session.exec(select(ManagedIncident)).all())
    assert seed_module.run_seed(CSV, engine).backfilled == 0  # y no repite el relleno


def test_manual_company_is_never_overwritten_by_the_backfill(seed_module, engine):
    from incident_manager.models import ManagedIncident
    from sqlmodel import Session, select

    seed_module.run_seed(CSV, engine)
    with Session(engine) as session:
        first = session.exec(select(ManagedIncident).order_by(ManagedIncident.id)).first()
        first.client_company = "Empresa corregida a mano"
        session.add(first)
        session.commit()

    seed_module.run_seed(CSV, engine)
    with Session(engine) as session:
        first = session.exec(select(ManagedIncident).order_by(ManagedIncident.id)).first()
        assert first.client_company == "Empresa corregida a mano"


# --- errores y códigos de salida -------------------------------------------


def _run_cli(seed_module, tmp_path, csv_text: str | None, db: str | None = None) -> int:
    csv = tmp_path / "in.csv"
    if csv_text is not None:
        csv.write_bytes(csv_text.encode("utf-8") if isinstance(csv_text, str) else csv_text)
    with pytest.raises(SystemExit) as exit_info:
        seed_module.main([str(csv), "--database-url", db or f"sqlite:///{tmp_path}/t.db"])
    return exit_info.value.code


def test_empty_csv_exits_with_error_and_clear_message(seed_module, tmp_path, capsys):
    assert _run_cli(seed_module, tmp_path, "") == 1
    err = capsys.readouterr().err
    assert err.startswith("Error:") and "Traceback" not in err


def test_csv_without_data_rows_exits_with_error(seed_module, tmp_path, capsys):
    assert _run_cli(seed_module, tmp_path, "ticket_id,date,client_company\n") == 1
    assert "Error:" in capsys.readouterr().err


def test_non_utf8_csv_exits_with_error(seed_module, tmp_path, capsys):
    csv = tmp_path / "in.csv"
    csv.write_bytes(b"ticket_id,date\n\xff\xfe\x00bad\n")
    with pytest.raises(SystemExit) as exit_info:
        seed_module.main([str(csv), "--database-url", f"sqlite:///{tmp_path}/t.db"])
    assert exit_info.value.code == 1
    assert "UTF-8" in capsys.readouterr().err


def test_unreachable_database_exits_with_error_without_credentials(seed_module, tmp_path, capsys):
    code = _run_cli(seed_module, tmp_path, CSV.read_text(encoding="utf-8"),
                    db="postgresql://usuario:secreto@127.0.0.1:1/x")  # secret-scan: allow (valor falso de prueba)
    assert code in (1, 2)  # remota sin --yes (2) o conexión rechazada (1): nunca 0 ni traza
    err = capsys.readouterr().err
    assert "secreto" not in err and "Traceback" not in err
