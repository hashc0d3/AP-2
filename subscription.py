"""Аккаунты по телефону, подписки, промокоды и фиктивная SMS."""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path

from loguru import logger

STORAGE_DIR = Path("storage")
ACCOUNTS_PATH = STORAGE_DIR / "accounts.json"
SESSION_PATH = STORAGE_DIR / "session.json"
LEGACY_PATH = STORAGE_DIR / "subscription.json"
PROMOS_PATH = STORAGE_DIR / "promos.json"

PRICE_RUB = 3000
TRIAL_HOURS = 24
PAID_DAYS = 30
SMS_CODE = "1111"
SMS_TTL = 600

_lock = threading.RLock()

_DEFAULT_PROMOS: dict[str, dict] = {
    "WELCOME": {"type": "percent", "value": 20, "uses_left": 100, "note": "скидка 20%"},
    "AVITO500": {"type": "fixed", "value": 500, "uses_left": 50, "note": "минус 500 ₽"},
    "START": {"type": "percent", "value": 100, "uses_left": 20, "note": "первый месяц бесплатно"},
}


def _read_json(path: Path, fallback):
    if not path.exists():
        return fallback
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback
    return data if isinstance(data, type(fallback)) else fallback


def _write_json(path: Path, data) -> None:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _now() -> float:
    return time.time()


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D+", "", raw or "")
    if len(digits) == 11 and digits[0] in {"7", "8"}:
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    if len(digits) != 11 or not digits.startswith("7"):
        raise ValueError("Введите номер в формате +7 XXX XXX-XX-XX")
    return "+" + digits


def format_phone(phone: str) -> str:
    digits = re.sub(r"\D+", "", phone or "")
    if len(digits) != 11:
        return phone
    return f"+{digits[0]} {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:]}"


def _empty_account(phone: str) -> dict:
    now = _now()
    return {
        "id": uuid.uuid4().hex[:12],
        "phone": phone,
        "created_at": now,
        "verified_at": now,
        "plan": "none",
        "active": False,
        "trial_used": False,
        "started_at": 0,
        "expires_at": 0,
        "promos_used": [],
        "history": [],
    }


def _empty_session() -> dict:
    return {"phone": "", "pending": {}}


def _load_accounts() -> dict[str, dict]:
    raw = _read_json(ACCOUNTS_PATH, {})
    if not isinstance(raw, dict):
        return {}
    items = raw.get("accounts") if "accounts" in raw else raw
    if not isinstance(items, dict):
        return {}
    return {str(phone): dict(acc) for phone, acc in items.items() if isinstance(acc, dict)}


def _save_accounts(accounts: dict[str, dict]) -> None:
    _write_json(ACCOUNTS_PATH, {"accounts": accounts})


def _load_session() -> dict:
    raw = _read_json(SESSION_PATH, {})
    session = _empty_session()
    if isinstance(raw, dict):
        session.update(raw)
    return session


def _save_session(session: dict) -> None:
    _write_json(SESSION_PATH, session)


def _load_promos() -> dict[str, dict]:
    raw = _read_json(PROMOS_PATH, None)
    if not isinstance(raw, dict) or not raw:
        _write_json(PROMOS_PATH, _DEFAULT_PROMOS)
        return dict(_DEFAULT_PROMOS)
    return raw


def _save_promos(promos: dict) -> None:
    _write_json(PROMOS_PATH, promos)


def _is_active(account: dict, now: float | None = None) -> bool:
    now = _now() if now is None else now
    return bool(float(account.get("expires_at") or 0) > now)


def _refresh_account(account: dict) -> dict:
    now = _now()
    account["active"] = _is_active(account, now)
    if not account["active"] and account.get("plan") not in {"none", "", "expired"} and float(account.get("expires_at") or 0):
        account["plan"] = "expired"
    return account


