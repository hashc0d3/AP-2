"""Набор мобильных прокси: рабочий сейчас и соседний на подхвате.

При бане IP не ждём смену адреса на том же канале: сразу переключаемся
на другой прокси, а на заблокированном меняем IP в фоне. Когда прокси
один — поведение прежнее: только смена IP.
"""

from __future__ import annotations

import threading

from loguru import logger

from avito_monitor.net.proxy import change_ip


def _label(proxy_string: str) -> str:
    """host:port без логина и пароля — для лога."""
    return proxy_string.rsplit("@", 1)[-1] or "прокси"


class ProxySlot:
    __slots__ = ("proxy_string", "change_url")

    def __init__(self, proxy_string: str, change_url: str) -> None:
        self.proxy_string = proxy_string
        self.change_url = change_url

    @property
    def label(self) -> str:
        return _label(self.proxy_string)


class ProxyPool:
    """Круг из одного или нескольких прокси."""

    def __init__(self) -> None:
        self._slots: list[ProxySlot] = []
        self._index = 0
        self._lock = threading.Lock()

    def configure(self, endpoints: tuple[tuple[str, str], ...]) -> None:
        slots = [
            ProxySlot(proxy_string, change_url)
            for proxy_string, change_url in endpoints
            if proxy_string
        ]
        with self._lock:
            self._slots = slots
            self._index = 0
        if not slots:
            logger.warning("Прокси не настроены")
            return
        names = ", ".join(slot.label for slot in slots)
        logger.info(f"Прокси в работе: {len(slots)} ({names})")

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._slots)

    def current(self) -> ProxySlot | None:
        with self._lock:
            if not self._slots:
                return None
            return self._slots[self._index]

    def current_string(self) -> str:
        slot = self.current()
        return slot.proxy_string if slot else ""

    def failover(self, reason: str) -> None:
        """Уйти с текущего прокси на соседний; на старом сменить IP в фоне."""
        with self._lock:
            if not self._slots:
                logger.warning(f"{reason}: прокси не настроены")
                return
            leaving = self._slots[self._index]
            if len(self._slots) > 1:
                self._index = (self._index + 1) % len(self._slots)
                incoming = self._slots[self._index]
                logger.warning(
                    f"{reason}: {leaving.label} откладываю, работаю через {incoming.label}"
                )
            else:
                logger.warning(f"{reason}: один прокси ({leaving.label}), меняю IP")
            banned = leaving
        self._change_async(banned)

    def _change_async(self, slot: ProxySlot) -> None:
        if not slot.change_url:
            logger.warning(f"Нет ссылки смены IP для {slot.label}")
            return

        def worker() -> None:
            try:
                change_ip(slot.change_url, slot.proxy_string, wait_max=0)
            except RuntimeError as err:
                logger.warning(f"Не удалось сменить IP {slot.label}: {err}")

        threading.Thread(target=worker, name=f"ip-change-{slot.label}", daemon=True).start()


PROXY_POOL = ProxyPool()


def current_proxy_string(fallback: str = "") -> str:
    """Рабочий сейчас прокси или запасной из настроек."""
    return PROXY_POOL.current_string() or fallback
