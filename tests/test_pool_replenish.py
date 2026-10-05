"""Если рабочих cookies нет — докупаем сразу, без ожидания."""

from __future__ import annotations

import time
from dataclasses import replace

from avito_monitor import spfa
from avito_monitor.cookies import pool


def test_replenish_if_empty_buys_when_all_blocked(settings, monkeypatch) -> None:
    pool.save_slot(
        {
            "id": "blocked-1",
            "cookies": {"sessid": "x"},
            "user_agent": "ua",
            "status": pool.STATUS_BLOCKED,
            "unblock_ok": True,
        }
    )

    def fake_buy(settings, *, pause: bool = True) -> dict:
        assert pause is False
        return pool.save_slot(
            {
                "id": "fresh-1",
                "cookies": {"sessid": "y"},
                "user_agent": "ua",
                "status": pool.STATUS_READY,
                "unblock_ok": True,
            }
        )

    monkeypatch.setattr(pool, "buy_one", fake_buy)
    assert pool.usable_slots() == []
    slot = pool.replenish_if_empty(settings)
    assert slot is not None
    assert slot["id"] == "fresh-1"
    assert pool.usable_slots()


def test_replenish_if_empty_skips_when_ready_exists(settings, monkeypatch) -> None:
    pool.save_slot(
        {
            "id": "ready-1",
            "cookies": {"sessid": "x"},
            "user_agent": "ua",
            "status": pool.STATUS_READY,
            "unblock_ok": True,
        }
    )
    monkeypatch.setattr(
        pool,
        "buy_one",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("не должны покупать")),
    )
    assert pool.replenish_if_empty(settings) is None


def test_wait_ready_cookie_buys_immediately(settings, monkeypatch) -> None:
    calls = {"n": 0}

    def fake_buy(settings, *, pause: bool = True) -> dict:
        calls["n"] += 1
        return pool.save_slot(
            {
                "id": "bought",
                "cookies": {"sessid": "z"},
                "user_agent": "ua",
                "status": pool.STATUS_READY,
                "unblock_ok": True,
            }
        )

    monkeypatch.setattr(pool, "buy_one", fake_buy)
    chosen = pool.wait_ready_cookie(settings=settings)
    assert calls["n"] == 1
    assert chosen is not None
    assert chosen["id"] == "bought"


def test_unblock_404_marks_dead_and_frees_slot(settings, monkeypatch) -> None:
    """404 от SPFA — набор мёртв: иначе 15 призраков занимают пул и докупки нет."""
    pool.save_slot(
        {
            "id": "gone-1",
            "cookies": {"sessid": "x"},
            "user_agent": "ua",
            "status": pool.STATUS_BLOCKED,
            "unblock_ok": False,
        }
    )

    def fake_unblock(*_a, **_k):
        raise spfa.SpfaCookieGone("404 Cookie не найден")

    monkeypatch.setattr(spfa, "unblock_cookies", fake_unblock)
    assert pool.unblock_one({"id": "gone-1"}, settings) is None
    dead = pool.load_slot("gone-1")
    assert dead is not None
    assert dead["status"] == pool.STATUS_DEAD
    assert len(pool.alive_slots()) == 0


def test_retire_takes_set_out_of_rotation(settings) -> None:
    pool.save_slot(
        {
            "id": "old-1",
            "cookies": {"sessid": "x"},
            "user_agent": "ua",
            "status": pool.STATUS_READY,
            "unblock_ok": True,
        }
    )
    assert len(pool.usable_slots()) == 1

    pool.retire("old-1")

    assert pool.usable_slots() == []
    assert pool.alive_slots() == []


def _ready(cookie_id: str, **extra) -> dict:
    return pool.save_slot(
        {
            "id": cookie_id,
            "cookies": {"sessid": "x"},
            "user_agent": "ua",
            "status": pool.STATUS_READY,
            "unblock_ok": True,
            **extra,
        }
    )


def test_retire_aged_takes_only_the_oldest_set(settings) -> None:
    now = time.time()
    _ready("a", bought_at=now - 30 * 3600)
    _ready("b", bought_at=now - 40 * 3600)
    _ready("c", bought_at=now - 3600)

    assert pool.retire_aged(settings) == "b"
    assert {slot["id"] for slot in pool.usable_slots()} == {"a", "c"}