def _migrate_legacy() -> None:
    accounts = _load_accounts()
    session = _load_session()
    legacy = _read_json(LEGACY_PATH, {})
    if not isinstance(legacy, dict):
        return
    phone = str(legacy.get("phone") or "")
    if phone and phone not in accounts and (legacy.get("expires_at") or legacy.get("plan") not in {None, "", "none"}):
        account = _empty_account(phone)
        for key in ("plan", "trial_used", "started_at", "expires_at", "promos_used", "history"):
            if key in legacy:
                account[key] = legacy[key]
        account["verified_at"] = float(legacy.get("started_at") or _now())
        _refresh_account(account)
        accounts[phone] = account
        _save_accounts(accounts)
        if not session.get("phone"):
            session["phone"] = phone
            _save_session(session)
        logger.info(f"Перенёс подписку в аккаунт {phone}")


def _current_account() -> dict | None:
    _migrate_legacy()
    session = _load_session()
    phone = str(session.get("phone") or "")
    if not phone:
        return None
    accounts = _load_accounts()
    account = accounts.get(phone)
    if not account:
        return None
    return _refresh_account(account)


def _promo_key(code: str) -> str:
    return (code or "").strip().upper()


def _quote_promo(code: str, promos: dict | None = None) -> dict:
    key = _promo_key(code)
    if not key:
        return {"code": "", "price": PRICE_RUB, "discount": 0, "note": ""}
    catalog = promos if promos is not None else _load_promos()
    item = catalog.get(key)
    if not isinstance(item, dict):
        raise ValueError("Промокод не найден")
    if int(item.get("uses_left") or 0) <= 0:
        raise ValueError("Промокод больше не действует")
    kind = str(item.get("type") or "percent")
    value = int(item.get("value") or 0)
    price = PRICE_RUB
    if kind == "percent":
        price = max(0, int(round(PRICE_RUB * (100 - value) / 100)))
    elif kind == "fixed":
        price = max(0, PRICE_RUB - value)
    elif kind == "free_days":
        price = 0
    else:
        raise ValueError("Промокод не найден")
    return {
        "code": key,
        "price": price,
        "discount": PRICE_RUB - price,
        "note": str(item.get("note") or ""),
        "extra_days": int(value) if kind == "free_days" else 0,
    }


def _public(account: dict | None = None) -> dict:
    account = _refresh_account(dict(account)) if account else None
    now = _now()
    expires = float((account or {}).get("expires_at") or 0)
    left = max(0, int(expires - now)) if account else 0
    active = bool(account and account.get("active"))
    return {
        "logged_in": bool(account),
        "account": (
            {
                "id": account.get("id"),
                "phone": account.get("phone"),
                "phone_label": format_phone(str(account.get("phone") or "")),
                "created_at": int(account.get("created_at") or 0),
            }
            if account
            else None
        ),
        "active": active,
        "plan": (account or {}).get("plan") or "none",
        "trial_used": bool((account or {}).get("trial_used")),
        "trial_available": bool(account) and not bool((account or {}).get("trial_used")),
        "expires_at": int(expires),
        "seconds_left": left,
        "phone": (account or {}).get("phone") or "",
        "price": PRICE_RUB,
        "currency": "RUB",
        "paid_days": PAID_DAYS,
        "trial_hours": TRIAL_HOURS,
    }


def public_status() -> dict:
    with _lock:
        return _public(_current_account())


def is_active() -> bool:
    return bool(public_status().get("active"))


def quote_promo(code: str) -> dict:
    with _lock:
        return _quote_promo(code)


def send_sms(phone: str) -> dict:
    number = normalize_phone(phone)
    with _lock:
        session = _load_session()
        pending = session.get("pending") if isinstance(session.get("pending"), dict) else {}
        last = float(pending.get("sent_at") or 0)
        if pending.get("phone") == number and _now() - last < 10:
            raise ValueError("Код уже отправлен, подождите несколько секунд")
        session["pending"] = {
            "phone": number,
            "sent_at": _now(),
            "attempts": 0,
        }
        _save_session(session)
    logger.info(f"Фиктивная SMS на {number}: код {SMS_CODE}")
    return {"ok": True, "phone": number, "message": f"Код отправлен на {number}"}


