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
    "https://m.avito.ru/api/1/items/{id}/phone/anonymous",
    "https://www.avito.ru/web/1/items/phone/{id}",
    "https://www.avito.ru/api/1/items/{id}/phone",
)
ITEM_URLS = (
    "https://m.avito.ru/api/15/items/{id}",
    "https://www.avito.ru/web/1/item/{id}",
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
_PHONE_FIELD_NAMES = frozenset(
    {
        "phone",
        "phonenumber",
        "number",
        "uri",
        "value",
        "tel",
        "msisdn",
        "mobile",
        "formatted",
        "display",
        "anonymousphone",
        "substitutionphone",
        "virtualphone",
        "tmpphone",
        "temporaryphone",
        "protectphone",
        "buyerphone",
    }
)
_PHONE_FIELD_HINT = re.compile(
    r"phone|tel|msisdn|mobile|номер|anonym",
    re.IGNORECASE,
)

_PHONE_KEY_PATTERNS = (
    r'"phoneKey"\s*:\s*"([^"]+)"',
    r'"phone_key"\s*:\s*"([^"]+)"',
    r'"key"\s*:\s*"([a-f0-9]{20,})"',
    r"phone\?key=([a-f0-9]{20,})",
)
_DIGITS = re.compile(r"\d+")
_URI_PHONE_RE = re.compile(
    r"(?:tel:|(?<![A-Za-z])(?:number|phone|msisdn)\]?=)(\+?[\d\s\-()]{8,})",
    re.IGNORECASE,
)
_FAKE_PHONE = re.compile(r"^(?:7|8)?(?:0{10}|1{10}|2{10}|3{10}|4{10}|5{10}|6{10}|7{10}|8{10}|9{10})$")


def _digits_to_e164(raw: str, *, allow_local: bool = False) -> str | None:
    digits = "".join(_DIGITS.findall(raw))
    if _FAKE_PHONE.match(digits):
        return None
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    if allow_local and len(digits) == 10:
        return "+7" + digits
    if 11 <= len(digits) <= 15 and digits[0] != "1":
        return "+" + digits
    return None


def _normalize_number(raw: str, *, field: str = "") -> str | None:
    """Свести строку Avito к ``+7…``. Мусор и короткие куски отбрасываем.

    В ``action.uri`` номер лежит в ``tel:`` / ``number=``. Нельзя собирать
    все цифры из URI: версия схемы ``://1/`` прилипает слева и даёт ``+1…``.
    Временный номер часто приходит полем ``phone`` / ``value``, не ``tel:``.
    """
    text = unquote(str(raw or "")).strip()
    if not text:
        return None
    match = _URI_PHONE_RE.search(text)
    if match:
        return _digits_to_e164(match.group(1), allow_local=True)
    if "://" in text:
        return None
    explicit = field.lower() in _PHONE_FIELD_NAMES or bool(_PHONE_FIELD_HINT.search(field))
    return _digits_to_e164(text, allow_local=explicit)


def _collect_phones(node: Any, field: str = "", found: list[str] | None = None) -> list[str]:
    """Все похожие на телефон значения из ответа — звоним по первому живому."""
    found = found if found is not None else []
    if isinstance(node, dict):
        for key, value in node.items():
            _collect_phones(value, str(key), found)
        return found
    if isinstance(node, list):
        for item in node:
            _collect_phones(item, field, found)
        return found
    if isinstance(node, bool) or node is None:
        return found
    if isinstance(node, (int, float)):
        number = _normalize_number(str(int(node)), field=field)
        if number and number not in found:
            found.append(number)
        return found
    if not isinstance(node, str):
        return found
    lowered = field.lower()
    if (
        lowered in _PHONE_FIELD_NAMES
        or _PHONE_FIELD_HINT.search(field)
        or "tel:" in node.lower()
        or "number=" in node.lower()
        or "phone=" in node.lower()
    ):
        number = _normalize_number(node, field=field)
        if number and number not in found:
            found.append(number)
    return found


def _pick_phone(candidates: list[str]) -> str | None:
    if not candidates:
        return None

    def score(phone: str) -> tuple[int, int, int]:
        digits = "".join(_DIGITS.findall(phone))
        pool = int(digits.startswith(("7958", "7495", "7499", "7800")))
        mobile = int(digits.startswith("79"))
        return (pool, mobile, len(digits))

    return max(candidates, key=score)


def extract_phone(payload: dict | str) -> str | None:
    """Найти любой звонибельный номер в ответе Avito — в JSON или в HTML."""
    if isinstance(payload, dict):
        picked = _pick_phone(_collect_phones(payload))
        if picked:
            return picked
        text = json.dumps(payload, ensure_ascii=False)
    else:
        text = payload or ""
    found: list[str] = []
    for pattern in (
        r"(?:number|phone|msisdn)\]?=%2B(\d+)",
        r"(?:number|phone|msisdn)\]?=\+?(\d+)",
        r"tel:(\+?\d[\d\s\-()]{8,})",
    ):
        for match in re.finditer(pattern, text, re.IGNORECASE):
            number = _normalize_number(match.group(1), field="tel")
            if number and number not in found:
                found.append(number)
    return _pick_phone(found)


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


def _payload_fields(payload: Any, prefix: str = "", out: list[str] | None = None) -> list[str]:
    """Имена полей ответа без значений — чтобы понять, что отдал Avito."""
    out = out if out is not None else []
    if len(out) >= 48:
        return out
    if isinstance(payload, dict):
        for key, value in payload.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, (dict, list)):
                out.append(path)
                _payload_fields(value, path, out)
            else:
                out.append(path)
        return out
    if isinstance(payload, list):
        for index, item in enumerate(payload[:6]):
            _payload_fields(item, f"{prefix}[{index}]", out)
    return out


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
    last_fields: list[str] = []

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
        last_fields = list(result.get("fields") or last_fields)
        if result.get("code") in {"blocked", "auth_required"}:
            return result

    looked_up = _lookup_phone_key(client, ad_id)
    if looked_up and looked_up != known_key:
        result = _request_phone(client, ad_id, looked_up)
        if result is not None:
            if result.get("ok") or result.get("code") in {"blocked", "auth_required"}:
                return result
            last_message = str(result.get("error") or last_message)
            last_fields = list(result.get("fields") or last_fields)

    _, page = _get(client, CARD_URL.format(id=ad_id))
    if page:
        html_key = find_phone_key(page)
        if html_key and html_key not in {known_key, looked_up}:
            result = _request_phone(client, ad_id, html_key)
            if result is not None:
                if result.get("ok") or result.get("code") in {"blocked", "auth_required"}:
                    return result
                last_message = str(result.get("error") or last_message)
                last_fields = list(result.get("fields") or last_fields)

    logger.info(
        f"Номер ad={ad_id} не разобран: {last_message or 'пустой ответ Avito'}"
        + (f"; поля={last_fields}" if last_fields else "")
    )
    error = last_message or "Номер недоступен — скрыт продавцом или только сообщения"
    result = {
        "ok": False,
        "error": error,
        "code": "unavailable",
    }
    if last_fields:
        result["fields"] = last_fields
    return result


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
    params = {"key": phone_key, "pkey": phone_key} if phone_key else {}
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
        if status in _BLOCKED_STATUSES and not isinstance(payload, dict):
            return {
                "ok": False,
                "error": "Avito ограничил запрос — обновите сессию или IP",
                "code": "blocked",
            }
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
        fields = _payload_fields(payload) if isinstance(payload, dict) else []
        logger.info(f"phone {url} http={status} fields={fields or '-'} {_preview(payload)}")
        last_error = {
            "ok": False,
            "error": message,
            "code": "unavailable",
        }
        if fields:
            last_error["fields"] = fields
    return last_error
