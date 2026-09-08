"""Разбор объявлений из ответа внутреннего API Avito.

Avito не документирует этот формат и меняет его без предупреждения: одно и то
же поле встречается под разными именами, а часть данных спрятана в блоке
``iva`` — списке «шагов» карточки. Поэтому все функции здесь читают JSON
защитно: неизвестная структура даёт пустое значение, а не исключение.

Наружу модуль отдаёт :func:`serialize_ad` — плоский словарь, который уходит
в веб-интерфейс.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

MAX_IMAGES = 12
"""Столько фотографий показываем в карточке."""

NO_TITLE = "без названия"
NO_PRICE = "—"

# Признак «звонок доступен»: у Avito десяток вариантов имени этого флага.
_CALL_KEYS = (
    "canCall",
    "can_call",
    "hasPhone",
    "has_phone",
    "isPhoneVisible",
    "phoneAvailable",
    "isAvailableForCalls",
    "callAvailable",
)
_MESSAGE_KEYS = (
    "canWrite",
    "can_write",
    "canMessage",
    "hasMessenger",
    "has_messenger",
    "messengerAvailable",
    "isAvailableForMessages",
    "messageAvailable",
    "chatAvailable",
)
_HIDDEN_PHONE_KEYS = ("isPhoneHidden", "phoneHidden", "isPhoneDisabled")

# Блоки, в которых встречается имя или профиль продавца.
_SELLER_NAME_KEYS = ("sellerName", "userName", "shopName", "companyName")
_SELLER_BLOB_KEYS = ("user", "seller", "shop", "profile")
_SELLER_TEXT_KEYS = ("title", "name", "text", "value", "link", "slug")
_COMPANY_URL_MARKERS = ("/brands/", "/shop/", "/company/")

_PROMOTED_TITLE = "Продвинуто"
_AVITO_BASE = "https://www.avito.ru"


def extract_items(payload: Any) -> list[dict]:
    """Список объявлений из ответа API.

    Список ``items`` лежит то в корне, то в ``catalog``, то в ``result`` —
    перебираем известные места и берём первое подходящее.
    """
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    candidates = (
        payload.get("catalog"),
        result.get("catalog") if isinstance(result, dict) else None,
        result,
        payload,
    )
    for candidate in candidates:
        if isinstance(candidate, dict) and isinstance(candidate.get("items"), list):
            return [
                item for item in candidate["items"] if isinstance(item, dict) and item.get("id")
            ]
    return []


def item_id(item: dict) -> int | None:
    """Числовой ID объявления или ``None``, если его нет."""
    try:
        return int(item["id"])
    except (KeyError, TypeError, ValueError):
        return None


# ── Время публикации ───────────────────────────────────────────────────────


def published_at(item: dict) -> datetime | None:
    """Момент публикации в UTC. Avito отдаёт его в миллисекундах."""
    raw = item.get("sortTimeStamp")
    if not raw:
        return None
    try:
        return datetime.fromtimestamp(int(raw) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def age_seconds(item: dict) -> int | None:
    """Сколько секунд назад опубликовано объявление."""
    published = published_at(item)
    if published is None:
        return None
    return int((datetime.now(UTC) - published).total_seconds())


def format_age(seconds: int | None) -> str:
    """«42 сек назад», «7 мин назад», «3 ч назад»."""
    if seconds is None:
        return ""
    if seconds < 60:
        return f"{seconds} сек назад"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} мин назад"
    return f"{minutes // 60} ч назад"


def format_published(ts: int, tz_name: str) -> str:
    """Время публикации в часовом поясе региона плюс возраст объявления."""
    added = datetime.fromtimestamp(ts, tz=UTC)
    seconds = max(0, int((datetime.now(UTC) - added).total_seconds()))
    local = added.astimezone(ZoneInfo(tz_name))
    age = format_age(seconds)
    if age:
        return f"{local.strftime('%H:%M:%S')} · {age}"
    return local.strftime("%d.%m.%Y %H:%M")


# ── Продвижение ────────────────────────────────────────────────────────────


def is_promoted(item: dict) -> bool:
    """Платное продвижение: такие объявления всплывают в выдаче повторно."""
    steps = (item.get("iva") or {}).get("DateInfoStep") or []
    for step in steps:
        payload = (step or {}).get("payload") or {}
        for vas in payload.get("vas") or []:
            if (vas or {}).get("title") == _PROMOTED_TITLE:
                return True
    return False


# ── Продавец ───────────────────────────────────────────────────────────────


def _add_text(out: list[str], value: Any) -> None:
    if isinstance(value, str) and value.strip():
        out.append(value.strip())


def _texts_from_blob(out: list[str], blob: Any, keys: tuple[str, ...]) -> None:
    if not isinstance(blob, dict):
        return
    for key in keys:
        _add_text(out, blob.get(key))


def _texts_from_badge(out: list[str], badge: Any) -> None:
    if isinstance(badge, dict):
        _add_text(out, badge.get("title") or badge.get("name"))
    elif isinstance(badge, str):
        _add_text(out, badge)
    elif isinstance(badge, list):
        for entry in badge:
            if isinstance(entry, dict):
                _add_text(out, entry.get("title") or entry.get("name"))
            else:
                _add_text(out, entry)


def seller_texts(item: dict) -> list[str]:
    """Всё, что похоже на имя, ссылку или бейдж продавца.

    Используется чёрным списком: пользователь вводит часть имени, и мы ищем
    её по всем найденным строкам сразу.
    """
    found: list[str] = []
    for key in _SELLER_NAME_KEYS:
        _add_text(found, item.get(key))
    _texts_from_blob(found, item.get("userLogo"), ("link", "slug"))

    for blob in (item.get(key) for key in _SELLER_BLOB_KEYS):
        if not isinstance(blob, dict):
            continue
        _texts_from_blob(found, blob, _SELLER_TEXT_KEYS)
        _texts_from_blob(found, blob.get("profile"), ("title", "name", "link", "slug"))

    iva = item.get("iva")
    if not isinstance(iva, dict):
        return found

    for name, steps in iva.items():
        if not any(part in str(name).lower() for part in ("user", "seller", "shop", "profile")):
            continue
        for step in steps if isinstance(steps, list) else [steps]:
            if not isinstance(step, dict):
                continue
            payload = step.get("payload") if isinstance(step.get("payload"), dict) else step
            _texts_from_blob(found, payload, ("title", "name", "text", "link", "slug"))
            profile = payload.get("profile") or payload.get("user") or {}
            if not isinstance(profile, dict):
                continue
            _texts_from_blob(found, profile, ("title", "name", "text", "link", "slug"))
            _texts_from_badge(
                found,
                profile.get("badge") or profile.get("badges") or payload.get("badge"),
            )
    return found


def seller_name(item: dict) -> str:
    """Имя продавца — первая строка, которая не выглядит ссылкой."""
    texts = seller_texts(item)
    for text in texts:
        if "://" not in text and not text.startswith("/"):
            return text
    return texts[0] if texts else ""


def seller_profile_links(item: dict) -> list[str]:
    """Ссылки на профиль продавца — по ним видно, частник это или магазин."""
    links: list[str] = []
    iva = item.get("iva")
    if isinstance(iva, dict):
        steps = iva.get("UserInfoStep") or []
        for step in steps if isinstance(steps, list) else [steps]:
            if not isinstance(step, dict):
                continue
            payload = step.get("payload") if isinstance(step.get("payload"), dict) else {}
            profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
            link = str(profile.get("link") or "").strip()
            if link:
                links.append(link)
    links.extend(text for text in seller_texts(item) if text.startswith("/") or "://" in text)
    return links


def is_company_seller(item: dict) -> bool:
    """Магазин или бренд, а не частное лицо."""
    if item.get("shopId") or item.get("shop_id"):
        return True
    return any(
        marker in link.lower()
        for link in seller_profile_links(item)
        for marker in _COMPANY_URL_MARKERS
    )


def is_private_seller(item: dict) -> bool:
    """Частник: профиль лежит по ``/user/…`` и признаков магазина нет."""
    if is_company_seller(item):
        return False
    return any("/user/" in link.lower() for link in seller_profile_links(item))


# ── Прочие поля карточки ───────────────────────────────────────────────────


def ad_address(item: dict) -> str:
    """Адрес объявления: сначала точный, потом район, потом город."""
    geo = item.get("geo")
    if isinstance(geo, dict):
        for key in ("formattedAddress", "address", "city"):
            value = geo.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        names = [
            (ref.get("content") or "").strip()
            for ref in geo.get("geoReferences") or []
            if isinstance(ref, dict) and (ref.get("content") or "").strip()
        ]
        if names:
            return ", ".join(names)

    location = item.get("location")
    if isinstance(location, dict):
        name = (location.get("name") or location.get("title") or "").strip()
        if name:
            return name
    if isinstance(location, str) and location.strip():
        return location.strip()
    return ""


def ad_url(item: dict) -> str:
    """Ссылка на карточку объявления."""
    path = (item.get("urlPath") or "").split("?", 1)[0]
    if path:
        return f"{_AVITO_BASE}{path}"
    ad_id = item.get("id")
    return f"{_AVITO_BASE}/{ad_id}" if ad_id else ""


def ad_price(item: dict) -> str:
    """Цена в том виде, в котором её показывает Avito."""
    detailed = item.get("priceDetailed") or {}
    text = detailed.get("string")
    if isinstance(text, str) and text.strip():
        return text.strip()
    value = detailed.get("value")
    return str(value) if value else NO_PRICE


def _collect_images(value: Any, out: list[str]) -> None:
    """Собрать ссылки на фото, выбирая для каждого снимка версию побольше.

    Ключи галереи выглядят как ``640x480`` — по ним и оцениваем размер.
    """
    if isinstance(value, str):
        if value.startswith("http") and value not in out:
            out.append(value)
        return

    if isinstance(value, list):
        for item in value:
            _collect_images(item, out)
        return

    if not isinstance(value, dict):
        return

    sized: list[tuple[int, str]] = []
    for key, url in value.items():
        if not (isinstance(url, str) and url.startswith("http")):
            continue
        sized.append((_pixel_area(str(key)), url))
    if sized:
        sized.sort(reverse=True)
        _collect_images(sized[0][1], out)
        return
    for nested in value.values():
        _collect_images(nested, out)


def _pixel_area(key: str) -> int:
    if "x" not in key:
        return 0
    width, _, height = key.partition("x")
    try:
        return int(width) * int(height.split("/")[0])
    except ValueError:
        return 0


def ad_images(item: dict) -> list[str]:
    """Ссылки на фотографии объявления, не больше :data:`MAX_IMAGES`."""
    images: list[str] = []
    _collect_images(item.get("gallery"), images)
    _collect_images(item.get("images"), images)
    return images[:MAX_IMAGES]


def _as_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _first_bool(blob: dict, keys: tuple[str, ...]) -> bool | None:
    for key in keys:
        if key in blob:
            found = _as_bool(blob.get(key))
            if found is not None:
                return found
    return None


def _iva_has_action(item: dict, *needles: str) -> bool:
    """Есть ли в блоках карточки кнопка с таким действием.

    Последняя попытка понять доступность связи, когда явных флагов нет:
    обходим ``iva`` целиком и ищем подходящий заголовок.
    """
    iva = item.get("iva")
    if not isinstance(iva, dict):
        return False
    wanted = tuple(needle.lower() for needle in needles)
    stack: list[Any] = list(iva.values())
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            title = str(
                current.get("title") or current.get("type") or current.get("id") or ""
            ).lower()
            if any(word in title for word in wanted):
                return True
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return False


def contact_flags(item: dict) -> tuple[bool, bool]:
    """``(доступен звонок, доступны сообщения)``."""
    blobs = [item]
    blobs.extend(
        blob
        for blob in (item.get(key) for key in ("contacts", "contact", "seller", "user"))
        if isinstance(blob, dict)
    )

    can_call: bool | None = None
    can_message: bool | None = None
    for blob in blobs:
        if can_call is None:
            hidden = _first_bool(blob, _HIDDEN_PHONE_KEYS)
            if hidden is not None:
                can_call = not hidden
        if can_call is None:
            can_call = _first_bool(blob, _CALL_KEYS)
        if can_call is None:
            can_call = _as_bool(blob.get("phone"))

        if can_message is None:
            can_message = _first_bool(blob, _MESSAGE_KEYS)
        if can_message is None:
            for key in ("messenger", "message", "chat"):
                can_message = _as_bool(blob.get(key))
                if can_message is not None:
                    break

    if can_call is None:
        can_call = _iva_has_action(item, "call", "phone", "позвон")
    if can_message is None:
        can_message = _iva_has_action(item, "message", "messenger", "write", "chat", "написа")
    return bool(can_call), bool(can_message)


def ad_description(item: dict) -> str:
    """Текст объявления: из корня или из блока описания."""
    text = item.get("description")
    if isinstance(text, str) and text.strip():
        return text.strip()

    iva = item.get("iva")
    if not isinstance(iva, dict):
        return ""
    steps = iva.get("DescriptionStep") or []
    for step in steps if isinstance(steps, list) else [steps]:
        if not isinstance(step, dict):
            continue
        payload = step.get("payload") if isinstance(step.get("payload"), dict) else {}
        description = payload.get("description")
        if isinstance(description, str) and description.strip():
            return description.strip()
    return ""


def serialize_ad(item: dict, *, tz_name: str) -> dict:
    """Объявление в виде, который отдаётся веб-интерфейсу.

    Набор ключей соответствует типу ``Ad`` во фронтенде
    (``web/src/types.ts``) — менять его нужно с двух сторон.
    """
    published = published_at(item)
    added_ts = int(published.timestamp()) if published else int(time.time())
    can_call, can_message = contact_flags(item)
    return {
        "id": item.get("id"),
        "title": item.get("title") or NO_TITLE,
        "price": ad_price(item),
        "address": ad_address(item),
        "url": ad_url(item),
        "images": ad_images(item),
        "can_call": can_call,
        "can_message": can_message,
        "seller": seller_name(item),
        "published": format_published(added_ts, tz_name),
        "ts": added_ts,
        "description": ad_description(item),
    }