def _consume_sms(phone: str, code: str) -> None:
    number = normalize_phone(phone)
    session = _load_session()
    pending = session.get("pending") if isinstance(session.get("pending"), dict) else {}
    if pending.get("phone") != number:
        raise ValueError("Сначала запросите код на этот номер")
    if _now() - float(pending.get("sent_at") or 0) > SMS_TTL:
        raise ValueError("Код устарел, запросите новый")
    attempts = int(pending.get("attempts") or 0) + 1
    pending["attempts"] = attempts
    session["pending"] = pending
    if attempts > 5:
        session["pending"] = {}
        _save_session(session)
        raise ValueError("Слишком много попыток, запросите код снова")
    if (code or "").strip() != SMS_CODE:
        _save_session(session)
        raise ValueError("Неверный код из SMS")
    session["pending"] = {}
    _save_session(session)


def verify_phone(phone: str, code: str) -> dict:
    number = normalize_phone(phone)
    with _lock:
        _consume_sms(number, code)
        accounts = _load_accounts()
        created = number not in accounts
        account = accounts.get(number) or _empty_account(number)
        account["phone"] = number
        account["verified_at"] = _now()
        if created:
            account["created_at"] = account["verified_at"]
            logger.info(f"Создан аккаунт {account['id']} для {number}")
        else:
            logger.info(f"Вход в аккаунт {account['id']} {number}")
        _refresh_account(account)
        accounts[number] = account
        _save_accounts(accounts)
        session = _load_session()
        session["phone"] = number
        _save_session(session)
        return _public(account) | {"created": created}


def logout() -> dict:
    with _lock:
        session = _load_session()
        session["phone"] = ""
        session["pending"] = {}
        _save_session(session)
        return _public(None)


def activate_trial() -> dict:
    with _lock:
        account = _current_account()
        if not account:
            raise ValueError("Сначала подтвердите номер телефона")
        if _is_active(account):
            raise ValueError("Подписка уже активна")
        if account.get("trial_used"):
            raise ValueError("Пробный период для этого аккаунта уже использован")
        now = _now()
        account["plan"] = "trial"
        account["active"] = True
        account["trial_used"] = True
        account["started_at"] = now
        account["expires_at"] = now + TRIAL_HOURS * 3600
        history = list(account.get("history") or [])
        history.append({"kind": "trial", "at": now, "hours": TRIAL_HOURS})
        account["history"] = history[-50:]
        accounts = _load_accounts()
        accounts[account["phone"]] = account
        _save_accounts(accounts)
        logger.info(f"Пробный период для {account['phone']}")
        return _public(account)


def pay(promo: str = "", phone: str = "", code: str = "") -> dict:
    with _lock:
        if phone and code:
            verify_phone(phone, code)
        account = _current_account()
        if not account:
            raise ValueError("Сначала подтвердите номер телефона")
        promos = _load_promos()
        quote = _quote_promo(promo, promos) if promo.strip() else _quote_promo("", promos)
        key = quote.get("code") or ""
        if key:
            used = {str(x).upper() for x in (account.get("promos_used") or [])}
            if key in used:
                raise ValueError("Этот промокод вы уже использовали")
            item = promos.get(key) or {}
            item["uses_left"] = int(item.get("uses_left") or 0) - 1
            promos[key] = item
            _save_promos(promos)
            used.add(key)
            account["promos_used"] = sorted(used)
        now = _now()
        extra = int(quote.get("extra_days") or 0)
        days = PAID_DAYS + extra
        start_from = now
        if _is_active(account) and float(account.get("expires_at") or 0) > now:
            start_from = float(account["expires_at"])
        account["plan"] = "paid"
        account["active"] = True
        account["started_at"] = now
        account["expires_at"] = start_from + days * 86400
        history = list(account.get("history") or [])
        history.append({"kind": "paid", "at": now, "price": quote["price"], "promo": key, "days": days})
        account["history"] = history[-50:]
        accounts = _load_accounts()
        accounts[account["phone"]] = account
        _save_accounts(accounts)
        logger.info(f"Оплата {quote['price']} ₽ на {account['phone']}, {days} дн., promo={key or '-'}")
        return _public(account) | {"charged": quote["price"], "promo": key}
