"""Параллельный опрос нескольких прокси в одном цикле."""

from __future__ import annotations

import json
import threading
import time

from avito_monitor.config import Settings
from avito_monitor.cookies import pool
from avito_monitor.cookies.ring import CookieRing
from avito_monitor.monitor import loop as loop_mod
from avito_monitor.monitor.pacer import ACTIVE_CHANNELS
from avito_monitor.monitor.seen import SeenStore
from avito_monitor.net.proxies import PROXY_POOL


def _slot(cookie_id: str) -> dict:
    moment = time.time()
    return {
        "id": cookie_id,
        "cookies": {"sessid": f"c-{cookie_id}"},
        "user_agent": "test-agent",
        "fingerprint": {"headers": {}, "impersonate": "chrome131_android"},
        "status": pool.STATUS_READY,
        "unblock_ok": True,
        "last_unblock_at": moment,
        "saved_at": moment,
    }


def _six_proxies_and_cookies(cookies_dir, settings: Settings) -> CookieRing:
    PROXY_POOL.configure(
        tuple((f"u:p@mproxy.site:{20000 + i}", f"http://change-{i}") for i in range(6))
    )
    for cookie_id in range(101, 107):
        slot = _slot(str(cookie_id))
        (cookies_dir / f"{slot['id']}.json").write_text(json.dumps(slot), encoding="utf-8")
    ring = CookieRing(settings)
    assert ring.refresh() == 6
    return ring


def test_parallel_fetch_merges_unique_ids(settings: Settings, cookies_dir, monkeypatch) -> None:
    """Разные каналы видят разные снимки — в ленту попадает объединение."""
    monkeypatch.setattr("avito_monitor.net.proxies.change_ip", lambda *a, **k: None)
    ring = _six_proxies_and_cookies(cookies_dir, settings)
    runtime = settings.for_search(web_url="", api_url="https://avito.test/items")

    def fake_fetch(client, url, *, attempts=2, timeout=10.0):
        assert timeout <= loop_mod.PARALLEL_PROBE_TIMEOUT
        proxies = client.proxies or {}
        proxy_url = str(proxies.get("https") or proxies.get("http") or "")
        port = int(proxy_url.rsplit(":", 1)[-1])
        return 200, {"items": [{"id": 1}, {"id": port}]}

    monkeypatch.setattr(loop_mod.net_client, "fetch_page", fake_fetch)

    result = loop_mod.fetch_items(runtime, ring)
    ids = {item["id"] for item in result.items}

    assert not result.failed
    assert 1 in ids
    assert len(ids) == 1 + ACTIVE_CHANNELS


def test_parallel_fetch_keeps_good_channels_after_429(
    settings: Settings, cookies_dir, monkeypatch
) -> None:
    """Один 429 не должен выкидывать цикл, если сосед уже отдал выдачу."""
    rotated: list[int] = []
    monkeypatch.setattr(
        "avito_monitor.net.proxies.change_ip", lambda *a, **k: rotated.append(1) or ""
    )
    ring = _six_proxies_and_cookies(cookies_dir, settings)
    runtime = settings.for_search(web_url="", api_url="https://avito.test/items")
    def fake_fetch(client, url, *, attempts=2, timeout=10.0):
        proxies = client.proxies or {}
        proxy_url = str(proxies.get("https") or proxies.get("http") or "")
        if proxy_url.endswith(":20000"):
            return 429, None
        return 200, {"items": [{"id": 77}]}

    monkeypatch.setattr(loop_mod.net_client, "fetch_page", fake_fetch)

    result = loop_mod.fetch_items(runtime, ring)

    assert result.throttled
    assert not result.failed
    assert {item["id"] for item in result.items} == {77}
    assert rotated == []


