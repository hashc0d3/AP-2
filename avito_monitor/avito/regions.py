"""Регионы Avito: список для выбора в интерфейсе и часовые пояса.

Список лежит в ``avito_monitor/data/regions.json`` — данные, которые меняются
без правок кода. Регион без ключа ``tz`` считается московским.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from avito_monitor.paths import DATA_DIR

DATA_PATH = DATA_DIR / "regions.json"
DEFAULT_TIMEZONE = "Europe/Moscow"
SEARCH_LIMIT = 20

# В путях Avito slug региона иногда шире, чем в нашем списке.
_WEB_SLUG_OVERRIDES = {"moskva": "moskva_i_mo"}


@dataclass(frozen=True, slots=True)
class Region:
    slug: str
    name: str
    timezone: str = DEFAULT_TIMEZONE

    def as_dict(self) -> dict[str, str]:
        """Вид для API веб-интерфейса."""
        return {"slug": self.slug, "name": self.name}


@lru_cache(maxsize=1)
def _regions() -> tuple[Region, ...]:
    rows = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return tuple(
        Region(
            slug=str(row["slug"]),
            name=str(row["name"]),
            timezone=str(row.get("tz") or DEFAULT_TIMEZONE),
        )
        for row in rows
    )


@lru_cache(maxsize=1)
def _by_slug() -> dict[str, Region]:
    return {region.slug: region for region in _regions()}


def all_regions() -> tuple[Region, ...]:
    return _regions()


def default_region() -> Region:
    """Первый регион списка — «Вся Россия»."""
    return _regions()[0]


def find_region(slug: str) -> Region:
    """Регион по slug.

    :raises ValueError: неизвестный slug.
    """
    key = (slug or "").strip().lower()
    region = _by_slug().get(key)
    if region is None:
        raise ValueError(f"Неизвестный регион: {slug}")
    return region


def region_or_default(slug: str) -> Region:
    """Регион по slug; пустой slug — регион по умолчанию."""
    return find_region(slug) if (slug or "").strip() else default_region()


def region_timezone(slug: str) -> str:
    """Часовой пояс региона (IANA); неизвестный регион — Москва."""
    region = _by_slug().get((slug or "").strip().lower())
    return region.timezone if region else DEFAULT_TIMEZONE


def web_slug(slug: str) -> str:
    """Slug для пути веб-поиска Avito."""
    key = (slug or "").strip().strip("/") or default_region().slug
    return _WEB_SLUG_OVERRIDES.get(key, key)


def _normalized(text: str) -> str:
    return text.lower().replace("ё", "е")


def search_regions(query: str, limit: int = SEARCH_LIMIT) -> list[dict[str, str]]:
    """Подсказки для поля выбора региона.

    Совпадение с начала названия важнее вхождения внутри, а вхождение в
    название — важнее совпадения по slug.
    """
    needle = _normalized((query or "").strip())
    if not needle:
        return [region.as_dict() for region in _regions()[:limit]]

    ranked: list[tuple[int, str, Region]] = []
    for region in _regions():
        name = _normalized(region.name)
        if name.startswith(needle):
            rank = 0
        elif needle in name:
            rank = 1
        elif needle in region.slug.lower():
            rank = 2
        else:
            continue
        ranked.append((rank, name, region))
    ranked.sort(key=lambda row: (row[0], row[1]))
    return [region.as_dict() for _, _, region in ranked[:limit]]
