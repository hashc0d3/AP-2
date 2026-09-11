"""Темп опроса Avito.

Замеры на 135 циклах показали, что доля ответов 429 одинакова и при паузе
15 секунд, и при 4 (около 22%): это свойство пула IP мобильного прокси, а не
нашей частоты. Значит, отступать после каждого 429 бессмысленно — скорость
потеряем, а отказов меньше не станет.

Поэтому замедляемся только на серии отказов подряд: одиночный 429 проходит
незамеченным, а настоящий бан не даёт долбить впустую.
"""

from __future__ import annotations

from loguru import logger


def parallel_width(live_proxies: int) -> int:
    """Сколько каналов опрашивать в одном цикле.

    До трёх прокси цикл остаётся последовательным: так проще уйти на соседа
    после 429. С четырёх и больше берём до половины живых, но не больше трёх:
    за проход снимаются разные снимки SERP, а каждый IP получает запрос
    примерно раз в ``poll_interval × каналы / ширина`` секунд.
    """
    live = max(0, int(live_proxies))
    if live < 4:
        return 1
    return min(3, live // 2)


def poll_delay(poll_interval: float, per_cookie_interval: float, cookies: int) -> float:
    """Пауза до следующего цикла.

    Лента обновляется не реже ``poll_interval``, но один набор cookies бьёт
    Avito не чаще ``per_cookie_interval``: чем больше наборов в пуле, тем
    чаще можно опрашивать, не рискуя ни одним из них.
    """
    return max(poll_interval, per_cookie_interval / max(1, cookies))


def next_interval(
    pacer_interval: float,
    per_cookie_interval: float,
    cookies: int,
    *,
    failed: bool,
    throttled: bool,
    retry_pause: float,
) -> float:
    """Целевой интервал после цикла.

    ``retry_pause`` только если сорвалась сеть, а не 429/439: соседний
    прокси уже работает, а одиночный бан по IP на долю отказов не влияет.
    """
    target = poll_delay(pacer_interval, per_cookie_interval, cookies)
    if failed and not throttled:
        return max(target, retry_pause)
    return target


class PollPacer:
    """Интервал опроса, который сам подстраивается под поведение Avito."""

    THROTTLE_STREAK = 3
    """Столько отказов подряд считаем баном, а не шумом."""

    CLEAN_STREAK = 3
    """Столько чистых циклов подряд — можно снова ускоряться."""

    STEP_UP = 1.6
    """Во сколько раз замедляемся при бане."""

    STEP_DOWN = 2.0
    """Во сколько раз ускоряемся при выходе из бана."""

    def __init__(self, floor: float, ceiling: float) -> None:
        self._floor = max(1.0, floor)
        self._ceiling = max(self._floor, ceiling)
        self._interval = self._floor
        self._clean_streak = 0
        self._throttle_streak = 0

    @property
    def interval(self) -> float:
        """Текущий интервал между циклами в секундах."""
        return self._interval

    def on_ok(self) -> None:
        """Отметить удачный цикл."""
        self._throttle_streak = 0
        if self._interval <= self._floor:
            return
        self._clean_streak += 1
        if self._clean_streak < self.CLEAN_STREAK:
            return
        self._interval = max(self._floor, self._interval / self.STEP_DOWN)
        self._clean_streak = 0
        logger.info(f"Avito снова отвечает — возвращаю опрос к {self._interval:.1f} с")

    def on_throttle(self) -> None:
        """Отметить отказ Avito (429/403/439 или пустой ответ)."""
        self._clean_streak = 0
        self._throttle_streak += 1
        if self._throttle_streak < self.THROTTLE_STREAK or self._interval >= self._ceiling:
            return
        self._interval = min(self._ceiling, self._interval * self.STEP_UP)
        self._throttle_streak = 0
        logger.warning(
            f"{self.THROTTLE_STREAK} отказа подряд — похоже на бан, "
            f"замедляю опрос до {self._interval:.1f} с"
        )
