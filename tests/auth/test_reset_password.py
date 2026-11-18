"""POST /auth/reset-password — consumo del token del email."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import DEFAULT_PASSWORD
from jose import jwt
from tinydb import TinyDB

NEW_PASSWORD = "Nueva-clave-9"


def _reset(api, token: str, new_password: str = NEW_PASSWORD):
    return api.post("/auth/reset-password", json={"token": token, "new_password": new_password})


def _issue_token(user_id: str) -> str:
    import password_resets

    return password_resets.create_reset_token(user_id)


def _expire(token: str, expires_at: datetime) -> None:
    """Fija la expiración del token (identificado por su hash) en la base de resets."""
    import password_resets
    from tinydb import Query

    db = TinyDB(password_resets.DB_PATH)
    db.table("password_resets").update(
        {"expires_at": expires_at.isoformat()}, Query().token_hash == password_resets._hash_token(token)
    )
    db.close()


# --- Camino feliz -------------------------------------------------------------


def test_valid_token_changes_the_password(api, register, login):
    user_id = register("ana@example.com")

    response = _reset(api, _issue_token(user_id))

    assert response.status_code == 200
    assert login("ana@example.com", NEW_PASSWORD).status_code == 200
    assert login("ana@example.com", DEFAULT_PASSWORD).status_code == 401


def test_completed_reset_is_audited(api, register, telemetry):
    user_id = register("ana@example.com")

    _reset(api, _issue_token(user_id))

    assert [event for event, _ in telemetry] == ["password_reset_completed"]


# --- Casos límite -------------------------------------------------------------


def test_token_can_only_be_used_once(api, register, login):
    user_id = register("ana@example.com")
    token = _issue_token(user_id)
    assert _reset(api, token).status_code == 200

    second = _reset(api, token, "Otra-clave-77")

    assert second.status_code == 400
    # La segunda contraseña no se aplicó: sigue valiendo la del primer uso.
    assert login("ana@example.com", NEW_PASSWORD).status_code == 200


def test_token_valid_until_its_expiry_and_dead_after(api, register):
    user_id = register("ana@example.com")
    almost = _issue_token(user_id)
    expired = _issue_token(user_id)
    now = datetime.now(UTC)
    _expire(almost, now + timedelta(minutes=1))
    _expire(expired, now - timedelta(seconds=1))

    assert _reset(api, expired).status_code == 400
    assert _reset(api, almost).status_code == 200


def test_short_new_password_is_rejected_without_burning_the_token(api, register):
    user_id = register("ana@example.com")
    token = _issue_token(user_id)

    assert _reset(api, token, "corta12").status_code == 422

    # La validación falla antes de consumir el token: el usuario puede reintentar.
    assert _reset(api, token).status_code == 200


# --- Modos de fallo -----------------------------------------------------------


def test_invented_or_empty_token_is_rejected(api, register):
    register("ana@example.com")

    for token in ("inventado", ""):
        response = _reset(api, token)
        assert response.status_code == 400
        assert "invalido" in response.json()["detail"]


def test_tenth_invalid_token_from_one_ip_is_blocked(api):
    for index in range(10):
        assert _reset(api, f"falso-{index}").status_code == 400

    assert _reset(api, "falso-11").status_code == 429


def test_valid_token_is_also_blocked_once_the_ip_is_locked_out(api, register):
    user_id = register("ana@example.com")
    token = _issue_token(user_id)
    for index in range(10):
        _reset(api, f"falso-{index}")

    assert _reset(api, token).status_code == 429


def test_reset_invalidates_sessions_issued_before_it(api, register, auth_header):
    import security

    user_id = register("ana@example.com")
    # Token emitido hace un minuto, es decir, antes del reset.
    old_token = jwt.encode(
        {"sub": user_id, "exp": datetime.now(UTC) + timedelta(minutes=30),
         "iat": int((datetime.now(UTC) - timedelta(minutes=1)).timestamp())},
        security.SECRET_KEY,
        algorithm=security.ALGORITHM,
    )
    assert api.get("/auth/me", headers={"Authorization": f"Bearer {old_token}"}).status_code == 200

    _reset(api, _issue_token(user_id))

    # Quien robó una sesión antigua pierde el acceso al restablecer la contraseña.
    assert api.get("/auth/me", headers={"Authorization": f"Bearer {old_token}"}).status_code == 401
