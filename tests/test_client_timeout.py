"""Таймаут запроса: соединение рвём быстро, тело JSON можно ждать дольше."""

from avito_monitor.net.client import CONNECT_TIMEOUT, call_timeout


def test_short_timeout_stays_a_single_number() -> None:
    assert call_timeout(5) == 5
    assert call_timeout(CONNECT_TIMEOUT) == CONNECT_TIMEOUT


def test_timeout_is_total_not_sum() -> None:
    """curl складывает кортеж: (5, 7) = 12 с, а не 5+12."""
    assert call_timeout(12) == (CONNECT_TIMEOUT, 7)
    assert call_timeout(20) == (CONNECT_TIMEOUT, 15)


def test_timeout_below_minimum_is_clamped() -> None:
    assert call_timeout(0) == 3
