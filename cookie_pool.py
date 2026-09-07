"""Пул мобильных cookies: по файлу на набор, выдача разблокированного парсеру."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests as std_requests
from loguru import logger

from settings import load_config

STORAGE_DIR = Path("storage")
COOKIES_DIR = STORAGE_DIR / "cookies"
POOL_PATH = STORAGE_DIR / "pool.json"
LEGACY_PATH = STORAGE_DIR / "cookies.json"
LIFECYCLE_LOG = Path("logs") / "cookie_lifecycle.log"
SPFA_COOKIES_URL = "https://spfa.pro/api/cookies/mobile/"
SPFA_UNBLOCK_URL = "https://spfa.pro/api/unblock/"
DEFAULT_POOL_SIZE = 5


def _fmt_dur(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    total = max(0, float(seconds))
    mins, secs = divmod(int(total), 60)
    hours, mins = divmod(mins, 60)
    if hours:
        human = f"{hours}ч {mins}м {secs}с"
    elif mins:
        human = f"{mins}м {secs}с"
    else:
        human = f"{secs}с"
    return f"{human}/{total:.1f}s"


def log_lifecycle(event: str, cookie_id, **fields) -> None:
    Path("logs").mkdir(exist_ok=True)
    parts = [f"event={event}", f"id={cookie_id}"]
    for key, value in fields.items():
        if value is None or value == "":
            continue
        parts.append(f"{key}={value}")
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | COOKIE " + " ".join(parts)
    with LIFECYCLE_LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def pool_size(cfg: dict | None = None) -> int:
    cfg = cfg or load_config()
    return max(1, int(cfg.get("cookie_pool_size") or DEFAULT_POOL_SIZE))


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def slot_path(cookie_id) -> Path:
    return COOKIES_DIR / f"{cookie_id}.json"


def load_pool() -> dict:
    if not POOL_PATH.exists():
        return {"active_id": None}
    try:
        data = json.loads(POOL_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"active_id": None}
    except (OSError, ValueError):
        return {"active_id": None}


def save_pool(data: dict) -> None:
    _write_json(POOL_PATH, data)


def load_slot(cookie_id) -> dict | None:
    path = slot_path(cookie_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("id") else None
    except (OSError, ValueError):
        return None


def save_slot(session: dict) -> dict:
    cookie_id = session.get("id")
    if not cookie_id:
        raise RuntimeError("session без id")
    session.setdefault("status", "ready")
    session.setdefault("mobile", True)
    session["saved_at"] = time.time()
    _write_json(slot_path(cookie_id), session)
    pool = load_pool()
    if pool.get("active_id") == cookie_id:
        _write_json(LEGACY_PATH, session)
    return session


def list_slots() -> list[dict]:
    COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    slots = []
    for path in sorted(COOKIES_DIR.glob("*.json")):
        if path.name.endswith(".tmp"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("id") and data.get("cookies"):
            slots.append(data)
    return slots


def import_legacy() -> dict | None:
    if not LEGACY_PATH.exists():
        return None
    try:
        data = json.loads(LEGACY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("id") or not data.get("cookies"):
        return None
    if load_slot(data["id"]):
        return load_slot(data["id"])
    data.setdefault("status", "ready")
    save_slot(data)
    logger.info(f"Импортировал cookies id={data['id']} в пул")
    return data


def buy_one(cfg: dict) -> dict:
    logger.info("Покупаю cookies для пула на spfa.pro...")
    response = std_requests.post(
        SPFA_COOKIES_URL,
        json={"api_key": cfg["cookies_api_key"], "mobile": True, "proxy": cfg["proxy_string"]},
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=40,
    )
    if not response.ok:
        raise RuntimeError(f"spfa.pro {response.status_code}: {response.text[:300]}")
    payload = response.json()
    results = payload.get("results") or {}
    cookies = results.get("cookies")
    fingerprint = results.get("fingerprint") or {}
    headers = fingerprint.get("headers") if isinstance(fingerprint, dict) else {}
    user_agent = results.get("user_agent") or (headers.get("user-agent") if isinstance(headers, dict) else None)
    if not payload.get("success") or not cookies or not user_agent:
        raise RuntimeError(f"Неполные cookies: {payload}")
    session = {
        "id": results.get("id"),
        "cookies": cookies,
        "user_agent": user_agent,
        "fingerprint": fingerprint,
        "mobile": results.get("mobile", True),
        "status": "ready",
        "last_unblock_at": time.time(),
        "unblock_ok": True,
        "saved_at": time.time(),
    }
    save_slot(session)
    logger.info(f"В пул добавлен id={session['id']}")
    time.sleep(3)
    return session


def unblock_one(session: dict, cfg: dict) -> dict | None:
    cookie_id = session.get("id")
    if not cookie_id:
        return None
    logger.info(f"Пул: разблокирую id={cookie_id}")
    try:
        response = std_requests.post(
            SPFA_UNBLOCK_URL,
            json={"id": cookie_id, "api_key": cfg["cookies_api_key"], "proxy": cfg["proxy_string"]},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=60,
        )
    except std_requests.RequestException as err:
        logger.warning(f"Пул: unblock id={cookie_id} сеть {err}")
        session["unblock_ok"] = False
        save_slot(session)
        return None
    if response.status_code == 410:
        logger.warning(f"Пул: id={cookie_id} мёртв (410, старше 12 часов)")
        session["status"] = "dead"
        session["unblock_ok"] = False
        blocked_for = None
        if session.get("blocked_at"):
            blocked_for = time.time() - float(session["blocked_at"])
        save_slot(session)
        log_lifecycle("dead", cookie_id, blocked_for=_fmt_dur(blocked_for))
        return None
    if not response.ok:
        logger.warning(f"Пул: unblock {response.status_code} id={cookie_id}: {response.text[:200]}")
        session["unblock_ok"] = False
        save_slot(session)
        return None
    try:
        payload = response.json()
    except ValueError:
        logger.warning(f"Пул: unblock id={cookie_id} не JSON")
        return None
    results = payload.get("results") or {}
    cookies = results.get("cookies")
    if not payload.get("success") or not cookies:
        logger.warning(f"Пул: unblock id={cookie_id} {payload}")
        session["unblock_ok"] = False
        save_slot(session)
        return None
    now = time.time()
    fresh = load_slot(cookie_id) or session
    was_blocked = fresh.get("status") == "blocked"
    blocked_at = fresh.get("blocked_at")
    fresh["cookies"] = cookies
    fresh["last_unblock_at"] = now
    fresh["unblock_ok"] = True
    if was_blocked:
        fresh["status"] = "ready"
        fresh["ready_at"] = now
    save_slot(fresh)
    if was_blocked:
        blocked_for = now - float(blocked_at) if blocked_at else None
        log_lifecycle(
            "ready",
            cookie_id,
            blocked_for=_fmt_dur(blocked_for),
            note="снова доступен парсеру",
        )
        logger.info(
            f"Пул: id={cookie_id} снова ready через {_fmt_dur(blocked_for) or '?'}"
        )
    else:
        logger.info(f"Пул: id={cookie_id} разблокирован, status={fresh.get('status')}")
    return fresh


def alive_slots() -> list[dict]:
    return [slot for slot in list_slots() if slot.get("status") != "dead"]


def ensure_pool(cfg: dict | None = None) -> list[dict]:
    cfg = cfg or load_config()
    import_legacy()
    size = pool_size(cfg)
    slots = alive_slots()
    while len(slots) < size:
        buy_one(cfg)
        slots = alive_slots()
    extra_dead = [slot for slot in list_slots() if slot.get("status") == "dead"]
    for slot in extra_dead:
        path = slot_path(slot["id"])
        if path.exists():
            path.unlink()
            logger.info(f"Пул: удалил мёртвый id={slot['id']}")
    return alive_slots()


def mark_blocked(cookie_id) -> None:
    session = load_slot(cookie_id)
    if not session:
        return
    now = time.time()
    used_for = None
    if session.get("last_used_at"):
        used_for = now - float(session["last_used_at"])
    session["status"] = "blocked"
    session["blocked_at"] = now
    save_slot(session)
    log_lifecycle("blocked", cookie_id, used_for=_fmt_dur(used_for))
    logger.info(f"Пул: id={cookie_id} помечен blocked, работал {_fmt_dur(used_for) or '?'}")


def usable_slots(exclude=None) -> list[dict]:
    """Только наборы, которые сервису уже удалось разблокировать."""
    exclude_id = str(exclude) if exclude is not None else None
    slots = []
    for slot in alive_slots():
        if str(slot.get("id")) == exclude_id:
            continue
        if slot.get("status") in {"blocked", "dead"}:
            continue
        if not slot.get("unblock_ok"):
            continue
        if slot.get("status") not in {"ready", "in_use"}:
            continue
        slots.append(slot)
    return slots


def wait_ready_cookie(exclude=None, attempts: int = 8, pause: float = 3) -> dict | None:
    """Ждёт, пока сервис пула вернёт хотя бы один ready-набор."""
    for attempt in range(1, attempts + 1):
        chosen = next_cookie(exclude)
        if chosen:
            return chosen
        logger.info(f"Пул без готовых cookies, жду сервис ({attempt}/{attempts})")
        time.sleep(pause)
    return None


def next_cookie(exclude=None) -> dict | None:
    """Следующий готовый набор. Вызывать только при старте или после блока."""
    pool = usable_slots(exclude)
    if not pool:
        return None
    pool.sort(key=lambda slot: str(slot.get("id")))
    data = load_pool()
    last = data.get("cursor") or data.get("active_id")
    ids = [str(slot["id"]) for slot in pool]
    index = 0
    if last is not None and str(last) in ids:
        index = (ids.index(str(last)) + 1) % len(ids)
    chosen = pool[index]
    for slot in list_slots():
        if slot.get("status") == "in_use" and str(slot.get("id")) != str(chosen["id"]):
            slot["status"] = "ready"
            save_slot(slot)
    now = time.time()
    recovered_in = None
    idle_ready = None
    if chosen.get("blocked_at"):
        recovered_in = now - float(chosen["blocked_at"])
    if chosen.get("ready_at"):
        idle_ready = now - float(chosen["ready_at"])
    prev_status = chosen.get("status")
    chosen["status"] = "in_use"
    chosen["last_used_at"] = now
    save_slot(chosen)
    save_pool({"active_id": chosen["id"], "cursor": chosen["id"]})
    _write_json(LEGACY_PATH, chosen)
    log_lifecycle(
        "acquired",
        chosen["id"],
        from_status=prev_status,
        recovered_in=_fmt_dur(recovered_in),
        idle_ready=_fmt_dur(idle_ready),
        rotate=f"{index + 1}/{len(ids)}",
    )
    extra = []
    if recovered_in is not None:
        extra.append(f"восстановлен за {_fmt_dur(recovered_in)}")
    if idle_ready is not None:
        extra.append(f"ждал в ready {_fmt_dur(idle_ready)}")
    suffix = f" ({', '.join(extra)})" if extra else ""
    logger.info(f"Пул: запрос на id={chosen['id']} [{index + 1}/{len(ids)}]{suffix}")
    return chosen


def acquire(exclude=None) -> dict | None:
    return next_cookie(exclude)


def current() -> dict | None:
    active_id = load_pool().get("active_id")
    if active_id:
        session = load_slot(active_id)
        if (
            session
            and session.get("status") not in {"dead", "blocked"}
            and session.get("unblock_ok")
        ):
            if session.get("status") != "in_use":
                session["status"] = "in_use"
                save_slot(session)
            return session
    return acquire()


def maintain(cfg: dict | None = None) -> None:
    cfg = cfg or load_config()
    ensure_pool(cfg)
    for slot in list_slots():
        if slot.get("status") == "dead":
            continue
        needs_unblock = slot.get("status") == "blocked" or not slot.get("unblock_ok")
        if not needs_unblock:
            continue
        unblock_one(slot, cfg)
        time.sleep(2)
    if len(alive_slots()) < pool_size(cfg):
        ensure_pool(cfg)
