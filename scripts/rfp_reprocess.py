"""
Reprocesa un ticket de RFP o hace una corrida en seco del grafo `rfp_intake`.

Uso (desde la raíz del repo):

    # Reprocesar un ticket existente en Supabase, en este proceso (sin Celery):
    uv run --project services/api python scripts/rfp_reprocess.py RFP-1A2B3C4D

    # Corrida en seco con el LLM real y store en memoria (no toca la base de datos):
    uv run --project services/api python scripts/rfp_reprocess.py --dry-run --pdf ruta/a/rfp.pdf

Lee `DATABASE_URL`, `LLM_BASE_URL`, `LLM_API_KEY` y `GENERATION_MODEL` de
`services/api/.env` o del entorno. Desde el host, el `.env` sirve tal cual:
este script no usa Qdrant ni el MCP.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data" / "pipelines"))

from rfp_intake import InMemoryRfpStore, SqlRfpStore, register_upload, run_intake  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ticket_id", nargs="?", help="Ticket a reprocesar (RFP-XXXXXXXX)")
    parser.add_argument("--dry-run", action="store_true", help="Store en memoria: no escribe en la BD")
    parser.add_argument("--pdf", type=Path, help="PDF a analizar en la corrida en seco")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.dry_run:
        if not args.pdf or not args.pdf.is_file():
            parser.error("--dry-run necesita --pdf con un PDF existente")
        store = InMemoryRfpStore()
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["RFP_RAW_DIR"] = tmp
            ticket = register_upload(args.pdf.read_bytes(), args.pdf.name, "dry-run", store)
            run_intake(ticket["ticket_id"], store=store)
            detail = store.get_ticket(ticket["ticket_id"])
        detail.pop("markdown", None)
        print(json.dumps(detail, ensure_ascii=False, indent=2, default=str))
        return 0 if detail["status"] in ("analisis_completo", "descartado") else 1

    if not args.ticket_id:
        parser.error("indica un ticket_id o usa --dry-run --pdf")
    result = run_intake(args.ticket_id, store=SqlRfpStore())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
