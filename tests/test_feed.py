"""Лента объявлений: хранение, рассылка подписчикам и память о показанном."""

from __future__ import annotations

import json
import queue
import time
from pathlib import Path

import pytest

from avito_monitor.monitor.seen import SeenStore
from avito_monitor.web import feed
from avito_monitor.web.feed import AdFeed


def _ad(ad_id: int) -> dict:
    return {"id": ad_id, "title": f"Объявление {ad_id}"}


# ── Лента ──────────────────────────────────────────────────────────────────


def test_new_ads_go_to_the_top(fresh_feed: AdFeed) -> None:
    fresh_feed.publish([_ad(1)])
    fresh_feed.publish([_ad(2)])
    assert [ad["id"] for ad in fresh_feed.snapshot()] == [2, 1]


def test_duplicates_are_ignored(fresh_feed: AdFeed) -> None:
    """SSE и опрос /api/ads работают одновременно — дубликаты неизбежны."""
    assert len(fresh_feed.publish([_ad(1), _ad(2)])) == 2
    assert fresh_feed.publish([_ad(2)]) == []
    assert len(fresh_feed.snapshot()) == 2


def test_publish_returns_only_new_ads(fresh_feed: AdFeed) -> None:
    fresh_feed.publish([_ad(1)])
    added = fresh_feed.publish([_ad(1), _ad(2)])
    assert [ad["id"] for ad in added] == [2]


def test_feed_is_capped(fresh_feed: AdFeed) -> None:
    fresh_feed.publish([_ad(index) for index in range(feed.MAX_ADS + 50)])
    assert len(fresh_feed.snapshot()) == feed.MAX_ADS


def test_oldest_ads_are_evicted() -> None:
    small = AdFeed(max_ads=2)
    small.publish([_ad(1)])
    small.publish([_ad(2)])
    small.publish([_ad(3)])
    assert [ad["id"] for ad in small.snapshot()] == [3, 2]


def test_snapshot_is_a_copy(fresh_feed: AdFeed) -> None:
    fresh_feed.publish([_ad(1)])
    fresh_feed.snapshot().clear()
    assert len(fresh_feed.snapshot()) == 1


def test_empty_publish_does_nothing(fresh_feed: AdFeed) -> None:
    assert fresh_feed.publish([]) == []


def test_publish_stamps_when_ad_hit_the_feed(fresh_feed: AdFeed) -> None:
    before = int(time.time())
    added = fresh_feed.publish([_ad(1)])
    stamp = added[0]["received_at"]
    assert isinstance(stamp, int)
    assert before <= stamp <= int(time.time())


def test_feed_keeps_ads_regardless_of_age(fresh_feed: AdFeed) -> None:
    """Возраст на Avito и время в ленте больше не выкидывают карточку."""
    now = time.time()
    fresh_feed.publish(
        [
            {"id": 1, "title": "свежее", "ts": now - 30},
            {"id": 2, "title": "старое", "ts": now - 400},
        ]
    )
    fresh_feed._ads[1]["received_at"] = now - 21 * 60
    assert [ad["id"] for ad in fresh_feed.snapshot()] == [1, 2]


# ── Диск ───────────────────────────────────────────────────────────────────


def test_feed_survives_restart(fresh_feed: AdFeed) -> None:
    fresh_feed.publish([_ad(1), _ad(2)])

    restarted = AdFeed()
    restarted.load_from_disk()
    assert [ad["id"] for ad in restarted.snapshot()] == [1, 2]


def test_ads_survive_load_even_if_published_long_ago(fresh_feed: AdFeed) -> None:
    now = time.time()
    fresh_feed.publish(
        [
            {"id": 1, "title": "свежее", "ts": now - 10},
            {"id": 2, "title": "старое", "ts": now - 10},
        ]
    )
    stored = json.loads(feed.ADS_PATH.read_text(encoding="utf-8"))
    stored[1]["ts"] = now - 400
    del stored[1]["received_at"]
    feed.ADS_PATH.write_text(json.dumps(stored), encoding="utf-8")

    restarted = AdFeed()
    restarted.load_from_disk()
    assert [ad["id"] for ad in restarted.snapshot()] == [1, 2]
    assert restarted.snapshot()[1]["received_at"]


def test_missing_file_gives_empty_feed() -> None:
    fresh = AdFeed()
    fresh.load_from_disk()
    assert fresh.snapshot() == []


