"""`rate_limit.RateLimiter` con reloj inyectado (sin dormir) y `client_ip`."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from rate_limit import RateLimiter, client_ip


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def limiter(clock):
    return RateLimiter(max_attempts=3, window_seconds=60, clock=clock)


# --- Camino feliz -------------------------------------------------------------


def test_attempts_below_the_limit_pass(limiter):
    for _ in range(2):
        limiter.hit("k")

    limiter.check("k")  # no lanza


# --- Casos límite -------------------------------------------------------------


def test_blocks_exactly_when_the_limit_is_reached(limiter):
    for _ in range(3):
        limiter.hit("k")

    with pytest.raises(HTTPException) as blocked:
        limiter.check("k")

    assert blocked.value.status_code == 429


def test_retry_after_counts_down_from_the_oldest_hit(limiter, clock):
    for _ in range(3):
        limiter.hit("k")
    clock.now += 20

    assert limiter.retry_after("k") == 40


def test_unlocks_when_the_window_ends(limiter, clock):
    for _ in range(3):
        limiter.hit("k")
    clock.now += 59.9
    assert limiter.retry_after("k") > 0

    clock.now += 0.1  # justo el final de la ventana

    assert limiter.retry_after("k") == 0
    limiter.check("k")


def test_retry_after_is_at_least_one_second(limiter, clock):
    for _ in range(3):
        limiter.hit("k")
    clock.now += 59.99

    assert limiter.retry_after("k") == 1


def test_keys_are_independent(limiter):
    for _ in range(3):
        limiter.hit("a")

    limiter.check("b")


def test_check_with_several_keys_blocks_if_any_is_exhausted(limiter):
    for _ in range(3):
        limiter.hit("por-ip")

    with pytest.raises(HTTPException):
        limiter.check("por-email", "por-ip")


def test_reset_forgets_only_that_key(limiter):
    for key in ("a", "b"):
        for _ in range(3):
            limiter.hit(key)

    limiter.reset("a")

    limiter.check("a")
    with pytest.raises(HTTPException):
        limiter.check("b")


# --- Modos de fallo -----------------------------------------------------------


def test_429_carries_retry_after_header(limiter):
    for _ in range(3):
        limiter.hit("k")

    with pytest.raises(HTTPException) as blocked:
        limiter.check("k")

    assert blocked.value.headers["Retry-After"] == "60"


def test_check_without_keys_never_blocks(limiter):
    limiter.check()


def test_clear_empties_every_key(limiter):
    for _ in range(3):
        limiter.hit("k")

    limiter.clear()

    limiter.check("k")


def test_client_ip_falls_back_when_there_is_no_client():
    class Request:
        client = None

    assert client_ip(Request()) == "unknown"
