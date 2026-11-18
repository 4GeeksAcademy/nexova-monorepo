#!/usr/bin/env python3
"""
Carga el histórico de incidencias del helpdesk (CSV) en el gestor de incidencias.

- Reutiliza la validación del analizador a través de `nexova_shared`
  (`packages/shared`): no hay reglas duplicadas.
- Aplica las transformaciones CSV → modelo del BRIEFING antes de insertar
  (estado, categoría, título, fecha, origin = customer, branch = central).
- Los registros inválidos NO se insertan y se listan al final.
- Es idempotente: cada fila se identifica por su `ticket_id` (o title +
  created_at) en la tabla `incident_seed_log`, de modo que ejecutarlo dos veces
  no duplica nada.
- Guarda la empresa cliente (`client_company`) y rellena la de filas cargadas antes sin ella.
- No toca la tabla `incident` (legacy) ni escribe `customer_email`.

Uso:
    DATABASE_URL=sqlite:///local.db python scripts/seed_incidents.py
    python scripts/seed_incidents.py scripts/incidents-COMPANY.csv --database-url sqlite:///local.db
    python scripts/seed_incidents.py --yes     # contra una base de datos remota (p. ej. Supabase)

Por seguridad, contra una base que no sea SQLite hay que confirmar con `--yes`.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (
    ROOT / "scripts",  # `_cli` (también al importar el script desde un test)
    ROOT / "services" / "api",
    ROOT / "packages" / "shared",
    ROOT / "packages" / "incidents-analyzer",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

from incident_manager.models import IncidentSeedLog, ManagedIncident  # noqa: E402
from _cli import fail, reason  # noqa: E402
from incidents_analyzer import EmptyFileError, MissingHeaderError, load_records_from_path  # noqa: E402
from nexova_shared.incident_csv import transform_csv_row  # noqa: E402
from nexova_shared.incident_domain import (  # noqa: E402
    EXPECTED_SEED_BY_CATEGORY,
    EXPECTED_SEED_BY_STATUS,
    EXPECTED_SEED_TOTAL,
)

DEFAULT_CSV = ROOT / "scripts" / "incidents-COMPANY.csv"


@dataclass
class InvalidRow:
    line: int  # línea del fichero (la 1 es la cabecera)
    ticket_id: str
    rules: list[str]


@dataclass
class SeedReport:
    total_rows: int = 0
    inserted: int = 0
    already_present: int = 0
    backfilled: int = 0  # ya cargadas sin empresa cliente: se completa desde el CSV
    duplicated_in_file: int = 0
    invalid: list[InvalidRow] = field(default_factory=list)
    by_status: dict[str, int] = field(default_factory=dict)
    by_category: dict[str, int] = field(default_factory=dict)
    seeded_total: int = 0

    @property
    def matches_briefing(self) -> bool:
        return (
            self.seeded_total == EXPECTED_SEED_TOTAL
            and self.by_status == EXPECTED_SEED_BY_STATUS
            and self.by_category == EXPECTED_SEED_BY_CATEGORY
        )


def seeded_counts(session: Session) -> tuple[int, dict[str, int], dict[str, int]]:
    """Totales del subconjunto cargado por este script (no cuenta altas manuales)."""
    rows = session.exec(
        select(ManagedIncident.status, ManagedIncident.category).join(
            IncidentSeedLog, IncidentSeedLog.incident_id == ManagedIncident.id
        )
    ).all()
    by_status = Counter(status for status, _ in rows)
    by_category = Counter(category for _, category in rows)
    return len(rows), dict(by_status), dict(by_category)


def run_seed(csv_path: Path, engine: Engine) -> SeedReport:
    records = load_records_from_path(str(csv_path))
    report = SeedReport(total_rows=len(records))

    SQLModel.metadata.create_all(
        engine, tables=[ManagedIncident.__table__, IncidentSeedLog.__table__]
    )

    with Session(engine) as session:
        seen_in_file: set[str] = set()
        for index, record in enumerate(records, start=2):
            row, failed = transform_csv_row(record)
            if row is None:
                report.invalid.append(
                    InvalidRow(index, (record.get("ticket_id") or "").strip(), failed)
                )
                continue
            if row.source_key in seen_in_file:
                report.duplicated_in_file += 1
                continue
            seen_in_file.add(row.source_key)

            logged = session.get(IncidentSeedLog, row.source_key)
            if logged is not None:
                report.already_present += 1
                existing = session.get(ManagedIncident, logged.incident_id)
                if existing is not None and not existing.client_company:
                    existing.client_company = row.client_company
                    session.add(existing)
                    report.backfilled += 1
                continue

            incident = ManagedIncident(
                title=row.title,
                description=row.description,
                category=row.category,
                status=row.status,
                origin=row.origin,
                branch=row.branch,
                client_company=row.client_company,
                created_at=row.created_at,
                updated_at=row.created_at,  # BRIEFING: updated_at = created_at al insertar
            )
            session.add(incident)
            session.flush()  # obtiene el id generado
            session.add(IncidentSeedLog(source_key=row.source_key, incident_id=incident.id))
            report.inserted += 1

        session.commit()  # todo o nada: un fallo a medias no deja filas sueltas

        report.seeded_total, report.by_status, report.by_category = seeded_counts(session)
    return report


def print_report(report: SeedReport) -> None:
    print(f"Filas leídas del CSV:        {report.total_rows}")
    print(f"Insertadas:                  {report.inserted}")
    print(f"Ya existían (omitidas):      {report.already_present}")
    if report.backfilled:
        print(f"Empresa cliente completada:  {report.backfilled}")
    if report.duplicated_in_file:
        print(f"Duplicadas dentro del CSV:   {report.duplicated_in_file}")
    print(f"Inválidas (no insertadas):   {len(report.invalid)}")
    for bad in report.invalid:
        label = bad.ticket_id or "(sin ticket_id)"
        print(f"  - línea {bad.line} · {label}: {', '.join(bad.rules)}")

    print("\nCargadas por este script, por estado del modelo:")
    for key, expected in EXPECTED_SEED_BY_STATUS.items():
        print(f"  {key:<18} {report.by_status.get(key, 0):>3}   (esperado {expected})")
    print("Por categoría del modelo:")
    for key, expected in EXPECTED_SEED_BY_CATEGORY.items():
        print(f"  {key:<18} {report.by_category.get(key, 0):>3}   (esperado {expected})")

    verdict = "COINCIDEN" if report.matches_briefing else "NO COINCIDEN"
    print(f"\nTotales vs BRIEFING ({EXPECTED_SEED_TOTAL} registros válidos): {verdict}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("csv", nargs="?", type=Path, default=DEFAULT_CSV, help="ruta del CSV")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="por defecto, la variable de entorno DATABASE_URL",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="confirma la carga en una base de datos que no es SQLite",
    )
    args = parser.parse_args(argv)

    if not args.database_url:
        parser.error("indica --database-url o define DATABASE_URL")
    if not args.csv.is_file():
        parser.error(f"no existe el fichero {args.csv}")

    engine = create_engine(args.database_url)
    if engine.dialect.name != "sqlite" and not args.yes:
        host = engine.url.host or "?"
        parser.error(
            f"la base de datos es remota ({engine.dialect.name} en {host}); "
            "añade --yes para confirmar la carga"
        )

    try:
        report = run_seed(args.csv, engine)
    except (EmptyFileError, MissingHeaderError) as exc:
        fail(f"el CSV no es utilizable: {exc}")
    except (OSError, UnicodeDecodeError) as exc:
        fail(f"no se pudo leer {args.csv} ({reason(exc)}); debe ser un CSV UTF-8.")
    except SQLAlchemyError as exc:  # nada se confirma: la carga es una sola transacción
        fail(f"no se pudo cargar en la base de datos ({reason(exc)}); no se guardó ninguna fila.")
    print_report(report)
    return 0 if report.matches_briefing else 1


if __name__ == "__main__":
    raise SystemExit(main())
