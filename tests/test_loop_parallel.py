"""Параллельный опрос нескольких прокси в одном цикле."""

from __future__ import annotations

import json
import threading
import time

from avito_monitor.config import Settings
from avito_monitor.cookies import pool
from avito_monitor.cookies.ring import CookieRing
from avito_monitor.monitor import loop as loop_mod
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
        proxies = client.proxies or {}
        proxy_url = str(proxies.get("https") or proxies.get("http") or "")
        port = int(proxy_url.rsplit(":", 1)[-1])
        return 200, {"items": [{"id": 1}, {"id": port}]}

    monkeypatch.setattr(loop_mod.net_client, "fetch_page", fake_fetch)

    result = loop_mod.fetch_items(runtime, ring)
    ids = {item["id"] for item in result.items}

    assert not result.failed
    assert 1 in ids
    assert len(ids) == 4


def test_parallel_fetch_keeps_good_channels_after_429(
    settings: Settings, cookies_dir, monkeypatch
) -> None:
    """Один 429 не должен выкидывать цикл, если сосед уже отдал выдачу."""
    monkeypatch.setattr("avito_monitor.net.proxies.change_ip", lambda *a, **k: None)
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
