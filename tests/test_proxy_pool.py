"""Балансировка мобильных прокси: соседний канал и смена IP на заблокированном."""

from __future__ import annotations

import threading
import time

from avito_monitor.net.proxies import PROXY_POOL, ProxyPool, current_proxy_string

A = ("u:p@mproxy.site:20085", "http://change-a")
B = ("u:p@mproxy.site:10341", "http://change-b")


def _pool(monkeypatch, change=None) -> tuple[ProxyPool, list, threading.Event]:
    done = threading.Event()
    changed: list[tuple[str, str, float]] = []

    def fake_change(url: str, proxy_string: str = "", wait_max: float = 12.0) -> None:
        if change is not None:
            change()
        changed.append((url, proxy_string, wait_max))
        done.set()

    monkeypatch.setattr("avito_monitor.net.proxies.change_ip", fake_change)
    pool = ProxyPool()
    pool.cooldown = 0.0
    return pool, changed, done


def test_failover_switches_to_neighbor_and_changes_old_ip(monkeypatch) -> None:
    pool, changed, done = _pool(monkeypatch)
    pool.configure((A, B))
    assert pool.current_string() == A[0]

    pool.failover("429")

    assert pool.current_string() == B[0]
    assert done.wait(timeout=1.0)
    assert changed == [("http://change-a", A[0], 12.0)]
    assert pool._slots[0].ready.wait(timeout=1.0)


def test_failover_walks_the_circle(monkeypatch) -> None:
    pool, changed, done = _pool(monkeypatch)
    pool.configure((A, B))
    pool.failover("429")
    assert done.wait(timeout=1.0)
    assert pool._slots[0].ready.wait(timeout=1.0)
    done.clear()
    pool.failover("таймаут")
    assert pool.current_string() == A[0]
    assert done.wait(timeout=1.0)
    assert changed[-1] == ("http://change-b", B[0], 12.0)


def test_single_proxy_stays_and_changes_own_ip(monkeypatch) -> None:
    pool, changed, done = _pool(monkeypatch)
    pool.configure((A,))
    pool.failover("429")
    assert pool.current_string() == A[0]
    assert done.wait(timeout=1.0)
    assert changed == [("http://change-a", A[0], 12.0)]


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


def test_failover_waits_for_own_ip_when_neighbor_still_changing(monkeypatch) -> None:
    """Сосед ещё меняет IP — не прыгаем на него, меняем свой."""
    first_started = threading.Event()
    release_first = threading.Event()
    calls = []

    def fake_change(url: str, proxy_string: str = "", wait_max: float = 12.0) -> None:
        calls.append(url)
        if url == "http://change-a":
            first_started.set()
            release_first.wait(timeout=2.0)

    monkeypatch.setattr("avito_monitor.net.proxies.change_ip", fake_change)
    pool = ProxyPool()
    pool.cooldown = 0.0
    pool.configure((A, B))
    pool.failover("429")
    assert first_started.wait(timeout=1.0)
    assert pool.current_string() == B[0]

    pool.failover("429")
    assert pool.current_string() == B[0]
    assert "http://change-b" in calls
    release_first.set()


def test_failover_does_not_jump_to_cooling_neighbor(monkeypatch) -> None:
    """Сосед только что получил новый IP — второй 429 будет сразу."""
    pool, changed, done = _pool(monkeypatch)
    pool.cooldown = 30.0
    pool.configure((A, B))
    pool.failover("429")
    assert done.wait(timeout=1.0)
    assert pool._slots[0].ready.wait(timeout=1.0)
    assert pool.current_string() == B[0]

    done.clear()
    pool.failover("ещё 429")
    assert pool.current_string() == B[0]
    assert done.wait(timeout=1.0)
    assert changed[-1] == ("http://change-b", B[0], 12.0)


def test_does_not_start_second_ip_change_while_first_runs(monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def fake_change(url: str, proxy_string: str = "", wait_max: float = 12.0) -> None:
        calls.append(url)
        started.set()
        release.wait(timeout=2.0)

    monkeypatch.setattr("avito_monitor.net.proxies.change_ip", fake_change)
    pool = ProxyPool()
    pool.cooldown = 0.0
    pool.configure((A,))
    pool.failover("429")
    assert started.wait(timeout=1.0)
    pool.failover("ещё раз")
    release.set()
    time.sleep(0.05)
    assert calls == ["http://change-a"]
