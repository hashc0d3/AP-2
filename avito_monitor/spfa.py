"""Клиент сервиса spfa.pro.

Сервис закрывает три задачи: продаёт мобильные cookies, разблокирует их и
превращает ссылку Avito в адрес внутреннего JSON API. Раньше каждый вызов
жил в своём модуле со своей обработкой ошибок — здесь всё в одном месте,
с общим разбором ответа.
"""

from __future__ import annotations

from typing import Any

import requests
from loguru import logger

BASE_URL = "https://spfa.pro/api"

COOKIES_URL = f"{BASE_URL}/cookies/mobile/"
UNBLOCK_URL = f"{BASE_URL}/unblock/"
AVITO_URL = f"{BASE_URL}/avito-url/"
BALANCE_URL = f"{BASE_URL}/balance/"

_JSON_HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}

# Таймауты подобраны по поведению сервиса: покупка и разблокировка идут
# долго, конвертация ссылки и баланс отвечают быстро.
BUY_TIMEOUT = 40.0
UNBLOCK_TIMEOUT = 60.0
CONVERT_TIMEOUT = 20.0
BALANCE_TIMEOUT = 45.0

# 410 на разблокировке значит «набор старше 12 часов и больше не оживёт».
GONE = 410


class SpfaError(RuntimeError):
    """Сервис недоступен или ответил ошибкой."""


class SpfaCookieGone(SpfaError):
    """Набор cookies мёртв — его нужно выбросить из пула."""


def post(url: str, payload: dict[str, Any], timeout: float) -> requests.Response:
    """POST в spfa.pro без разбора тела — вызывающий сам решает, что с ним делать."""
    try:
        return requests.post(url, json=payload, headers=_JSON_HEADERS, timeout=timeout)
    except requests.Timeout as err:
        raise SpfaError(f"Сервис не ответил за {timeout:.0f} с — попробуйте позже") from err
    except requests.RequestException as err:
        raise SpfaError(f"Не удалось связаться с ресурсом: {err}") from err


def _payload(response: requests.Response) -> dict[str, Any]:
    """Разобрать успешный JSON-ответ, превратив любую беду в :class:`SpfaError`."""
    if response.status_code == 429:
        raise SpfaError("Лимит запросов к ресурсу — подождите минуту и повторите")
    try:
        data = response.json()
    except ValueError as err:
        raise SpfaError(f"Ресурс вернул не JSON: {response.text[:240]}") from err
    if not isinstance(data, dict):
        raise SpfaError("Ресурс вернул неожиданный ответ")
    if not response.ok:
        message = data.get("message") or data.get("error") or response.text[:240]
        raise SpfaError(f"Ошибка ресурса {response.status_code}: {message}")
    if not data.get("success"):
        raise SpfaError(str(data.get("message") or "Ресурс отказал без объяснения"))
    return data


def fetch_balance(api_key: str) -> float:
    """Остаток на счёте сервиса."""
    if not api_key:
        raise ValueError("Не настроен COOKIES_API_KEY в .env")
    data = _payload(post(BALANCE_URL, {"api_key": api_key}, BALANCE_TIMEOUT))
    balance = data.get("balance")
    if balance is None:
        raise SpfaError("Ресурс не вернул баланс")
    try:
        return float(balance)
    except (TypeError, ValueError) as err:
        raise SpfaError(f"Ресурс вернул нечисловой баланс: {balance!r}") from err


def convert_avito_url(web_url: str) -> str:
    """Превратить ссылку веб-поиска Avito в адрес внутреннего JSON API."""
    logger.info("Запрос spfa.pro для преобразования URL (до 20 с)…")
    response = post(AVITO_URL, {"url": web_url}, CONVERT_TIMEOUT)
    if response.status_code == 429:
        raise SpfaError("Лимит spfa.pro: 2 запроса в минуту — подождите и повторите")
    if response.status_code == 400:
        raise SpfaError("Сервис не принял ссылку Avito")
    api_url = _payload(response).get("api_url")
    if not isinstance(api_url, str) or not api_url.startswith("http"):
        raise SpfaError("Сервис не вернул api_url")
    return api_url


def buy_cookies(api_key: str, proxy_string: str) -> dict[str, Any]:
    """Купить новый набор мобильных cookies.

    Возвращает ``results`` из ответа сервиса: id, cookies, user_agent,
    fingerprint.
    """
    response = post(
        COOKIES_URL,
        {"api_key": api_key, "mobile": True, "proxy": proxy_string},
        BUY_TIMEOUT,
    )
    results = _payload(response).get("results") or {}
    if not isinstance(results, dict):
        raise SpfaError("Сервис вернул results не в виде объекта")
    return results


def unblock_cookies(cookie_id: Any, api_key: str, proxy_string: str) -> dict[str, Any]:
    """Попросить сервис переоформить набор cookies.

    :raises SpfaCookieGone: набор устарел и восстановлению не подлежит.
    """
    response = post(
        UNBLOCK_URL,
        {"id": cookie_id, "api_key": api_key, "proxy": proxy_string},
        UNBLOCK_TIMEOUT,
    )
    if response.status_code == GONE:
        raise SpfaCookieGone(f"Набор id={cookie_id} мёртв (410, старше 12 часов)")
    results = _payload(response).get("results") or {}
    if not isinstance(results, dict) or not results.get("cookies"):
        raise SpfaError(f"Сервис не вернул cookies для id={cookie_id}")
    return results
