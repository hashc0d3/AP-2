"""Пул мобильных cookies: по файлу на набор.

Один набор cookies живёт около 12 часов и сгорает после нескольких отказов
Avito. Чтобы опрос не останавливался, наборов держим несколько: сгоревший
уходит на разблокировку к сервису, а цикл продолжает работать на остальных.

Каждый набор — отдельный файл ``storage/cookies/<id>.json``, чтобы parser и
фоновый сервис обслуживания могли писать одновременно, не мешая друг другу.
Состояние набора:

``ready``
    свободен и разблокирован — можно брать в работу;
``in_use``
    сейчас используется циклом опроса;
``blocked``
    Avito его отверг, ждёт разблокировки сервисом;
``dead``
    старше 12 часов, восстановлению не подлежит — удаляется из пула.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from loguru import logger

from avito_monitor import spfa
from avito_monitor.config import Settings
from avito_monitor.paths import (
    COOKIE_LIFECYCLE_LOG,
    COOKIES_DIR,
    LEGACY_COOKIES_PATH,
    LOG_DIR,
    POOL_PATH,
)

STATUS_READY = "ready"
STATUS_IN_USE = "in_use"
STATUS_BLOCKED = "blocked"
STATUS_DEAD = "dead"

_USABLE_STATUSES = frozenset({STATUS_READY, STATUS_IN_USE})
_UNUSABLE_STATUSES = frozenset({STATUS_BLOCKED, STATUS_DEAD})

# Сервису нужно время, чтобы новый набор стал рабочим.
_AFTER_BUY_PAUSE = 3.0
_BETWEEN_UNBLOCK_PAUSE = 2.0


# ── Чтение и запись ────────────────────────────────────────────────────────


def _write_json(path: Path, data: dict) -> None:
    """Атомарная запись: сначала во временный файл, потом подмена.

    Иначе фоновый сервис может прочитать недописанный файл и решить, что
    набор сломан.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def slot_path(cookie_id: Any) -> Path:
    return COOKIES_DIR / f"{cookie_id}.json"


def load_slot(cookie_id: Any) -> dict | None:
    data = _read_json(slot_path(cookie_id))
    return data if data and data.get("id") else None


def save_slot(slot: dict) -> dict:
    """Записать набор на диск.

    :raises ValueError: набор без идентификатора.
    """
    cookie_id = slot.get("id")
    if not cookie_id:
        raise ValueError("Набор cookies без id")
    slot.setdefault("status", STATUS_READY)
    slot.setdefault("mobile", True)
    slot["saved_at"] = time.time()
    _write_json(slot_path(cookie_id), slot)
    return slot


def list_slots() -> list[dict]:
    """Все наборы из ``storage/cookies``."""
    COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    slots = []
    for path in sorted(COOKIES_DIR.glob("*.json")):
        data = _read_json(path)
        if data and data.get("id") and data.get("cookies"):
            slots.append(data)
    return slots


def alive_slots() -> list[dict]:
    return [slot for slot in list_slots() if slot.get("status") != STATUS_DEAD]


def usable_slots(exclude: Any = None) -> list[dict]:
    """Наборы, которые сервису уже удалось разблокировать."""
    exclude_id = str(exclude) if exclude is not None else None
    return [
        slot
        for slot in alive_slots()
        if str(slot.get("id")) != exclude_id
        and slot.get("status") not in _UNUSABLE_STATUSES
        and slot.get("unblock_ok")
        and slot.get("status") in _USABLE_STATUSES
    ]


def _load_cursor() -> Any:
    """Последний выданный набор — чтобы ротация шла по кругу между запусками."""
    data = _read_json(POOL_PATH) or {}
    return data.get("cursor") or data.get("active_id")


def _save_cursor(cookie_id: Any) -> None:
    _write_json(POOL_PATH, {"active_id": cookie_id, "cursor": cookie_id})


def pool_size(settings: Settings) -> int:
    return settings.cookie_pool_size


# ── Журнал жизненного цикла ────────────────────────────────────────────────


