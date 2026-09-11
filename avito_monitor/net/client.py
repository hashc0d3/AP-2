"""HTTP-клиент для внутреннего API Avito.

Avito отсекает запросы по отпечатку TLS, поэтому вместо ``requests`` берём
``curl_cffi``: он умеет притворяться настоящим браузером. Отпечаток приходит
вместе с набором cookies — так пара «cookies + user-agent» остаётся
согласованной.
"""

from __future__ import annotations

import time
from typing import Any

from curl_cffi import requests as curl_requests
from loguru import logger

Session = curl_requests.Session

DEFAULT_IMPERSONATE = "chrome131_android"
DEFAULT_ATTEMPTS = 2
RETRY_PAUSE = 0.5
CONNECT_TIMEOUT = 5.0
"""Сколько ждать установку соединения. Мёртвый туннель отваливается быстро,
а тело JSON может качаться до ``request_timeout``."""

RATE_LIMITED = 429
"""Avito ограничил IP: cookies целы, нужен другой адрес."""

COOKIE_BLOCKED = (403, 439)
"""Набор cookies сгорел — его нужно заменить."""

REJECTED_STATUSES = (*COOKIE_BLOCKED, RATE_LIMITED)


def call_timeout(timeout: float) -> float | tuple[float, float]:
    """Таймаут для curl: соединение рвём быстро, тело ждём до ``timeout``.

    Один общий лимит в 5 с обрывает уже качающийся JSON (в логе
    ``timed out … with 81001 bytes received``) и зря гоняет смену IP.
    """
    timeout = max(3.0, timeout)
    connect = min(CONNECT_TIMEOUT, timeout)
    if connect >= timeout:
        return timeout
    return (connect, timeout)


def build_client(session: dict, proxy_string: str = "") -> Session:
    """Собрать клиент по набору cookies из пула.

    :param session: набор из пула: ``cookies``, ``user_agent``, ``fingerprint``.
    :param proxy_string: ``user:pass@host:port`` или пустая строка.
    """
    fingerprint = session.get("fingerprint") or {}
    headers: dict[str, str] = dict(fingerprint.get("headers") or {})
    user_agent = session.get("user_agent") or headers.get("user-agent")
    if user_agent:
        headers["user-agent"] = user_agent
    headers.setdefault("referer", "https://www.avito.ru/")
    headers.setdefault("accept", "application/json, text/plain, */*")

    client = Session(impersonate=fingerprint.get("impersonate") or DEFAULT_IMPERSONATE)
    client.headers.update(headers)
    client.cookies.update(session.get("cookies") or {})
    if proxy_string:
        proxy_url = f"http://{proxy_string}"
        client.proxies = {"http": proxy_url, "https": proxy_url}
    return client


def fetch_page(
    client: Session,
    url: str,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    timeout: float = 10.0,
) -> tuple[int, dict[str, Any] | None]:
    """Запросить страницу выдачи.

    Возвращает ``(HTTP-код, разобранный JSON)``. ``None`` вместо JSON значит,
    что ответ пришёл, но пользы в нём нет: отказ Avito или HTML антибота —
    решение о смене IP или cookies принимает вызывающий по коду ответа.

    :raises curl_cffi.requests.exceptions.RequestException: сеть не ответила
        за все попытки.
    """
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = client.get(url, timeout=call_timeout(timeout))
            if response.status_code in REJECTED_STATUSES:
                return response.status_code, None
            response.raise_for_status()
            try:
                return response.status_code, response.json()
            except ValueError:
                logger.warning(
                    f"Ответ не JSON, status={response.status_code}, начало={response.text[:180]!r}"
                )
                return response.status_code, None
        except curl_requests.exceptions.RequestException as err:
            last_error = err
            logger.warning(f"Сбой сети ({attempt}/{attempts}): {err}")
            if attempt < attempts:
                time.sleep(RETRY_PAUSE)

    if last_error is None:
        raise ValueError(f"attempts должно быть больше нуля, получено {attempts}")
    raise last_error
