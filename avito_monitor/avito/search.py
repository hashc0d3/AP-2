"""Подготовка поискового запроса: ссылка веб-поиска и адрес JSON API.

Avito не даёт публичного API. Ссылку веб-поиска мы собираем сами.
Внутренний JSON API собираем в таком порядке:

1. знакомая категория и известный ``locationId`` — локально;
2. вставленная пользователем ссылка, из которой хватает региона и фильтра;
3. иначе один раз спрашиваем spfa.pro и кэшируем ответ.

После любого источника адрес приводим к выдаче 6 сентября:
``presentationType=serp``, ``sort=date``, частные объявления.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from loguru import logger

from avito_monitor import spfa
from avito_monitor.avito import catalog, iphone, iphone_params
from avito_monitor.avito.catalog import Category
from avito_monitor.avito.regions import Region, find_region, region_or_default

_cache_lock = threading.Lock()
_api_url_cache: dict[str, str] = {}


@dataclass(frozen=True, slots=True)
class SearchPlan:
    """Что именно будем искать — до обращения к API."""

    query: str
    region: Region
    category: Category | None
    web_url: str
    iphone_models: tuple[str, ...] | None = None

    def as_dict(self) -> dict:
        """Вид для веб-интерфейса."""
        return {
            "query": self.query,
            "region": self.region.as_dict(),
            "category": self.category.as_dict() if self.category else dict(catalog.NO_CATEGORY),
            "web_url": self.web_url,
            "iphone_models": list(self.iphone_models) if self.iphone_models is not None else None,
        }


def plan_search(
    query: str,
    region_slug: str,
    category_id: str = "",
    iphone_models: object = None,
) -> SearchPlan:
    """Собрать ссылку веб-поиска по параметрам из интерфейса.

    :raises ValueError: неизвестный регион или категория.
    """
    region = region_or_default(region_slug)
    category = catalog.find_category(category_id)
    text = (query or "").strip()
    if category.id == catalog.ALL_CATEGORY_ID and not text:
        raise ValueError("Укажите поисковый запрос")

    models: tuple[str, ...] | None = None
    if iphone_models is not None and category.id == catalog.IPHONE_CATEGORY_ID:
        models = iphone.normalize_models(iphone_models)

    web_url = catalog.build_web_url(text, region.slug, category.id)
    if models is not None:
        web_url, _ = iphone_params.append_model_params(web_url, models)

    return SearchPlan(
        query=text,
        region=region,
        category=category,
        web_url=web_url,
        iphone_models=models,
    )


def _normalize_pasted_url(web_url: str) -> str:
    link = (web_url or "").strip()
    if link and "://" not in link:
        link = f"https://{link.lstrip('/')}"
    return link


def plan_from_url(web_url: str) -> SearchPlan:
    """План для готовой ссылки Avito, вставленной пользователем."""
    link = _normalize_pasted_url(web_url)
    if not link:
        raise ValueError("Укажите ссылку Avito")
    slug = catalog.region_slug_from_web_url(link)
    try:
        region = find_region(slug)
    except ValueError:
        region = region_or_default("")
    return SearchPlan(
        query=link,
        region=region,
        category=catalog.category_from_web_url(link),
        web_url=catalog.with_date_sort(link),
    )


def resolve_api_url(
    web_url: str, *, region_slug: str = "", category_id: str = "", query: str = ""
) -> str:
    """Адрес JSON API для ссылки веб-поиска.

    :raises spfa.SpfaError: локально собрать не удалось и сервис не ответил.
    """
    if category_id:
        local = catalog.build_api_url(region_slug, category_id, query=query)
        if local:
            logger.info("API URL собран локально, без обращения к сервису")
            return _finish_api_url(local, web_url)
    from_web = catalog.api_url_from_web_url(web_url)
    if from_web:
        logger.info("API URL собран из ссылки пользователя, без обращения к сервису")
        return _finish_api_url(from_web, web_url)
    return _finish_api_url(_convert_via_service(web_url), web_url)


def _finish_api_url(api_url: str, web_url: str) -> str:
    """Поставить serp/sort=date и переписать коды моделей."""
    _ = web_url
    cleaned = catalog.normalize_items_api_url(api_url)
    return iphone_params.retarget_web_model_params(cleaned)


def _convert_via_service(web_url: str) -> str:
    """Спросить внешний сервис, закэшировав результат.

    Ключ кэша — ссылка без фильтра моделей: сервис всё равно не различает
    такие ссылки, а лимит запросов лучше поберечь.
    """
    key = iphone_params.strip_model_params(web_url)
    cached = _api_url_cache.get(key)
    if cached:
        return cached

    with _cache_lock:
        # Пока ждали блокировку, соседний запрос мог уже всё выяснить.
        cached = _api_url_cache.get(key)
        if cached:
            return cached
        api_url = spfa.convert_avito_url(key)
        _api_url_cache[key] = api_url

    catalog.remember_location_id(key, api_url)
    return api_url


def clear_api_url_cache() -> None:
    """Сбросить кэш соответствия «ссылка → адрес API» (нужно тестам)."""
    with _cache_lock:
        _api_url_cache.clear()
