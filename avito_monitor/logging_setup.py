"""Настройка логов: компактные ротируемые файлы рядом с консолью."""

from __future__ import annotations

import time

from loguru import logger

from avito_monitor.paths import LOG_DIR

_MAX_AGE_SEC = 86_400  # сутки
_ROTATION = "500 KB"
_RETENTION = "1 day"


def prune_logs() -> None:
    """Удалить файлы логов старше суток.

    Loguru чистит только то, что создал сам, — оставшиеся с прошлых запусков
    файлы (например, ``cookie_lifecycle.log``) убираем руками.
    """
    if not LOG_DIR.exists():
        return
    cutoff = time.time() - _MAX_AGE_SEC
    for path in LOG_DIR.glob("*"):
        if not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            # Файл занят другим процессом — не повод падать на старте.
            continue


def setup_file_log(filename: str) -> None:
    """Добавить файловый сток логов уровня INFO."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    prune_logs()
    logger.add(
        LOG_DIR / filename,
        rotation=_ROTATION,
        retention=_RETENTION,
        compression="zip",
        level="INFO",
        enqueue=True,
    )
