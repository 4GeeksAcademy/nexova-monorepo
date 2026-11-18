"""
Informe NIST · Protect — ningún secreto hardcodeado (README, criterio de evaluación 2).

El test ejecuta el mismo escáner que se documenta en el informe
(`scripts/security/secret_scan.py`) sobre los archivos del repositorio y, además,
comprueba que el escáner detecta de verdad cada formato (si no, un 0 no prueba nada).
Los valores de prueba se construyen en tiempo de ejecución para que este archivo no
se detecte a sí mismo.

    uv run --project services/api pytest tests/security/test_no_hardcoded_secrets.py -v
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("secret_scan", ROOT / "scripts" / "security" / "secret_scan.py")
secret_scan = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["secret_scan"] = secret_scan  # los dataclasses resuelven su módulo por nombre
_spec.loader.exec_module(secret_scan)


def test_repository_has_no_hardcoded_secrets() -> None:
    findings, scanned = secret_scan.scan(ROOT)
    assert scanned > 100
    assert findings == [], secret_scan.report(findings, scanned)


A = "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8s9T0"


@pytest.mark.parametrize("line, rule", [
    (f'LLM_API_KEY = "{"sk-or-v1-" + A}"', "openai_like_key"),
    (f'key = "{"re_" + A[:24]}"', "resend_key"),
    (f'aws = "{"AKIA" + "ABCDEFGHIJKLMNOP"}"', "aws_access_key"),
    (f'token = "{"ghp_" + A}"', "github_token"),
    ("-----BEGIN " + "RSA PRIVATE KEY-----", "private_key_pem"),
    ("eyJ" + "hbGciOiJIUzI1NiJ9" + ".eyJ" + "zdWIiOiJ1c2VyLTEyMyJ9" + "." + A[:20], "jwt"),
    ("DATABASE_URL=postgresql://postgres:" + "Sup3rS3cret0" + "@db.supabase.co:5432/postgres", "db_url_with_password"),
    ('SECRET_KEY = "' + "k8Jd02-hS7q" + '"', "literal_secret_key"),
    ('"client_secret": "' + "Zx81LmQp0Rt5" + '"', "literal_client_secret"),
    ('ACCESS_TOKEN = "' + "Zx81LmQp0Rt5aa" + '"', "literal_access_token"),
])
def test_scanner_detects_each_format(line: str, rule: str) -> None:
    findings = secret_scan.scan_text("services/api/config.py", line)
    assert [f.rule for f in findings] == [rule]
    assert A not in findings[0].masked  # el valor nunca se imprime entero


@pytest.mark.parametrize("line", [
    'SECRET_KEY = os.environ["SECRET_KEY"]',
    "SECRET_KEY=${SECRET_KEY}",
    'api_key = os.getenv("LLM_API_KEY")',
    'password = "<tu-contraseña>"',
    "DATABASE_URL=postgresql://user:password@localhost:5432/nexova",
    'const token = process.env.NEXT_PUBLIC_TOKEN ?? "";',
    'const TOKEN_KEY = "nexova_auth_token";',  # nombre de la clave de localStorage
])
def test_scanner_ignores_env_references_and_placeholders(line: str) -> None:
    assert secret_scan.scan_text("services/api/config.py", line) == []


def test_test_credentials_are_only_allowed_inside_tests() -> None:
    line = 'password = "Sup3r-secreta!"'
    assert secret_scan.scan_text("tests/security/test_x.py", line) == []
    assert secret_scan.scan_text("services/api/seed.py", line) != []
