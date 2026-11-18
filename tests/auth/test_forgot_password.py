"""POST /auth/forgot-password — recuperación sin revelar qué cuentas existen."""

from __future__ import annotations

from routes.auth import GENERIC_FORGOT_PASSWORD_MESSAGE


def _forgot(api, email: str):
    return api.post("/auth/forgot-password", json={"email": email})


# --- Camino feliz -------------------------------------------------------------


def test_active_user_gets_one_email_with_a_usable_token(api, register, outbox):
    user_id = register("ana@example.com")

    response = _forgot(api, "ana@example.com")

    assert response.status_code == 200
    assert response.json()["detail"] == GENERIC_FORGOT_PASSWORD_MESSAGE
    assert len(outbox) == 1
    email, token = outbox[0]
    assert email == "ana@example.com"
    import password_resets

    assert password_resets.consume_reset_token(token) == user_id


# --- Casos límite -------------------------------------------------------------


def test_email_in_other_capitalization_reaches_the_same_account(api, register, outbox):
    register("ana@example.com")

    _forgot(api, "  ANA@Example.com ")

    assert [email for email, _ in outbox] == ["ana@example.com"]


def test_fourth_request_is_blocked_and_sends_nothing(api, register, outbox):
    register("ana@example.com")
    for _ in range(3):
        assert _forgot(api, "ana@example.com").status_code == 200

    blocked = _forgot(api, "ana@example.com")

    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers
    assert len(outbox) == 3


def test_limit_is_also_per_ip_so_rotating_emails_does_not_bypass_it(api, outbox):
    for index in range(3):
        _forgot(api, f"cuenta{index}@example.com")

    # Cuarto email distinto desde la misma IP: bloqueado por la clave de IP.
    assert _forgot(api, "otra@example.com").status_code == 429


# --- Modos de fallo -----------------------------------------------------------


def test_unknown_email_gets_the_same_answer_and_no_email(api, register, outbox):
    register("ana@example.com")

    known = _forgot(api, "ana@example.com")
    unknown = _forgot(api, "nadie@example.com")

    assert (known.status_code, known.json()) == (unknown.status_code, unknown.json())
    assert [email for email, _ in outbox] == ["ana@example.com"]


def test_inactive_user_gets_the_same_answer_and_no_email(api, register, set_user_field, outbox):
    user_id = register("ana@example.com")
    set_user_field(user_id, is_active=False)

    response = _forgot(api, "ana@example.com")

    assert response.status_code == 200
    assert response.json()["detail"] == GENERIC_FORGOT_PASSWORD_MESSAGE
    assert outbox == []


def test_email_provider_failure_does_not_change_the_answer(api, register, monkeypatch):
    from routes import auth

    register("ana@example.com")

    def broken_provider(email, token):
        raise RuntimeError("proveedor caído")

    monkeypatch.setattr(auth, "send_password_reset_email", broken_provider)

    response = _forgot(api, "ana@example.com")

    # Un 500 aquí revelaría que la cuenta existe: se responde igual que siempre.
    assert response.status_code == 200
    assert response.json()["detail"] == GENERIC_FORGOT_PASSWORD_MESSAGE


def test_reset_request_is_audited_only_for_real_accounts(api, register, telemetry):
    register("ana@example.com")

    _forgot(api, "nadie@example.com")
    assert telemetry == []

    _forgot(api, "ana@example.com")
    assert [event for event, _ in telemetry] == ["password_reset_requested"]
