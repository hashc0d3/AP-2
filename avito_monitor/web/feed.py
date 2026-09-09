"""Лента объявлений и её рассылка в браузеры.

Лента живёт в памяти и дублируется на диск, чтобы перезагрузка страницы или
перезапуск сервера не оставили пользователя с пустым экраном.

Новые объявления доходят до браузера двумя путями: основной — Server-Sent
Events (открытое соединение ``/events``), запасной — опрос ``/api/ads``.
Дубликаты отсекаются по ID, поэтому оба пути могут работать одновременно.

Ленту подчищаем каждую минуту: оставляем объявления, полученные за
последние 20 минут. В поиск по-прежнему попадают только свежие карточки
Avito (``max_age`` в фильтрах, тоже 20 минут).
"""

from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from loguru import logger

from avito_monitor.paths import ADS_PATH

MAX_ADS = 200
"""Столько объявлений храним; более старые вытесняются, даже если ещё свежие."""

SWEEP_INTERVAL_SEC = 60
"""Как часто подчищаем ленту."""

KEEP_RECENT_SEC = 20 * 60
"""Оставляем объявления, полученные за последние 20 минут."""

RESET_EVENT = {"reset": True}
"""Служебное сообщение подписчикам: ленту очистили."""


def _received_at(ad: dict) -> float:
    """Когда объявление попало в нашу ленту. Без метки считаем нулём."""
    raw = ad.get("received_at")
    if raw is None:
        raw = ad.get("ts")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


class AdFeed:
    """Потокобезопасная лента с подписчиками."""

    def __init__(self, max_ads: int = MAX_ADS) -> None:
        self._max_ads = max_ads
        self._lock = threading.Lock()
        self._ads: list[dict] = []
        self._listeners: list[queue.Queue] = []

    def load_from_disk(self) -> None:
        """Прочитать сохранённую ленту. Битый файл считаем пустым."""
        if not ADS_PATH.exists():
            return
        try:
            data = json.loads(ADS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        now = time.time()
        with self._lock:
            loaded = data[: self._max_ads] if isinstance(data, list) else []
            for ad in loaded:
                if isinstance(ad, dict) and ad.get("received_at") is None:
                    ad["received_at"] = now
            self._ads = loaded

    def _save_to_disk(self) -> None:
        """Вызывать под ``self._lock``."""
        ADS_PATH.parent.mkdir(parents=True, exist_ok=True)
        ADS_PATH.write_text(json.dumps(self._ads, ensure_ascii=False), encoding="utf-8")

    def snapshot(self) -> list[dict]:
        """Текущая лента, свежие объявления первыми.

        Карточки старше ``KEEP_RECENT_SEC`` с момента попадания к нам
        уже не отдаём — иначе опрос ``/api/ads`` вернул бы их обратно.
        """
        cutoff = time.time() - KEEP_RECENT_SEC
        with self._lock:
            return [ad for ad in self._ads if _received_at(ad) >= cutoff]

    def publish(self, ads: list[dict]) -> list[dict]:
        """Добавить объявления в ленту и разослать подписчикам.

        Возвращает те, которых в ленте ещё не было. Помечаем ``received_at``,
        чтобы плановая чистка оставляла только свежеполученные.
        """
        if not ads:
            return []
        now = time.time()
        with self._lock:
            known = {item.get("id") for item in self._ads}
            incoming = []
            for ad in ads:
                if ad.get("id") in known:
                    continue
                stamped = dict(ad)
                stamped["received_at"] = now
                incoming.append(stamped)
            if incoming:
                self._ads[0:0] = incoming
                del self._ads[self._max_ads :]
                self._save_to_disk()
            if not incoming:
                return []
            listeners = list(self._listeners)

        for listener in listeners:
            listener.put(incoming)
        logger.info(f"В веб-ленту добавлено {len(incoming)} объявлений")
        return incoming

    def sweep(self, keep_seconds: int = KEEP_RECENT_SEC) -> int:
        """Убрать всё, кроме объявлений, полученных за ``keep_seconds``.

        Браузерам шлём ``reset`` и оставшийся хвост, чтобы лента не вспыхнула
        пустой на несколько секунд до следующего опроса.
        """
        cutoff = time.time() - max(0, keep_seconds)
        with self._lock:
            kept = [ad for ad in self._ads if _received_at(ad) >= cutoff]
            removed = len(self._ads) - len(kept)
            if removed == 0:
                return 0
            self._ads = kept
            self._save_to_disk()
            listeners = list(self._listeners)

        for listener in listeners:
            listener.put(dict(RESET_EVENT))
            if kept:
                listener.put(list(kept))
        logger.info(f"Плановая чистка ленты: убрано {removed}, осталось {len(kept)}")
        return removed

    def clear(self) -> int:
        """Очистить ленту. Возвращает число удалённых объявлений."""
        with self._lock:
            count = len(self._ads)
            self._ads.clear()
            self._save_to_disk()
            listeners = list(self._listeners)

        for listener in listeners:
            listener.put(dict(RESET_EVENT))
        logger.info(f"Лента очищена, удалено {count} объявлений")
        return count

    @contextmanager
    def subscription(self) -> Iterator[queue.Queue]:
        """Очередь сообщений для одного SSE-соединения.

        Подписка снимается при выходе из блока, даже если браузер отвалился
        посреди отправки, — иначе очереди копились бы до конца работы сервера.
        """
        listener: queue.Queue = queue.Queue()
        with self._lock:
            self._listeners.append(listener)
        try:
            yield listener
        finally:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

    @property
    def listener_count(self) -> int:
        with self._lock:
            return len(self._listeners)


FEED = AdFeed()
"""Единственная лента на процесс."""


def publish_ads(ads: list[dict[str, Any]]) -> None:
    """Опубликовать объявления и отправить push тем, кто его включил."""
    incoming = FEED.publish(ads)
    if not incoming:
        return
    from avito_monitor.web.push import notify_new_ads

    notify_new_ads(incoming)


def clear_ads() -> int:
    return FEED.clear()


def snapshot_ads() -> list[dict]:
    return FEED.snapshot()


def start_sweeper(store: AdFeed | None = None) -> None:
    """Фоновая чистка ленты раз в минуту."""
    target = store if store is not None else FEED

    def loop() -> None:
        while True:
            time.sleep(SWEEP_INTERVAL_SEC)
            try:
                target.sweep(KEEP_RECENT_SEC)
            except Exception:
                logger.exception("Плановая чистка ленты не удалась")

    threading.Thread(target=loop, name="feed-sweeper", daemon=True).start()
