"""Funciones puras de `security.py`: JWT, validez tras cambios de cuenta y hashing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
import security
from jose import JWTError, jwt
from models import User


def _encode(claims: dict, key: str | None = None, algorithm: str | None = None) -> str:
    return jwt.encode(claims, key or security.SECRET_KEY, algorithm=algorithm or security.ALGORITHM)


def _user(**overrides) -> User:
    return User(id="u-1", email="ana@example.com", hashed_password="x", **overrides)


# --- Camino feliz -------------------------------------------------------------


def test_token_roundtrip_keeps_the_subject_and_lifetime():
    token = security.create_access_token("u-1")

    claims = security.decode_token_claims(token)

    assert claims["sub"] == "u-1"
    assert security.decode_access_token(token) == "u-1"
    # La vida del token es exactamente la configurada: si alguien la cambia sin querer
    # (la regresión que motivó el ticket), este test falla.
    assert claims["exp"] - claims["iat"] == security.ACCESS_TOKEN_EXPIRE_MINUTES * 60


def test_password_hash_verifies_only_the_right_password():
    hashed = security.hash_password("Sup3r-secreta!")

    assert hashed != "Sup3r-secreta!"
    assert security.verify_password("Sup3r-secreta!", hashed)
    assert not security.verify_password("sup3r-secreta!", hashed)  # distingue mayúsculas
    assert not security.verify_password("", hashed)


def test_same_password_hashes_differently_each_time():
    assert security.hash_password("Sup3r-secreta!") != security.hash_password("Sup3r-secreta!")


# --- Casos límite: expiración -------------------------------------------------


def test_token_expired_one_second_ago_is_rejected():
    now = datetime.now(UTC)
    token = _encode({"sub": "u-1", "exp": now - timedelta(seconds=1), "iat": int(now.timestamp()) - 60})

    with pytest.raises(JWTError):
        security.decode_token_claims(token)


def test_token_expiring_in_the_future_is_accepted():
    now = datetime.now(UTC)
    token = _encode({"sub": "u-1", "exp": now + timedelta(seconds=30), "iat": int(now.timestamp())})

    assert security.decode_token_claims(token)["sub"] == "u-1"


def test_lifetime_follows_the_configured_minutes(monkeypatch):
    monkeypatch.setattr(security, "ACCESS_TOKEN_EXPIRE_MINUTES", 1)

    claims = security.decode_token_claims(security.create_access_token("u-1"))

    assert claims["exp"] - claims["iat"] == 60


def test_token_without_expiry_is_still_accepted_by_the_decoder():
    # Documenta el comportamiento actual: `create_access_token` siempre pone `exp`, pero el
    # decodificador no lo exige. Si algún día se vuelve obligatorio, este test debe cambiar.
    token = _encode({"sub": "u-1", "iat": int(datetime.now(UTC).timestamp())})

    assert security.decode_token_claims(token)["sub"] == "u-1"


# --- Modos de fallo: tokens inválidos -----------------------------------------


@pytest.mark.parametrize("token", ["", "basura", "a.b.c", "a.b"])
def test_malformed_tokens_raise(token):
    with pytest.raises(JWTError):
        security.decode_token_claims(token)


def test_token_signed_with_another_key_is_rejected():
    now = datetime.now(UTC)
    token = _encode({"sub": "u-1", "exp": now + timedelta(minutes=5)}, key="otra-clave")

    with pytest.raises(JWTError):
        security.decode_token_claims(token)


def test_tampered_payload_is_rejected():
    header, _payload, signature = security.create_access_token("u-1").split(".")
    forged = _encode({"sub": "admin", "exp": datetime.now(UTC) + timedelta(minutes=5)}).split(".")[1]

    with pytest.raises(JWTError):
        security.decode_token_claims(".".join([header, forged, signature]))


def test_token_with_alg_none_is_rejected():
    import base64
    import json

    def b64(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    exp = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    unsigned = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64({'sub': 'u-1', 'exp': exp})}."

    with pytest.raises(JWTError):
        security.decode_token_claims(unsigned)


def test_token_signed_with_an_unexpected_algorithm_is_rejected():
    now = datetime.now(UTC)
    token = _encode({"sub": "u-1", "exp": now + timedelta(minutes=5)}, algorithm="HS512")

    with pytest.raises(JWTError):
        security.decode_token_claims(token)


def test_token_without_subject_is_rejected():
    token = _encode({"exp": datetime.now(UTC) + timedelta(minutes=5)})

    with pytest.raises(JWTError, match="sub"):
        security.decode_token_claims(token)


# --- token_still_valid: cuenta y contraseña -----------------------------------

CHANGED = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
CHANGED_TS = int(CHANGED.timestamp())


def test_valid_when_the_password_was_never_changed():
    assert security.token_still_valid(_user(), {"iat": CHANGED_TS})


def test_valid_even_without_iat_if_the_password_was_never_changed():
    assert security.token_still_valid(_user(), {})


def test_inactive_account_invalidates_every_token():
    assert not security.token_still_valid(_user(is_active=False), {"iat": CHANGED_TS})


def test_token_issued_before_the_password_change_is_invalid():
    assert not security.token_still_valid(_user(password_changed_at=CHANGED), {"iat": CHANGED_TS - 1})


def test_token_issued_after_the_password_change_is_valid():
    assert security.token_still_valid(_user(password_changed_at=CHANGED), {"iat": CHANGED_TS + 1})


def test_token_issued_in_the_same_second_as_the_change_is_valid():
    # El `iat` tiene resolución de segundos: el límite es inclusivo para no bloquear
    # a quien inicia sesión justo después de cambiar la contraseña.
    assert security.token_still_valid(_user(password_changed_at=CHANGED), {"iat": CHANGED_TS})


def test_token_without_iat_is_invalid_once_the_password_changed():
    assert not security.token_still_valid(_user(password_changed_at=CHANGED), {})


def test_naive_password_changed_at_is_treated_as_utc():
    naive = CHANGED.replace(tzinfo=None)

    assert security.token_still_valid(_user(password_changed_at=naive), {"iat": CHANGED_TS})
    assert not security.token_still_valid(_user(password_changed_at=naive), {"iat": CHANGED_TS - 1})


def test_timezone_aware_non_utc_change_is_compared_as_an_instant():
    madrid = timezone(timedelta(hours=1))
    changed = CHANGED.astimezone(madrid)  # mismo instante, otra zona

    assert security.token_still_valid(_user(password_changed_at=changed), {"iat": CHANGED_TS})
    assert not security.token_still_valid(_user(password_changed_at=changed), {"iat": CHANGED_TS - 1})
