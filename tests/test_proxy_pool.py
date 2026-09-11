"""Балансировка мобильных прокси: наборы cookies разложены по живым каналам."""

from __future__ import annotations

import threading
import time

from avito_monitor.net.proxies import PROXY_POOL, ProxyPool, current_proxy_string

A = ("u:p@mproxy.site:20085", "http://change-a")
B = ("u:p@mproxy.site:10341", "http://change-b")


class FakeChange:
    """Смена IP, которая не ходит в сеть и длится столько, сколько нужно тесту.

    Настоящая смена занимает секунды, и всё поведение пула завязано на то,
    что канал в это время недоступен. Мгновенная заглушка это скрывала бы.
    """

    def __init__(self, monkeypatch) -> None:
        self.calls: list[tuple[str, str, float]] = []
        self.started = threading.Event()
        self.finish = threading.Event()
        monkeypatch.setattr("avito_monitor.net.proxies.change_ip", self)

    def __call__(self, url: str, proxy_string: str = "", wait_max: float = 12.0) -> None:
        self.calls.append((url, proxy_string, wait_max))
        self.started.set()
        self.finish.wait(timeout=2.0)

    def complete(self, pool: ProxyPool) -> None:
        """Отпустить смену IP и дождаться, пока канал вернётся в работу."""
        self.finish.set()
        deadline = time.monotonic() + 2.0
        while any(channel.changing for channel in pool._channels):
            assert time.monotonic() < deadline, "канал так и не вернулся"
            time.sleep(0.01)


def test_live_size_ignores_channel_that_changes_ip(monkeypatch) -> None:
    change = FakeChange(monkeypatch)
    pool = ProxyPool()
    pool.configure((A, B))
    assert pool.live_size == 2

    pool.ban(A[0], "429")
    assert change.started.wait(timeout=1.0)
    assert pool.live_size == 1
    change.complete(pool)
    assert pool.live_size == 2


def test_cookies_are_split_between_channels(monkeypatch) -> None:
    """Ради этого всё и затевалось: каждый IP берёт свою половину запросов."""
    FakeChange(monkeypatch)
    pool = ProxyPool()
    pool.configure((A, B))

    assigned = [pool.proxy_for(key) for key in ("101", "102", "103", "104")]

    assert sorted(assigned) == sorted([A[0], A[0], B[0], B[0]])


def test_cookie_keeps_its_channel(monkeypatch) -> None:
    """Канал закреплён за набором — иначе на каждом цикле терялся бы keep-alive."""
    FakeChange(monkeypatch)
    pool = ProxyPool()
    pool.configure((A, B))

    assert pool.proxy_for("101") == pool.proxy_for("101") == A[0]


def test_ban_moves_cookies_to_neighbor_and_changes_ip(monkeypatch) -> None:
    change = FakeChange(monkeypatch)
    pool = ProxyPool()
    pool.configure((A, B))
    assert pool.proxy_for("101") == A[0]

    pool.ban(A[0], "429")

    assert change.started.wait(timeout=1.0)
    assert pool.proxy_for("101") == B[0]
    assert change.calls == [("http://change-a", A[0], pool.change_wait)]
    change.complete(pool)


def test_ban_leaves_the_healthy_channel_alone(monkeypatch) -> None:
    """Наборы соседа не должны терять соединение из-за чужого бана."""
    change = FakeChange(monkeypatch)
    pool = ProxyPool()
    pool.configure((A, B))
    pool.proxy_for("101")
    assert pool.proxy_for("102") == B[0]

    pool.ban(A[0], "429")

    assert change.started.wait(timeout=1.0)
    assert pool.proxy_for("102") == B[0]
    change.complete(pool)


def test_channels_rebalance_after_ip_change(monkeypatch) -> None:
    """Вернувшийся канал снова берёт свою долю, иначе балансировки бы не стало."""
    change = FakeChange(monkeypatch)
    pool = ProxyPool()
    pool.configure((A, B))
    for key in ("101", "102", "103", "104"):
        pool.proxy_for(key)

    pool.ban(A[0], "429")
    assert change.started.wait(timeout=1.0)
    assert {pool.proxy_for(key) for key in ("101", "103")} == {B[0]}

    change.complete(pool)

    assigned = [pool.proxy_for(key) for key in ("101", "102", "103", "104")]
    assert sorted(assigned) == sorted([A[0], A[0], B[0], B[0]])


def test_single_proxy_keeps_cookie_and_changes_own_ip(monkeypatch) -> None:
    change = FakeChange(monkeypatch)
    pool = ProxyPool()
    pool.configure((A,))
    pool.proxy_for("101")

    pool.ban(A[0], "429")

    assert change.started.wait(timeout=1.0)
    assert change.calls == [("http://change-a", A[0], pool.change_wait)]
    change.complete(pool)
    assert pool.proxy_for("101") == A[0]


def test_ban_waits_when_no_channel_is_free(monkeypatch) -> None:
    """Оба канала меняют IP — работать не на чем, ждём и не долбим впустую."""
    change = FakeChange(monkeypatch)
    pool = ProxyPool()
    pool.change_wait = 0.05
    pool.configure((A, B))
    pool.proxy_for("101")
    pool.proxy_for("102")
    pool.ban(A[0], "429")
    assert change.started.wait(timeout=1.0)

    started = time.monotonic()
    pool.ban(B[0], "429")
    waited = time.monotonic() - started

    assert pool.change_wait <= waited <= pool.change_wait + 1.5
    change.complete(pool)


def test_does_not_start_second_ip_change_while_first_runs(monkeypatch) -> None:
    change = FakeChange(monkeypatch)
    pool = ProxyPool()
    pool.configure((A,))

    pool.ban(A[0], "429")
    assert change.started.wait(timeout=1.0)
    pool.ban(A[0], "ещё раз")

    assert change.calls == [("http://change-a", A[0], pool.change_wait)]
    change.complete(pool)


def test_release_frees_the_channel(monkeypatch) -> None:
    """Сгоревший набор не должен вечно занимать место в раскладке."""
    FakeChange(monkeypatch)
    pool = ProxyPool()
    pool.configure((A, B))
    pool.proxy_for("101")
    pool.proxy_for("102")

    pool.release("101")

    assert pool.proxy_for("103") == A[0]


def test_empty_pool_does_not_call_change_ip(monkeypatch) -> None:
    change = FakeChange(monkeypatch)
    pool = ProxyPool()

    pool.ban("", "429")

    assert pool.proxy_for("101") == ""
    assert change.calls == []


def test_current_proxy_string_falls_back() -> None:
    assert current_proxy_string("fallback") == "fallback"
    PROXY_POOL.configure((("u:p@host:1", "http://x"),))
    assert current_proxy_string("fallback") == "u:p@host:1"
