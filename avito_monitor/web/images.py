"""Прокси фотографий Avito.

Avito отдаёт снимки только со своим ``Referer``, поэтому браузер не может
загрузить их напрямую — картинки идут через наш сервер.

Разрешён единственный источник: домены Avito. Без этой проверки эндпоинт
превратился бы в открытый прокси, через который можно ходить куда угодно от
имени сервера.
"""

from __future__ import annotations

from urllib.parse import urlparse

import requests

TIMEOUT = 20.0
CACHE_CONTROL = "public, max-age=86400"
DEFAULT_CONTENT_TYPE = "image/jpeg"

_ALLOWED_SUFFIXES = (".avito.st", ".avito.ru", "avito.st", "avito.ru")

_HEADERS = {
    "Referer": "https://www.avito.ru/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}


def is_allowed(url: str) -> bool:
    """Ведёт ли ссылка на домен Avito."""
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    return host.endswith(_ALLOWED_SUFFIXES) or "img.avito" in host


def fetch(url: str) -> tuple[bytes, str]:
    """Скачать картинку. Возвращает ``(содержимое, content-type)``.

    :raises ValueError: ссылка не на Avito.
    :raises requests.RequestException: Avito не отдал картинку.
    """
    if not is_allowed(url):
        raise ValueError("Разрешены только изображения с Avito")
    response = requests.get(url, headers=_HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return response.content, response.headers.get("Content-Type", DEFAULT_CONTENT_TYPE)
