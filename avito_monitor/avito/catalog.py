"""Категории Avito и сборка ссылок поиска.

Категория описана один раз в ``avito_monitor/data/categories.json``: путь для
веб-поиска и параметры для внутреннего JSON API. Это позволяет собрать адрес
API самостоятельно и не тратить лимит внешнего сервиса на каждый запуск.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from loguru import logger

from avito_monitor.avito import regions
from avito_monitor.paths import DATA_DIR, LOCATION_CACHE_PATH

DATA_PATH = DATA_DIR / "categories.json"
ITEMS_API_URL = "https://www.avito.ru/web/1/js/items"
WEB_BASE_URL = "https://www.avito.ru"

IPHONE_CATEGORY_ID = "apple_phones"
"""Единственная категория, для которой работает фильтр моделей iPhone."""

ALL_CATEGORY_ID = "all"
"""Поиск по тексту во всех категориях: без пути и без categoryId."""

NO_CATEGORY = {"id": "none", "name": "Без категории"}
"""Заглушка для поиска по готовой ссылке — категорию там задаёт пользователь."""

# Avito отдаёт выдачу по числовому locationId. Соответствие slug -> id мы
# узнаём из ответов внешнего сервиса и запоминаем, чтобы больше не спрашивать.
_KNOWN_LOCATION_IDS = {
    "all": "621540",
    "moskva": "637640",
    "moskva_i_mo": "107620",
    "moskovskaya_oblast": "637680",
    "sankt-peterburg": "653240",
    "leningradskaya_oblast": "653240",
}

_LOCATION_ID_RE = re.compile(r"(?:^|[?&])locationId=(\d+)")
# Хеш фильтра в пути категории: apple-ASgBAgIC…
_FILTER_HASH_RE = re.compile(r"-(ASgB[\w-]+)$")


@dataclass(frozen=True, slots=True)
class Category:
    id: str
    name: str
    path: str
    """Путь веб-поиска Avito без региона."""
    api_category_id: str
    api_params: tuple[tuple[str, str], ...] = ()
    """Обязательные ``params[...]`` для JSON API этой категории."""
    extra: tuple[str, ...] = field(default=())
    """Дополнительные параметры веб-ссылки (например, готовый фильтр ``f=``)."""

    def as_dict(self) -> dict[str, str]:
        """Вид для API веб-интерфейса."""
        return {"id": self.id, "name": self.name}


ALL_CATEGORY = Category(
    id=ALL_CATEGORY_ID,
    name="Все категории",
    path="",
    api_category_id="",
)


@lru_cache(maxsize=1)
def _categories() -> tuple[Category, ...]:
    rows = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return tuple(
        Category(
            id=str(row["id"]),
            name=str(row["name"]),
            path=str(row["path"]),
            api_category_id=str(row["api_category_id"]),
            api_params=tuple((str(k), str(v)) for k, v in (row.get("api_params") or {}).items()),
            extra=tuple(str(item) for item in row.get("extra") or ()),
        )
        for row in rows
    )


@lru_cache(maxsize=1)
def _by_id() -> dict[str, Category]:
    return {item.id: item for item in _categories()}


def all_categories() -> tuple[Category, ...]:
    return _categories()


def list_categories() -> list[dict[str, str]]:
    """Категории для выпадающего списка в интерфейсе."""
    return [ALL_CATEGORY.as_dict(), *[item.as_dict() for item in _categories()]]


def default_category() -> Category:
    return _categories()[0]


def find_category(category_id: str) -> Category:
    """Категория по id.

    :raises ValueError: неизвестный id.
    """
    key = (category_id or "").strip().lower()
    if not key:
        return default_category()
    if key == ALL_CATEGORY_ID:
        return ALL_CATEGORY
    category = _by_id().get(key)
    if category is None:
        raise ValueError(f"Неизвестная категория: {category_id}")
    return category


# ── locationId: кэш на диске ────────────────────────────────────────────────


def _load_location_ids() -> dict[str, str]:
    known = dict(_KNOWN_LOCATION_IDS)
    if not LOCATION_CACHE_PATH.exists():
        return known
    try:
        data = json.loads(LOCATION_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return known
    if isinstance(data, dict):
        known.update({str(k): str(v) for k, v in data.items()})
    return known


def _save_location_ids(data: dict[str, str]) -> None:
    LOCATION_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCATION_CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def location_id_from_api_url(api_url: str) -> str | None:
    match = _LOCATION_ID_RE.search(api_url)
    return match.group(1) if match else None


def region_slug_from_web_url(web_url: str) -> str:
    """Первый сегмент пути веб-ссылки Avito — это регион."""
    path = urlsplit(web_url).path.strip("/")
    return path.split("/", 1)[0] if path else regions.default_region().slug


def remember_location_id(web_url: str, api_url: str) -> None:
    """Запомнить locationId, который вернул внешний сервис."""
    location_id = location_id_from_api_url(api_url)
    if not location_id:
        return
    slug = region_slug_from_web_url(web_url)
    cache = _load_location_ids()
    if cache.get(slug) == location_id:
        return
    cache[slug] = location_id
    _save_location_ids(cache)
    logger.debug(f"Запомнил locationId={location_id} для региона {slug}")


# ── Сборка ссылок ──────────────────────────────────────────────────────────


def build_web_url(query: str, region_slug: str, category_id: str = "") -> str:
    """Ссылка обычного веб-поиска Avito: свежие частные объявления."""
    category = find_category(category_id)
    path = "/".join(part for part in (regions.web_slug(region_slug), category.path) if part)
    params = [*category.extra, "localPriority=0", "s=104", "owner[]=private"]
    text = (query or "").strip()
    if text:
        params.insert(0, f"q={quote(text)}")
    return f"{WEB_BASE_URL}/{path}?{'&'.join(params)}"


def category_filter_hash(category: Category) -> str:
    """Хеш ``f`` из пути категории или extra веб-ссылки.

    Без него JSON API игнорирует ``owner[]=private`` и отдаёт смешанную SERP
    с магазинами.
    """
    for extra in category.extra:
        if extra.startswith("f="):
            return extra[2:]
    match = _FILTER_HASH_RE.search(category.path)
    return match.group(1) if match else ""


def build_api_url(region_slug: str, category_id: str, *, query: str = "") -> str | None:
    """Адрес JSON API, собранный локально.

    ``None`` — для этого региона ещё не известен ``locationId``, придётся
    спросить внешний сервис.
    """
    try:
        category = find_category(category_id)
    except ValueError:
        return None
    slug = (region_slug or "").strip() or regions.default_region().slug
    cache = _load_location_ids()
    location_id = cache.get(slug) or cache.get(regions.web_slug(slug))
    if not location_id:
        return None

    params: list[tuple[str, str]] = []
    text = (query or "").strip()
    if text:
        params.append(("q", text))
    if category.api_category_id:
        params.append(("categoryId", category.api_category_id))
    params.extend(
        (
            ("localPriority", "0"),
            ("locationId", location_id),
            ("owner[]", "private"),
            ("privateOnly", "1"),
            ("s", "104"),
            ("user", "1"),
        )
    )
    f_hash = category_filter_hash(category)
    if f_hash:
        params.append(("f", f_hash))
    params.extend((f"params[{key}]", value) for key, value in category.api_params)
    return f"{ITEMS_API_URL}?{urlencode(params)}"


def with_page(api_url: str, page: int) -> str:
    """Тот же адрес API, но для указанной страницы выдачи."""
    split = urlsplit(api_url)
    query = [
        (key, value)
        for key, value in parse_qsl(split.query, keep_blank_values=True)
        if key not in {"p", "page"}
    ]
    query.append(("page", str(page)))
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))
