"""POST /auth/register — registro público."""

from __future__ import annotations

import pytest
from conftest import DEFAULT_PASSWORD
from tinydb import TinyDB


def _stored_users():
    from routes import users

    db = TinyDB(users.DB_PATH)
    try:
        return db.table("users").all()
    finally:
        db.close()


# --- Camino feliz -------------------------------------------------------------


def test_registration_creates_an_active_regular_user_with_a_linked_profile(api, register, auth_header):
    user_id = register("ana@example.com", name="Ana", phone="600000000")

    stored = _stored_users()
    assert len(stored) == 1
    assert stored[0]["id"] == user_id
    assert stored[0]["role"] == "user"
    assert stored[0]["is_active"] is True

    # El perfil comparte el id del usuario: /auth/me lo encuentra.
    me = api.get("/auth/me", headers=auth_header("ana@example.com")).json()
    assert me["profile"]["user_id"] == user_id
    assert me["profile"]["name"] == "Ana"


# --- Casos límite -------------------------------------------------------------


def test_duplicate_email_with_other_capitalization_is_rejected(api, register):
    register("ana@example.com")

    response = api.post("/auth/register", json={"email": "ANA@Example.com", "password": DEFAULT_PASSWORD})

    assert response.status_code == 400
    assert len(_stored_users()) == 1


def test_email_is_stored_normalized(register):
    register("  Ana@Example.COM ")

    assert _stored_users()[0]["email"] == "ana@example.com"


@pytest.mark.parametrize(("password", "status"), [("a" * 8, 201), ("a" * 7, 422)])
def test_password_length_boundary(api, password, status):
    response = api.post("/auth/register", json={"email": "ana@example.com", "password": password})

    assert response.status_code == status


def test_password_with_surrounding_spaces_works_at_login_as_typed(register, login):
    # Un usuario que se registra con " clave-con-espacios " debe poder entrar escribiendo
    # exactamente lo mismo: registro y login han de tratar los espacios igual.
    register("ana@example.com", password=" clave-con-espacios ")

    assert login("ana@example.com", " clave-con-espacios ").status_code == 200


# --- Modos de fallo -----------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "no-es-un-email", "password": DEFAULT_PASSWORD},
        {"email": "ana@example.com"},
        {"password": DEFAULT_PASSWORD},
        {"email": "ana@example.com", "password": " " * 10},
    ],
)
def test_invalid_payload_is_rejected_and_nothing_is_stored(api, payload):
    assert api.post("/auth/register", json=payload).status_code == 422
    assert _stored_users() == []


def test_role_in_the_payload_is_ignored(api):
    response = api.post(
        "/auth/register",
        json={"email": "ana@example.com", "password": DEFAULT_PASSWORD, "role": "admin", "is_active": False},
    )

    assert response.status_code == 201
    stored = _stored_users()[0]
    # El registro público nunca crea cuentas privilegiadas ni inactivas.
    assert stored["role"] == "user"
    assert stored["is_active"] is True


def test_password_is_stored_only_as_a_bcrypt_hash(register):
    register("ana@example.com")

    stored = _stored_users()[0]
    assert stored["hashed_password"] != DEFAULT_PASSWORD
    assert stored["hashed_password"].startswith("$2")
    assert DEFAULT_PASSWORD not in str(stored)
