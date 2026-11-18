"""
Escáner de secretos hardcodeados en los archivos versionados (informe NIST · Protect;
README: "ninguna API key o secreto debe estar hardcodeado").

Recorre `git ls-files` (versionados y nuevos no ignorados) y busca:

- formatos de proveedor: OpenAI/OpenRouter/Anthropic (`sk-…`), Resend (`re_…`),
  AWS (`AKIA…`), GitHub (`ghp_…`), claves privadas PEM y JWT firmados;
- URLs de Postgres/Supabase con contraseña;
- asignaciones literales a `*_SECRET`, `*_API_KEY`, `*_PASSWORD`, `*_TOKEN`
  (fuera de `tests/`, donde las credenciales de prueba son intencionadas);
- archivos `.env*` versionados (salvo plantillas `*.example`).

Una línea con el comentario `secret-scan: allow` se omite: solo para valores falsos
que un test necesita a propósito (p. ej. un JWT de juguete que el guard de salida
debe redactar). Cada uso queda visible en el diff.

Los valores se enmascaran en la salida. Sale con código 1 si encuentra algo.

    python scripts/security/secret_scan.py
    python scripts/security/secret_scan.py --output docs/security/evidence/after/secret-scan.txt
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_BYTES = 2 * 1024 * 1024

PROVIDER_PATTERNS: dict[str, re.Pattern[str]] = {
    "openai_like_key": re.compile(r"\bsk-(?:or-v1-|proj-|ant-[a-z0-9]+-)?[A-Za-z0-9_\-]{24,}"),
    "resend_key": re.compile(r"\bre_[A-Za-z0-9]{20,}\b"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "private_key_pem": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{16,}"),
    "db_url_with_password": re.compile(r"\bpostgres(?:ql)?(?:\+\w+)?://[^:\s/@]+:([^@\s<>{}$\"']{6,})@"),
}
ASSIGNMENT = re.compile(
    r"\b([A-Za-z0-9_]*(?:SECRET|API_KEY|APIKEY|PASSWORD|PASSWD|TOKEN|PRIVATE_KEY)[A-Za-z0-9_]*)\b"
    r"""["']?\s*[:=]\s*["']([^"'\s]{8,})["']""",
    re.IGNORECASE,
)
PLACEHOLDER = re.compile(
    r"^(\$\{|<|\{\{)|example|change[-_ ]?me|your[-_]|xxx|dummy|fake|placeholder|redacted|"
    r"^(password|blocked|postgres|secret|test[-_\w]*)$|os\.environ|getenv|process\.env",
    re.IGNORECASE,
)
SKIP_SUFFIXES = (".lock", "-lock.json", ".min.js", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf",
                 ".woff", ".woff2", ".ttf", ".pyc", ".svg", ".webp", ".csv", ".parquet", ".pkl", ".joblib")
CODE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".json", ".yml", ".yaml", ".toml",
                 ".sh", ".ini", ".cfg", ".conf", ".env")
# Hashes bcrypt de cuentas de prueba versionados: riesgo aceptado y documentado (informe OWASP §7).
ACCEPTED = {"services/api/data/users.json"}
ALLOW_PRAGMA = "secret-scan: allow"
# `TOKEN_KEY = "nexova_auth_token"` es el NOMBRE de una clave de localStorage, no un token.
_STRONG_NAME = re.compile(r"SECRET|PASSWORD|PASSWD|API_?KEY|PRIVATE_KEY", re.IGNORECASE)
_NAMING_SUFFIX = re.compile(r"_(KEY|NAME|HEADER|URL|PATH|FIELD|PARAM|COOKIE|STORAGE|TYPE|PREFIX)$", re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    masked: str


def mask(value: str) -> str:
    return value[:4] + "…" + f"({len(value)} caracteres)" if len(value) > 4 else "…"


def tracked_files(root: Path) -> list[str]:
    # Versionados + nuevos sin ignorar: un secreto en un archivo aún sin commitear también cuenta.
    out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                         cwd=root, check=True, capture_output=True).stdout
    return [p for p in out.decode().split("\0") if p]


def scan_text(path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    in_tests = path.startswith("tests/") or "/tests/" in path
    check_assignments = path.endswith(CODE_SUFFIXES) and not in_tests
    for number, line in enumerate(text.splitlines(), start=1):
        if ALLOW_PRAGMA in line:
            continue
        for rule, pattern in PROVIDER_PATTERNS.items():
            for match in pattern.finditer(line):
                value = match.group(1) if match.groups() else match.group(0)
                if rule == "db_url_with_password" and PLACEHOLDER.search(value):
                    continue
                findings.append(Finding(path, number, rule, mask(value)))
        if check_assignments and not any(f.line == number and f.path == path for f in findings):
            for match in ASSIGNMENT.finditer(line):
                name, value = match.group(1), match.group(2)
                if PLACEHOLDER.search(value):
                    continue
                if not _STRONG_NAME.search(name) and _NAMING_SUFFIX.search(name):
                    continue
                findings.append(Finding(path, number, f"literal_{match.group(1).lower()}", mask(value)))
    return findings


def scan(root: Path = REPO_ROOT) -> tuple[list[Finding], int]:
    findings: list[Finding] = []
    files = tracked_files(root)
    scanned = 0
    for rel in files:
        name = Path(rel).name
        if name.startswith(".env") and not name.endswith(".example"):
            findings.append(Finding(rel, 0, "tracked_env_file", "(archivo .env versionado)"))
            continue
        if rel in ACCEPTED or rel.endswith(SKIP_SUFFIXES) or name.endswith(".example"):
            continue
        path = root / rel
        try:
            if not path.is_file() or path.stat().st_size > MAX_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        scanned += 1
        findings.extend(scan_text(rel, text))
    return findings, scanned


def report(findings: list[Finding], scanned: int) -> str:
    lines = [f"secret_scan · {datetime.now(UTC).isoformat(timespec='seconds')} · {scanned} archivos versionados revisados"]
    if not findings:
        lines.append("0 hallazgos: ningún secreto hardcodeado en el repositorio.")
    else:
        lines.append(f"{len(findings)} hallazgos:")
        lines += [f"  {f.path}:{f.line}  {f.rule}  {f.masked}" for f in findings]
    lines.append("Excluidos a propósito: plantillas *.example (placeholders), lockfiles, binarios y "
                 + ", ".join(sorted(ACCEPTED)) + " (riesgo aceptado, informe OWASP §7).")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, help="guarda también el informe en este archivo")
    args = parser.parse_args()
    findings, scanned = scan()
    text = report(findings, scanned)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
