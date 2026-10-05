"""Замеры работы парсера для страницы мониторинга.

Счётчики живут в памяти процесса: после перезапуска контейнера они
начинаются заново. В снимок не попадают cookies, пароли прокси и ссылки
смены IP.
"""

from __future__ import annotations

import re
import statistics
import threading
import time
from collections import deque
from typing import Any

_SECRET = re.compile(r"(?P<scheme>https?://)?[^/\s:@]+:[^/\s@]+@")
_EVENT_LIMIT = 40
_SAMPLE_LIMIT = 60

_KINDS = ("json", "429", "403", "439", "timeout", "drop", "antibot", "other")


def public_text(text: str) -> str:
    """Убрать из строки логин и пароль, если они попали в текст ошибки."""
    cleaned = _SECRET.sub(lambda match: match.group("scheme") or "", text)
    return cleaned.replace("\n", " ").strip()[:300]


def _empty_counts() -> dict[str, int]:
    return {kind: 0 for kind in _KINDS}


class Metrics:
    """Счётчики запросов, циклов и последних ошибок."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.requests = _empty_counts()
        self.by_proxy: dict[str, dict[str, int]] = {}
        self.cycles = 0
        self.cycles_ok = 0
        self.cycles_failed = 0
        self.cycles_throttled = 0
        self.new_ads = 0
        self.pace = 0.0
        self.cookie_sets = 0
        self.json_age_min: float | None = None
        self.json_age_max: float | None = None
        self._durations: deque[float] = deque(maxlen=_SAMPLE_LIMIT)
        self._events: deque[dict[str, Any]] = deque(maxlen=_EVENT_LIMIT)

    def reset(self) -> None:
        fresh = Metrics()
        with self._lock:
            self.started_at = fresh.started_at
            self.requests = fresh.requests
            self.by_proxy = fresh.by_proxy
            self.cycles = 0
            self.cycles_ok = 0
            self.cycles_failed = 0
            self.cycles_throttled = 0
            self.new_ads = 0
            self.pace = 0.0
            self.cookie_sets = 0
            self.json_age_min = None
            self.json_age_max = None
            self._durations = fresh._durations
            self._events = fresh._events

    def note_request(
        self,
        proxy: str,
        kind: str,
        *,
        status: int = 0,
        cookie_id: object = None,
    ) -> None:
        """Один ответ канала. ``kind`` — json, 429, 403, 439, timeout, drop, antibot, other."""
        label = _proxy_label(proxy)
        bucket = kind if kind in self.requests else "other"
        with self._lock:
            self.requests[bucket] += 1
            row = self.by_proxy.setdefault(label, _empty_counts())
            row[bucket] += 1
            if bucket != "json":
                self._events.append(
                    {
                        "at": time.time(),
                        "proxy": label,
                        "kind": bucket,
                        "text": _event_text(bucket, status, cookie_id),
                    }
                )

    def note_event(self, kind: str, text: str, *, proxy: str = "") -> None:
        """Ошибка цикла или обслуживания, не привязанная к одному ответу Avito."""
        with self._lock:
            self._events.append(
                {
                    "at": time.time(),
                    "proxy": _proxy_label(proxy) if proxy else "",
                    "kind": kind or "error",
                    "text": public_text(text) or "ошибка",
                }
            )

    def note_json_age(self, fresh: float, oldest: float) -> None:
        with self._lock:
            self.json_age_min = fresh
            self.json_age_max = oldest

    def note_cycle(
        self,
        *,
        seconds: float,
        failed: bool,
        throttled: bool,
        new_ads: int,
        pace: float,
        cookie_sets: int,
    ) -> None:
        with self._lock:
            self.cycles += 1
            if failed:
                self.cycles_failed += 1
            else:
                self.cycles_ok += 1
            if throttled:
                self.cycles_throttled += 1
            self.new_ads += max(0, new_ads)
            self.pace = pace
            self.cookie_sets = cookie_sets
            self._durations.append(max(0.0, seconds))

    def snapshot(self) -> dict[str, Any]:
        """Счётчики плюс живое состояние прокси, cookies и поиска."""
        with self._lock:
            durations = list(self._durations)
            payload = {
                "started_at": self.started_at,
                "now": time.time(),
                "requests": dict(self.requests),
                "cycles": self.cycles,
                "cycles_ok": self.cycles_ok,
                "cycles_failed": self.cycles_failed,
                "cycles_throttled": self.cycles_throttled,
                "new_ads": self.new_ads,
                "pace_sec": round(self.pace, 1),
                "cookie_sets": self.cookie_sets,
                "last_cycle_sec": round(durations[-1], 1) if durations else None,
                "cycle_median_sec": round(statistics.median(durations), 1) if durations else None,
                "json_age_min_sec": None if self.json_age_min is None else round(self.json_age_min),
                "json_age_max_sec": None if self.json_age_max is None else round(self.json_age_max),
                "events": list(self._events),
                "by_proxy": {label: dict(counts) for label, counts in self.by_proxy.items()},
            }
        payload["proxies"] = _proxy_rows(payload.pop("by_proxy"))
        payload["cookies"] = _cookie_view()
        payload["search"] = _search_view()
        return payload


def _proxy_label(proxy: str) -> str:
    text = public_text(proxy)
    return text.rsplit("@", 1)[-1] or "без прокси"


def _event_text(kind: str, status: int, cookie_id: object) -> str:
    cookie = f" id={cookie_id}" if cookie_id else ""
    if kind == "429":
        return f"429, бан по IP{cookie}"
    if kind in {"403", "439"}:
        return f"{kind}, cookie{cookie} отклонён"
    if kind == "timeout":
        return f"таймаут{cookie}"
    if kind == "drop":
        return f"обрыв соединения{cookie}"
    if kind == "antibot":
        return f"ответ без JSON{cookie}"
    if status:
        return f"отказ {status}{cookie}"
    return f"отказ{cookie}"


def _proxy_rows(counts: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    from avito_monitor.net.proxies import PROXY_POOL

    rows = []
    seen: set[str] = set()
    for channel in PROXY_POOL.snapshot():
        label = str(channel["label"])
        seen.add(label)
        rows.append({**channel, "requests": counts.get(label, _empty_counts())})
    for label, row_counts in counts.items():
        if label in seen:
            continue
        rows.append(
            {
                "label": label,
                "ip": "",
                "prefix": "",
                "changing": False,
                "available": False,
                "cooldown_sec": 0,
                "strikes": 0,
                "bans": 0,
                "hangs": 0,
                "leases": 0,
                "requests": row_counts,
            }
        )
    return rows


def _cookie_view() -> dict[str, Any]:
    from avito_monitor.cookies import pool

    now = time.time()
    counts = {"ready": 0, "in_use": 0, "blocked": 0, "dead": 0}
    ages: list[float] = []
    blocked: list[dict[str, Any]] = []
    for slot in pool.list_slots():
        status = str(slot.get("status") or "")
        if status in counts:
            counts[status] += 1
        bought_at = slot.get("bought_at")
        age_hours = None
        try:
            if bought_at is not None:
                age_hours = round((now - float(bought_at)) / 3600, 2)
                ages.append(age_hours)
        except (TypeError, ValueError):
            age_hours = None
        if status == pool.STATUS_BLOCKED:
            blocked_for = None
            try:
                if slot.get("blocked_at") is not None:
                    blocked_for = round(now - float(slot["blocked_at"]))
            except (TypeError, ValueError):
                blocked_for = None
            blocked.append(
                {
                    "id": slot.get("id"),
                    "blocked_sec": blocked_for,
                    "age_hours": age_hours,
                }
            )
    return {
        "counts": counts,
        "usable": counts["ready"] + counts["in_use"],
        "oldest_hours": max(ages) if ages else None,
        "youngest_hours": min(ages) if ages else None,
        "blocked": blocked[:20],
    }


def _search_view() -> dict[str, Any]:
    from avito_monitor.search_session import SESSION

    state = SESSION.snapshot()
    region = state.get("region") or {}
    category = state.get("category") or {}
    return {
        "running": bool(state.get("running")),
        "query": state.get("query") or "",
        "region": region.get("name") or "",
        "category": category.get("name") or "",
        "error": public_text(str(state.get("error") or "")),
        "started_at": state.get("started_at") or 0,
    }


METRICS = Metrics()
