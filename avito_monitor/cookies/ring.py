"""Кольцо наборов cookies с постоянными соединениями.

Через мобильный прокси установка TCP и TLS стоит дороже самого запроса,
поэтому клиент на каждый набор живёт между циклами и переиспользует
соединение (keep-alive). Наборы при этом честно чередуются: каждый бьёт
Avito не чаще, чем позволяет ``per_cookie_interval``.

Канал набору выдаёт :data:`~avito_monitor.net.proxies.PROXY_POOL`: пока
прокси несколько, наборы разложены по ним поровну, и бан одного канала
не рвёт соединения наборов, которые сидят на другом.

Клиент пересоздаётся, когда:

* набор пропал из пула или сервис перевыпустил ему cookies;
* набор сгорел (:meth:`CookieRing.burn`);
* набор переехал на другой канал — соединение было к старому прокси;
* клиент прожил дольше :data:`CookieRing.CLIENT_MAX_AGE` — за это время
  Avito подмешивает свои cookies, и набор лучше вернуть к исходному.
"""

from __future__ import annotations

import time

from loguru import logger

from avito_monitor.config import Settings
from avito_monitor.cookies import pool
from avito_monitor.net.client import Session, build_client
from avito_monitor.net.proxies import PROXY_POOL


class CookieRing:
    """Пул живых HTTP-клиентов поверх наборов cookies из :mod:`~.pool`."""

    REFRESH_EVERY = 30.0
    """Как часто перечитывать пул с диска."""

    CLIENT_MAX_AGE = 300.0
    """Максимальный срок жизни соединения на один набор."""

    STRIKE_LIMIT = 5
    """Столько 429 подряд, пока другие наборы получали JSON, — и набор засвечен."""

    RETIRE_PER_HOUR = 6
    """Больше за час не выводим: при бане всей сети иначе скупим пул заново."""

    def __init__(self, settings: Settings) -> None:
        self._fallback_proxy = settings.proxy_string
        """Прокси на случай, если пул ещё не настроен."""
        self._clients: dict[str, Session] = {}
        self._created_at: dict[str, float] = {}
        self._proxy_of: dict[str, str] = {}
        self._slots: dict[str, dict] = {}
        self._order: list[str] = []
        self._index = 0
        self._refreshed_at = 0.0
        self._used_at: dict[str, float] = {}
        """Когда набор последний раз ушёл в запрос: внутри канала берём самый отдохнувший."""
        self._strikes: dict[str, int] = {}
        self._retired_at: list[float] = []

    # ── Состав кольца ───────────────────────────────────────────────────

    def refresh(self) -> int:
        """Перечитать пул с диска. Возвращает число готовых наборов."""
        fresh = {str(slot["id"]): slot for slot in pool.usable_slots()}

        for gone in set(self._slots) - set(fresh):
            self._close(gone)
            PROXY_POOL.release(gone)

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
        self._proxy_of.pop(cookie_id, None)
        client = self._clients.pop(cookie_id, None)
        if client is None:
            return
        try:
            client.close()
        except Exception as err:  # закрытие мёртвого соединения не должно ломать цикл
            logger.debug(f"Кольцо: клиент id={cookie_id} не закрылся — {err}")

    def reset_clients(self) -> None:
        """Забыть все соединения: после сбоя сети они могут быть мертвы."""
        for cookie_id in list(self._clients):
            self._close(cookie_id)

    def proxy_of(self, slot: dict) -> str:
        """Канал, через который набор ходил в прошлый раз."""
        return self._proxy_of.get(str(slot.get("id")), "")

    def client_for(self, slot: dict) -> Session:
        """Клиент для набора на его канале: существующий или новый.

        Пул мог увести набор на соседний прокси, пока этот меняет IP, —
        тогда старое соединение уже никуда не ведёт и клиент пересобирается.
        """
        key = str(slot.get("id"))
        proxy = PROXY_POOL.proxy_for(key) or self._fallback_proxy
        expired = time.time() - self._created_at.get(key, 0.0) > self.CLIENT_MAX_AGE
        if expired or self._proxy_of.get(key) != proxy:
            self._close(key)
        client = self._clients.get(key)
        if client is None:
            client = build_client(slot, proxy)
            self._clients[key] = client
            self._created_at[key] = time.time()
            self._proxy_of[key] = proxy
        return client

    # ── Выдача ──────────────────────────────────────────────────────────

    def burn(self, cookie_id: object) -> None:
        """Убрать сгоревший набор из кольца и отдать его на разблокировку."""
        pool.mark_blocked(cookie_id)
        self._drop(str(cookie_id))

    def note_cycle(self, json_ids: list[object], limited_ids: list[object]) -> None:
        """Итог параллельного цикла для счёта засвеченных наборов.

        429 засчитывается набору, только если в том же цикле другой набор
        получил JSON: так бан адреса или всей сети не выдаётся за вину cookies.
        """
        for cookie_id in json_ids:
            self._strikes.pop(str(cookie_id), None)
        if not json_ids:
            return
        for cookie_id in limited_ids:
            key = str(cookie_id)
            self._strikes[key] = self._strikes.get(key, 0) + 1
            if self._strikes[key] >= self.STRIKE_LIMIT:
                self._retire(key)

    def _retire(self, key: str) -> None:
        now = time.time()
        self._retired_at = [at for at in self._retired_at if now - at < 3600]
        if len(self._retired_at) >= self.RETIRE_PER_HOUR:
            return
        self._retired_at.append(now)
        pool.retire(key, f"{self._strikes[key]} раз 429 подряд, пока другие получали JSON")
        self._strikes.pop(key, None)
        self._drop(key)

    def _drop(self, key: str) -> None:
        self._close(key)
        PROXY_POOL.release(key)
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
            return None, None

        key = self._order[self._index % len(self._order)]
        self._index = (self._index + 1) % len(self._order)
        slot = self._slots[key]
        client = self.client_for(slot)
        self._mark_used(key)
        return slot, client

    def next_many(
        self, n: int, *, skip_proxies: set[str] | frozenset[str] | None = None
    ) -> list[tuple[dict, Session]]:
        """До ``n`` наборов на разных каналах — для параллельного опроса.

        Каналы идут по очереди: первым берём тот, что дольше всех не бил
        Avito, поэтому нагрузка ходит по кругу всех прокси, а не по одним
        и тем же. На канале — набор, который дольше всех отдыхал.
        Каналы из ``skip_proxies`` в этом цикле уже спрашивали; каналы на
        паузе после 429 и со сменой IP пропускаем.
        """
        stale = time.time() - self._refreshed_at > self.REFRESH_EVERY
        if not self._order or stale:
            self.refresh()
        if not self._order:
            return []

        forbidden = set(skip_proxies or ())
        rested: dict[str, str] = {}
        for key in self._order:
            proxy = PROXY_POOL.proxy_for(key) or self._fallback_proxy
            if proxy in forbidden or not PROXY_POOL.is_available(proxy):
                continue
            best = rested.get(proxy)
            if best is None or self._used_at.get(key, 0.0) < self._used_at.get(best, 0.0):
                rested[proxy] = key

        queue = sorted(rested, key=PROXY_POOL.last_used)[: max(1, n)]
        batch: list[tuple[dict, Session]] = []
        for proxy in queue:
            key = rested[proxy]
            slot = self._slots[key]
            batch.append((slot, self.client_for(slot)))
            self._mark_used(key)
        return batch

    def _mark_used(self, key: str) -> None:
        self._used_at[key] = time.monotonic()
        PROXY_POOL.touch(self._proxy_of.get(key, ""))
