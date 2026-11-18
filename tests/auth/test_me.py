"""GET /auth/me — identidad de la sesión activa."""

from __future__ import annotations

import pytest
from tinydb import TinyDB

# --- Camino feliz -------------------------------------------------------------


def test_me_returns_the_callers_own_email_role_and_profile(api, register, auth_header):
    user_id = register("ana@example.com", name="Ana")
    register("luis@example.com", name="Luis")

    body = api.get("/auth/me", headers=auth_header("ana@example.com")).json()

    assert body["email"] == "ana@example.com"
    assert body["role"] == "user"
    assert body["profile"]["user_id"] == user_id


# --- Casos límite -------------------------------------------------------------


def test_me_reflects_a_role_change_made_after_the_token_was_issued(api, register, auth_header, set_user_field):
    user_id = register("ana@example.com")
    headers = auth_header("ana@example.com")

    set_user_field(user_id, role="manager")

    # El rol se lee de la base en cada petición, no del token.
    assert api.get("/auth/me", headers=headers).json()["role"] == "manager"


# --- Modos de fallo -----------------------------------------------------------


def test_missing_authorization_header_is_unauthorized(api):
    assert api.get("/auth/me").status_code in (401, 403)


@pytest.mark.parametrize("token", ["", "basura", "a.b.c"])
def test_malformed_token_is_unauthorized(api, token):
    response = api.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_user_without_profile_gets_404(api, register, auth_header):
    from routes import profiles

    register("ana@example.com")
    headers = auth_header("ana@example.com")
    db = TinyDB(profiles.DB_PATH)
    db.table("profiles").truncate()
    db.close()

    assert api.get("/auth/me", headers=headers).status_code == 404


def test_token_of_a_deleted_user_is_rejected(api, register, auth_header):
    from routes import users

    register("ana@example.com")
    headers = auth_header("ana@example.com")
    db = TinyDB(users.DB_PATH)
    db.table("users").truncate()
    db.close()

    assert api.get("/auth/me", headers=headers).status_code == 401