def test_parallel_fetch_streams_before_slow_channel(
    settings: Settings, cookies_dir, monkeypatch
) -> None:
    """Быстрый канал отдаёт выдачу, не дожидаясь самого медленного."""
    monkeypatch.setattr("avito_monitor.net.proxies.change_ip", lambda *a, **k: None)
    ring = _six_proxies_and_cookies(cookies_dir, settings)
    runtime = settings.for_search(web_url="", api_url="https://avito.test/items")
    fast_done = threading.Event()
    streamed: list[list[int]] = []

    def fake_fetch(client, url, *, attempts=2, timeout=10.0):
        proxies = client.proxies or {}
        proxy_url = str(proxies.get("https") or proxies.get("http") or "")
        port = int(proxy_url.rsplit(":", 1)[-1])
        if port == 20000:
            assert fast_done.wait(2)
            return 200, {"items": [{"id": 999}]}
        return 200, {"items": [{"id": port}]}

    def on_items(items: list[dict]) -> None:
        streamed.append([item["id"] for item in items])
        fast_done.set()

    monkeypatch.setattr(loop_mod.net_client, "fetch_page", fake_fetch)

    result = loop_mod.fetch_items(runtime, ring, on_items=on_items)

    assert streamed
    assert 999 not in streamed[0]
    assert 999 in {item["id"] for item in result.items}
    assert not result.failed


def test_run_cycle_publishes_each_channel_without_duplicates(
    settings: Settings, cookies_dir, monkeypatch
) -> None:
    """Второй канал не публикует id, который уже ушёл с первого."""
    monkeypatch.setattr("avito_monitor.net.proxies.change_ip", lambda *a, **k: None)
    ring = _six_proxies_and_cookies(cookies_dir, settings)
    runtime = settings.for_search(web_url="", api_url="https://avito.test/items")
    published: list[list[int]] = []

    def fake_fetch(client, url, *, attempts=2, timeout=10.0):
        proxies = client.proxies or {}
        proxy_url = str(proxies.get("https") or proxies.get("http") or "")
        port = int(proxy_url.rsplit(":", 1)[-1])
        if port == 20000:
            return 200, {"items": [{"id": 1}, {"id": 2}]}
        return 200, {"items": [{"id": 2}, {"id": port}]}

    monkeypatch.setattr(loop_mod.net_client, "fetch_page", fake_fetch)

    selected, failed, throttled = loop_mod.run_cycle(
        runtime,
        ring,
        SeenStore(),
        first_run=False,
        on_selected=lambda items: published.append([item["id"] for item in items]),
    )

    assert not failed
    assert not throttled
    assert published
    flat = [ad_id for batch in published for ad_id in batch]
    assert len(flat) == len(set(flat))
    assert set(flat) == {item["id"] for item in selected}
    assert 1 in flat
    assert 2 in flat


def test_spares_replace_every_429_in_the_same_cycle(
    settings: Settings, cookies_dir, monkeypatch
) -> None:
    """Пять отказов подряд — шестой прокси из запаса снимает выдачу в том же цикле."""
    monkeypatch.setattr("avito_monitor.net.proxies.change_ip", lambda *a, **k: None)
    ring = _six_proxies_and_cookies(cookies_dir, settings)
    runtime = settings.for_search(web_url="", api_url="https://avito.test/items")
    calls = {"n": 0}

    def fake_fetch(client, url, *, attempts=2, timeout=10.0):
        calls["n"] += 1
        if calls["n"] <= 5:
            return 429, None
        return 200, {"items": [{"id": 55}]}

    monkeypatch.setattr(loop_mod.net_client, "fetch_page", fake_fetch)

    result = loop_mod.fetch_items(runtime, ring)

    assert calls["n"] == 6
    assert result.throttled
    assert not result.failed
    assert {item["id"] for item in result.items} == {55}


def test_parallel_timeout_does_not_rotate_ip(settings: Settings, cookies_dir, monkeypatch) -> None:
    """Первый таймаут — медленный Avito, не мёртвый туннель: IP не крутим."""
    from curl_cffi.requests.exceptions import RequestException

    rotated: list[int] = []
    monkeypatch.setattr(
        "avito_monitor.net.proxies.change_ip", lambda *a, **k: rotated.append(1) or ""
    )
    ring = _six_proxies_and_cookies(cookies_dir, settings)
    runtime = settings.for_search(web_url="", api_url="https://avito.test/items")

    def fake_fetch(client, url, *, attempts=2, timeout=10.0):
        raise RequestException("HTTPSConnectionPool: Read timed out.")

    monkeypatch.setattr(loop_mod.net_client, "fetch_page", fake_fetch)

    result = loop_mod.fetch_items(runtime, ring)

    assert result.failed
    assert rotated == []


