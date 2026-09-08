"""Кольцо наборов cookies с постоянными соединениями.

Через мобильный прокси установка TCP и TLS стоит дороже самого запроса,
поэтому клиент на каждый набор живёт между циклами и переиспользует
соединение (keep-alive). Наборы при этом честно чередуются: каждый бьёт
Avito не чаще, чем позволяет ``per_cookie_interval``.

Клиент пересоздаётся, когда:

* набор пропал из пула или сервис перевыпустил ему cookies;
* набор сгорел (:meth:`CookieRing.burn`);
* сменился IP — старые соединения после этого мертвы
  (:meth:`CookieRing.reset_clients`);
* клиент прожил дольше :data:`CookieRing.CLIENT_MAX_AGE` — за это время
  Avito подмешивает свои cookies, и набор лучше вернуть к исходному.
"""

from __future__ import annotations

import time

from loguru import logger

from avito_monitor.config import Settings
from avito_monitor.cookies import pool
from avito_monitor.net.client import Session, build_client


class CookieRing:
    """Пул живых HTTP-клиентов поверх наборов cookies из :mod:`~.pool`."""

    REFRESH_EVERY = 30.0
    """Как часто перечитывать пул с диска."""

    CLIENT_MAX_AGE = 300.0
    """Максимальный срок жизни соединения на один набор."""

    def __init__(self, settings: Settings) -> None:
        self._proxy = settings.proxy_string
        self._clients: dict[str, Session] = {}
        self._created_at: dict[str, float] = {}
        self._slots: dict[str, dict] = {}
        self._order: list[str] = []
        self._index = 0
        self._refreshed_at = 0.0

    # ── Состав кольца ───────────────────────────────────────────────────

    def refresh(self) -> int:
        """Перечитать пул с диска. Возвращает число готовых наборов."""
        fresh = {str(slot["id"]): slot for slot in pool.usable_slots()}

        for gone in set(self._clients) - set(fresh):
            self._close(gone)

        # Сервис мог перевыпустить cookies — тогда клиент держит старые.
        for key, slot in fresh.items():
            known = self._slots.get(key)
            if known and known.get("last_unblock_at") != slot.get("last_unblock_at"):
                self._close(key)

        self._slots = fresh
        self._order = sorted(fresh)
        self._refreshed_at = time.time()
        if self._index >= len(self._order):
            self._index = 0
        return len(self._order)

    def size(self) -> int:
        return len(self._order)

    def position(self) -> str:
        """Позиция в кольце для лога: ``2/5``."""
        total = len(self._order)
        return f"{self._index or total}/{total}"

    # ── Клиенты ─────────────────────────────────────────────────────────

    def _close(self, cookie_id: str) -> None:
        self._created_at.pop(cookie_id, None)
        client = self._clients.pop(cookie_id, None)
        if client is None:
            return
        try:
            client.close()
        except Exception as err:  # закрытие мёртвого соединения не должно ломать цикл
            logger.debug(f"Кольцо: клиент id={cookie_id} не закрылся — {err}")

    def reset_clients(self) -> None:
        """Забыть все соединения: после смены IP они больше не работают."""
        for cookie_id in list(self._clients):
            self._close(cookie_id)

    def client_for(self, slot: dict) -> Session:
        """Клиент для набора: существующий или новый."""
        key = str(slot.get("id"))
        if time.time() - self._created_at.get(key, 0.0) > self.CLIENT_MAX_AGE:
            self._close(key)
        client = self._clients.get(key)
        if client is None:
            client = build_client(slot, self._proxy)
            self._clients[key] = client
            self._created_at[key] = time.time()
        return client

    # ── Выдача ──────────────────────────────────────────────────────────

    def burn(self, cookie_id: object) -> None:
        """Убрать сгоревший набор из кольца и отдать его на разблокировку."""
        key = str(cookie_id)
        pool.mark_blocked(cookie_id)
        self._close(key)
        self._slots.pop(key, None)
        if key in self._order:
            self._order.remove(key)
        if self._index >= len(self._order):
            self._index = 0

    def next(self) -> tuple[dict | None, Session | None]:
        """Следующий набор и его клиент.

        ``(None, None)`` — готовых наборов нет даже после ожидания сервиса;
        бить Avito заблокированным набором бессмысленно.
        """
        stale = time.time() - self._refreshed_at > self.REFRESH_EVERY
        if not self._order or stale:
            self.refresh()
        if not self._order:
            pool.wait_ready_cookie()
            if not self.refresh():
                return None, None

        key = self._order[self._index % len(self._order)]
        self._index = (self._index + 1) % len(self._order)
        slot = self._slots[key]
        return slot, self.client_for(slot)
