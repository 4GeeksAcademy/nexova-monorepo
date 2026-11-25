"""
Auditoría OWASP — A07 (autenticación) y A01 (autorización por rol) en la API.

- H1: sin límite de intentos en `/auth/*` → fuerza bruta y email bombing.
- M:  un token seguía siendo válido tras desactivar la cuenta o cambiar la contraseña.
- H2: `GET /users` y `GET /users/{id}` exponían todas las cuentas a cualquier usuario.
- H3: `/knowledge/query` (sin guardrails) abierto a cualquier cuenta.

    uv run --project services/api pytest tests/security -v
"""

from __future__ import annotations

from datetime import UTC



def bearer(token: str) -> dict[str, str]:
    # Local, no importado de `conftest`: con varios directorios de tests en la misma
    # ejecución, `import conftest` puede resolver al conftest de otra carpeta.
    return {"Authorization": f"Bearer {token}"}


def test_sixth_failed_login_is_rate_limited_with_retry_after(api, register):
    register("ana@nexova-solutions.com")
    for _ in range(5):
        assert api.post("/auth/login", json={"email": "ana@nexova-solutions.com",
                                             "password": "incorrecta1"}).status_code == 401
    blocked = api.post("/auth/login", json={"email": "ana@nexova-solutions.com", "password": "Sup3r-secreta!"})
    assert blocked.status_code == 429  # ni siquiera con la contraseña buena durante el bloqueo
    assert int(blocked.headers["Retry-After"]) > 0


def test_unknown_emails_are_limited_too_and_answer_like_wrong_passwords(api):
    first = api.post("/auth/login", json={"email": "nadie@x.com", "password": "loquesea1"})
    assert first.status_code == 401 and first.json()["detail"] == "Email o contraseña incorrectos."
    for _ in range(4):
        api.post("/auth/login", json={"email": "nadie@x.com", "password": "loquesea1"})
    assert api.post("/auth/login", json={"email": "nadie@x.com", "password": "loquesea1"}).status_code == 429


def test_forgot_password_cannot_be_used_to_flood_an_inbox(api, register):
    register("ana@nexova-solutions.com")
    codes = [api.post("/auth/forgot-password", json={"email": "ana@nexova-solutions.com"}).status_code
             for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_token_of_deactivated_account_is_rejected(api, register):
    from routes import users
    from tinydb import Query, TinyDB

    user_id, token = register("ana@nexova-solutions.com")
    assert api.get("/auth/me", headers=bearer(token)).status_code == 200
    db = TinyDB(users.DB_PATH)
    db.table("users").update({"is_active": False}, Query().id == user_id)
    db.close()
    assert api.get("/auth/me", headers=bearer(token)).status_code == 401


def test_password_change_invalidates_older_tokens(api, register):
    from datetime import datetime, timedelta, timezone

    from routes import users
    from tinydb import Query, TinyDB

    user_id, token = register("ana@nexova-solutions.com")
    later = (datetime.now(UTC) + timedelta(seconds=5)).isoformat()
    db = TinyDB(users.DB_PATH)
    db.table("users").update({"password_changed_at": later}, Query().id == user_id)
    db.close()
    assert api.get("/auth/me", headers=bearer(token)).status_code == 401


def test_regular_user_cannot_list_or_read_other_accounts(api, register):
    other_id, _ = register("otra@cliente.com")
    _, token = register("curioso@cliente.com")
    assert api.get("/users", headers=bearer(token)).status_code == 403
    assert api.get(f"/users/{other_id}", headers=bearer(token)).status_code == 403


def test_admin_can_list_accounts(api, register):
    register("otra@cliente.com")
    _, admin = register("admin@nexova-solutions.com", role="admin")
    assert len(api.get("/users", headers=bearer(admin)).json()) == 2


def test_self_registration_cannot_escalate_role(api):
    r = api.post("/auth/register", json={"email": "x@x.com", "password": "Sup3r-secreta!", "role": "admin"})
    assert r.status_code == 201 and r.json()["role"] == "user"


def test_knowledge_query_without_guardrails_is_staff_only(api, register):
    _, token = register("cliente@acme.com")
    r = api.post("/knowledge/query", json={"question": "¿Qué descuento máximo dais?"}, headers=bearer(token))
    assert r.status_code == 403


def test_api_responses_carry_security_headers(api):
    r = api.post("/auth/login", json={"email": "nadie@x.com", "password": "loquesea1"})
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]


def test_wildcard_cors_origin_is_ignored(monkeypatch):
    from http_security import allowed_origins, origin_allowed

    monkeypatch.setenv("ALLOWED_ORIGINS", "*, http://localhost:3001 ")
    assert allowed_origins() == ["http://localhost:3001"]
    assert origin_allowed("http://localhost:3001") and origin_allowed(None)
    assert not origin_allowed("https://evil.example")
