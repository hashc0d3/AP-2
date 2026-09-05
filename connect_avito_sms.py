"""CLI: вход в Avito по телефону + SMS (отдельный процесс)."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from avito_login import BrowserClosedError, finish_login, run_sms_login

try:
    from avito_connect import BrowserClosedError as _BrowserClosedError
except ImportError:
    _BrowserClosedError = BrowserClosedError  # type: ignore[misc, assignment]


def main() -> None:
    try:
        run_sms_login()
        finish_login(True)
    except Exception as err:
        logger.error(f"Avito SMS login: {err}")
        message = str(err)
        if isinstance(err, _BrowserClosedError):
            message = "Окно закрыто до входа — запросите код снова"
        finish_login(False, message)
        raise SystemExit(1) from err


if __name__ == "__main__":
    Path("logs").mkdir(exist_ok=True)
    logger.add("logs/connect_avito_sms.log", rotation="2 MB", retention="3 days")
    main()
