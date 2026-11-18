"""
Últimas decisiones del agente de soporte (mismo registro que `GET /agent/decisions`):
qué hizo en cada corrida y por qué, sin preguntas ni respuestas.

    uv run --project services/api python scripts/agent_decisions_report.py
    uv run --project services/api python scripts/agent_decisions_report.py --limit 100 --outcome blocked
    uv run --project services/api python scripts/agent_decisions_report.py --run-id <uuid> --json

El registro está en `AGENT_DECISION_LOG` (por defecto
`services/api/data/agent/agent_decisions.jsonl`). El detalle por nodo de una
corrida sigue en su trace (`services/api/data/agent/traces/<run_id>.json`).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))
load_dotenv(REPO_ROOT / ".env")

from _cli import fail, reason  # noqa: E402
from agent.audit import log_path, read_decisions  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--outcome", help="answered | blocked | redirected | tool_fallback | rejected | failed | interrupted")
    parser.add_argument("--run-id")
    parser.add_argument("--json", action="store_true", help="salida JSON")
    args = parser.parse_args()

    if args.limit < 1:
        fail("--limit debe ser un entero ≥ 1.", code=2)
    try:
        records = read_decisions(args.limit if not args.run_id else 100_000)
    except (OSError, ValueError) as exc:
        fail(f"no se pudo leer el registro de decisiones en {log_path()} ({reason(exc)}).")
    if args.outcome:
        records = [r for r in records if r.get("outcome") == args.outcome]
    if args.run_id:
        records = [r for r in records if r.get("run_id") == args.run_id]

    if args.json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
        return

    print(f"Registro: {log_path()}  ·  {len(records)} decisiones")
    counts = Counter(r.get("outcome") for r in records)
    print("Por resultado: " + (", ".join(f"{k}={v}" for k, v in counts.most_common()) or "(ninguna)"))
    print()
    for r in records:
        tenant = (r.get("tenant") or {}).get("client_company") or ("interno" if (r.get("tenant") or {}).get("is_staff") else "-")
        tools = ", ".join(f"{t['tool']}:{t['status']}" for t in r.get("tools") or []) or "-"
        print(f"{r.get('ts', '')[:19]}  {r.get('run_id', '')[:8]}  {r.get('outcome', ''):<13} "
              f"user={r.get('user_id') or '-'} tenant={tenant} tools={tools} {r.get('latency_ms')} ms")
        print(f"    motivo: {r.get('reason')}")


if __name__ == "__main__":
    main()
