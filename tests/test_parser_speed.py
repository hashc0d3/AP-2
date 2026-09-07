"""Проверка ротации cookies и расчёта интервала опроса. Сеть не нужна.

Запуск: python tests/test_parser_speed.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cookie_pool
from parser import CookieRing, PollPacer, poll_delay

CFG = {"proxy_string": "", "cookies_api_key": "test"}


def make_slot(cookie_id: str, unblock_at: float) -> dict:
    return {
        "id": cookie_id,
        "cookies": {"sessid": f"c-{cookie_id}"},
        "user_agent": "test-agent",
        "fingerprint": {"headers": {}, "impersonate": "chrome131_android"},
        "status": "ready",
        "unblock_ok": True,
        "last_unblock_at": unblock_at,
        "saved_at": unblock_at,
    }


def write_slot(root: Path, slot: dict) -> None:
    (root / f"{slot['id']}.json").write_text(json.dumps(slot), encoding="utf-8")


def with_temp_pool(func):
    def wrapper() -> None:
        tmp = Path(tempfile.mkdtemp(prefix="pool-test-"))
        saved = (cookie_pool.COOKIES_DIR, cookie_pool.POOL_PATH, cookie_pool.LEGACY_PATH)
        cookie_pool.COOKIES_DIR = tmp
        cookie_pool.POOL_PATH = tmp / "pool.json"
        cookie_pool.LEGACY_PATH = tmp / "cookies.json"
        try:
            func(tmp)
        finally:
            cookie_pool.COOKIES_DIR, cookie_pool.POOL_PATH, cookie_pool.LEGACY_PATH = saved
            shutil.rmtree(tmp, ignore_errors=True)

    wrapper.__name__ = func.__name__
    return wrapper


@with_temp_pool
def test_rotation_is_round_robin(tmp: Path) -> None:
    now = time.time()
    for cookie_id in ("101", "102", "103"):
        write_slot(tmp, make_slot(cookie_id, now))

    ring = CookieRing(CFG)
    assert ring.refresh() == 3, "должно быть три готовых набора"

    picked = [ring.next()[0]["id"] for _ in range(6)]
    assert picked == ["101", "102", "103", "101", "102", "103"], picked
    print("  ротация по кругу: ок")


@with_temp_pool
def test_client_is_reused(tmp: Path) -> None:
    now = time.time()
    write_slot(tmp, make_slot("201", now))
    ring = CookieRing(CFG)
    ring.refresh()

    first = ring.next()[1]
    second = ring.next()[1]
    assert first is second, "клиент должен переиспользоваться между циклами"

    ring.reset_clients()
    third = ring.next()[1]
    assert third is not first, "после смены IP клиент обязан пересоздаться"
    print("  keep-alive и сброс после смены IP: ок")


@with_temp_pool
def test_burn_removes_cookie(tmp: Path) -> None:
    now = time.time()
    for cookie_id in ("301", "302"):
        write_slot(tmp, make_slot(cookie_id, now))
    ring = CookieRing(CFG)
    ring.refresh()

    ring.burn("301")
    assert ring.size() == 1, ring.size()
    left = {ring.next()[0]["id"] for _ in range(3)}
    assert left == {"302"}, left

    burned = json.loads((tmp / "301.json").read_text(encoding="utf-8"))
    assert burned["status"] == "blocked", burned["status"]
    print("  сгоревший набор выбывает из ротации: ок")


@with_temp_pool
def test_client_rebuilt_after_unblock(tmp: Path) -> None:
    now = time.time()
    write_slot(tmp, make_slot("401", now))
    ring = CookieRing(CFG)
    ring.refresh()
    before = ring.next()[1]

    # Сервис пула перевыпустил cookies — клиент держит устаревшие.
    write_slot(tmp, make_slot("401", now + 60))
    ring.refresh()
    after = ring.next()[1]
    assert after is not before, "новые cookies требуют нового клиента"
    print("  перевыпуск cookies пересобирает клиент: ок")


@with_temp_pool
def test_empty_pool_is_reported(tmp: Path) -> None:
    ring = CookieRing(CFG)
    assert ring.refresh() == 0
    original = cookie_pool.wait_ready_cookie
    cookie_pool.wait_ready_cookie = lambda *a, **k: None
    try:
        session, client = ring.next()
    finally:
        cookie_pool.wait_ready_cookie = original
    assert session is None and client is None, "пустой пул не должен выдавать набор"
    print("  пустой пул не ломает цикл: ок")


def test_poll_delay() -> None:
    cases = [
        # (poll_interval, per_cookie_interval, cookies) -> ожидаемый интервал
        ((3, 12, 5), 3.0),   # пул из 5 — упираемся в пол по частоте ленты
        ((3, 12, 4), 3.0),
        ((3, 12, 2), 6.0),   # два набора — 6 с
        ((3, 12, 1), 12.0),  # один набор — как было раньше, не агрессивнее
        ((3, 12, 0), 12.0),  # деления на ноль быть не должно
    ]
    for args, expected in cases:
        got = poll_delay(*args)
        assert got == expected, f"poll_delay{args} = {got}, ожидалось {expected}"
    print("  интервал опроса считается верно: ок")


def test_pacer_ignores_single_throttles() -> None:
    """Одиночные 429 — фон мобильного прокси, скорость из-за них терять нельзя."""
    pacer = PollPacer(floor=4, ceiling=24)
    for _ in range(30):
        for _ in range(PollPacer.THROTTLE_STREAK - 1):
            pacer.on_throttle()
        pacer.on_ok()
    assert pacer.interval == 4, "разрозненные 429 не должны замедлять опрос"
    print("  одиночные 429 не сбивают темп: ок")


def test_pacer_backs_off_on_ban() -> None:
    pacer = PollPacer(floor=4, ceiling=24)
    for _ in range(PollPacer.THROTTLE_STREAK - 1):
        pacer.on_throttle()
    assert pacer.interval == 4, "тормозить раньше серии отказов рано"
    pacer.on_throttle()
    assert pacer.interval > 4, "серия отказов подряд должна замедлить опрос"
    for _ in range(100):
        pacer.on_throttle()
    assert pacer.interval == 24, "замедление не должно уходить за потолок"
    print("  серия отказов тормозит опрос и не уходит за потолок: ок")


def test_pacer_returns_to_speed() -> None:
    pacer = PollPacer(floor=4, ceiling=24)
    for _ in range(PollPacer.THROTTLE_STREAK):
        pacer.on_throttle()
    banned = pacer.interval
    for _ in range(PollPacer.CLEAN_STREAK - 1):
        pacer.on_ok()
    assert pacer.interval == banned, "возвращаться к скорости до серии чистых циклов рано"
    pacer.on_ok()
    assert pacer.interval < banned, "после чистых циклов опрос должен снова ускориться"
    for _ in range(100):
        pacer.on_ok()
    assert pacer.interval == 4, "ускорение не должно пробивать пол"
    print("  выход из бана возвращает скорость и не пробивает пол: ок")


def main() -> None:
    tests = [
        test_rotation_is_round_robin,
        test_client_is_reused,
        test_burn_removes_cookie,
        test_client_rebuilt_after_unblock,
        test_empty_pool_is_reported,
        test_poll_delay,
        test_pacer_ignores_single_throttles,
        test_pacer_backs_off_on_ban,
        test_pacer_returns_to_speed,
    ]
    print("Проверки парсера:")
    for test in tests:
        test()
    print(f"\nВсе проверки пройдены ({len(tests)}).")


if __name__ == "__main__":
    main()
