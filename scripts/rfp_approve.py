"""
Operación de la Parte 3 sobre un ticket real (Supabase + checkpointer Postgres), sin Celery.

Uso (desde la raíz del repo):

    # Estado de las ramas de aprobación (pausa, aprobador esperado, ronda):
    uv run --project services/api python scripts/rfp_approve.py status RFP-1A2B3C4D

    # (Re)abrir la aprobación de un departamento (o de todos sin --department):
    uv run --project services/api python scripts/rfp_approve.py start RFP-1A2B3C4D --department soporte

    # Registrar una decisión (resume programático) o continuar una rama caída (sin --decision):
    uv run --project services/api python scripts/rfp_approve.py resume RFP-1A2B3C4D --department soporte \
        --decision approve --as "Roberto Díaz"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data" / "pipelines"))

from rfp_approval import (  # noqa: E402
    active_departments,
    open_checkpointer,
    pending_interrupt,
    resume_approval,
    start_approval,
)
from rfp_intake import SqlRfpStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("status", "start", "resume"))
    parser.add_argument("ticket_id")
    parser.add_argument("--department", help="seleccion | capacitacion | soporte")
    parser.add_argument("--decision", choices=("approve", "request_changes", "reject"))
    parser.add_argument("--as", dest="acting_as", help="Responsable (o árbitro) en cuyo nombre se decide")
    parser.add_argument("--comment", default="")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    store = SqlRfpStore()
    ticket = store.get_ticket(args.ticket_id)
    if ticket is None:
        raise SystemExit(f"No existe el ticket {args.ticket_id}.")
    departments = [args.department] if args.department else active_departments(ticket)

    with open_checkpointer() as checkpointer:
        result = {}
        for department_id in departments:
            if args.command == "status":
                result[department_id] = pending_interrupt(args.ticket_id, department_id, store=store,
                                                          checkpointer=checkpointer)
            elif args.command == "start":
                result[department_id] = start_approval(args.ticket_id, department_id, store=store,
                                                       checkpointer=checkpointer)
            else:
                if not args.department:
                    parser.error("resume necesita --department")
                decision = None
                if args.decision:
                    decision = {"decision": args.decision, "acting_as": args.acting_as or "",
                                "comment": args.comment, "decided_by_user": "scripts/rfp_approve.py"}
                result[department_id] = resume_approval(args.ticket_id, department_id, decision, store=store,
                                                        checkpointer=checkpointer)
    print(json.dumps({"ticket": store.get_ticket(args.ticket_id)["status"], "branches": result},
                     ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
