"""
Ultimate document synthesizer (Parte 3): consolida las secciones APROBADAS en
el documento final que recibe Ventas.

Es determinista (sin LLM): el texto de cada sección es exactamente el que su
responsable aprobó; aquí solo se añade la cabecera, las condiciones generales
del BRIEFING §5 y el registro de aprobaciones. Solo se genera cuando TODAS las
secciones activas están `aprobada`.
"""

from __future__ import annotations

from typing import Any

from rfp_intake.departments import (
    APPROVAL_APPROVED,
    CURRENCY_BY_HQ,
    DEPARTMENT_IDS,
    DEPARTMENTS,
)
from rfp_intake.store import RfpStore


def active_departments(ticket: dict[str, Any]) -> list[str]:
    """Departamentos de la propuesta: los que la Parte 2 entregó a aprobación, en orden canónico."""
    handoff = ticket.get("approval_handoff") or {}
    active = {s["department_id"] for s in handoff.get("sections") or []}
    return [d for d in DEPARTMENT_IDS if d in active]


def all_approved(ticket: dict[str, Any]) -> bool:
    sections = {s["department_id"]: s for s in ticket.get("sections") or []}
    active = active_departments(ticket)
    return bool(active) and all(
        sections.get(d, {}).get("approval_status") == APPROVAL_APPROVED for d in active
    )


def _approval_record(section: dict[str, Any]) -> dict[str, Any]:
    history = section.get("approval_history") or []
    return {
        "department_id": section["department_id"],
        "department": DEPARTMENTS[section["department_id"]].name,
        "owner": DEPARTMENTS[section["department_id"]].owner,
        "approver": section.get("approver"),
        "approved_at": section.get("approved_at"),
        "decided_by_user": section.get("decided_by_user"),
        "requested_at": section.get("approval_requested_at"),
        "rounds": section.get("approval_round", 0),
        "iterations": section.get("iteration_count", 0),
        "decisions": len(history),
    }


def _short(value: Any) -> str:
    """`2026-11-16T22:15:00.04+00:00` → `2026-11-16 22:15 UTC`."""
    text = str(value or "")
    return f"{text[:16].replace('T', ' ')} UTC" if len(text) >= 16 else (text or "—")


def build_final_document(ticket: dict[str, Any]) -> dict[str, Any]:
    metadata = ticket.get("metadata") or {}
    handoff = ticket.get("approval_handoff") or {}
    client_hq = metadata.get("client_hq") or handoff.get("client_hq")
    currency = CURRENCY_BY_HQ.get(client_hq or "") or handoff.get("currency")
    by_id = {s["department_id"]: s for s in ticket.get("sections") or []}
    departments = active_departments(ticket)

    sections = [
        {
            "department_id": d,
            "department": DEPARTMENTS[d].name,
            "owner": DEPARTMENTS[d].owner,
            "content": by_id[d]["draft_content"],
            "approver": by_id[d].get("approver"),
            "approved_at": by_id[d].get("approved_at"),
        }
        for d in departments
    ]
    approvals = [_approval_record(by_id[d]) for d in departments]

    client = metadata.get("client_name") or handoff.get("client_name") or "Cliente"
    lines = [
        f"# Propuesta económica de Nexova Solutions para {client}",
        "",
        f"- **Ticket:** {ticket['ticket_id']}" + (f" · **Ref. RFP:** {ticket['rfp_id']}" if ticket.get("rfp_id") else ""),
        f"- **Sede del cliente:** {client_hq or 'por confirmar'} · **Moneda de la propuesta:** {currency or 'por confirmar'}",
    ]
    if metadata.get("deadline") or handoff.get("deadline"):
        lines.append(f"- **Fecha límite de entrega de la propuesta:** {metadata.get('deadline') or handoff.get('deadline')}")
    lines += ["", "---", ""]
    for section in sections:
        lines += [section["content"].strip(), "",
                  f"_Sección aprobada por {section['approver']} ({section['department']})._", "", "---", ""]
    lines += [
        "## Condiciones generales",
        "",
        "- Todos los servicios incluyen la garantía de satisfacción estándar de Nexova a 90 días.",
        f"- Todas las cifras de esta propuesta se expresan en {currency or 'la moneda de la sede del cliente'}.",
        "- Las referencias a otros clientes se presentan de forma anónima.",
        "",
        "## Registro de aprobaciones",
        "",
        "| Departamento | Responsable | Aprobado por | Fecha | Rondas de cambios |",
        "|---|---|---|---|---|",
        *[f"| {a['department']} | {a['owner']} | {a['approver']} | {_short(a['approved_at'])} | {a['rounds']} |"
          for a in approvals],
    ]
    return {"currency": currency, "sections": sections, "approvals": approvals,
            "content_markdown": "\n".join(lines).strip() + "\n"}


def maybe_finalize(store: RfpStore, ticket_id: str) -> dict[str, Any] | None:
    """Si todas las secciones activas están aprobadas, guarda el documento final (ticket → `terminado`)."""
    ticket = store.get_ticket(ticket_id)
    if ticket is None or not all_approved(ticket):
        return None
    return store.save_final_document(ticket_id, build_final_document(ticket))