@pytest.mark.parametrize("content", ["{не json", '{"ads": []}', "null"])
def test_broken_file_does_not_break_startup(content: str) -> None:
    """Обрыв записи не должен мешать запуску — лента не критичные данные."""
    feed.ADS_PATH.write_text(content, encoding="utf-8")
    fresh = AdFeed()
    fresh.load_from_disk()
    assert fresh.snapshot() == []


def test_clear_empties_disk_too(fresh_feed: AdFeed) -> None:
    fresh_feed.publish([_ad(1)])
    assert fresh_feed.clear() == 1
    assert json.loads(feed.ADS_PATH.read_text(encoding="utf-8")) == []


# ── Подписчики ─────────────────────────────────────────────────────────────


def test_subscriber_receives_new_ads(fresh_feed: AdFeed) -> None:
    with fresh_feed.subscription() as listener:
        fresh_feed.publish([_ad(1)])
        assert [ad["id"] for ad in listener.get(timeout=1)] == [1]


def test_every_subscriber_gets_a_copy(fresh_feed: AdFeed) -> None:
    """Открыто два браузера — оба должны увидеть объявление."""
    with fresh_feed.subscription() as first, fresh_feed.subscription() as second:
        fresh_feed.publish([_ad(1)])
        assert first.get(timeout=1)
        assert second.get(timeout=1)


def test_subscribers_are_told_about_reset(fresh_feed: AdFeed) -> None:
    with fresh_feed.subscription() as listener:
        fresh_feed.publish([_ad(1)])
        listener.get(timeout=1)
        fresh_feed.clear()
        assert listener.get(timeout=1) == feed.RESET_EVENT


def test_subscription_is_released(fresh_feed: AdFeed) -> None:
    """Иначе очереди закрытых вкладок копились бы до перезапуска сервера."""
    with fresh_feed.subscription():
        assert fresh_feed.listener_count == 1
    assert fresh_feed.listener_count == 0


def test_subscription_is_released_after_error(fresh_feed: AdFeed) -> None:
    with pytest.raises(RuntimeError), fresh_feed.subscription():
        raise RuntimeError("браузер отвалился")
    assert fresh_feed.listener_count == 0


def test_no_ads_no_messages(fresh_feed: AdFeed) -> None:
    with fresh_feed.subscription() as listener:
        fresh_feed.publish([])
        with pytest.raises(queue.Empty):
            listener.get(timeout=0.05)


# ── Память о показанном ────────────────────────────────────────────────────


@pytest.fixture
def seen_path(storage_dir: Path) -> Path:
    return storage_dir / "seen.json"


def test_seen_ids_survive_restart(seen_path: Path) -> None:
    """Иначе после перезапуска лента заполнится уже виденным."""
    store = SeenStore(seen_path)
    store.ids.update({1, 2, 3})
    store.save(force=True)

    assert SeenStore(seen_path).load() == {1, 2, 3}


def test_missing_file_gives_empty_set(seen_path: Path) -> None:
    assert SeenStore(seen_path).load() == set()


@pytest.mark.parametrize("content", ["{не json", '{"ids": [1]}', '["не число"]'])
def test_broken_seen_file_is_ignored(seen_path: Path, content: str) -> None:
    seen_path.write_text(content, encoding="utf-8")
    assert SeenStore(seen_path).load() == set()


def test_writes_are_throttled(seen_path: Path) -> None:
    """За сутки набегают тысячи ID — писать их каждый цикл незачем."""
    store = SeenStore(seen_path, throttle=60)
    store.ids.add(1)
    store.save(force=True)

    store.ids.add(2)
    store.save()
    assert json.loads(seen_path.read_text(encoding="utf-8")) == [1]

    store.save(force=True)
    assert json.loads(seen_path.read_text(encoding="utf-8")) == [1, 2]


def test_reset_forgets_everything_at_once(seen_path: Path) -> None:
    """Новый поиск — прошлые объявления к нему отношения не имеют."""
    store = SeenStore(seen_path)
    store.ids.update({1, 2})
    store.save(force=True)

    store.reset()
    assert len(store) == 0
    assert json.loads(seen_path.read_text(encoding="utf-8")) == []


def test_membership_and_length(seen_path: Path) -> None:
    store = SeenStore(seen_path)
    store.ids.update({7, 8})
    assert 7 in store
    assert 9 not in store
    assert len(store) == 2
