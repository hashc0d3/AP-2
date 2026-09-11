"""Ротация наборов cookies и переиспользование соединений."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from avito_monitor.config import Settings
from avito_monitor.cookies import pool
from avito_monitor.cookies.ring import CookieRing


def _slot(cookie_id: str, unblocked_at: float | None = None) -> dict:
    moment = unblocked_at if unblocked_at is not None else time.time()
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


@pytest.fixture
def write_slot(cookies_dir: Path):
    def _write(slot: dict) -> None:
        (cookies_dir / f"{slot['id']}.json").write_text(json.dumps(slot), encoding="utf-8")

    return _write


@pytest.fixture
def ring(settings: Settings) -> CookieRing:
    return CookieRing(settings)


def test_rotation_is_round_robin(ring: CookieRing, write_slot) -> None:
    for cookie_id in ("101", "102", "103"):
        write_slot(_slot(cookie_id))
    assert ring.refresh() == 3

    picked = [ring.next()[0]["id"] for _ in range(6)]
    assert picked == ["101", "102", "103", "101", "102", "103"]


def test_client_is_reused_between_cycles(ring: CookieRing, write_slot) -> None:
    """Через мобильный прокси установка соединения дороже самого запроса."""
    write_slot(_slot("201"))
    ring.refresh()
    assert ring.next()[1] is ring.next()[1]


def test_clients_are_rebuilt_after_ip_change(ring: CookieRing, write_slot) -> None:
    write_slot(_slot("201"))
    ring.refresh()
    before = ring.next()[1]
    ring.reset_clients()
    assert ring.next()[1] is not before


def test_cookies_ride_their_own_proxy(ring: CookieRing, write_slot) -> None:
    """Наборы разложены по каналам — оба IP работают, а не ждут своей очереди."""
    from avito_monitor.net.proxies import PROXY_POOL

    PROXY_POOL.configure(
        (("u:p@mproxy.site:20085", "http://change-a"), ("u:p@mproxy.site:10341", "http://change-b"))
    )
    for cookie_id in ("201", "202"):
        write_slot(_slot(cookie_id))
    ring.refresh()

    ports = {str(ring.next()[1].proxies) for _ in range(2)}

    assert len(ports) == 2


def test_next_many_picks_distinct_proxies(ring: CookieRing, write_slot) -> None:
    """Параллельный цикл не должен дважды бить один и тот же IP."""
    from avito_monitor.net.proxies import PROXY_POOL

    PROXY_POOL.configure(
        (("u:p@mproxy.site:20085", "http://change-a"), ("u:p@mproxy.site:10341", "http://change-b"))
    )
    for cookie_id in ("201", "202", "203", "204"):
        write_slot(_slot(cookie_id))
    ring.refresh()

    batch = ring.next_many(2)
    ports = {ring.proxy_of(slot) for slot, _ in batch}

    assert len(batch) == 2
    assert len(ports) == 2


def test_client_is_rebuilt_when_its_channel_is_banned(
    ring: CookieRing, write_slot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Соединение вело к забаненному прокси — держать его бессмысленно."""
    from avito_monitor.net.proxies import PROXY_POOL

    changing = threading.Event()
    monkeypatch.setattr(
        "avito_monitor.net.proxies.change_ip", lambda *a, **k: changing.wait(timeout=2.0)
    )
    PROXY_POOL.configure(
        (("u:p@mproxy.site:20085", "http://change-a"), ("u:p@mproxy.site:10341", "http://change-b"))
    )
    write_slot(_slot("201"))
    ring.refresh()
    slot, before = ring.next()

    PROXY_POOL.ban(ring.proxy_of(slot), "429")
    after = ring.client_for(slot)
    changing.set()

    assert after is not before
    assert "10341" in str(after.proxies)


def test_burned_cookie_leaves_rotation(ring: CookieRing, write_slot, cookies_dir: Path) -> None:
    for cookie_id in ("301", "302"):
        write_slot(_slot(cookie_id))
    ring.refresh()

    ring.burn("301")
    assert ring.size() == 1
    assert {ring.next()[0]["id"] for _ in range(3)} == {"302"}

    stored = json.loads((cookies_dir / "301.json").read_text(encoding="utf-8"))
    assert stored["status"] == pool.STATUS_BLOCKED


def test_reissued_cookies_force_new_client(ring: CookieRing, write_slot) -> None:
    """Сервис перевыпустил cookies — старый клиент держит недействительные."""
    now = time.time()
    write_slot(_slot("401", now))
    ring.refresh()
    before = ring.next()[1]

    write_slot(_slot("401", now + 60))
    ring.refresh()
    assert ring.next()[1] is not before


def test_empty_pool_does_not_break_cycle(ring: CookieRing, monkeypatch: pytest.MonkeyPatch) -> None:
    assert ring.refresh() == 0
    monkeypatch.setattr(pool, "wait_ready_cookie", lambda *a, **k: None)
    assert ring.next() == (None, None)


def test_blocked_slots_are_not_offered(ring: CookieRing, write_slot) -> None:
    ready = _slot("501")
    blocked = _slot("502") | {"status": pool.STATUS_BLOCKED}
    not_unblocked = _slot("503") | {"unblock_ok": False}
    dead = _slot("504") | {"status": pool.STATUS_DEAD}
    for slot in (ready, blocked, not_unblocked, dead):
        write_slot(slot)

    assert ring.refresh() == 1
    assert ring.next()[0]["id"] == "501"


def test_position_is_reported_for_logs(ring: CookieRing, write_slot) -> None:
    for cookie_id in ("601", "602"):
        write_slot(_slot(cookie_id))
    ring.refresh()
    ring.next()
    assert ring.position() == "1/2"


def test_mark_blocked_records_lifecycle(write_slot, isolated_storage: Path) -> None:
    write_slot(_slot("701"))
    pool.mark_blocked("701")
    log = (isolated_storage / "logs" / "cookie_lifecycle.log").read_text(encoding="utf-8")
    assert "event=blocked" in log
    assert "id=701" in log
