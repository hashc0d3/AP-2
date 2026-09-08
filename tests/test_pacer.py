"""Темп опроса: интервал и реакция на отказы Avito."""

from __future__ import annotations

import pytest

from avito_monitor.monitor.pacer import PollPacer, poll_delay


@pytest.mark.parametrize(
    ("poll_interval", "per_cookie_interval", "cookies", "expected"),
    [
        (3, 12, 5, 3.0),  # пул из пяти — упираемся в пол по частоте ленты
        (3, 12, 4, 3.0),
        (3, 12, 2, 6.0),  # два набора — 6 с на каждый
        (3, 12, 1, 12.0),  # один набор — не агрессивнее его лимита
        (3, 12, 0, 12.0),  # деления на ноль быть не должно
    ],
)
def test_poll_delay(
    poll_interval: float, per_cookie_interval: float, cookies: int, expected: float
) -> None:
    assert poll_delay(poll_interval, per_cookie_interval, cookies) == expected


@pytest.fixture
def pacer() -> PollPacer:
    return PollPacer(floor=4, ceiling=24)


def test_starts_at_floor(pacer: PollPacer) -> None:
    assert pacer.interval == 4


def test_single_throttles_do_not_slow_polling(pacer: PollPacer) -> None:
    """Одиночные 429 — фон мобильного прокси, скорость из-за них терять нельзя."""
    for _ in range(30):
        for _ in range(PollPacer.THROTTLE_STREAK - 1):
            pacer.on_throttle()
        pacer.on_ok()
    assert pacer.interval == 4


def test_streak_of_refusals_slows_polling(pacer: PollPacer) -> None:
    for _ in range(PollPacer.THROTTLE_STREAK - 1):
        pacer.on_throttle()
    assert pacer.interval == 4, "тормозить раньше полной серии рано"

    pacer.on_throttle()
    assert pacer.interval > 4


def test_slowdown_stops_at_ceiling(pacer: PollPacer) -> None:
    for _ in range(100):
        pacer.on_throttle()
    assert pacer.interval == 24


def test_clean_cycles_restore_speed(pacer: PollPacer) -> None:
    for _ in range(PollPacer.THROTTLE_STREAK):
        pacer.on_throttle()
    slowed = pacer.interval

    for _ in range(PollPacer.CLEAN_STREAK - 1):
        pacer.on_ok()
    assert pacer.interval == slowed, "ускоряться до серии чистых циклов рано"

    pacer.on_ok()
    assert pacer.interval < slowed


def test_speedup_stops_at_floor(pacer: PollPacer) -> None:
    for _ in range(100):
        pacer.on_throttle()
    for _ in range(100):
        pacer.on_ok()
    assert pacer.interval == 4


def test_ceiling_below_floor_is_clamped() -> None:
    assert PollPacer(floor=10, ceiling=5).interval == 10
