"""Получение номера телефона из объявления.

Avito прячет номер за отдельным запросом и не показывает его гостям. Номер
приходит по-разному в зависимости от эндпоинта: то полем ``phone``, то
внутри ссылки ``tel:``, то в параметре ``number=%2B7…``, поэтому вытаскиваем
его несколькими способами.

Часть эндпоинтов требует одноразовый ключ ``phoneKey`` из карточки — сначала
забираем карточку, потом просим номер.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote

from curl_cffi import requests as curl_requests
from loguru import logger

PHONE_URLS = (
    "https://m.avito.ru/api/1/items/{id}/phone",
    "https://www.avito.ru/web/1/items/phone/{id}",
)
ITEM_URLS = (
    "https://www.avito.ru/web/1/main/items/{id}",
    "https://m.avito.ru/api/1/items/{id}",
)
CARD_URL = "https://www.avito.ru/items/{id}"

REQUEST_TIMEOUT = 8.0
_BLOCKED_STATUSES = {403, 429, 439}

_PHONE_PATTERNS = (
    r"number=%2B(\d+)",
    r'"phone"\s*:\s*"([^"]+)"',
    r"tel:(\+?\d+)",
)
_JSON_PHONE_PATTERNS = (
    r"number=%2B(\d+)",
    r'"phone"\s*:\s*"(\+?\d+)"',
    r'"uri"\s*:\s*"[^"]*number=%2B(\d+)',
)
_PHONE_KEY_PATTERNS = (
    r'"phoneKey"\s*:\s*"([^"]+)"',
    r'"key"\s*:\s*"([a-f0-9]{20,})"',
    r"phone\?key=([a-f0-9]{20,})",
)
_PHONE_FIELDS = frozenset({"phone", "phoneNumber", "number", "uri"})
_DIGITS = re.compile(r"\+?\d{10,15}")


def _with_plus(number: str) -> str:
    return number if number.startswith("+") else f"+{number}"


def _search_patterns(text: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _with_plus(match.group(1))
    return None


def _walk_for_phone(node: Any) -> str | None:
    """Обойти JSON и найти поле, похожее на номер."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _PHONE_FIELDS and isinstance(value, str):
                if "number=" in value:
                    candidate = unquote(value.split("number=")[-1].split("&")[0])
                    if _DIGITS.match(candidate):
                        return _with_plus(candidate)
                if _DIGITS.match(value):
                    return _with_plus(value)
            found = _walk_for_phone(value)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _walk_for_phone(item)
            if found:
                return found
    return None


def extract_phone(payload: dict | str) -> str | None:
    """Найти номер в ответе Avito — в JSON или в HTML."""
    if isinstance(payload, str):
        return _search_patterns(payload, _PHONE_PATTERNS)
    found = _search_patterns(json.dumps(payload, ensure_ascii=False), _JSON_PHONE_PATTERNS)
    return found or _walk_for_phone(payload)


def find_phone_key(payload: dict | str) -> str | None:
    """Одноразовый ключ, без которого часть эндпоинтов номер не отдаёт."""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    for pattern in _PHONE_KEY_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def needs_auth(payload: Any) -> bool:
    """Просит ли Avito авторизоваться."""
    if not isinstance(payload, dict):
        return False
    return "authenticate" in json.dumps(payload, ensure_ascii=False).lower()


def _get(
    client: curl_requests.Session,
    url: str,
    params: dict | None = None,
) -> tuple[int, dict | str | None]:
    """GET, который не бросает исключений: ``(код, тело)``, ``0`` — сеть."""
    try:
        response = client.get(url, params=params or {}, timeout=REQUEST_TIMEOUT)
    except curl_requests.exceptions.RequestException as err:
        logger.warning(f"GET {url} → {err}")
        return 0, None

    content_type = response.headers.get("content-type", "")
    if "json" in content_type or response.text.strip().startswith(("{", "[")):
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, response.text
    return response.status_code, response.text or None


def fetch_phone(client: curl_requests.Session, ad_id: str | int) -> dict[str, Any]:
    """Запросить номер объявления.

    Возвращает ``{"ok": True, "phone": "+7…"}`` либо
    ``{"ok": False, "error": …, "code": …}``, где ``code`` — одно из
    ``bad_id``, ``blocked``, ``auth_required``, ``unavailable``.
    """
    ad_id = str(ad_id).strip()
    if not ad_id.isdigit():
        return {"ok": False, "error": "Некорректный ID объявления", "code": "bad_id"}

    client.headers["referer"] = CARD_URL.format(id=ad_id)
    client.headers.setdefault("accept", "application/json, text/plain, */*")

    # Сначала сам номер: ключ часто не нужен, а поиск ключа — лишние секунды
    # на зависших URL, из-за которых браузер успевает оборвать запрос.
    result = _request_phone(client, ad_id, None)
    if result is not None:
        return result

    phone_key = _lookup_phone_key(client, ad_id)
    if phone_key:
        result = _request_phone(client, ad_id, phone_key)
        if result is not None:
            return result

    _, page = _get(client, CARD_URL.format(id=ad_id))
    if isinstance(page, str):
        phone_key = phone_key or find_phone_key(page)
        if phone_key:
            result = _request_phone(client, ad_id, phone_key)
            if result is not None:
                return result

    return {
        "ok": False,
        "error": "Номер недоступен — скрыт продавцом или только сообщения",
        "code": "unavailable",
    }


def _lookup_phone_key(client: curl_requests.Session, ad_id: str) -> str | None:
    for template in ITEM_URLS:
        _, payload = _get(client, template.format(id=ad_id))
        if payload:
            phone_key = find_phone_key(payload)
            if phone_key:
                return phone_key
    return None


def _request_phone(
    client: curl_requests.Session,
    ad_id: str,
    phone_key: str | None,
) -> dict[str, Any] | None:
    """Пройти по эндпоинтам номера. ``None`` — ни один не помог."""
    params = {"key": phone_key} if phone_key else {}
    for template in PHONE_URLS:
        status, payload = _get(client, template.format(id=ad_id), params)
        if not payload:
            if status in _BLOCKED_STATUSES:
                return {
                    "ok": False,
                    "error": "Avito ограничил запрос — обновите сессию или IP",
                    "code": "blocked",
                }
            continue
        if needs_auth(payload):
            return {
                "ok": False,
                "error": "Нужна авторизация в Avito — подключите аккаунт",
                "code": "auth_required",
            }
        phone = extract_phone(payload)
        if phone:
            return {"ok": True, "phone": phone}
    return None