def test_retire_aged_stamps_sets_without_purchase_time(settings) -> None:
    _ready("old")
    _ready("other")

    assert pool.retire_aged(settings) is None
    assert pool.load_slot("old")["bought_at"]


def test_retire_aged_keeps_the_last_working_set(settings) -> None:
    _ready("only", bought_at=time.time() - 100 * 3600)

    assert pool.retire_aged(settings) is None


def test_retire_aged_off_when_zero(settings) -> None:
    _ready("a", bought_at=time.time() - 100 * 3600)
    _ready("b", bought_at=time.time() - 100 * 3600)

    assert pool.retire_aged(replace(settings, cookie_max_age_hours=0)) is None


def test_unblock_skips_set_older_than_twelve_hours(settings, monkeypatch) -> None:
    """После 12 часов spfa.pro набор уже не восстановит — в сервис его не шлём."""
    _ready("aged", status=pool.STATUS_BLOCKED, bought_at=time.time() - 12 * 3600 - 5)
    called = {"n": 0}

    def fake_unblock(*_a, **_k):
        called["n"] += 1
        raise AssertionError("старый набор не должен уходить на разблокировку")

    monkeypatch.setattr(spfa, "unblock_cookies", fake_unblock)
    assert pool.unblock_one(pool.load_slot("aged"), settings) is None
    assert called["n"] == 0
    assert pool.load_slot("aged")["status"] == pool.STATUS_DEAD


def test_refresh_waits_while_the_pool_is_young(settings, monkeypatch) -> None:
    _ready("young", bought_at=time.time() - 2 * 3600)
    monkeypatch.setattr(pool, "_buy_for_refresh", lambda _settings: (_ for _ in ()).throw(AssertionError("рано")))

    pool.refresh_pool_if_due(settings)

    assert pool.load_slot("young")["status"] == pool.STATUS_READY
    assert "refresh_retire_ids" not in pool._load_pool()


def test_refresh_replaces_the_whole_pool_before_twelve_hours(settings, monkeypatch) -> None:
    now = time.time()
    _ready("old-a", bought_at=now - 11.6 * 3600)
    _ready("old-b", bought_at=now - 11.6 * 3600)
    issued = {"n": 0}

    def fake_buy(_settings):
        issued["n"] += 1
        return _ready(f"new-{issued['n']}", bought_at=time.time())

    monkeypatch.setattr(pool, "_buy_for_refresh", fake_buy)
    pool.refresh_pool_if_due(settings)

    assert issued["n"] == settings.cookie_pool_size
    assert pool.load_slot("old-a")["status"] == pool.STATUS_DEAD
    assert pool.load_slot("old-b")["status"] == pool.STATUS_DEAD
    assert len(pool.usable_slots()) == settings.cookie_pool_size
    meta = pool._load_pool()
    assert "refresh_retire_ids" not in meta
    assert time.time() - float(meta["refreshed_at"]) < 5


def test_refresh_keeps_old_sets_until_the_new_pool_is_complete(settings, monkeypatch) -> None:
    _ready("old-a", bought_at=time.time() - 11.6 * 3600)
    _ready("old-b", bought_at=time.time() - 11.6 * 3600)

    def fail_buy(_settings):
        raise spfa.SpfaError("сервис недоступен")

    monkeypatch.setattr(pool, "buy_one", fail_buy)
    pool.refresh_pool_if_due(settings)

    assert pool.load_slot("old-a")["status"] == pool.STATUS_READY
    assert pool.load_slot("old-b")["status"] == pool.STATUS_READY
    assert pool._load_pool()["refresh_retire_ids"] == ["old-a", "old-b"]


def test_cursor_save_keeps_refresh_mark(settings) -> None:
    _ready("keep", bought_at=time.time())
    pool._save_pool({"refreshed_at": 123.0, "cursor": "keep"})

    pool._save_cursor("keep")

    assert pool._load_pool()["refreshed_at"] == 123.0
    assert pool._load_pool()["cursor"] == "keep"


def test_buy_one_uses_given_proxy(settings, monkeypatch) -> None:
    seen = {}

    def fake_buy(api_key, proxy_string):
        seen["proxy"] = proxy_string
        return {"id": "new-1", "cookies": {"sessid": "y"}, "user_agent": "ua"}

    monkeypatch.setattr(spfa, "buy_cookies", fake_buy)
    pool.buy_one(settings, pause=False, proxy_string="u:p@host:23650")

    assert seen["proxy"] == "u:p@host:23650"

