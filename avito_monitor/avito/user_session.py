"""Личная сессия Avito пользователя — только для кнопки «Позвонить».

Номер телефона Avito показывает лишь авторизованным пользователям, а
регистрировать аккаунт из кода нельзя: нужен ввод кода из SMS. Поэтому
пользователь входит в Avito сам в своём браузере и переносит cookies в
приложение (см. раздел «Привязка Avito» в README).

Cookies используются исключительно для запроса номера. Опрос выдачи идёт на
покупных наборах из пула, чтобы личный аккаунт не попадал под ограничения.
"""

from __future__ import annotations

import json
import time
from typing import Any

from loguru import logger

from avito_monitor.avito.phone import fetch_phone as request_phone
from avito_monitor.config import load_settings
from avito_monitor.net.client import Session, build_client
from avito_monitor.paths import AVITO_USER_SESSION_PATH

# Присутствие любого из этих cookie означает выполненный вход.
AUTH_COOKIE_KEYS = ("sessid", "auth", "uas", "buyer_session_id", "rt", "ft", "authzone")

# Ошибки, при которых стоит повторить запрос через мобильный прокси.
_RETRY_VIA_PROXY_CODES = frozenset({"blocked", "unavailable", "auth_required"})

_WRITE_ATTEMPTS = 5
_WRITE_PAUSE = 0.05


def normalize_import(payload: Any) -> dict[str, Any]:
    """Привести выгрузку cookies к виду ``{"cookies": {имя: значение}}``.

    Расширения браузера отдают cookies по-разному: массивом объектов
    ``{name, value}``, объектом целиком или строкой с JSON внутри.

    :raises ValueError: разобрать выгрузку не удалось.
    """
    if isinstance(payload, list):
        cookies: dict[str, str] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            name = item.get("name") if item.get("name") is not None else item.get("Name")
            value = item.get("value") if item.get("value") is not None else item.get("Value")
            if name is None or value is None:
                continue
            cookies[str(name)] = str(value)
        return {"cookies": cookies}

    if not isinstance(payload, dict):
        raise ValueError("Ожидается JSON-объект или массив cookies")

    if isinstance(payload.get("cookies"), list):
        payload = {**payload, **normalize_import(payload["cookies"])}

    cookies = payload.get("cookies")
    if isinstance(cookies, str):
        try:
            parsed = json.loads(cookies)
        except ValueError as err:
            raise ValueError("Поле cookies — невалидный JSON") from err
        if isinstance(parsed, dict):
            payload = {**payload, "cookies": parsed}
        elif isinstance(parsed, list):
            payload = {**payload, **normalize_import(parsed)}
        else:
            raise ValueError("cookies должны быть объектом или массивом")

    return payload


def is_logged_in(cookies: dict) -> bool:
    """Есть ли в наборе признак выполненного входа."""
    return any(cookies.get(key) for key in AUTH_COOKIE_KEYS)


def _write_json(data: dict) -> None:
    """Записать сессию, переждав недолгую блокировку файла антивирусом."""
    AVITO_USER_SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    for attempt in range(_WRITE_ATTEMPTS):
        try:
            AVITO_USER_SESSION_PATH.write_text(payload, encoding="utf-8")
            return
        except OSError:
            if attempt == _WRITE_ATTEMPTS - 1:
                raise
            time.sleep(_WRITE_PAUSE * (attempt + 1))


