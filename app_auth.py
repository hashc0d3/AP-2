"""Простой вход по логину и паролю."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from loguru import logger

STORAGE_DIR = Path("storage")
SESSION_PATH = STORAGE_DIR / "app_login.json"

LOGIN_USER = "sotik77"
LOGIN_PASSWORD = "admin77!"

_lock = threading.RLock()


def _read_session() -> dict:
    if not SESSION_PATH.exists():
        return {}
    try:
        data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_session(data: dict) -> None:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_authenticated() -> bool:
    with _lock:
        session = _read_session()
        return bool(session.get("logged_in") and session.get("username") == LOGIN_USER)


def auth_status() -> dict:
    authed = is_authenticated()
    return {
        "logged_in": authed,
        "username": LOGIN_USER if authed else "",
        "active": authed,
    }


def login(username: str, password: str) -> dict:
    user = (username or "").strip()
    if user != LOGIN_USER or password != LOGIN_PASSWORD:
        raise ValueError("Неверный логин или пароль")
    with _lock:
        _write_session({"logged_in": True, "username": LOGIN_USER, "at": time.time()})
        logger.info(f"Вход: {LOGIN_USER}")
        return auth_status()


def logout() -> dict:
    with _lock:
        if SESSION_PATH.exists():
            SESSION_PATH.unlink(missing_ok=True)
        logger.info("Выход из приложения")
        return auth_status()
