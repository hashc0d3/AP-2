"""Параллельный опрос нескольких прокси в одном цикле."""

from __future__ import annotations

import json
import time

from avito_monitor.config import Settings
from avito_monitor.cookies import pool
from avito_monitor.cookies.ring import CookieRing
from avito_monitor.monitor import loop as loop_mod
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
