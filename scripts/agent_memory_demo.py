"""
Evidencia REAL de la memoria del agente: dos ciclos completos y los ejemplos
del BRIEFING que no deben generar propuesta.

    uv run --project services/api python scripts/agent_memory_demo.py

Usa el grafo de producción (enrutador y generación con los modelos de 4Geeks,
Qdrant, MCP Server y memoria en Supabase). Requiere lo mismo que
`scripts/agent_record_traces.py` más la migración
`services/api/migrations/007_agent_memory.sql` aplicada.

- Ciclo aprobado: el usuario corrige un SLA → el agente propone recordarlo →
  "sí" → se escribe → otra persona, en otra conversación, pregunta y la
  respuesta usa el hecho.
- Ciclo rechazado: preferencia de un cliente → propuesta → "no" → la memoria
  activa no cambia y una pregunta posterior no la usa.

Escribe `docs/agent/memory-evidence.md` (respuestas, decisiones y filas de
`agent_memory_audit`) y copia los traces a `tests/pipelines/fixtures/agent_memory/`.
Si el modelo real no propone o no clasifica como se espera, el informe lo dice.
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))
load_dotenv(REPO_ROOT / ".env")

from agent.graph import get_agent_graph  # noqa: E402
from agent.memory.schemas import AuditEvent  # noqa: E402
from agent.memory.store import PostgresMemoryStore  # noqa: E402
from agent.runner import AgentRunResult, run_agent  # noqa: E402

EVIDENCE = REPO_ROOT / "docs" / "agent" / "memory-evidence.md"
FIXTURES = REPO_ROOT / "tests" / "pipelines" / "fixtures" / "agent_memory"
SUPPORT_A = "demo-soporte-a"
SUPPORT_B = "demo-soporte-b"

CYCLES = {
    "approved": {
        "proposal": ("En realidad los tickets de nómina para clientes de retail ahora se resuelven en 24 horas, "
                     "no en 48 como decía el SLA anterior."),
        "decision": "Sí, guárdalo.",
        "later": "¿En cuánto tiempo hay que resolver los tickets de nómina de clientes de retail?",
    },
    "rejected": {
        "proposal": ("Ese cliente de finanzas siempre pide que le confirmemos por email antes de cerrar un "
                     "ticket, aunque el sistema permita cerrarlo directo."),
        "decision": "No, no hace falta que lo recuerdes.",
        "later": "¿Hay algo especial que deba hacer antes de cerrar los tickets del cliente de finanzas?",
    },
}
NOT_MEMORABLE = [
    "¿Cuántos tickets abiertos hay ahora mismo?",
    "Perfecto, gracias por la explicación.",
    "Resume este ticket en dos líneas para el reporte de Laura.",
]


def _save(name: str, result: AgentRunResult) -> str:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    target = FIXTURES / f"{name}.json"
    shutil.copyfile(result.trace_path, target)
    return target.relative_to(REPO_ROOT).as_posix()


def _block(text: str | None) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in (text or "—").splitlines())


def _reset_demo_facts(store: PostgresMemoryStore) -> int:
    """Expira (con auditoría, sin borrar) los hechos que aprobaron los usuarios demo en corridas anteriores.

    Sin esto, una segunda ejecución vería el SLA ya aprendido y no volvería a proponerlo.
    """
    now = datetime.now(timezone.utc)
    demo = [f for f in store.active_facts() if f.approved_by in (SUPPORT_A, SUPPORT_B)]
    with store.transaction():
        for fact in demo:
            store.set_fact_status(fact.id, "expired", "demo_reset", now)
            store.add_audit(AuditEvent(event="expired", entry_id=fact.id, user_id=fact.approved_by,
                                       details={"reason": "demo_reset"}, created_at=now))
    return len(demo)


def main() -> None:
    graph = get_agent_graph()
    store = PostgresMemoryStore()
    reset = _reset_demo_facts(store)
    lines = [
        "# Evidencia: memoria del agente (Hito 8 · Parte 1)",
        "",
        f"Generado con `scripts/agent_memory_demo.py` el {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC, "
        "contra el grafo de producción (modelos de 4Geeks, Qdrant, MCP Server y Supabase).",
        "",
        f"Antes de empezar se expiraron {reset} hechos de corridas anteriores de los usuarios demo "
        "(estado `expired`, motivo `demo_reset`, auditado; nada se borra).",
        "",
    ]

    for cycle, msgs in CYCLES.items():
        facts_before = {f.id for f in store.active_facts()}
        first = run_agent(msgs["proposal"], graph=graph, user_id=SUPPORT_A)
        conv = first.conversation_id
        second = run_agent(msgs["decision"], graph=graph, conversation_id=conv, user_id=SUPPORT_A)
        facts_after = {f.id for f in store.active_facts()}
        third = run_agent(msgs["later"], graph=graph, user_id=SUPPORT_B)

        lines += [
            f"## Ciclo {'aprobado' if cycle == 'approved' else 'rechazado'}",
            "",
            f"`conversation_id` = `{conv}`",
            "",
            f"**1. Usuario ({SUPPORT_A}):** {msgs['proposal']}",
            "",
            _block(first.answer),
            "",
            f"Propuesta registrada: `{first.memory_proposal}` · trace `{_save(f'{cycle}-1-proposal', first)}`",
            "",
            f"**2. Usuario ({SUPPORT_A}), misma conversación:** {msgs['decision']}",
            "",
            _block(second.answer),
            "",
            f"Decisión: `{second.memory_decision}` · trace `{_save(f'{cycle}-2-decision', second)}`",
            "",
            f"Hechos activos antes: {len(facts_before)} · después: {len(facts_after)} · "
            f"nuevos: {sorted(facts_after - facts_before) or 'ninguno'}",
            "",
            f"**3. Otra persona ({SUPPORT_B}), conversación nueva:** {msgs['later']}",
            "",
            _block(third.answer),
            "",
            f"trace `{_save(f'{cycle}-3-later', third)}`",
            "",
            "Auditoría de la conversación (`agent_memory_audit`):",
            "",
            "| created_at | event | proposal_id | entry_id | details |",
            "|---|---|---|---|---|",
        ]
        for event in store.list_audit(conversation_id=conv):
            details = str(event.details).replace("|", "\\|")
            lines.append(f"| {event.created_at:%Y-%m-%d %H:%M:%S} | {event.event} | {event.proposal_id} | "
                         f"{event.entry_id or '—'} | {details} |")
        lines.append("")

    lines += ["## Interacciones que no generan propuesta", "",
              "| Mensaje | ¿Propuesta? |", "|---|---|"]
    for i, message in enumerate(NOT_MEMORABLE, start=1):
        result = run_agent(message, graph=graph, user_id=SUPPORT_A)
        _save(f"not-memorable-{i}", result)
        lines.append(f"| {message} | {'sí (revisar)' if result.memory_proposal else 'no'} |")
    lines.append("")

    EVIDENCE.write_text("\n".join(lines), encoding="utf-8")
    print(f"Evidencia escrita en {EVIDENCE.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
