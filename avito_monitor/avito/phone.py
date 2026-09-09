"""Получение номера телефона из объявления.

Avito прячет номер за отдельным запросом и не показывает его гостям. Номер
приходит по-разному: поле ``phone``, ссылка ``tel:``, параметр
``number=%2B7…`` внутри ``result.action.uri``. Часть эндпоинтов просит
одноразовый ``phoneKey`` из карточки.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote, urlparse

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
_AUTH_STATUSES = frozenset(
    {"unauthorized", "unauthorised", "need-auth", "need_auth", "forbidden", "authenticate"}
)
_AUTH_MARKERS = (
    "authenticate",
    "unauthorized",
    "unauthorised",
    "need-auth",
    "не авторизован",
    "user_unauthorized",
)
_PHONE_KEY_NAMES = frozenset({"phonekey", "phone_key", "buyerphonekey", "pkey"})
_PHONE_FIELD_NAMES = frozenset({"phone", "phonenumber", "number", "uri", "value", "tel"})

_PHONE_KEY_PATTERNS = (
    r'"phoneKey"\s*:\s*"([^"]+)"',
    r'"phone_key"\s*:\s*"([^"]+)"',
    r'"key"\s*:\s*"([a-f0-9]{20,})"',
    r"phone\?key=([a-f0-9]{20,})",
)
_DIGITS = re.compile(r"\d+")


def _normalize_number(raw: str) -> str | None:
    """Свести строку Avito к ``+7…``. Мусор и короткие куски отбрасываем."""
    text = unquote(raw or "").strip()
    if not text:
        return None
    digits = "".join(_DIGITS.findall(text))
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    if len(digits) == 10:
        return "+7" + digits
    if 11 <= len(digits) <= 15:
        return "+" + digits
    return None


def _walk_for_phone(node: Any, field: str = "") -> str | None:
    if isinstance(node, str):
        lowered = field.lower()
        if (
            lowered in _PHONE_FIELD_NAMES
            or "tel:" in node.lower()
            or "number=" in node.lower()
        ):
            return _normalize_number(node)
        return None
    if isinstance(node, dict):
        for key, value in node.items():
            found = _walk_for_phone(value, str(key))
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _walk_for_phone(item, field)
            if found:
                return found
    return None


def extract_phone(payload: dict | str) -> str | None:
    """Найти номер в ответе Avito — в JSON или в HTML."""
    if isinstance(payload, dict):
        found = _walk_for_phone(payload)
        if found:
            return found
        text = json.dumps(payload, ensure_ascii=False)
    else:
        text = payload or ""
    for pattern in (
        r"number=%2B(\d+)",
        r"number=\+?(\d+)",
        r"tel:(\+?\d[\d\s\-()]{8,})",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            number = _normalize_number(match.group(1))
            if number:
                return number
    return None


def find_phone_key(payload: dict | str) -> str | None:
    """Одноразовый ключ, без которого часть эндпоинтов номер не отдаёт."""
    if isinstance(payload, dict):
        walked = _walk_for_key(payload)
        if walked:
            return walked
        text = json.dumps(payload, ensure_ascii=False)
    else:
        text = payload or ""
    for pattern in _PHONE_KEY_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _walk_for_key(node: Any, field: str = "") -> str | None:
    if isinstance(node, str) and field.lower() in _PHONE_KEY_NAMES and len(node) >= 16:
        return node
    if isinstance(node, dict):
        for key, value in node.items():
            found = _walk_for_key(value, str(key))
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _walk_for_key(item, field)
            if found:
                return found
    return None


def needs_auth(payload: Any) -> bool:
    """Просит ли Avito авторизоваться."""
    if isinstance(payload, dict):
        status = str(payload.get("status") or "").lower()
        if status in _AUTH_STATUSES:
            return True
        text = json.dumps(payload, ensure_ascii=False).lower()
    else:
        text = str(payload or "").lower()
    return any(marker in text for marker in _AUTH_MARKERS)


def _preview(payload: Any) -> str:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return text.replace("\n", " ")[:240]


def _avito_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    result = payload.get("result")
    if isinstance(result, dict):
        return str(result.get("message") or result.get("error") or "").strip()
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or "").strip()
    return str(payload.get("message") or "").strip()


def _get(
    client: curl_requests.Session,
    url: str,
    params: dict | None = None,
) -> tuple[int, dict | str | None]:
    """GET, который не бросает исключений: ``(код, тело)``, ``0`` — сеть."""
    parsed = urlparse(url)
    headers = {"referer": f"{parsed.scheme}://{parsed.netloc}/"}
    if parsed.hostname == "m.avito.ru":
        headers["x-requested-with"] = "XMLHttpRequest"
    try:
        response = client.get(
            url, params=params or {}, timeout=REQUEST_TIMEOUT, headers=headers
        )
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


def fetch_phone(
    client: curl_requests.Session,
    ad_id: str | int,
    phone_key: str | None = None,
) -> dict[str, Any]:
    """Запросить номер объявления.

    Возвращает ``{"ok": True, "phone": "+7…"}`` либо
    ``{"ok": False, "error": …, "code": …}``, где ``code`` — одно из
    ``bad_id``, ``blocked``, ``auth_required``, ``unavailable``.
    """
    ad_id = str(ad_id).strip()
    if not ad_id.isdigit():
        return {"ok": False, "error": "Некорректный ID объявления", "code": "bad_id"}

    client.headers.setdefault("accept", "application/json, text/plain, */*")
    known_key = (phone_key or "").strip() or None
    last_message = ""

    attempts: list[str | None] = []
    if known_key:
        attempts.append(known_key)
    attempts.append(None)

    for key in attempts:
        result = _request_phone(client, ad_id, key)
        if result is None:
            continue
        if result.get("ok"):
            return result
        last_message = str(result.get("error") or last_message)
        if result.get("code") in {"blocked", "auth_required"}:
            return result

    looked_up = _lookup_phone_key(client, ad_id)
    if looked_up and looked_up != known_key:
        result = _request_phone(client, ad_id, looked_up)
        if result is not None:
            if result.get("ok") or result.get("code") in {"blocked", "auth_required"}:
                return result
            last_message = str(result.get("error") or last_message)

    _, page = _get(client, CARD_URL.format(id=ad_id))
    if page:
        html_key = find_phone_key(page)
        if html_key and html_key not in {known_key, looked_up}:
            result = _request_phone(client, ad_id, html_key)
            if result is not None:
                if result.get("ok") or result.get("code") in {"blocked", "auth_required"}:
                    return result
                last_message = str(result.get("error") or last_message)

    logger.info(f"Номер ad={ad_id} не разобран: {last_message or 'пустой ответ Avito'}")
    return {
        "ok": False,
        "error": last_message or "Номер недоступен — скрыт продавцом или только сообщения",
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
    last_error: dict[str, Any] | None = None
    for template in PHONE_URLS:
        url = template.format(id=ad_id)
        status, payload = _get(client, url, params)
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
        message = _avito_message(payload)
        logger.debug(f"phone {url} http={status} {_preview(payload)}")
        if message:
            last_error = {"ok": False, "error": message, "code": "unavailable"}
    return last_error
