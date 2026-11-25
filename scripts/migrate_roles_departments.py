"""
Migra `services/api/data/users.json` al modelo de Roles y Permisos (idempotente):

- `manager` pasa a `supervisor` (mismas capacidades internas, ahora con alcance departamental).
- Un `admin` sin departamento pasa a `technology` (el Admin sigue perteneciendo a un departamento).
- Las cuentas externas (`user`) no se tocan.

Los supervisores migrados quedan SIN departamento: hasta que un admin se lo asigne
(`PATCH /users/{id}/assignment` o la vista de administración) no podrán leer datos departamentales.
Haz una copia de `users.json` antes de ejecutarlo sin `--dry-run`.

    uv run --project services/api python scripts/migrate_roles_departments.py --dry-run
    uv run --project services/api python scripts/migrate_roles_departments.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))
sys.path.insert(0, str(ROOT / "scripts"))

from _cli import fail  # noqa: E402

USERS_PATH = ROOT / "services" / "api" / "data" / "users.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", type=Path, default=USERS_PATH)
    parser.add_argument("--dry-run", action="store_true", help="Solo muestra los cambios.")
    args = parser.parse_args(argv)

    if not args.db.exists():
        return fail(f"No existe {args.db}.")

    from rbac import seeding

    seeding.seed_roles_and_departments()
    changes = seeding.migrate_users(args.db, dry_run=args.dry_run)
    if not changes:
        print("Nada que migrar.")
    for change in changes:
        print(change)
    print(("Simulación: no se escribió nada." if args.dry_run else f"{len(changes)} usuario(s) migrado(s)."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
