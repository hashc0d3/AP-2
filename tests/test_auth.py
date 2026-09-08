"""Вход в веб-интерфейс и сессии."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from avito_monitor import auth


def test_login_returns_token(credentials: tuple[str, str]) -> None:
    login, password = credentials
    token = auth.login(login, password)
    assert token
    assert auth.is_authenticated(token) is True


@pytest.mark.parametrize(
    ("login", "password"),
    [
        ("tester", "неверный"),
        ("другой", "s3cret-pass"),
        ("", ""),
        ("tester", ""),
    ],
)
def test_wrong_credentials_are_rejected(
    credentials: tuple[str, str], login: str, password: str
) -> None:
    with pytest.raises(ValueError, match="Неверный логин или пароль"):
        auth.login(login, password)


def test_unknown_token_is_not_authenticated(credentials: tuple[str, str]) -> None:
    auth.login(*credentials)
    assert auth.is_authenticated("подделка") is False


@pytest.mark.parametrize("token", [None, ""])
def test_missing_token_is_not_authenticated(token: str | None) -> None:
    assert auth.is_authenticated(token) is False


def test_sessions_are_independent(credentials: tuple[str, str]) -> None:
    """Ноутбук и телефон входят отдельно; выход на одном не выкидывает второй."""
    laptop = auth.login(*credentials)
    phone = auth.login(*credentials)
    assert laptop != phone

    auth.logout(laptop)
    assert auth.is_authenticated(laptop) is False
    assert auth.is_authenticated(phone) is True


def test_revoke_all_ends_every_session(credentials: tuple[str, str]) -> None:
    tokens = [auth.login(*credentials) for _ in range(3)]
    auth.revoke_all_sessions()
    assert all(auth.is_authenticated(token) is False for token in tokens)


def test_expired_session_is_rejected(
    credentials: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SESSION_TTL_DAYS", "1")
    token = auth.login(*credentials)

    stored = json.loads(auth.APP_SESSIONS_PATH.read_text(encoding="utf-8"))
    stored[-1]["expires_at"] = time.time() - 1
    auth.APP_SESSIONS_PATH.write_text(json.dumps(stored), encoding="utf-8")

    assert auth.is_authenticated(token) is False


def test_token_is_not_stored_in_plain_text(credentials: tuple[str, str]) -> None:
    """Украденный файл сессий не должен давать доступ."""
    token = auth.login(*credentials)
    stored = auth.APP_SESSIONS_PATH.read_text(encoding="utf-8")
    assert token not in stored
    assert "token_hash" in stored


def test_old_sessions_are_evicted(credentials: tuple[str, str]) -> None:
    for _ in range(auth._MAX_SESSIONS + 5):
        auth.login(*credentials)
    stored = json.loads(auth.APP_SESSIONS_PATH.read_text(encoding="utf-8"))
    assert len(stored) == auth._MAX_SESSIONS


def test_auth_status_shape(credentials: tuple[str, str]) -> None:
    login, _ = credentials
    token = auth.login(*credentials)

    assert auth.auth_status(token) == {"logged_in": True, "username": login, "active": True}
    assert auth.auth_status(None) == {"logged_in": False, "username": "", "active": False}


def test_default_password_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PASSWORD", auth.DEFAULT_PASSWORD)
    assert auth.credentials().is_default_password is True

    monkeypatch.setenv("APP_PASSWORD", "свой-пароль")
    assert auth.credentials().is_default_password is False


def test_legacy_session_file_is_removed(isolated_storage: Path) -> None:
    """До 1.0 факт входа был одним флагом на весь сервер — файл больше не нужен."""
    auth.LEGACY_APP_SESSION_PATH.write_text(
        json.dumps({"logged_in": True, "username": "admin"}), encoding="utf-8"
    )
    auth.migrate_legacy_session()
    assert auth.LEGACY_APP_SESSION_PATH.exists() is False


def test_migration_is_safe_without_legacy_file() -> None:
    auth.migrate_legacy_session()
    assert auth.LEGACY_APP_SESSION_PATH.exists() is False


def test_corrupted_session_file_denies_access(credentials: tuple[str, str]) -> None:
    token = auth.login(*credentials)
    auth.APP_SESSIONS_PATH.write_text("{не json", encoding="utf-8")
    assert auth.is_authenticated(token) is False
