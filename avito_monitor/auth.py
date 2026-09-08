"""Вход в веб-интерфейс по логину и паролю.

Логин один (задаётся в ``.env``), но сессий может быть несколько — например,
ноутбук и телефон одновременно. Каждая сессия — случайный токен, который
браузер держит в ``HttpOnly``-cookie; на диск пишется только его хэш, поэтому
украденный ``storage/app_sessions.json`` не даёт войти.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass

from loguru import logger

from avito_monitor.config import load_env
from avito_monitor.paths import APP_SESSIONS_PATH, LEGACY_APP_SESSION_PATH

COOKIE_NAME = "signal_session"
DEFAULT_PASSWORD = "change_me"
DEFAULT_LOGIN = "admin"

_DEFAULT_TTL_DAYS = 30
_MAX_SESSIONS = 20
_lock = threading.RLock()


@dataclass(frozen=True, slots=True)
class Credentials:
    login: str
    password: str

    @property
    def is_default_password(self) -> bool:
        return self.password == DEFAULT_PASSWORD


def credentials() -> Credentials:
    """Логин и пароль из окружения."""
    load_env()
    return Credentials(
        login=os.environ.get("APP_LOGIN", DEFAULT_LOGIN).strip() or DEFAULT_LOGIN,
        password=os.environ.get("APP_PASSWORD", DEFAULT_PASSWORD),
    )


def session_ttl_seconds() -> int:
    """Срок жизни сессии. По умолчанию 30 дней, чтобы PWA не просила пароль."""
    raw = os.environ.get("SESSION_TTL_DAYS", "").strip()
    days = int(raw) if raw.isdigit() and int(raw) > 0 else _DEFAULT_TTL_DAYS
    return days * 86_400


def warn_about_weak_password() -> None:
    """Предупредить в лог, если пароль остался из примера конфига."""
    if not credentials().is_default_password:
        return
    logger.warning(
        "APP_PASSWORD не изменён (значение из .env.example). "
        "Смените пароль в .env — иначе доступ к интерфейсу открыт всем, "
        "кто знает адрес."
    )


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _secrets_equal(left: str, right: str) -> bool:
    """Сравнение за постоянное время, устойчивое к не-ASCII.

    ``secrets.compare_digest`` отказывается сравнивать строки с символами вне
    ASCII, поэтому сравниваем байты: иначе кириллица в пароле давала бы
    ``TypeError`` вместо честного «неверный пароль».
    """
    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _read_sessions() -> list[dict]:
    if not APP_SESSIONS_PATH.exists():
        return []
    try:
        data = json.loads(APP_SESSIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _write_sessions(sessions: list[dict]) -> None:
    APP_SESSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = APP_SESSIONS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(sessions, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(APP_SESSIONS_PATH)


def _alive(sessions: list[dict], now: float) -> list[dict]:
    return [item for item in sessions if float(item.get("expires_at") or 0) > now]


def is_authenticated(token: str | None) -> bool:
    """Действительна ли сессия с таким токеном."""
    if not token:
        return False
    wanted = _hash(token)
    now = time.time()
    with _lock:
        sessions = _alive(_read_sessions(), now)
        return any(_secrets_equal(str(item.get("token_hash") or ""), wanted) for item in sessions)


def auth_status(token: str | None) -> dict:
    """Состояние входа для веб-интерфейса."""
    authed = is_authenticated(token)
    return {
        "logged_in": authed,
        "username": credentials().login if authed else "",
        "active": authed,
    }


def login(username: str, password: str) -> str:
    """Проверить пару логин/пароль и выдать токен новой сессии.

    :raises ValueError: неверные данные.
    """
    expected = credentials()
    # Оба сравнения выполняются всегда, чтобы по времени ответа нельзя было
    # понять, угадан ли логин.
    user_ok = _secrets_equal((username or "").strip(), expected.login)
    password_ok = _secrets_equal(password or "", expected.password)
    if not (user_ok and password_ok):
        logger.warning(f"Неудачная попытка входа: логин «{(username or '').strip()[:40]}»")
        raise ValueError("Неверный логин или пароль")

    token = secrets.token_urlsafe(32)
    now = time.time()
    entry = {
        "token_hash": _hash(token),
        "created_at": now,
        "expires_at": now + session_ttl_seconds(),
    }
    with _lock:
        sessions = _alive(_read_sessions(), now)
        sessions.append(entry)
        # Старые сессии вытесняются, чтобы файл не рос без предела.
        _write_sessions(sessions[-_MAX_SESSIONS:])
    logger.info(f"Вход: {expected.login}")
    return token


def logout(token: str | None) -> None:
    """Завершить одну сессию; остальные устройства остаются в системе."""
    if not token:
        return
    wanted = _hash(token)
    now = time.time()
    with _lock:
        sessions = [
            item
            for item in _alive(_read_sessions(), now)
            if str(item.get("token_hash") or "") != wanted
        ]
        _write_sessions(sessions)
    logger.info("Выход из приложения")


def revoke_all_sessions() -> None:
    """Разлогинить все устройства (например, после смены пароля)."""
    with _lock:
        _write_sessions([])


def migrate_legacy_session() -> None:
    """Убрать файл входа старого формата.

    До версии 1.0 факт входа хранился одним флагом на весь сервер: если
    владелец залогинился, доступ получал любой, кто открыл адрес. Флаг больше
    не читается, но и оставлять его на диске незачем.
    """
    if not LEGACY_APP_SESSION_PATH.exists():
        return
    LEGACY_APP_SESSION_PATH.unlink(missing_ok=True)
    logger.info("Удалён app_login.json старого формата — войдите заново")