def load_session() -> dict | None:
    """Сохранённая сессия или ``None``."""
    if not AVITO_USER_SESSION_PATH.exists():
        return None
    try:
        data = json.loads(AVITO_USER_SESSION_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("cookies") else None


def _infer_impersonate(user_agent: str) -> str | None:
    """Подобрать TLS-отпечаток под user-agent браузера пользователя.

    Отпечаток должен совпадать с браузером, из которого перенесли cookies:
    иначе Avito видит вход с iPhone и запрос с профилем Chrome на Android.
    """
    ua = (user_agent or "").lower()
    if any(device in ua for device in ("iphone", "ipad", "ipod")):
        return "safari18_0_ios" if "version/18" in ua else "safari17_2_ios"
    if "android" in ua and "chrome" in ua:
        return "chrome131_android"
    if "safari" in ua and "chrome" not in ua and "chromium" not in ua:
        return "safari17_0"
    if "chrome" in ua:
        return "chrome131"
    return None


def _session_label(cookies: dict) -> str:
    """Короткая метка сессии для интерфейса, без раскрытия значений cookie."""
    for key in AUTH_COOKIE_KEYS:
        if cookies.get(key):
            return f"cookie:{key}"
    user = cookies.get("u")
    if user:
        return f"u:…{str(user).split('.')[-1][:6]}"
    return "guest"


def save_session(payload: Any) -> dict[str, Any]:
    """Сохранить перенесённую сессию Avito.

    :raises ValueError: cookies пустые или без признака входа.
    """
    session = normalize_import(payload)
    cookies = session.get("cookies") or {}
    if not isinstance(cookies, dict) or not cookies:
        raise ValueError("Пустые cookies")
    if not is_logged_in(cookies):
        raise ValueError(
            "Нет cookies входа (sessid/auth). Экспортируйте все cookies с m.avito.ru, не только u."
        )

    user_agent = str(session.get("user_agent") or "").strip()
    fingerprint = dict(session.get("fingerprint") or {})
    impersonate = fingerprint.get("impersonate") or _infer_impersonate(user_agent)
    if impersonate:
        fingerprint["impersonate"] = impersonate

    stored = {
        "cookies": cookies,
        "user_agent": user_agent,
        "fingerprint": fingerprint,
        "saved_at": time.time(),
        "label": session.get("label") or _session_label(cookies),
    }
    _write_json(stored)
    logger.info(f"Сессия Avito сохранена ({stored['label']})")
    return stored


def clear_session() -> None:
    """Удалить сохранённую сессию."""
    AVITO_USER_SESSION_PATH.unlink(missing_ok=True)
    logger.info("Сессия Avito удалена")


def session_status() -> dict[str, Any]:
    """Состояние привязки Avito для интерфейса."""
    session = load_session()
    if not session:
        return {"connected": False, "logged_in": False, "label": "", "saved_at": 0.0}
    cookies = session.get("cookies") or {}
    logged_in = is_logged_in(cookies)
    return {
        "connected": logged_in,
        "logged_in": logged_in,
        "label": session.get("label") or _session_label(cookies),
        "saved_at": float(session.get("saved_at") or 0),
    }


def build_user_client(session: dict, proxy_string: str = "") -> Session:
    """HTTP-клиент с cookies пользователя."""
    return build_client(
        {
            "cookies": session.get("cookies") or {},
            "user_agent": session.get("user_agent"),
            "fingerprint": session.get("fingerprint") or {},
        },
        proxy_string,
    )


def fetch_phone(ad_id: str | int, phone_key: str | None = None) -> dict[str, Any]:
    """Запросить номер объявления от имени пользователя.

    Сначала пробуем напрямую: вход в Avito выполнен с этого же IP, и такой
    запрос выглядит естественнее. Если Avito отказал, повторяем через
    мобильный прокси.
    """
    session = load_session()
    if not session:
        return {
            "ok": False,
            "error": "Avito не подключён — войдите в аккаунт",
            "code": "no_session",
        }
    if not is_logged_in(session.get("cookies") or {}):
        return {
            "ok": False,
            "error": "Сессия без входа в Avito — подключите аккаунт заново",
            "code": "not_logged_in",
        }

    key = (phone_key or "").strip() or None
    result = request_phone(build_user_client(session), ad_id, phone_key=key)
    if result.get("ok"):
        logger.info(f"Номер ad={ad_id}: получен напрямую")
        return result

    from avito_monitor.net.proxies import current_proxy_string

    proxy_string = current_proxy_string(load_settings().proxy_string)
    if proxy_string and result.get("code") in _RETRY_VIA_PROXY_CODES:
        logger.info(f"Номер ad={ad_id}: повтор через прокси")
        result = request_phone(build_user_client(session, proxy_string), ad_id, phone_key=key)

    if result.get("ok"):
        logger.info(f"Номер ad={ad_id}: получен через прокси")
    elif result.get("code") == "auth_required":
        logger.warning(f"Номер ad={ad_id}: сессия Avito устарела")
    else:
        logger.info(f"Номер ad={ad_id}: {result.get('code') or result.get('error')}")
    return result
