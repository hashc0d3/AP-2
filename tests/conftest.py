"""Общие фикстуры тестов.

Главная задача — изоляция от диска. Модули приложения запоминают пути к
файлам в своих глобальных переменных при импорте, поэтому подменять
``avito_monitor.paths`` недостаточно: нужно переопределить константы в каждом
модуле, который их использует. Это делает :func:`isolated_storage` — она
включена автоматически, так что ни один тест не пишет в реальные
``storage/`` и ``logs/``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from avito_monitor import auth, logging_setup, search_session, spfa
from avito_monitor.avito import catalog, search, user_session
from avito_monitor.config import Settings
from avito_monitor.cookies import pool, service
from avito_monitor.monitor import seen
from avito_monitor.search_session import SearchSession
from avito_monitor.web import feed, push, routes, server
from avito_monitor.web.feed import AdFeed

# Модуль -> {имя константы: путь относительно временного каталога}.
_PATCHED_PATHS: dict[object, dict[str, str]] = {
    auth: {
        "APP_SESSIONS_PATH": "storage/app_sessions.json",
        "LEGACY_APP_SESSION_PATH": "storage/app_login.json",
    },
    catalog: {"LOCATION_CACHE_PATH": "storage/spfa_regions.json"},
    user_session: {"AVITO_USER_SESSION_PATH": "storage/avito_user.json"},
    pool: {
        "COOKIES_DIR": "storage/cookies",
        "POOL_PATH": "storage/pool.json",
        "LEGACY_COOKIES_PATH": "storage/cookies.json",
        "LOG_DIR": "logs",
        "COOKIE_LIFECYCLE_LOG": "logs/cookie_lifecycle.log",
    },
    service: {"PID_PATH": "storage/cookies/service.pid"},
    feed: {"ADS_PATH": "storage/ads.json"},
    push: {
        "PUSH_SUBSCRIPTIONS_PATH": "storage/push_subscriptions.json",
        "VAPID_PRIVATE_PATH": "storage/vapid_private.pem",
        "VAPID_PUBLIC_PATH": "storage/vapid_public.key",
    },
    seen: {"SEEN_PATH": "storage/seen.json"},
    logging_setup: {"LOG_DIR": "logs"},
}


@pytest.fixture(scope="module")
def module_monkeypatch() -> Iterator[pytest.MonkeyPatch]:
    """``monkeypatch`` со временем жизни модуля (штатный — только на тест)."""
    patch = pytest.MonkeyPatch()
    try:
        yield patch
    finally:
        patch.undo()


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Перевести все файлы приложения во временный каталог."""
    (tmp_path / "storage" / "cookies").mkdir(parents=True)
    (tmp_path / "logs").mkdir()
    for module, constants in _PATCHED_PATHS.items():
        for name, relative in constants.items():
            monkeypatch.setattr(module, name, tmp_path / relative)
    monkeypatch.setattr(service, "_owned_by_this_process", False)
    return tmp_path


@pytest.fixture(autouse=True)
def fresh_search_session(monkeypatch: pytest.MonkeyPatch) -> SearchSession:
    """Дать каждому тесту собственное состояние поиска.

    ``SESSION`` — синглтон, разделяемый веб-интерфейсом и циклом опроса;
    без подмены состояние протекало бы из теста в тест.
    """
    session = SearchSession()
    for module in (search_session, routes):
        monkeypatch.setattr(module, "SESSION", session)
    return session


@pytest.fixture(autouse=True)
def fresh_feed(monkeypatch: pytest.MonkeyPatch) -> AdFeed:
    """Дать каждому тесту пустую ленту (``FEED`` тоже синглтон)."""
    ads = AdFeed()
    for module in (feed, server):
        monkeypatch.setattr(module, "FEED", ads)
    return ads


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Запретить обращения к spfa.pro и очистить кэш адресов API.

    Локальная сборка адреса API продолжает работать — тесты проверяют именно
    её, а поход в сеть должен быть заметной ошибкой, а не тихой задержкой.
    """

    def _refuse(*args: object, **kwargs: object) -> str:
        raise spfa.SpfaError("обращение к сети запрещено в тестах")

    monkeypatch.setattr(spfa, "convert_avito_url", _refuse)
    search.clear_api_url_cache()


@pytest.fixture
def storage_dir(isolated_storage: Path) -> Path:
    return isolated_storage / "storage"


@pytest.fixture
def cookies_dir(storage_dir: Path) -> Path:
    return storage_dir / "cookies"


@pytest.fixture
def settings() -> Settings:
    """Настройки для тестов: без сети и без секретов."""
    return Settings(
        proxy_string="",
        proxy_change_url="",
        cookies_api_key="test-key",
        poll_interval=4,
        poll_interval_max=24,
        per_cookie_interval=12,
        cookie_pool_size=3,
    )


@pytest.fixture(autouse=True)
def reset_proxy_pool() -> Iterator[None]:
    """Тесты не должны видеть прокси, оставшиеся от соседа."""
    from avito_monitor.net.proxies import PROXY_POOL

    PROXY_POOL.configure(())
    yield
    PROXY_POOL.configure(())


@pytest.fixture
def credentials(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    """Известная пара логин/пароль для проверок входа."""
    monkeypatch.setenv("APP_LOGIN", "tester")
    monkeypatch.setenv("APP_PASSWORD", "s3cret-pass")
    return "tester", "s3cret-pass"
