"""
Asigna rol (y opcionalmente empresa cliente) a un usuario existente, escribiendo
directamente en el TinyDB de la API (`services/api/data/users.json`).

Desde la auditoría OWASP (hallazgos C1/C2) el registro público crea siempre
`user` sin empresa, que no accede a datos de negocio. El personal interno
(`manager`/`admin`) y la cuenta de servicio del MCP (`NEXOVA_API_EMAIL`, debe ser
`manager`) se promueven con este script, que solo puede ejecutar quien tenga
acceso al servidor: nunca existe un endpoint público para ello.

    uv run --project services/api python scripts/set_user_role.py ana@nexova-solutions.com manager
    uv run --project services/api python scripts/set_user_role.py ops@cliente.com user --company "Acme Corp"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from models import Role  # noqa: E402
from tinydb import TinyDB  # noqa: E402

DB_PATH = ROOT / "services" / "api" / "data" / "users.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("email")
    parser.add_argument("role", choices=[r.value for r in Role])
    parser.add_argument("--company", default=None, help="client_company (solo usuarios externos)")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    args = parser.parse_args(argv)

    if args.company and args.role != Role.USER.value:
        parser.error("Un usuario con empresa cliente es externo: su rol debe ser 'user'.")

    db = TinyDB(args.db)
    try:
        table = db.table("users")
        email = args.email.strip().lower()
        docs = [d for d in table.all() if str(d.get("email", "")).strip().lower() == email]
        if not docs:
            print(f"No existe ningún usuario con email {email}.", file=sys.stderr)
            return 1
        table.update({"role": args.role, "client_company": args.company}, doc_ids=[docs[0].doc_id])
    finally:
        db.close()
    print(f"{email}: rol={args.role} client_company={args.company}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
