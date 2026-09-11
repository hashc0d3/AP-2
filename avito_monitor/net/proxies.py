"""Мобильные прокси: наборы cookies поделены между живыми каналами.

Каналы работают параллельно, а не по очереди. Набор cookies закреплён за
своим прокси, поэтому его соединение (keep-alive) живёт между циклами, а
каждый IP получает лишь свою долю запросов и реже ловит 429.

Забаненный канал уходит менять адрес в фоне, его наборы на это время
переезжают к соседу — опрос не останавливается. Ждём новый IP, только когда
свободных каналов не осталось.
"""

from __future__ import annotations

import threading

from loguru import logger

from avito_monitor.net.proxy import change_ip


def _label(proxy_string: str) -> str:
    """host:port без логина и пароля — для лога."""
    return proxy_string.rsplit("@", 1)[-1] or "прокси"


class ProxyChannel:
    """Один мобильный прокси и состояние смены его IP."""

    __slots__ = ("proxy_string", "change_url", "changing", "ready", "leases")

    def __init__(self, proxy_string: str, change_url: str) -> None:
        self.proxy_string = proxy_string
        self.change_url = change_url
        self.changing = False
        self.ready = threading.Event()
        self.ready.set()
        # Сколько наборов cookies закреплено за каналом — по этому числу
        # раскладываем нагрузку поровну.
        self.leases = 0

    @property
    def label(self) -> str:
        return _label(self.proxy_string)


