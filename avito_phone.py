"""Запрос номера объявления Avito через HTTP-сессию (curl_cffi)."""

from __future__ import annotations

import json
import re
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


def extract_phone(payload: dict | str) -> str | None:
    if isinstance(payload, str):
        for pattern in (
            r"number=%2B(\d+)",
            r'"phone"\s*:\s*"([^"]+)"',
            r"tel:(\+?\d+)",
        ):
            m = re.search(pattern, payload)
            if m:
                num = m.group(1)
                return num if num.startswith("+") else "+" + num
        return None

    text = json.dumps(payload, ensure_ascii=False)
    for pattern in (
        r"number=%2B(\d+)",
        r'"phone"\s*:\s*"(\+?\d+)"',
        r'"uri"\s*:\s*"[^"]*number=%2B(\d+)',
    ):
        m = re.search(pattern, text)
        if m:
            num = m.group(1)
            return num if num.startswith("+") else "+" + num

    def walk(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in {"phone", "phoneNumber", "number", "uri"} and isinstance(value, str):
                    if "number=" in value:
                        part = unquote(value.split("number=")[-1].split("&")[0])
                        if re.match(r"\+?\d{10,15}", part):
                            return part if part.startswith("+") else "+" + part
                    if re.match(r"\+?\d{10,15}", value):
                        return value if value.startswith("+") else "+" + value
                found = walk(value)
                if found:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = walk(item)
                if found:
                    return found
        return None

    return walk(payload)


def find_phone_key(payload) -> str | None:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    for pattern in (
        r'"phoneKey"\s*:\s*"([^"]+)"',
        r'"key"\s*:\s*"([a-f0-9]{20,})"',
        r"phone\?key=([a-f0-9]{20,})",
    ):
        m = re.search(pattern, text, re.I)
        if m:
            return m.group(1)
    return None


def needs_auth(payload) -> bool:
    if not isinstance(payload, dict):
        return False
    text = json.dumps(payload, ensure_ascii=False)
    return "authenticate" in text.lower()


def try_get(
    client: curl_requests.Session,
    url: str,
    params: dict | None = None,
) -> tuple[int, dict | str | None]:
    try:
        response = client.get(url, params=params or {}, timeout=30)
    except curl_requests.exceptions.RequestException as err:
        logger.warning(f"GET {url} → {err}")
        return 0, None
    content_type = response.headers.get("content-type", "")
    if "json" in content_type or response.text.strip().startswith(("{", "[")):
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, response.text
    return response.status_code, response.text if response.text else None


def fetch_phone(client: curl_requests.Session, ad_id: str | int) -> dict:
    """Возвращает {ok, phone?, error?, code?}."""
    ad_id = str(ad_id).strip()
    if not ad_id.isdigit():
        return {"ok": False, "error": "Некорректный ID объявления", "code": "bad_id"}

    phone_key = None
    referer = f"https://www.avito.ru/items/{ad_id}"
    client.headers["referer"] = referer
    client.headers.setdefault("accept", "application/json, text/plain, */*")

    for tpl in ITEM_URLS:
        url = tpl.format(id=ad_id)
        status, payload = try_get(client, url)
        if payload:
            phone_key = find_phone_key(payload)
            if phone_key:
                break

    for tpl in PHONE_URLS:
        url = tpl.format(id=ad_id)
        params = {"key": phone_key} if phone_key else {}
        status, payload = try_get(client, url, params)
        if not payload:
            if status in {403, 429, 439}:
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

    card_url = f"https://www.avito.ru/items/{ad_id}"
    status, payload = try_get(client, card_url)
    if isinstance(payload, str):
        phone_key = phone_key or find_phone_key(payload)
        if phone_key:
            for tpl in PHONE_URLS:
                url = tpl.format(id=ad_id)
                status, payload = try_get(client, url, {"key": phone_key})
                phone = extract_phone(payload) if payload else None
                if phone:
                    return {"ok": True, "phone": phone}

    return {
        "ok": False,
        "error": "Номер недоступен — скрыт продавцом или только сообщения",
        "code": "unavailable",
    }
