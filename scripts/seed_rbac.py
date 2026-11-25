"""
Siembra los datos de Roles y Permisos del BRIEFING: 3 roles, 7 departamentos, 5 usuarios (rol y
departamento independientes) y 2 informes internos. Idempotente: no pisa lo que ya existe.

La contraseña de desarrollo (compartida por los usuarios sembrados) se lee de `RBAC_SEED_PASSWORD`;
nunca se escribe en el repositorio.

    $env:RBAC_SEED_PASSWORD = "<contraseña de desarrollo>"
    uv run --project services/api python scripts/seed_rbac.py
    uv run --project services/api python scripts/seed_rbac.py --reports-only
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))
sys.path.insert(0, str(ROOT / "scripts"))

from _cli import fail  # noqa: E402

DATA_DIR = ROOT / "services" / "api" / "data"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--users-db", type=Path, default=DATA_DIR / "users.json")
    parser.add_argument("--profiles-db", type=Path, default=DATA_DIR / "profiles.json")
    parser.add_argument("--reports-only", action="store_true", help="No crea usuarios; solo roles, departamentos e informes.")
    args = parser.parse_args(argv)

    from rbac import seeding

    seeding.seed_roles_and_departments()
    print("roles y departamentos: OK")

    if not args.reports_only:
        password = os.environ.get("RBAC_SEED_PASSWORD", "")
        if len(password) < 8:
            return fail("Define RBAC_SEED_PASSWORD (mínimo 8 caracteres) para sembrar los usuarios.")
        from security import hash_password

        result = seeding.seed_users(args.users_db, args.profiles_db, hash_password(password))
        print(f"usuarios: {result['created']} creados, {result['skipped']} ya existían")

    result = seeding.seed_internal_reports(args.users_db)
    print(f"informes internos: {result['created']} creados, {result['skipped']} ya existían")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
