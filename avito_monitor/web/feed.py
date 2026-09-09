"""Лента объявлений и её рассылка в браузеры.

Лента живёт в памяти и дублируется на диск, чтобы перезагрузка страницы или
перезапуск сервера не оставили пользователя с пустым экраном.

Новые объявления доходят до браузера двумя путями: основной — Server-Sent
Events (открытое соединение ``/events``), запасной — опрос ``/api/ads``.
Дубликаты отсекаются по ID, поэтому оба пути могут работать одновременно.
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

DEFAULT_MAX_AGE = 300
"""По умолчанию в ленте только объявления не старше 5 минут. ``0`` — без лимита."""

RESET_EVENT = {"reset": True}
"""Служебное сообщение подписчикам: ленту очистили."""


class AdFeed:
    """Потокобезопасная лента с подписчиками."""

    def __init__(self, max_ads: int = MAX_ADS, max_age: int = DEFAULT_MAX_AGE) -> None:
        self._max_ads = max_ads
        self._max_age = max(0, max_age)
        self._lock = threading.Lock()
        self._ads: list[dict] = []
        self._listeners: list[queue.Queue] = []

    def set_max_age(self, max_age: int) -> None:
        """Подставить ``max_age`` из настроек. ``0`` — возраст не ограничиваем."""
        with self._lock:
            self._max_age = max(0, int(max_age))

    def _fresh(self, ads: list[dict], now: float | None = None) -> list[dict]:
        """Оставить объявления не старше лимита. Без ``ts`` не трогаем — тесты."""
        if not self._max_age:
            return list(ads)
        moment = time.time() if now is None else now
        kept: list[dict] = []
        for ad in ads:
            ts = ad.get("ts")
            if ts is None or moment - float(ts) <= self._max_age:
                kept.append(ad)
        return kept

    def _prune_locked(self) -> bool:
        """Вызывать под ``self._lock``. ``True``, если что-то удалили."""
        kept = self._fresh(self._ads)
        if len(kept) == len(self._ads):
            return False
        self._ads = kept
        return True

    def load_from_disk(self) -> None:
        """Прочитать сохранённую ленту. Битый файл считаем пустым."""
        if not ADS_PATH.exists():
            return
        try:
            data = json.loads(ADS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        with self._lock:
            loaded = data[: self._max_ads] if isinstance(data, list) else []
            self._ads = self._fresh(loaded)
            if len(self._ads) != len(loaded):
                self._save_to_disk()

    def _save_to_disk(self) -> None:
        """Вызывать под ``self._lock``."""
        ADS_PATH.parent.mkdir(parents=True, exist_ok=True)
        ADS_PATH.write_text(json.dumps(self._ads, ensure_ascii=False), encoding="utf-8")

    def snapshot(self) -> list[dict]:
        """Текущая лента, свежие объявления первыми. Старше лимита выкидываем."""
        with self._lock:
            if self._prune_locked():
                self._save_to_disk()
            return list(self._ads)

    def publish(self, ads: list[dict]) -> list[dict]:
        """Добавить объявления в ленту и разослать подписчикам.

        Возвращает те, которых в ленте ещё не было. Старше ``max_age``
        не принимаем и заодно вычищаем уже лежащие.
        """
        if not ads:
            return []
        with self._lock:
            pruned = self._prune_locked()
            known = {item.get("id") for item in self._ads}
            incoming = self._fresh([ad for ad in ads if ad.get("id") not in known])
            if incoming:
                self._ads[0:0] = incoming
                del self._ads[self._max_ads :]
            if incoming or pruned:
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
