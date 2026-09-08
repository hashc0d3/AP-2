"""Сигнал — мониторинг новых объявлений Avito.

Пакет разделён по ответственности:

* :mod:`avito_monitor.config` — типизированные настройки (``config.toml`` + ``.env``);
* :mod:`avito_monitor.avito` — всё, что знает про форматы Avito (поиск, разбор
  объявлений, фильтры, номера телефонов);
* :mod:`avito_monitor.net` — транспорт: HTTP-клиенты, прокси, смена IP;
* :mod:`avito_monitor.cookies` — пул мобильных cookies и его обслуживание;
* :mod:`avito_monitor.monitor` — цикл опроса, темп и память о показанных ID;
* :mod:`avito_monitor.web` — веб-интерфейс, лента, push-уведомления.

Запуск: ``python -m avito_monitor``.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"
