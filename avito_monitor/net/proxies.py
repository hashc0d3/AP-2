"""Набор мобильных прокси: рабочий сейчас и соседний на подхвате.

При бане IP не ждём смену адреса на том же канале: сразу переключаемся
на готовый соседний, а на заблокированном меняем IP в фоне и не возвращаемся,
пока туннель не поднимется. Когда прокси один — поведение прежнее: только
смена IP.
"""

from __future__ import annotations

import threading
import time

from loguru import logger

from avito_monitor.net.proxy import change_ip


def _label(proxy_string: str) -> str:
    """host:port без логина и пароля — для лога."""
    return proxy_string.rsplit("@", 1)[-1] or "прокси"


class ProxySlot:
    __slots__ = ("proxy_string", "change_url", "changing", "ready")

    def __init__(self, proxy_string: str, change_url: str) -> None:
        self.proxy_string = proxy_string
        self.change_url = change_url
        self.changing = False
        self.ready = threading.Event()
        self.ready.set()

    @property
    def label(self) -> str:
        return _label(self.proxy_string)


class ProxyPool:
    """Круг из одного или нескольких прокси."""

    def __init__(self) -> None:
        self._slots: list[ProxySlot] = []
        self._index = 0
        self._lock = threading.Lock()
        self.change_wait = 12.0
        """Сколько ждать подъёма туннеля после смены IP."""
        self.cooldown = 5.0
        """Пауза после нового IP, чтобы не прыгать обратно на ещё горячий канал."""

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
        """Уйти с текущего прокси на готовый соседний; на старом сменить IP."""
        wait_for: ProxySlot | None = None
        banned: ProxySlot | None = None
        with self._lock:
            if not self._slots:
                logger.warning(f"{reason}: прокси не настроены")
                return
            leaving = self._slots[self._index]
            incoming = leaving
            if len(self._slots) > 1:
                incoming = self._pick_incoming(leaving)
                self._index = self._slots.index(incoming)
                logger.warning(
                    f"{reason}: {leaving.label} откладываю, работаю через {incoming.label}"
                )
                if incoming is not leaving and not incoming.ready.is_set():
                    wait_for = incoming
            else:
                logger.warning(f"{reason}: один прокси ({leaving.label}), меняю IP")
            start_change = not leaving.changing
            if start_change:
                leaving.changing = True
                leaving.ready.clear()
            banned = leaving if start_change else None

        if banned is not None:
            self._change_async(banned)
        elif leaving.change_url:
            logger.info(f"{leaving.label}: смена IP уже идёт")

        if wait_for is not None:
            timeout = self.change_wait + self.cooldown + 1.0
            logger.info(f"Жду, пока {wait_for.label} поднимет новый IP")
            if not wait_for.ready.wait(timeout=timeout):
                logger.warning(f"{wait_for.label} так и не готов, пробую как есть")

    def _pick_incoming(self, leaving: ProxySlot) -> ProxySlot:
        """Соседний готовый канал; если все остывают — тот, что следующий по кругу."""
        others = [slot for slot in self._slots if slot is not leaving]
        ready = [slot for slot in others if slot.ready.is_set() and not slot.changing]
        if ready:
            return ready[0]
        idx = self._slots.index(leaving)
        return self._slots[(idx + 1) % len(self._slots)]

    def _change_async(self, slot: ProxySlot) -> None:
        if not slot.change_url:
            logger.warning(f"Нет ссылки смены IP для {slot.label}")
            slot.changing = False
            slot.ready.set()
            return

        def worker() -> None:
            try:
                change_ip(slot.change_url, slot.proxy_string, wait_max=self.change_wait)
                if self.cooldown > 0:
                    time.sleep(self.cooldown)
            except RuntimeError as err:
                logger.warning(f"Не удалось сменить IP {slot.label}: {err}")
            finally:
                with self._lock:
                    slot.changing = False
                slot.ready.set()
                logger.info(f"{slot.label} снова в работе")

        threading.Thread(target=worker, name=f"ip-change-{slot.label}", daemon=True).start()


PROXY_POOL = ProxyPool()


def current_proxy_string(fallback: str = "") -> str:
    """Рабочий сейчас прокси или запасной из настроек."""
    return PROXY_POOL.current_string() or fallback
