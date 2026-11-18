"""
Resumen de activaciones de los guardrails del agente (mismo cálculo que
`GET /agent/guardrails/summary`), leído del log JSONL append-only.

    uv run --project services/api python scripts/agent_guardrails_report.py
    uv run --project services/api python scripts/agent_guardrails_report.py --since 2026-11-15T10:00:00Z
    uv run --project services/api python scripts/agent_guardrails_report.py --conversation-id <uuid> --json

El log está en `AGENT_GUARDRAIL_LOG` (por defecto
`services/api/data/agent/guardrail_events.jsonl`).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))
load_dotenv(REPO_ROOT / ".env")

from _cli import fail, reason  # noqa: E402
from agent.guardrails.events import log_path, read_events, summarize  # noqa: E402


def _table(title: str, counts: dict[str, int]) -> str:
    rows = "\n".join(f"  {name:<40} {count:>5}" for name, count in counts.items()) or "  (ninguna)"
    return f"{title}\n{rows}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", type=datetime.fromisoformat, help="ISO 8601 (inicio de la sesión de pruebas)")
    parser.add_argument("--until", type=datetime.fromisoformat, help="ISO 8601")
    parser.add_argument("--conversation-id")
    parser.add_argument("--json", action="store_true", help="salida JSON")
    args = parser.parse_args()

    try:
        events = read_events()
    except (OSError, ValueError) as exc:
        fail(f"no se pudo leer el log de guardrails en {log_path()} ({reason(exc)}).")
    summary = summarize(events, since=args.since, until=args.until, conversation_id=args.conversation_id)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    print(f"Log: {log_path()}")
    print(f"Activaciones: {summary['total']} en {summary['runs_affected']} corridas\n")
    print(_table("Por tipo de fallo", summary["by_failure_type"]), end="\n\n")
    print(_table("Por guard", summary["by_guard"]), end="\n\n")
    print(_table("Por acción", summary["by_action"]), end="\n\n")
    print(_table("Por regla (guard:regla)", summary["by_rule"]))


if __name__ == "__main__":
    main()
