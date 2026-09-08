"""Пути проекта в одном месте.

Все каталоги считаются от корня репозитория, а не от текущей рабочей
директории: иначе запуск ``python -m avito_monitor`` из другого каталога
создавал бы ``storage/`` и ``logs/`` не там, где их ждёт Docker-volume.
"""

from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent

DATA_DIR = PACKAGE_DIR / "data"
"""Справочники регионов, категорий и моделей — часть кода, а не данных."""

CONFIG_PATH = PROJECT_ROOT / "config.toml"
ENV_PATH = PROJECT_ROOT / ".env"
STATIC_DIR = PROJECT_ROOT / "static"

STORAGE_DIR = PROJECT_ROOT / "storage"
LOG_DIR = PROJECT_ROOT / "logs"
COOKIES_DIR = STORAGE_DIR / "cookies"

ADS_PATH = STORAGE_DIR / "ads.json"
APP_SESSIONS_PATH = STORAGE_DIR / "app_sessions.json"
AVITO_USER_SESSION_PATH = STORAGE_DIR / "avito_user.json"
LEGACY_COOKIES_PATH = STORAGE_DIR / "cookies.json"
LEGACY_APP_SESSION_PATH = STORAGE_DIR / "app_login.json"
LOCATION_CACHE_PATH = STORAGE_DIR / "spfa_regions.json"
POOL_PATH = STORAGE_DIR / "pool.json"
PUSH_SUBSCRIPTIONS_PATH = STORAGE_DIR / "push_subscriptions.json"
SEEN_PATH = STORAGE_DIR / "seen.json"
VAPID_PRIVATE_PATH = STORAGE_DIR / "vapid_private.pem"
VAPID_PUBLIC_PATH = STORAGE_DIR / "vapid_public.key"

COOKIE_LIFECYCLE_LOG = LOG_DIR / "cookie_lifecycle.log"


def ensure_runtime_dirs() -> None:
    """Создать каталоги, в которые приложение пишет при работе."""
    COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
