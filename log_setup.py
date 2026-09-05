"""Компактные ротируемые логи для продакшена."""

from __future__ import annotations

import time
from pathlib import Path

from loguru import logger

LOG_DIR = Path("logs")
_MAX_AGE_SEC = 86400  # 1 день


def prune_logs() -> None:
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
            pass


def setup_file_log(filename: str) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    prune_logs()
    logger.add(
        LOG_DIR / filename,
        rotation="500 KB",
        retention="1 day",
        compression="zip",
        level="INFO",
        enqueue=True,
    )
