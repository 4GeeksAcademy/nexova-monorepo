"""`password_resets.py`: creación y consumo de tokens de recuperación."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import password_resets
import pytest
from tinydb import Query, TinyDB


def _records() -> list[dict]:
    db = TinyDB(password_resets.DB_PATH)
    try:
        return db.table("password_resets").all()
    finally:
        db.close()


def _set_expiry(token: str, moment: datetime) -> None:
    db = TinyDB(password_resets.DB_PATH)
    db.table("password_resets").update(
        {"expires_at": moment.isoformat()}, Query().token_hash == password_resets._hash_token(token)
    )
    db.close()


# --- Camino feliz -------------------------------------------------------------


def test_consuming_a_fresh_token_returns_its_user():
    token = password_resets.create_reset_token("u-1")

    assert password_resets.consume_reset_token(token) == "u-1"


def test_tokens_are_unique_and_long_enough():
    tokens = {password_resets.create_reset_token("u-1") for _ in range(20)}

    assert len(tokens) == 20
    assert all(len(token) >= 40 for token in tokens)  # token_urlsafe(32) ≈ 43 caracteres


def test_each_token_maps_to_its_own_user():
    first = password_resets.create_reset_token("u-1")
    second = password_resets.create_reset_token("u-2")

    assert password_resets.consume_reset_token(second) == "u-2"
    assert password_resets.consume_reset_token(first) == "u-1"


# --- Casos límite: expiración y un solo uso -----------------------------------


def test_default_lifetime_matches_the_configured_minutes():
    password_resets.create_reset_token("u-1")

    record = _records()[0]
    lifetime = datetime.fromisoformat(record["expires_at"]) - datetime.fromisoformat(record["created_at"])
    assert lifetime == timedelta(minutes=password_resets.TOKEN_EXPIRE_MINUTES)


def test_token_is_usable_just_before_expiry():
    token = password_resets.create_reset_token("u-1")
    _set_expiry(token, datetime.now(UTC) + timedelta(seconds=30))

    assert password_resets.consume_reset_token(token) == "u-1"


def test_token_is_dead_at_the_expiry_instant(monkeypatch):
    token = password_resets.create_reset_token("u-1")
    moment = datetime(2026, 1, 1, tzinfo=UTC)
    _set_expiry(token, moment)

    class FrozenAtExpiry(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment

    monkeypatch.setattr(password_resets, "datetime", FrozenAtExpiry)

    # La comparación es `ahora >= expira`: en el instante exacto ya no sirve.
    assert password_resets.consume_reset_token(token) is None


def test_expired_token_is_rejected():
    token = password_resets.create_reset_token("u-1")
    _set_expiry(token, datetime.now(UTC) - timedelta(seconds=1))

    assert password_resets.consume_reset_token(token) is None


def test_token_works_only_once():
    token = password_resets.create_reset_token("u-1")

    assert password_resets.consume_reset_token(token) == "u-1"
    assert password_resets.consume_reset_token(token) is None


def test_expired_token_is_not_marked_as_used():
    token = password_resets.create_reset_token("u-1")
    _set_expiry(token, datetime.now(UTC) - timedelta(minutes=1))

    password_resets.consume_reset_token(token)

    assert _records()[0]["used"] is False


# --- Modos de fallo -----------------------------------------------------------


@pytest.mark.parametrize("token", ["", "inventado", " "])
def test_unknown_tokens_return_none(token):
    password_resets.create_reset_token("u-1")

    assert password_resets.consume_reset_token(token) is None


def test_consuming_with_no_database_yet_returns_none():
    assert password_resets.consume_reset_token("cualquiera") is None


def test_only_the_hash_is_persisted_never_the_clear_token():
    token = password_resets.create_reset_token("u-1")

    record = _records()[0]
    assert record["token_hash"] == password_resets._hash_token(token)
    assert token not in str(record)
    assert record["used"] is False