class ProxyPool:
    """Все настроенные прокси и раскладка наборов cookies по ним."""

    def __init__(self) -> None:
        self._channels: list[ProxyChannel] = []
        self._leases: dict[str, ProxyChannel] = {}
        self._lock = threading.Lock()
        self.change_wait = 12.0
        """Сколько ждать подъёма туннеля после смены IP."""

    def configure(self, endpoints: tuple[tuple[str, str], ...]) -> None:
        channels = [
            ProxyChannel(proxy_string, change_url)
            for proxy_string, change_url in endpoints
            if proxy_string
        ]
        with self._lock:
            self._channels = channels
            self._leases.clear()
        if not channels:
            logger.warning("Прокси не настроены")
            return
        names = ", ".join(channel.label for channel in channels)
        logger.info(f"Прокси в работе: {len(channels)} ({names})")

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._channels)

    @property
    def live_size(self) -> int:
        """Сколько каналов сейчас принимают запросы, а не меняют IP."""
        with self._lock:
            return sum(1 for channel in self._channels if not channel.changing)

    def proxy_for(self, key: str) -> str:
        """Канал набора cookies: закреплённый, пока он жив, иначе новый.

        Набор без канала садится на самый свободный, поэтому при двух прокси
        запросы делятся между ними примерно поровну.
        """
        with self._lock:
            leased = self._leases.get(key)
            if leased is not None and not leased.changing:
                return leased.proxy_string
            chosen = self._least_busy()
            if chosen is None:
                return ""
            if chosen is not leased:
                self._lease(key, chosen)
            return chosen.proxy_string

    def release(self, key: str) -> None:
        """Набор ушёл из кольца — освободить его место на канале."""
        with self._lock:
            channel = self._leases.pop(key, None)
            if channel is not None:
                channel.leases -= 1

    def current_string(self) -> str:
        """Живой канал для разовых запросов вне кольца: покупка cookies, номер."""
        with self._lock:
            channel = self._least_busy()
            return channel.proxy_string if channel else ""

    def ban(self, proxy_string: str, reason: str, *, wait: bool = True) -> None:
        """Канал поймал бан: сменить ему IP в фоне, наборы отдать соседям.

        :param proxy_string: канал, на котором пришёл отказ; пустая строка —
            выбрать самый свободный.
        :param wait: ждать новый IP, если работать больше не на чем.
        """
        with self._lock:
            channel = self._channel(proxy_string) or self._least_busy()
            if channel is None:
                logger.warning(f"{reason}: прокси не настроены")
                return

            spare = [
                other
                for other in self._channels
                if other is not channel and not other.changing
            ]
            if spare:
                names = ", ".join(other.label for other in spare)
                logger.warning(f"{reason}: {channel.label} меняет IP, наборы уходят на {names}")
            elif len(self._channels) > 1:
                logger.warning(f"{reason}: свободных каналов нет, жду новый IP на {channel.label}")
            else:
                logger.warning(f"{reason}: один прокси ({channel.label}), меняю IP")

            starting = not channel.changing
            if starting:
                channel.changing = True
                channel.ready.clear()
            self._resettle(channel, spare)
            waiting = channel if not spare and len(self._channels) > 1 else None

        if starting:
            self._change_async(channel)
        else:
            logger.info(f"{channel.label}: смена IP уже идёт")

        if wait and waiting is not None and not waiting.ready.wait(self.change_wait + 1.0):
            logger.warning(f"{waiting.label} так и не поднялся, пробую как есть")

    # ── Внутреннее: вызывается под ``self._lock`` ───────────────────────

    def _channel(self, proxy_string: str) -> ProxyChannel | None:
        for channel in self._channels:
            if channel.proxy_string == proxy_string:
                return channel
        return None

    def _least_busy(self) -> ProxyChannel | None:
        """Самый свободный живой канал; если живых нет — самый свободный вообще."""
        if not self._channels:
            return None
        live = [channel for channel in self._channels if not channel.changing]
        return min(live or self._channels, key=lambda channel: channel.leases)

    def _lease(self, key: str, channel: ProxyChannel) -> None:
        previous = self._leases.get(key)
        if previous is not None:
            previous.leases -= 1
        self._leases[key] = channel
        channel.leases += 1

    def _resettle(self, channel: ProxyChannel, spare: list[ProxyChannel]) -> None:
        """Пересадить наборы с забаненного канала на свободные."""
        moving = [key for key, held in self._leases.items() if held is channel]
        for key in moving:
            del self._leases[key]
        channel.leases = 0
        for key in moving:
            if spare:
                self._lease(key, min(spare, key=lambda other: other.leases))

    def _rebalance(self) -> None:
        """Разложить наборы по живым каналам поровну.

        Нужно, когда канал вернулся с новым IP: без этого все наборы так и
        остались бы на соседе, и второй прокси снова простаивал бы.
        Раскладка детерминированная, поэтому набор возвращается на свой
        прежний канал и переиспользует уже открытое соединение.
        """
        live = [channel for channel in self._channels if not channel.changing]
        if len(live) < 2:
            return
        settled = sorted(key for key, held in self._leases.items() if held in live)
        for channel in live:
            channel.leases = 0
        for position, key in enumerate(settled):
            self._leases[key] = live[position % len(live)]
            live[position % len(live)].leases += 1

    def _change_async(self, channel: ProxyChannel) -> None:
        """Сменить IP в отдельном потоке: остальные каналы продолжают работать."""
        if not channel.change_url:
            logger.warning(f"Нет ссылки смены IP для {channel.label}")
            with self._lock:
                channel.changing = False
            channel.ready.set()
            return

        def worker() -> None:
            try:
                change_ip(channel.change_url, channel.proxy_string, wait_max=self.change_wait)
            except RuntimeError as err:
                logger.warning(f"Не удалось сменить IP {channel.label}: {err}")
            finally:
                with self._lock:
                    channel.changing = False
                    self._rebalance()
                channel.ready.set()
                logger.info(f"{channel.label} снова в работе, наборы разложены заново")

        threading.Thread(target=worker, name=f"ip-change-{channel.label}", daemon=True).start()


PROXY_POOL = ProxyPool()


def current_proxy_string(fallback: str = "") -> str:
    """Живой прокси из пула или запасной из настроек."""
    return PROXY_POOL.current_string() or fallback