def _port(client) -> int:
    proxies = client.proxies or {}
    return int(str(proxies.get("https") or proxies.get("http") or "").rsplit(":", 1)[-1])


def test_429_launches_spare_without_waiting_for_the_rest(
    settings: Settings, cookies_dir, monkeypatch
) -> None:
    """Отказ сразу заменяет запасной, пока соседи ещё качают JSON; SIM не крутим."""
    rotated: list[int] = []
    monkeypatch.setattr(
        "avito_monitor.net.proxies.change_ip", lambda *a, **k: rotated.append(1) or ""
    )
    ring = _six_proxies_and_cookies(cookies_dir, settings)
    runtime = settings.for_search(web_url="", api_url="https://avito.test/items")
    ports: list[int] = []
    lock = threading.Lock()

    def fake_fetch(client, url, *, attempts=2, timeout=10.0):
        port = _port(client)
        with lock:
            ports.append(port)
        if port == 20000:
            return 429, None
        time.sleep(0.2)
        return 200, {"items": [{"id": port}]}

    monkeypatch.setattr(loop_mod.net_client, "fetch_page", fake_fetch)

    result = loop_mod.fetch_items(runtime, ring)

    assert not result.failed
    assert len(ports) == ACTIVE_CHANNELS + 1
    assert rotated == []


def test_channels_take_turns_between_cycles(settings: Settings, cookies_dir, monkeypatch) -> None:
    """Второй цикл берёт три отдохнувших прокси, а не те же самые."""
    monkeypatch.setattr("avito_monitor.net.proxies.change_ip", lambda *a, **k: None)
    ring = _six_proxies_and_cookies(cookies_dir, settings)
    runtime = settings.for_search(web_url="", api_url="https://avito.test/items")
    ports: list[int] = []
    lock = threading.Lock()

    def fake_fetch(client, url, *, attempts=2, timeout=10.0):
        port = _port(client)
        with lock:
            ports.append(port)
        return 200, {"items": [{"id": port}]}

    monkeypatch.setattr(loop_mod.net_client, "fetch_page", fake_fetch)

    loop_mod.fetch_items(runtime, ring)
    first = set(ports)
    ports.clear()
    loop_mod.fetch_items(runtime, ring)

    assert len(first) == ACTIVE_CHANNELS
    assert first.isdisjoint(ports)


def test_spare_skips_channel_that_changes_ip(settings: Settings, cookies_dir, monkeypatch) -> None:
    """Запасной не берём с порта, который меняет IP: запрос уйдёт в полуподнятый туннель."""
    monkeypatch.setattr("avito_monitor.net.proxies.change_ip", lambda *a, **k: None)
    ring = _six_proxies_and_cookies(cookies_dir, settings)
    runtime = settings.for_search(web_url="", api_url="https://avito.test/items")
    PROXY_POOL._channels[3].changing = True
    ports: list[int] = []
    lock = threading.Lock()

    def fake_fetch(client, url, *, attempts=2, timeout=10.0):
        with lock:
            ports.append(_port(client))
        return 429, None

    monkeypatch.setattr(loop_mod.net_client, "fetch_page", fake_fetch)

    loop_mod.fetch_items(runtime, ring)

    assert 20003 not in ports
    assert len(ports) == 5


def test_all_channels_resting_skips_request(settings: Settings, cookies_dir, monkeypatch) -> None:
    """Все на паузе после 429 — не бьём отдыхающий порт, ждём и отдаём цикл."""
    monkeypatch.setattr("avito_monitor.net.proxies.change_ip", lambda *a, **k: None)
    monkeypatch.setattr(loop_mod, "ALL_RESTING_WAIT", 0.05)
    ring = _six_proxies_and_cookies(cookies_dir, settings)
    runtime = settings.for_search(web_url="", api_url="https://avito.test/items")
    for channel in PROXY_POOL._channels:
        channel.cooling_until = time.monotonic() + 60
    calls = {"n": 0}

    def fake_fetch(client, url, *, attempts=2, timeout=10.0):
        calls["n"] += 1
        return 200, {"items": [{"id": 1}]}

    monkeypatch.setattr(loop_mod.net_client, "fetch_page", fake_fetch)

    result = loop_mod.fetch_items(runtime, ring)

    assert result.failed
    assert calls["n"] == 0
