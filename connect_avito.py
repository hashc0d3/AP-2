"""CLI: вход в Avito через браузер (отдельный процесс)."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from avito_connect import _finish_connect, run_connect


def main() -> None:
    try:
        run_connect()
        _finish_connect(True)
    except Exception as err:
        logger.error(f"Avito connect: {err}")
        _finish_connect(False, str(err))
        raise SystemExit(1) from err


if __name__ == "__main__":
    Path("logs").mkdir(exist_ok=True)
    logger.add("logs/connect_avito.log", rotation="2 MB", retention="3 days")
    main()
