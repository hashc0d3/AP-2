"""Мобильные прокси: наборы cookies поделены между живыми каналами.

Каналы работают параллельно, а не по очереди. Набор cookies закреплён за
своим прокси, поэтому его соединение (keep-alive) живёт между циклами, а
каждый IP получает лишь свою долю запросов и реже ловит 429.

После 429 канал молчит, не меняя SIM: смена IP на том же порту часто
возвращает ту же ``/21`` и на 10 с выключает канал, а сосед уже другой
оператор. IP крутим, когда запас кончился, порт словил серию 429 или
туннель реально мёртв. Ждём новый адрес, только если свободных каналов
не осталось.
"""

from __future__ import annotations

import threading
import time

from loguru import logger

from avito_monitor.net.proxy import change_ip, current_ip, ip_prefix21


def _label(proxy_string: str) -> str:
    """host:port без логина и пароля — для лога."""
    return proxy_string.rsplit("@", 1)[-1] or "прокси"


class ProxyChannel:
    """Один мобильный прокси и состояние смены его IP."""

    __slots__ = (
        "proxy_string",
        "change_url",
        "changing",
        "ready",
        "leases",
        "last_ip",
        "hangs",
        "cooling_until",
        "strikes",
        "bans",
        "last_used",
    )

    def __init__(self, proxy_string: str, change_url: str) -> None:
        self.proxy_string = proxy_string
        self.change_url = change_url
        self.changing = False
        self.ready = threading.Event()
        self.ready.set()
        # Сколько наборов cookies закреплено за каналом — по этому числу
        # раскладываем нагрузку поровну.
        self.leases = 0
        self.last_ip = ""
        """Последний известный выходной адрес — для лога /21 после 429."""
        self.hangs = 0
        """Подряд таймаутов: меняем IP только когда туннель реально молчит."""
        self.cooling_until = 0.0
        """До этого момента (monotonic) канал не берём в опрос: недавний 429."""
        self.strikes = 0
        """Сколько 429 подряд на этом порту. После серии — меняем IP."""
        self.bans = 0
        """429 подряд без единого JSON, смена IP не сбрасывает: по нему растёт пауза."""
        self.last_used = 0.0
        """Когда (monotonic) канал последний раз ушёл в запрос — для очереди."""

    @property
    def label(self) -> str:
        return _label(self.proxy_string)


