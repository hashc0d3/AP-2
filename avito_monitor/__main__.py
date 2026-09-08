"""Точка входа приложения: ``python -m avito_monitor``."""

from __future__ import annotations

import sys

from loguru import logger


def main() -> int:
    from avito_monitor.monitor.loop import main as run_monitor

    try:
        run_monitor()
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем")
        return 0
    except (FileNotFoundError, RuntimeError, ValueError) as err:
        # Понятные ошибки настройки показываем без трассировки: чаще всего
        # это отсутствующий config.toml или занятый порт.
        logger.error(str(err))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