def _format_duration(seconds: float | None) -> str | None:
    """«1ч 20м 5с/4805.0s» — для человека и для машинного разбора."""
    if seconds is None:
        return None
    total = max(0.0, float(seconds))
    minutes, secs = divmod(int(total), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        human = f"{hours}ч {minutes}м {secs}с"
    elif minutes:
        human = f"{minutes}м {secs}с"
    else:
        human = f"{secs}с"
    return f"{human}/{total:.1f}s"


def log_lifecycle(event: str, cookie_id: Any, **fields: Any) -> None:
    """Дописать строку в ``logs/cookie_lifecycle.log``.

    Отдельный файл, а не общий лог: по нему удобно считать, сколько набор
    жил и как быстро сервис его восстанавливает.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    parts = [f"event={event}", f"id={cookie_id}"]
    parts.extend(f"{key}={value}" for key, value in fields.items() if value not in (None, ""))
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | COOKIE " + " ".join(parts)
    with COOKIE_LIFECYCLE_LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


# ── Пополнение пула ───────────────────────────────────────────────────────


def import_legacy() -> dict | None:
    """Перенести в пул набор из ``storage/cookies.json`` старого формата.

    Разовая миграция: до появления пула набор был один и лежал отдельным
    файлом. После переноса файл удаляется.
    """
    data = _read_json(LEGACY_COOKIES_PATH)
    if not data or not data.get("id") or not data.get("cookies"):
        return None
    existing = load_slot(data["id"])
    if existing is None:
        data.setdefault("status", STATUS_READY)
        save_slot(data)
        logger.info(f"Импортировал cookies id={data['id']} в пул")
        existing = data
    LEGACY_COOKIES_PATH.unlink(missing_ok=True)
    return existing


def buy_one(settings: Settings, *, pause: bool = True) -> dict:
    """Купить набор cookies и положить в пул.

    :raises spfa.SpfaError: сервис не отдал набор.
    """
    logger.info("Покупаю cookies для пула…")
    results = spfa.buy_cookies(settings.cookies_api_key, settings.proxy_string)

    fingerprint = results.get("fingerprint") or {}
    headers = fingerprint.get("headers") if isinstance(fingerprint, dict) else {}
    user_agent = results.get("user_agent") or (
        headers.get("user-agent") if isinstance(headers, dict) else None
    )
    if not results.get("cookies") or not user_agent:
        raise spfa.SpfaError(f"Неполные cookies от сервиса: {results}")

    now = time.time()
    slot = save_slot(
        {
            "id": results.get("id"),
            "cookies": results["cookies"],
            "user_agent": user_agent,
            "fingerprint": fingerprint,
            "mobile": results.get("mobile", True),
            "status": STATUS_READY,
            "last_unblock_at": now,
            "unblock_ok": True,
        }
    )
    logger.info(f"В пул добавлен id={slot['id']}")
    if pause:
        time.sleep(_AFTER_BUY_PAUSE)
    return slot


def replenish_if_empty(settings: Settings) -> dict | None:
    """Если рабочих наборов нет — купить один сразу, без ожидания сервиса."""
    if usable_slots():
        return None
    logger.warning("Рабочих cookies нет — докупаю сразу")
    return buy_one(settings, pause=False)


def ensure_pool(settings: Settings) -> list[dict]:
    """Довести пул до нужного размера и убрать мёртвые наборы."""
    import_legacy()
    target = pool_size(settings)
    while len(alive_slots()) < target:
        buy_one(settings)

    if not usable_slots():
        replenish_if_empty(settings)

    for slot in list_slots():
        if slot.get("status") != STATUS_DEAD:
            continue
        path = slot_path(slot["id"])
        if path.exists():
            path.unlink(missing_ok=True)
            logger.info(f"Пул: удалил мёртвый id={slot['id']}")
    return alive_slots()


def unblock_one(slot: dict, settings: Settings) -> dict | None:
    """Попросить сервис переоформить набор.

    ``None`` — не получилось; набор останется заблокированным до следующего
    круга обслуживания.
    """
    cookie_id = slot.get("id")
    if not cookie_id:
        return None
    logger.info(f"Пул: разблокирую id={cookie_id}")

    try:
        results = spfa.unblock_cookies(cookie_id, settings.cookies_api_key, settings.proxy_string)
    except spfa.SpfaCookieGone as err:
        blocked_for = _elapsed_since(slot.get("blocked_at"))
        slot["status"] = STATUS_DEAD
        slot["unblock_ok"] = False
        save_slot(slot)
        log_lifecycle("dead", cookie_id, blocked_for=_format_duration(blocked_for))
        logger.warning(f"Пул: {err}")
        return None
    except spfa.SpfaError as err:
        slot["unblock_ok"] = False
        save_slot(slot)
        logger.warning(f"Пул: unblock id={cookie_id} — {err}")
        return None

    # Перечитываем с диска: пока шёл запрос, набор мог сгореть в цикле опроса.
    fresh = load_slot(cookie_id) or slot
    was_blocked = fresh.get("status") == STATUS_BLOCKED
    blocked_at = fresh.get("blocked_at")
    now = time.time()
    fresh["cookies"] = results["cookies"]
    fresh["last_unblock_at"] = now
    fresh["unblock_ok"] = True
    if was_blocked:
        fresh["status"] = STATUS_READY
        fresh["ready_at"] = now
    save_slot(fresh)

    if was_blocked:
        blocked_for = _format_duration(_elapsed_since(blocked_at))
        log_lifecycle("ready", cookie_id, blocked_for=blocked_for, note="снова доступен парсеру")
        logger.info(f"Пул: id={cookie_id} снова ready через {blocked_for or '?'}")
    else:
        logger.info(f"Пул: id={cookie_id} разблокирован, status={fresh.get('status')}")
    return fresh


def _elapsed_since(timestamp: Any) -> float | None:
    if not timestamp:
        return None
    try:
        return time.time() - float(timestamp)
    except (TypeError, ValueError):
        return None


# ── Выдача и возврат наборов ──────────────────────────────────────────────


def mark_blocked(cookie_id: Any) -> None:
    """Отметить, что Avito отверг набор."""
    slot = load_slot(cookie_id)
    if not slot:
        return
    used_for = _elapsed_since(slot.get("last_used_at"))
    slot["status"] = STATUS_BLOCKED
    slot["blocked_at"] = time.time()
    save_slot(slot)
    log_lifecycle("blocked", cookie_id, used_for=_format_duration(used_for))
    logger.info(f"Пул: id={cookie_id} помечен blocked, работал {_format_duration(used_for) or '?'}")


def next_cookie(exclude: Any = None) -> dict | None:
    """Следующий готовый набор по кругу; ``None`` — готовых нет."""
    pool = usable_slots(exclude)
    if not pool:
        return None
    pool.sort(key=lambda slot: str(slot.get("id")))
    ids = [str(slot["id"]) for slot in pool]

    cursor = _load_cursor()
    index = (ids.index(str(cursor)) + 1) % len(ids) if cursor and str(cursor) in ids else 0
    chosen = pool[index]

    # Освобождаем наборы, которые остались in_use после прошлого запуска.
    for slot in list_slots():
        if slot.get("status") == STATUS_IN_USE and str(slot.get("id")) != str(chosen["id"]):
            slot["status"] = STATUS_READY
            save_slot(slot)

    recovered_in = _elapsed_since(chosen.get("blocked_at"))
    idle_ready = _elapsed_since(chosen.get("ready_at"))
    previous_status = chosen.get("status")
    chosen["status"] = STATUS_IN_USE
    chosen["last_used_at"] = time.time()
    save_slot(chosen)
    _save_cursor(chosen["id"])

    log_lifecycle(
        "acquired",
        chosen["id"],
        from_status=previous_status,
        recovered_in=_format_duration(recovered_in),
        idle_ready=_format_duration(idle_ready),
        rotate=f"{index + 1}/{len(ids)}",
    )
    notes = []
    if recovered_in is not None:
        notes.append(f"восстановлен за {_format_duration(recovered_in)}")
    if idle_ready is not None:
        notes.append(f"ждал в ready {_format_duration(idle_ready)}")
    suffix = f" ({', '.join(notes)})" if notes else ""
    logger.info(f"Пул: запрос на id={chosen['id']} [{index + 1}/{len(ids)}]{suffix}")
    return chosen


def wait_ready_cookie(
    exclude: Any = None,
    settings: Settings | None = None,
) -> dict | None:
    """Вернуть готовый набор. Если рабочих нет — сразу докупить, без паузы."""
    chosen = next_cookie(exclude)
    if chosen:
        return chosen
    if settings is None:
        return None
    try:
        replenish_if_empty(settings)
    except Exception as err:
        logger.error(f"Не удалось докупить cookies: {err}")
        return None
    return next_cookie(exclude)


def maintain(settings: Settings) -> None:
    """Один круг обслуживания: докупить, разблокировать, добить до размера."""
    ensure_pool(settings)
    empty = not usable_slots()
    for slot in list_slots():
        if slot.get("status") == STATUS_DEAD:
            continue
        if slot.get("status") == STATUS_BLOCKED or not slot.get("unblock_ok"):
            unblock_one(slot, settings)
            if not empty:
                time.sleep(_BETWEEN_UNBLOCK_PAUSE)
    if len(alive_slots()) < pool_size(settings) or not usable_slots():
        ensure_pool(settings)
