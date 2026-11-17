"""
Ruta E2E reproducible del flujo de RFPs (Partes 1 → 3) con aprobaciones humanas SIMULADAS.

Sin Supabase, sin Redis y sin LLM reales: usa los PDF de muestra del BRIEFING
(`tests/pipelines/fixtures/rfp/`), el LLM falso de los tests, un store y un
checkpointer en memoria. Las aprobaciones se hacen con `resume_approval`
programático (el mismo punto de entrada que usa el worker).

Uso (desde la raíz del repo):

    uv run --project services/api python scripts/rfp_e2e.py                 # Vantex (Madrid, EUR)
    uv run --project services/api python scripts/rfp_e2e.py --rfp informal  # NubeSoft (Miami, USD)
    uv run --project services/api python scripts/rfp_e2e.py --request-changes  # una ronda de cambios
    uv run --project services/api python scripts/rfp_e2e.py --trace         # imprime el trace completo

Imprime: la RFP de entrada, cada pausa y decisión por departamento, los estados
del ticket en orden y el documento final generado.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data" / "pipelines"))
sys.path.insert(0, str(ROOT / "tests" / "pipelines"))  # dobles de prueba (LLM falso y borradores canónicos)

from approval_fakes import Approvals, StatusSpy, drafting_llm  # noqa: E402
from drafting_fakes import GOOD_CAPACITACION, GOOD_SELECCION, GOOD_SOPORTE, Sequence, no_knowledge, section  # noqa: E402
from rfp_drafting import run_drafting  # noqa: E402
from rfp_fakes import FORMAL_PDF, FORMAL_RESPONSES, INFORMAL_PDF, INFORMAL_RESPONSES, FakeLLM  # noqa: E402
from rfp_intake import register_upload, run_intake  # noqa: E402
from rfp_intake.departments import DEPARTMENTS  # noqa: E402

SCENARIOS = {
    "formal": (FORMAL_PDF, FORMAL_RESPONSES, {"seleccion": GOOD_SELECCION, "capacitacion": GOOD_CAPACITACION}),
    "informal": (INFORMAL_PDF, INFORMAL_RESPONSES, {"soporte": GOOD_SOPORTE}),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rfp", choices=sorted(SCENARIOS), default="formal")
    parser.add_argument("--request-changes", action="store_true",
                        help="El primer departamento pide cambios antes de aprobar (vuelve a la Parte 2)")
    parser.add_argument("--trace", action="store_true", help="Imprime el trace completo (agente por nodo)")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    pdf, intake_responses, drafts = SCENARIOS[args.rfp]
    departments = list(drafts)
    if args.request_changes:
        first = departments[0]
        revised = drafts[first].replace("garantía de satisfacción de 90 días de Nexova",
                                        "garantía de satisfacción de 90 días de Nexova (revisada)")
        drafts = {**drafts, first: Sequence(section(drafts[first]), section(revised))}

    store = StatusSpy()
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["RFP_RAW_DIR"] = tmp
        print(f"== RFP de entrada: {pdf.name}")
        ticket_id = register_upload(pdf.read_bytes(), pdf.name, "e2e@nexova.test", store)["ticket_id"]

        print("== Parte 1 · recepción y enrutamiento")
        intake = run_intake(ticket_id, store=store, llm=FakeLLM(intake_responses))
        metadata = store.get_ticket(ticket_id)["metadata"]
        print(f"   {intake['status']} · cliente={metadata['client_name']} · sede={metadata['client_hq']} "
              f"· moneda={metadata['currency']} · departamentos={intake['departments']}")

        print("== Parte 2 · generación y evaluación")
        llm = drafting_llm(drafts)
        drafting = run_drafting(ticket_id, store=store, llm=llm, knowledge_fn=no_knowledge, clients_fn=lambda: [])
        print(f"   {drafting['status']} · secciones={drafting['sections']} · iteraciones={drafting['iterations']}")

        print("== Parte 3 · aprobación humana por departamento (simulada)")
        flow = Approvals(store, llm, ticket_id=ticket_id)
        for department_id in departments:
            pause = flow.start(department_id)["interrupt"]
            print(f"   ⏸ {department_id}: esperando a {pause['expected_approver']} "
                  f"(permitido: {', '.join(pause['allowed_decisions'])}) · hilo rfp-{ticket_id}:{department_id}")
        for department_id in reversed(departments):  # en orden inverso: las ramas son independientes
            owner = DEPARTMENTS[department_id].owner
            if args.request_changes and department_id == departments[0]:
                comment = "Destacar la garantía en un párrafo propio."
                status = flow.decide(department_id, "request_changes", owner, comment)
                print(f"   ✎ {owner} pide cambios en {department_id}: “{comment}” → ronda {status['approval_round']}, "
                      f"nueva pausa para {status['interrupt']['expected_approver']}")
            status = flow.decide(department_id, "approve", owner)
            print(f"   ✓ {owner} aprueba {department_id} → ticket {store.get_ticket(ticket_id)['status']}")

    ticket = store.get_ticket(ticket_id)
    print(f"== Estados del ticket: {' → '.join(store.status_log)}")
    if args.trace:
        print("== Trace")
        for event in store.list_trace(ticket_id):
            print(f"   #{event['seq']:>3} [{event['part']:<8}] {event['agent']:<42} {event['status']:<12} "
                  f"{event['thread_id'] or ''}")
    document = ticket["final_document"]
    if not document:
        print("!! No se generó el documento final")
        return 1
    print("== Documento final")
    print(document["content_markdown"])
    print(json.dumps({"ticket_id": ticket_id, "status": ticket["status"], "currency": document["currency"],
                      "approvals": [{k: a[k] for k in ("department_id", "approver", "rounds")}
                                    for a in document["approvals"]]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
