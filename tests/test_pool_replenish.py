"""Если рабочих cookies нет — докупаем сразу, без ожидания."""

from __future__ import annotations

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
