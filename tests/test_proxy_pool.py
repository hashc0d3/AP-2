"""Балансировка мобильных прокси: соседний канал и смена IP на заблокированном."""

from __future__ import annotations

import threading

from avito_monitor.net.proxies import PROXY_POOL, ProxyPool, current_proxy_string


def _wait_changes(monkeypatch, pool: ProxyPool | None = None):
    done = threading.Event()
    changed: list[tuple[str, str, float]] = []

    def fake_change(url: str, proxy_string: str = "", wait_max: float = 12.0) -> None:
        changed.append((url, proxy_string, wait_max))
        done.set()

    monkeypatch.setattr("avito_monitor.net.proxies.change_ip", fake_change)
    return pool or ProxyPool(), changed, done


def test_failover_switches_to_neighbor_and_changes_old_ip(monkeypatch) -> None:
    pool, changed, done = _wait_changes(monkeypatch)
    pool.configure(
        (
            ("u:p@mproxy.site:20085", "http://change-a"),
            ("u:p@mproxy.site:10341", "http://change-b"),
        )
    )
    assert pool.current_string() == "u:p@mproxy.site:20085"

    pool.failover("429")

    assert pool.current_string() == "u:p@mproxy.site:10341"
    assert done.wait(timeout=1.0)
    assert changed == [("http://change-a", "u:p@mproxy.site:20085", 0)]


def test_failover_walks_the_circle(monkeypatch) -> None:
    pool, changed, done = _wait_changes(monkeypatch)
    pool.configure(
        (
            ("u:p@mproxy.site:20085", "http://change-a"),
            ("u:p@mproxy.site:10341", "http://change-b"),
        )
    )
    pool.failover("429")
    assert done.wait(timeout=1.0)
    done.clear()
    pool.failover("таймаут")
    assert pool.current_string() == "u:p@mproxy.site:20085"
    assert done.wait(timeout=1.0)
    assert changed[-1] == ("http://change-b", "u:p@mproxy.site:10341", 0)


def test_single_proxy_stays_and_changes_own_ip(monkeypatch) -> None:
    pool, changed, done = _wait_changes(monkeypatch)
    pool.configure((("u:p@mproxy.site:20085", "http://change-a"),))
    pool.failover("429")
    assert pool.current_string() == "u:p@mproxy.site:20085"
    assert done.wait(timeout=1.0)
    assert changed == [("http://change-a", "u:p@mproxy.site:20085", 0)]


def test_empty_pool_does_not_call_change_ip(monkeypatch) -> None:
    called = []
    monkeypatch.setattr(
        "avito_monitor.net.proxies.change_ip", lambda *a, **k: called.append(a)
    )
    pool = ProxyPool()
    pool.failover("429")
    assert pool.current() is None
    assert called == []


def test_current_proxy_string_falls_back() -> None:
    assert current_proxy_string("fallback") == "fallback"
    PROXY_POOL.configure((("u:p@host:1", "http://x"),))
    assert current_proxy_string("fallback") == "u:p@host:1"
