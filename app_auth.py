"""Простой вход по логину и паролю."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from loguru import logger

from settings import load_env

STORAGE_DIR = Path("storage")
SESSION_PATH = STORAGE_DIR / "app_login.json"

_lock = threading.RLock()


def _credentials() -> tuple[str, str]:
    load_env()
    login = os.environ.get("APP_LOGIN", "admin").strip()
    password = os.environ.get("APP_PASSWORD", "change_me")
    return login, password


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
    login_user, _ = _credentials()
    with _lock:
        session = _read_session()
        return bool(session.get("logged_in") and session.get("username") == login_user)


def auth_status() -> dict:
    login_user, _ = _credentials()
    authed = is_authenticated()
    return {
        "logged_in": authed,
        "username": login_user if authed else "",
        "active": authed,
    }


def login(username: str, password: str) -> dict:
    login_user, login_password = _credentials()
    user = (username or "").strip()
    if user != login_user or password != login_password:
        raise ValueError("Неверный логин или пароль")
    with _lock:
        _write_session({"logged_in": True, "username": login_user, "at": time.time()})
        logger.info(f"Вход: {login_user}")
        return auth_status()


def logout() -> dict:
    with _lock:
        if SESSION_PATH.exists():
            SESSION_PATH.unlink(missing_ok=True)
        logger.info("Выход из приложения")
        return auth_status()
