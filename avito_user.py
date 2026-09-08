"""Авторизованная сессия Avito пользователя (только для запроса номера по клику)."""

from __future__ import annotations

import json
import time
from pathlib import Path

from loguru import logger

from avito_phone import fetch_phone
from parser import build_client
from settings import load_config

SESSION_PATH = Path("storage") / "avito_user.json"
_AUTH_COOKIE_KEYS = (
    "sessid",
    "auth",
    "uas",
    "buyer_session_id",
    "rt",
    "ft",
    "authzone",
)


def normalize_import(payload: dict | list) -> dict:
    if isinstance(payload, list):
        cookies = {
            str(item.get("name")): str(item.get("value"))
            for item in payload
            if isinstance(item, dict) and item.get("name") is not None and item.get("value") is not None
        }
        return {"cookies": cookies}

    if not isinstance(payload, dict):
        raise ValueError("Ожидается JSON-объект или массив cookies")

    if isinstance(payload.get("cookies"), list):
        listed = normalize_import(payload["cookies"])
        payload = {**payload, **listed}

    cookies = payload.get("cookies")
    if isinstance(cookies, str):
        try:
            parsed = json.loads(cookies)
        except ValueError as err:
            raise ValueError("Поле cookies — невалидный JSON") from err
        if isinstance(parsed, dict):
            payload["cookies"] = parsed
        elif isinstance(parsed, list):
            payload = {**payload, **normalize_import(parsed)}
        else:
            raise ValueError("cookies должны быть объектом или массивом")

    return payload


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    for attempt in range(5):
        try:
            with path.open("w", encoding="utf-8") as fh:
                fh.write(payload)
            return
        except OSError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def load_user_session() -> dict | None:
    if not SESSION_PATH.exists():
        return None
    try:
        data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("cookies"):
        return None
    return data


def _infer_impersonate(user_agent: str) -> str | None:
    ua = (user_agent or "").lower()
    if "iphone" in ua or "ipad" in ua or "ipod" in ua:
        if "version/18" in ua:
            return "safari18_0_ios"
        return "safari17_2_ios"
    if "android" in ua and "chrome" in ua:
        return "chrome131_android"
    if "safari" in ua and "chrome" not in ua and "chromium" not in ua:
        return "safari17_0"
    if "chrome" in ua:
        return "chrome131"
    return None


def save_user_session(session: dict) -> dict:
    session = normalize_import(session)
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
    payload = {
        "cookies": cookies,
        "user_agent": user_agent,
        "fingerprint": fingerprint,
        "saved_at": time.time(),
        "label": session.get("label") or _session_label(cookies),
    }
    _write_json(SESSION_PATH, payload)
    logger.info(f"Avito user session сохранена ({payload['label']})")
    return payload


def clear_user_session() -> None:
    if SESSION_PATH.exists():
        SESSION_PATH.unlink()
    logger.info("Avito user session удалена")


def _session_label(cookies: dict) -> str:
    for key in _AUTH_COOKIE_KEYS:
        if cookies.get(key):
            return f"cookie:{key}"
    user = cookies.get("u")
    if user:
        tail = str(user).split(".")[-1][:6]
        return f"u:…{tail}"
    return "guest"


def is_logged_in(cookies: dict) -> bool:
    return any(cookies.get(key) for key in _AUTH_COOKIE_KEYS)


def session_status() -> dict:
    session = load_user_session()
    if not session:
        return {"connected": False, "logged_in": False, "label": ""}
    cookies = session.get("cookies") or {}
    logged_in = is_logged_in(cookies)
    saved_at = float(session.get("saved_at") or 0)
    return {
        "connected": logged_in,
        "logged_in": logged_in,
        "label": session.get("label") or _session_label(cookies),
        "saved_at": saved_at,
    }


def build_user_client(session: dict, proxy_string: str | None = None):
    wrapped = {
        "cookies": session.get("cookies") or {},
        "user_agent": session.get("user_agent"),
        "fingerprint": session.get("fingerprint") or {},
    }
    client = build_client(wrapped, proxy_string or "")
    client.headers["accept"] = "application/json, text/plain, */*"
    return client


def fetch_user_phone(ad_id: str | int) -> dict:
    session = load_user_session()
    if not session:
        return {
            "ok": False,
            "error": "Avito не подключён — войдите в аккаунт",
            "code": "no_session",
        }
    cookies = session.get("cookies") or {}
    if not is_logged_in(cookies):
        return {
            "ok": False,
            "error": "Сессия без входа в Avito — подключите аккаунт заново",
            "code": "not_logged_in",
        }

    cfg = load_config()
    proxy_string = (cfg.get("proxy_string") or "").strip()

    # Сначала с тем же IP, с которого пользователь входил (без прокси).
    client = build_user_client(session, "")
    result = fetch_phone(client, ad_id)
    if result.get("ok"):
        logger.info(f"Avito phone ad={ad_id}: номер получен (прямое соединение)")
        return result

    if proxy_string and result.get("code") in {"blocked", "unavailable", "auth_required"}:
        logger.info(f"Avito phone ad={ad_id}: повтор через прокси")
        client = build_user_client(session, proxy_string)
        result = fetch_phone(client, ad_id)

    if result.get("code") == "auth_required":
        logger.warning(f"Avito phone ad={ad_id}: сессия устарела")
    elif result.get("ok"):
        logger.info(f"Avito phone ad={ad_id}: номер получен")
    else:
        logger.info(f"Avito phone ad={ad_id}: {result.get('code') or result.get('error')}")
    return result
