"""POST /auth/login — qué decide la lógica de inicio de sesión."""

from __future__ import annotations

from conftest import DEFAULT_PASSWORD
from jose import jwt

WRONG_CREDENTIALS = "Email o contraseña incorrectos."


# --- Camino feliz -------------------------------------------------------------


def test_valid_credentials_return_a_token_for_that_user(login, register):
    user_id = register("ana@example.com")

    response = login("ana@example.com")

    assert response.status_code == 200
    import security

    claims = jwt.decode(response.json()["access_token"], security.SECRET_KEY, algorithms=[security.ALGORITHM])
    assert claims["sub"] == user_id


def test_successful_login_is_recorded_with_the_role(login, register, telemetry):
    register("ana@example.com")

    login("ana@example.com")

    assert ("login_succeeded", {"role": "user"}) in telemetry


# --- Casos límite -------------------------------------------------------------


def test_email_is_matched_ignoring_case_and_surrounding_spaces(login, register):
    register("ana@example.com")

    assert login("  ANA@Example.COM  ").status_code == 200


def test_empty_password_is_rejected_not_crashed(login, register):
    register("ana@example.com")

    assert login("ana@example.com", "").status_code == 401


def test_sixth_attempt_is_blocked_even_with_the_correct_password(login, register):
    register("ana@example.com")
    for _ in range(5):
        assert login("ana@example.com", "incorrecta-1").status_code == 401

    blocked = login("ana@example.com", DEFAULT_PASSWORD)

    # El bloqueo se decide antes de mirar la contraseña: un atacante que acierta a la
    # sexta no debe obtener un token.
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0


def test_lockout_is_per_ip_and_email_so_another_account_is_unaffected(login, register):
    register("ana@example.com")
    register("luis@example.com")
    for _ in range(5):
        login("ana@example.com", "incorrecta-1")

    assert login("luis@example.com").status_code == 200


def test_successful_login_resets_the_failure_counter(login, register):
    register("ana@example.com")
    for _ in range(4):
        login("ana@example.com", "incorrecta-1")
    assert login("ana@example.com").status_code == 200

    # Si el contador no se hubiera reiniciado, estos 4 nuevos fallos llevarían al bloqueo.
    for _ in range(4):
        assert login("ana@example.com", "incorrecta-1").status_code == 401
    assert login("ana@example.com").status_code == 200


# --- Modos de fallo -----------------------------------------------------------


def test_unknown_email_gets_the_same_answer_as_a_wrong_password(login, register):
    register("ana@example.com")

    wrong_password = login("ana@example.com", "incorrecta-1")
    unknown_email = login("nadie@example.com")

    # Mismo estado y mismo mensaje: la respuesta no revela qué cuentas existen.
    assert (wrong_password.status_code, wrong_password.json()) == (unknown_email.status_code, unknown_email.json())
    assert unknown_email.json()["detail"] == WRONG_CREDENTIALS


def test_unknown_email_still_runs_bcrypt(login, monkeypatch):
    import security
    from routes import auth

    verified: list[str] = []
    monkeypatch.setattr(auth, "verify_password", lambda plain, hashed: verified.append(hashed) or False)

    login("nadie@example.com")

    # Con un email inexistente se verifica contra el hash señuelo: mismo coste de CPU
    # que con un usuario real (sin canal de tiempo para enumerar cuentas).
    assert verified == [security.DUMMY_PASSWORD_HASH]


def test_inactive_user_cannot_log_in_even_with_the_right_password(login, register, set_user_field):
    user_id = register("ana@example.com")
    set_user_field(user_id, is_active=False)

    response = login("ana@example.com")

    assert response.status_code == 401
    assert response.json()["detail"] == "Usuario inactivo."


def test_failed_login_records_the_reason_without_the_email(login, register, telemetry):
    register("ana@example.com")

    login("ana@example.com", "incorrecta-1")

    assert ("login_failed", {"failure_reason": "invalid_credentials"}) in telemetry
    assert "ana@example.com" not in str(telemetry)
