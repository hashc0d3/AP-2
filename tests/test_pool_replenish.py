"""Если рабочих cookies нет — докупаем сразу, без ожидания."""

from __future__ import annotations

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


def test_buy_one_uses_given_proxy(settings, monkeypatch) -> None:
    seen = {}

    def fake_buy(api_key, proxy_string):
        seen["proxy"] = proxy_string
        return {"id": "new-1", "cookies": {"sessid": "y"}, "user_agent": "ua"}

    monkeypatch.setattr(spfa, "buy_cookies", fake_buy)
    pool.buy_one(settings, pause=False, proxy_string="u:p@host:23650")

    assert seen["proxy"] == "u:p@host:23650"