class ProxyPool:
    """Все настроенные прокси и раскладка наборов cookies по ним."""

    TIMEOUT_BAN_STREAK = 3
    """Столько таймаутов подряд — туннель мёртв, а не просто медленный Avito."""

    def __init__(self) -> None:
        self._channels: list[ProxyChannel] = []
        self._leases: dict[str, ProxyChannel] = {}
        self._lock = threading.Lock()
        self.change_wait = 12.0
        """Сколько ждать подъёма туннеля после смены IP."""
        self.ban_cooldown = 20.0
        """После 429 с запасом каналов не крутим IP: соседние порты уже другие /21."""
        self.ip_change_after_strikes = 3
        """Смена IP, когда один порт поймал столько 429 подряд."""
        self.ban_cooldown_max = 240.0
        """Потолок паузы: порт, чей пул адресов Avito режет целиком, почти не бьём."""

    def _cooldown(self, channel: ProxyChannel) -> float:
        """Пауза после 429 удваивается с каждым отказом подряд, пока нет JSON."""
        steps = max(0, channel.bans - 1)
        return min(self.ban_cooldown * 2**steps, self.ban_cooldown_max)

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
        """Сколько каналов сейчас принимают запросы, а не меняют IP и не в паузе."""
        with self._lock:
            self._expire_cooldowns()
            return sum(1 for channel in self._channels if self._available(channel))

    def note_hang(self, proxy_string: str) -> bool:
        """Таймаут на канале. ``True`` — пора менять IP, не с первого же зависания."""
        with self._lock:
            channel = self._channel(proxy_string)
            if channel is None:
                return False
            channel.hangs += 1
            logger.warning(
                f"{channel.label}: туннель не ответил "
                f"({channel.hangs}/{self.TIMEOUT_BAN_STREAK})"
            )
            return channel.hangs >= self.TIMEOUT_BAN_STREAK

    def learn_ips(self) -> None:
        """Узнать адрес каждого канала в фоне: без него не видно общих /21."""
        with self._lock:
            channels = list(self._channels)

        def worker(channel: ProxyChannel) -> None:
            ip = current_ip(channel.proxy_string)
            if not ip:
                return
            with self._lock:
                if not channel.last_ip:
                    channel.last_ip = ip
            logger.info(f"{channel.label}: адрес {ip} ({ip_prefix21(ip)})")

        for channel in channels:
            threading.Thread(
                target=worker, args=(channel,), name=f"ip-learn-{channel.label}", daemon=True
            ).start()

    def touch(self, proxy_string: str) -> None:
        """Канал ушёл в запрос — в очереди он становится последним."""
        with self._lock:
            channel = self._channel(proxy_string)
            if channel is not None:
                channel.last_used = time.monotonic()

    def last_used(self, proxy_string: str) -> float:
        """Когда канал последний раз бил Avito; ``0`` — ещё ни разу."""
        with self._lock:
            channel = self._channel(proxy_string)
            return channel.last_used if channel is not None else 0.0

    def is_available(self, proxy_string: str) -> bool:
        """Канал можно бить: не отдыхает после 429 и не меняет IP. Чужой прокси — можно."""
        with self._lock:
            self._expire_cooldowns()
            channel = self._channel(proxy_string)
            return channel is None or self._available(channel)

    def wait_available(self, timeout: float) -> bool:
        """Дождаться хоть одного канала, который не отдыхает и не меняет IP."""
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            if self.live_size > 0:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.2)

    def snapshot(self) -> list[dict]:
        """Состояние каналов для страницы мониторинга, без логина и ссылки смены IP."""
        now = time.monotonic()
        with self._lock:
            rows = []
            for channel in self._channels:
                cooling = max(0.0, channel.cooling_until - now) if channel.cooling_until else 0.0
                resting = bool(channel.cooling_until and now < channel.cooling_until)
                rows.append(
                    {
                        "label": channel.label,
                        "ip": channel.last_ip,
                        "prefix": ip_prefix21(channel.last_ip) if channel.last_ip else "",
                        "changing": channel.changing,
                        "available": not channel.changing and not resting,
                        "cooldown_sec": round(cooling, 1),
                        "strikes": channel.strikes,
                        "bans": channel.bans,
                        "hangs": channel.hangs,
                        "leases": channel.leases,
                    }
                )
            return rows

    def note_ok(self, proxy_string: str) -> None:
        """Канал отдал ответ — сбрасываем серию 429 и таймаутов."""
        if not proxy_string:
            return
        with self._lock:
            channel = self._channel(proxy_string)
            if channel is not None:
                channel.strikes = 0
                channel.bans = 0
                channel.hangs = 0

    def clear_hangs(self, proxy_string: str) -> None:
        """Канал снова отдал ответ — счётчик зависаний сбрасываем."""
        with self._lock:
            channel = self._channel(proxy_string)
            if channel is not None:
                channel.hangs = 0

    def proxy_for(self, key: str) -> str:
        """Канал набора cookies: закреплённый, пока он жив, иначе новый.

        Набор без канала садится на самый свободный, поэтому при двух прокси
        запросы делятся между ними примерно поровну.
        """
        with self._lock:
            self._expire_cooldowns()
            leased = self._leases.get(key)
            if leased is not None and self._available(leased):
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
            self._expire_cooldowns()
            channel = self._least_busy()
            return channel.proxy_string if channel else ""

    def ban(
        self,
        proxy_string: str,
        reason: str,
        *,
        wait: bool = True,
        rotate_ip: bool = False,
    ) -> None:
        """Канал поймал бан: увести наборы на соседей.

        ``429`` при живом соседе — пауза без смены IP: ``change_ip`` на том
        же порту часто возвращает ту же ``/21`` и на 10 с глушит канал.
        IP меняем, если запас кончился, порт словил серию 429, туннель
        мёртв (``rotate_ip``) или прокси один.
        """
        with self._lock:
            self._expire_cooldowns()
            channel = self._channel(proxy_string) or self._least_busy()
            if channel is None:
                logger.warning(f"{reason}: прокси не настроены")
                return

            prefix = ip_prefix21(channel.last_ip)
            if prefix:
                logger.warning(f"{reason}: {channel.label} {prefix}")

            # Avito режет подсеть целиком: порт с адресом из той же /21 —
            # не запасной, а следующий 429.
            siblings = [
                other
                for other in self._channels
                if not rotate_ip
                and prefix
                and other is not channel
                and self._available(other)
                and ip_prefix21(other.last_ip) == prefix
            ]
            for other in siblings:
                other.cooling_until = time.monotonic() + self.ban_cooldown
            if siblings:
                names = ", ".join(other.label for other in siblings)
                logger.warning(f"{reason}: та же {prefix} у {names} — тоже на паузу")
                self._rebalance()

            spare = [
                other
                for other in self._channels
                if other is not channel and self._available(other)
            ]
            if not rotate_ip:
                channel.strikes += 1
                channel.bans += 1
            cooldown = self._cooldown(channel)
            need_rotate = (
                rotate_ip
                or not spare
                or channel.strikes >= self.ip_change_after_strikes
            )

            if spare:
                names = ", ".join(other.label for other in spare)
                if need_rotate:
                    logger.warning(
                        f"{reason}: {channel.label} меняет IP"
                        f"{'' if rotate_ip else f' после {channel.strikes} отказов'}"
                        f", наборы уходят на {names}"
                    )
                else:
                    logger.warning(
                        f"{reason}: {channel.label} пауза {cooldown:.0f} с "
                        f"без смены IP ({channel.bans}-й отказ подряд), "
                        f"наборы уходят на {names}"
                    )
            elif len(self._channels) > 1:
                logger.warning(f"{reason}: свободных каналов нет, жду новый IP на {channel.label}")
            else:
                logger.warning(f"{reason}: один прокси ({channel.label}), меняю IP")

            if not need_rotate:
                channel.cooling_until = time.monotonic() + cooldown
                self._resettle(channel, spare)
                return

            channel.strikes = 0
            channel.cooling_until = 0.0
            starting = not channel.changing
            if starting:
                channel.changing = True
                channel.hangs = 0
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

    def _available(self, channel: ProxyChannel) -> bool:
        """Канал принимает запросы: не меняет IP и не в паузе после 429."""
        if channel.changing:
            return False
        if channel.cooling_until and time.monotonic() < channel.cooling_until:
            return False
        return True

    def _expire_cooldowns(self) -> None:
        """Вернуть в раскладку порты, у которых кончилась пауза после 429."""
        now = time.monotonic()
        revived = False
        for channel in self._channels:
            if channel.cooling_until and now >= channel.cooling_until:
                channel.cooling_until = 0.0
                revived = True
        if revived:
            self._rebalance()

    def _least_busy(self) -> ProxyChannel | None:
        """Самый свободный живой канал; если живых нет — самый свободный вообще."""
        if not self._channels:
            return None
        live = [channel for channel in self._channels if self._available(channel)]
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
        live = [channel for channel in self._channels if self._available(channel)]
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
            new_ip = ""
            try:
                new_ip = change_ip(
                    channel.change_url, channel.proxy_string, wait_max=self.change_wait
                ) or ""
            except RuntimeError as err:
                logger.warning(f"Не удалось сменить IP {channel.label}: {err}")
            finally:
                with self._lock:
                    channel.last_ip = new_ip
                    channel.changing = False
                    channel.strikes = 0
                    channel.hangs = 0
                    self._rebalance()
                channel.ready.set()
                logger.info(f"{channel.label} снова в работе, наборы разложены заново")

        threading.Thread(target=worker, name=f"ip-change-{channel.label}", daemon=True).start()


PROXY_POOL = ProxyPool()


def current_proxy_string(fallback: str = "") -> str:
    """Живой прокси из пула или запасной из настроек."""
    return PROXY_POOL.current_string() or fallback
