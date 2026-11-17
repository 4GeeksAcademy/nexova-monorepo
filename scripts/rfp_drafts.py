"""
Genera y evalúa los borradores de una RFP (Parte 2) sin pasar por Celery.

Uso (desde la raíz del repo):

    # Regenerar los borradores de un ticket ya analizado (escribe en Supabase):
    uv run --project services/api python scripts/rfp_drafts.py RFP-1A2B3C4D

    # Corrida en seco: lee el handoff del ticket en Supabase, pero escribe en memoria:
    uv run --project services/api python scripts/rfp_drafts.py RFP-1A2B3C4D --dry-run

    # Corrida en seco a partir de un PDF (Parte 1 + Parte 2 en memoria, sin BD):
    uv run --project services/api python scripts/rfp_drafts.py --dry-run --pdf ruta/a/rfp.pdf

Opciones: `--iterations N` (por defecto `RFP_MAX_ITERATIONS` o 3) y
`--no-knowledge` para generar sin la base de conocimiento. Desde el host, el
RAG necesita `QDRANT_URL=http://localhost:6333` (el `.env` usa el nombre de Docker).
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data" / "pipelines"))

from rfp_drafting import run_drafting  # noqa: E402
from rfp_drafting.knowledge import no_knowledge, rag_knowledge  # noqa: E402
from rfp_intake import InMemoryRfpStore, SqlRfpStore, register_upload, run_intake  # noqa: E402


def _copy_ticket_to_memory(ticket_id: str) -> InMemoryRfpStore:
    """Copia el ticket (handoff + secciones de la Parte 1) de Supabase a un store en memoria."""
    ticket = SqlRfpStore().get_ticket(ticket_id)
    if ticket is None:
        raise SystemExit(f"No existe el ticket {ticket_id}.")
    store = InMemoryRfpStore()
    store.create_ticket(ticket_id=ticket_id, raw_pdf_path=ticket["raw_pdf_path"],
                        original_filename=ticket["original_filename"], created_by="dry-run")
    for section in ticket["sections"]:
        store.upsert_section(ticket_id, copy.deepcopy(section))
    store.complete(ticket_id, ticket["synthesis"] or {}, ticket["handoff_payload"] or {})
    if not ticket["ready_for_drafting"]:
        store.tickets[ticket_id]["ready_for_drafting"] = False
    return store


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ticket_id", nargs="?", help="Ticket con el análisis de la Parte 1 (RFP-XXXXXXXX)")
    parser.add_argument("--dry-run", action="store_true", help="Escribe en memoria, no en la BD")
    parser.add_argument("--pdf", type=Path, help="Con --dry-run: ejecuta antes la Parte 1 sobre este PDF")
    parser.add_argument("--iterations", type=int, help="Límite del ciclo generador-evaluador")
    parser.add_argument("--no-knowledge", action="store_true", help="Sin base de conocimiento (RAG)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    knowledge = no_knowledge if args.no_knowledge else rag_knowledge

    if args.dry_run:
        if args.pdf:
            store = InMemoryRfpStore()
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["RFP_RAW_DIR"] = tmp
                ticket_id = register_upload(args.pdf.read_bytes(), args.pdf.name, "dry-run", store)["ticket_id"]
                if run_intake(ticket_id, store=store)["status"] != "analisis_completo":
                    print(json.dumps(store.get_ticket(ticket_id)["classification"], ensure_ascii=False, indent=2))
                    return 1
        elif args.ticket_id:
            ticket_id, store = args.ticket_id, _copy_ticket_to_memory(args.ticket_id)
        else:
            parser.error("--dry-run necesita un ticket_id o --pdf")
        run_drafting(ticket_id, store=store, knowledge_fn=knowledge, iterations=args.iterations)
        detail = store.get_ticket(ticket_id)
        print(json.dumps({"status": detail["status"], "approval_handoff": detail["approval_handoff"]},
                         ensure_ascii=False, indent=2, default=str))
        return 0

    if not args.ticket_id:
        parser.error("indica un ticket_id")
    result = run_drafting(args.ticket_id, store=SqlRfpStore(), knowledge_fn=knowledge, iterations=args.iterations)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
