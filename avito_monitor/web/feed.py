"""Лента объявлений и её рассылка в браузеры.

Лента живёт в памяти и дублируется на диск, чтобы перезагрузка страницы или
перезапуск сервера не оставили пользователя с пустым экраном.

Новые объявления доходят до браузера двумя путями: основной — Server-Sent
Events (открытое соединение ``/events``), запасной — опрос ``/api/ads``.
Дубликаты отсекаются по ID, поэтому оба пути могут работать одновременно.

В ленту попадают только объявления, которых не было в выдаче на момент
запуска поиска. Карточки живут, пока их не вытеснит лимит ``MAX_ADS``
или новый поиск не очистит ленту.
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
"""Столько объявлений храним; более старые вытесняются новыми."""

RESET_EVENT = {"reset": True}
"""Служебное сообщение подписчикам: ленту очистили."""


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
        """Текущая лента, свежие объявления первыми."""
        with self._lock:
            return list(self._ads)

    def publish(self, ads: list[dict]) -> list[dict]:
        """Добавить объявления в ленту и разослать подписчикам.

        Возвращает те, которых в ленте ещё не было. Помечаем ``received_at``,
        чтобы в карточке было видно, когда объявление попало к нам.
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


