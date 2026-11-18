"""POST /auth/change-password — cambio con la sesión activa."""

from __future__ import annotations

from conftest import DEFAULT_PASSWORD

NEW_PASSWORD = "Nueva-clave-9"


def _change(api, headers, current: str = DEFAULT_PASSWORD, new: str = NEW_PASSWORD):
    return api.post(
        "/auth/change-password",
        json={"current_password": current, "new_password": new},
        headers=headers,
    )


# --- Camino feliz -------------------------------------------------------------


def test_correct_current_password_changes_it(api, register, auth_header, login, telemetry):
    register("ana@example.com")

    response = _change(api, auth_header())

    assert response.status_code == 200
    assert login("ana@example.com", NEW_PASSWORD).status_code == 200
    assert login("ana@example.com", DEFAULT_PASSWORD).status_code == 401
    assert [event for event, _ in telemetry if event == "password_changed"] == ["password_changed"]


# --- Casos límite -------------------------------------------------------------


def test_new_password_equal_to_the_current_one_is_accepted(api, register, auth_header, login):
    register("ana@example.com")

    assert _change(api, auth_header(), new=DEFAULT_PASSWORD).status_code == 200
    assert login("ana@example.com", DEFAULT_PASSWORD).status_code == 200


def test_new_password_of_seven_characters_is_rejected_and_nothing_changes(api, register, auth_header, login):
    register("ana@example.com")

    assert _change(api, auth_header(), new="corta12").status_code == 422
    assert login("ana@example.com", DEFAULT_PASSWORD).status_code == 200


def test_current_password_is_compared_like_at_login_ignoring_edge_spaces(api, register, auth_header):
    register("ana@example.com")

    assert _change(api, auth_header(), current=f" {DEFAULT_PASSWORD} ").status_code == 200


# --- Modos de fallo -----------------------------------------------------------


def test_wrong_current_password_is_rejected_and_nothing_changes(api, register, auth_header, login):
    register("ana@example.com")

    response = _change(api, auth_header(), current="no-es-esta-1")

    assert response.status_code == 400
    assert login("ana@example.com", DEFAULT_PASSWORD).status_code == 200
    assert login("ana@example.com", NEW_PASSWORD).status_code == 401


def test_sixth_attempt_is_blocked_even_with_the_correct_password(api, register, auth_header, login):
    register("ana@example.com")
    headers = auth_header()
    for _ in range(5):
        assert _change(api, headers, current="no-es-esta-1").status_code == 400

    blocked = _change(api, headers)

    assert blocked.status_code == 429
    assert login("ana@example.com", DEFAULT_PASSWORD).status_code == 200  # no cambió


def test_lockout_is_per_user(api, register, auth_header):
    register("ana@example.com")
    register("luis@example.com")
    ana, luis = auth_header("ana@example.com"), auth_header("luis@example.com")
    for _ in range(5):
        _change(api, ana, current="no-es-esta-1")

    assert _change(api, luis).status_code == 200


def test_without_authentication_it_is_unauthorized(api, register):
    register("ana@example.com")

    response = api.post(
        "/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": NEW_PASSWORD},
    )

    assert response.status_code in (401, 403)
