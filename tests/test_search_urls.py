"""Сборка ссылок Avito: веб-поиск, JSON API и фильтр моделей iPhone."""

from __future__ import annotations

import pytest

from avito_monitor.avito import catalog, iphone, iphone_params
from avito_monitor.avito.search import plan_from_url, plan_search, resolve_api_url


def _web_url_with_models(models: list[str] | None, region: str = "moskva") -> str:
    return plan_search("", region, catalog.IPHONE_CATEGORY_ID, models).web_url


# ── Веб-ссылки ─────────────────────────────────────────────────────────────


def test_web_url_uses_region_and_category_path() -> None:
    url = catalog.build_web_url("", "kazan", "game_consoles")
    assert url.startswith("https://www.avito.ru/kazan/")
    assert "igrovye_pristavki/igrovye_pristavki-ASgBAgICAkSSAsoJ9M0UmsqPAw" in url
    assert "localPriority=0" in url
    assert "s=104" in url
    assert "owner[]=private" not in url


def test_moscow_city_and_moscow_area_are_different_urls() -> None:
    """Москва и «Москва и МО» на Avito — разные регионы, разные пути."""
    city = catalog.build_web_url("", "moskva", "tablets")
    area = catalog.build_web_url("", "moskva_i_mo", "tablets")
    assert "https://www.avito.ru/moskva/" in city
    assert "https://www.avito.ru/moskva_i_mo/" in area
    assert city != area


def test_web_url_keeps_category_extra_params() -> None:
    url = catalog.build_web_url("", "moskva", "tablets")
    assert "planshety-ASgBAgICAUSYAoZO" in url
    assert "f=ASgBAgICAkSYAoZOwPgO~qagDw" in url


def test_web_url_encodes_query() -> None:
    url = catalog.build_web_url("iphone 13 про", "all", catalog.IPHONE_CATEGORY_ID)
    assert "q=iphone%2013%20" in url


def test_unknown_category_is_rejected() -> None:
    with pytest.raises(ValueError, match="Неизвестная категория"):
        catalog.find_category("нет такой")


def test_all_categories_web_url_has_region_date_and_private() -> None:
    """Без категории остаётся регион и сортировка по дате."""
    url = catalog.build_web_url("iphone 15", "kazan", catalog.ALL_CATEGORY_ID)
    assert url.startswith("https://www.avito.ru/kazan?")
    assert "/telefony" not in url
    assert "q=iphone%2015" in url
    assert "s=104" in url
    assert "owner[]=private" not in url


def test_all_categories_requires_query() -> None:
    with pytest.raises(ValueError, match="запрос"):
        plan_search("", "moskva", catalog.ALL_CATEGORY_ID)


# ── Локальная сборка API URL ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("region", "category", "expected"),
    [
        ("all", "apple_phones", ["categoryId=84", "locationId=621540"]),
        ("moskva", "game_consoles", ["categoryId=97", "locationId=637640", "params%5B137%5D=613"]),
        ("moskva_i_mo", "game_consoles", ["categoryId=97", "locationId=107620"]),
        ("moskva", "laptops_apple", ["categoryId=98", "params%5B112916%5D=841338"]),
        ("all", "tablets", ["categoryId=96", "params%5B140%5D=4995"]),
    ],
)
def test_api_url_built_locally(region: str, category: str, expected: list[str]) -> None:
    url = catalog.build_api_url(region, category)
    assert url is not None
    for fragment in [*expected, "s=104"]:
        assert fragment in url
    assert "privateOnly=" not in url
    assert "owner" not in url
    assert "user=1" not in url
    assert "presentationType" not in url
    assert "sort=date" not in url


def test_api_url_passes_catalog_filter_hash() -> None:
    """Хеш категории должен попасть в JSON API."""
    url = catalog.build_api_url("moskva", catalog.IPHONE_CATEGORY_ID)
    assert url is not None
    assert "f=ASgBAgICAkS0wA3OqzmwwQ2I_Dc" in url


def test_tablets_api_url_uses_extra_filter_hash() -> None:
    url = catalog.build_api_url("moskva", "tablets")
    assert url is not None
    assert "f=ASgBAgICAkSYAoZOwPgO~qagDw" in url or "f=ASgBAgICAkSYAoZOwPgO%7EqagDw" in url


def test_all_categories_api_url_has_query_and_no_category() -> None:
    url = catalog.build_api_url("moskva", catalog.ALL_CATEGORY_ID, query="iphone")
    assert url is not None
    assert "categoryId=" not in url
    assert "q=iphone" in url
    assert "s=104" in url
    assert "privateOnly=" not in url
    assert "owner" not in url
    assert "user=1" not in url
    assert "locationId=637640" in url


def test_api_url_unknown_region_needs_service() -> None:
    """Для незнакомого региона locationId неизвестен — нужен внешний сервис."""
    assert catalog.build_api_url("нет-такого-региона", "apple_phones") is None


def test_with_page_replaces_existing_page() -> None:
    url = catalog.build_api_url("all", "apple_phones")
    assert url is not None
    second = catalog.with_page(url, 2)
    assert "p=2" in second
    assert "page=" not in second
    assert catalog.with_page(second, 3).count("p=") == 1


def test_normalize_always_forces_date_sort() -> None:
    """Даже если во вставленной ссылке s=1, запрос идёт «по дате»."""
    dirty = "https://www.avito.ru/web/1/js/items?locationId=637640&s=1"
    clean = catalog.normalize_items_api_url(dirty, prefer_s="1")
    assert "s=104" in clean
    assert "s=1" not in clean.replace("s=104", "")


def test_normalize_strips_private_filter() -> None:
    """Пока смотрим всю выдачу по дате, owner[] и privateOnly мешают."""
    dirty = (
        "https://www.avito.ru/web/1/js/items?locationId=637640&s=104"
        "&owner[]=private&privateOnly=1&user=1"
    )
    clean = catalog.normalize_items_api_url(dirty)
    assert "owner" not in clean
    assert "privateOnly=" not in clean
    assert "user=1" not in clean
    assert "s=104" in clean


def test_normalize_drops_serp_mixers() -> None:
    dirty = (
        "https://www.avito.ru/web/1/js/items?locationId=637640"
        "&presentationType=serp&sort=date&s=1&owner[]=private"
    )
    clean = catalog.normalize_items_api_url(dirty, prefer_s="104")
    assert "presentationType" not in clean
    assert "sort=" not in clean
    assert "s=104" in clean
    assert "privateOnly=" not in clean
    assert "user=1" not in clean
    assert "owner" not in clean


def test_api_url_from_pasted_apple_link() -> None:
    web = (
        "https://www.avito.ru/moskva/telefony/mobilnye_telefony/"
        "apple-ASgBAgICAkS0wA3OqzmwwQ2I_Dc?cd=1&s=104&owner[]=private"
    )
    url = catalog.api_url_from_web_url(web)
    assert url is not None
    assert "locationId=637640" in url
    assert "categoryId=84" in url
    assert "s=104" in url
    assert "f=ASgBAgICAkS0wA3OqzmwwQ2I_Dc" in url
    assert "presentationType" not in url
    assert "sort=date" not in url


def test_api_url_from_pasted_link_keeps_query_and_price() -> None:
    web = "https://www.avito.ru/moskva?q=iphone&pmin=10000&s=104"
    url = catalog.api_url_from_web_url(web)
    assert url is not None
    assert "q=iphone" in url
    assert "pmin=10000" in url
    assert "locationId=637640" in url
    assert "categoryId=" not in url


def test_bare_category_path_needs_service() -> None:
    """/moskva/telefony без хеша и без q — не угадываем categoryId."""
    assert catalog.api_url_from_web_url("https://www.avito.ru/moskva/telefony") is None


def test_plan_from_url_reads_region() -> None:
    plan = plan_from_url(
        "https://www.avito.ru/sankt-peterburg/telefony/mobilnye_telefony/"
        "apple-ASgBAgICAkS0wA3OqzmwwQ2I_Dc?s=104"
    )
    assert plan.region.slug == "sankt-peterburg"
    assert plan.category is not None
    assert plan.category.id == catalog.IPHONE_CATEGORY_ID


def test_resolve_sanitizes_service_url(monkeypatch: pytest.MonkeyPatch) -> None:
    dirty = (
        "https://www.avito.ru/web/1/js/items?locationId=653240"
        "&presentationType=serp&sort=date&s=1"
    )
    monkeypatch.setattr("avito_monitor.spfa.convert_avito_url", lambda _url: dirty)
    url = resolve_api_url("https://www.avito.ru/kazan/telefony?s=104")
    assert "presentationType" not in url
    assert "sort=" not in url
    assert "s=104" in url


# ── Фильтр моделей ─────────────────────────────────────────────────────────


def test_selected_models_land_in_web_url() -> None:
    url = _web_url_with_models(["13-pro", "13-pro-max"])
    assert "1642359" in url
    assert "1642361" in url
    assert str(iphone_params.TYPE_VALUE) in url
    assert iphone_params.has_model_params(url) is True


def test_all_models_selected_means_no_filter() -> None:
    """Все модели — та же выдача, но без лишних параметров в ссылке."""
    every_id = [model.id for model in iphone.all_models()]
    url = _web_url_with_models(every_id)
    assert f"params[{iphone_params.MODEL_PARAM_WEB}]" not in url
    assert iphone_params.has_model_params(url) is False


def test_model_values_follow_selection_order() -> None:
    assert iphone_params.models_to_avito_values(["13-pro", "13-pro-max"]) == [1642359, 1642361]
    assert iphone_params.models_to_avito_values(["13-pro-max", "13-pro"]) == [1642361, 1642359]


def test_api_url_uses_its_own_param_code() -> None:
    """У JSON API код параметра «модель» отличается от веб-поиска."""
    api = (
        "https://www.avito.ru/web/1/js/items?categoryId=84&locationId=637640"
        "&owner%5B0%5D=private&sort=date"
    )
    updated, applied = iphone_params.append_model_params(api, ["13-pro"])
    assert applied is True
    assert f"params%5B{iphone_params.MODEL_PARAM_API}%5D%5B0%5D=1642359" in updated
    assert iphone_params.has_model_params(updated) is True


def test_append_is_idempotent() -> None:
    """Повторное применение не должно накапливать параметры."""
    once, _ = iphone_params.append_model_params(
        catalog.build_web_url("", "moskva", catalog.IPHONE_CATEGORY_ID), ["13-pro"]
    )
    twice, _ = iphone_params.append_model_params(once, ["13-pro"])
    assert once == twice


def test_strip_removes_model_filter() -> None:
    url = _web_url_with_models(["13-pro"])
    stripped = iphone_params.strip_model_params(url)
    assert "1642359" not in stripped
    assert iphone_params.has_model_params(stripped) is False


def test_retarget_moves_web_model_codes_to_api() -> None:
    web_style = (
        "https://www.avito.ru/web/1/js/items?categoryId=84&locationId=637640"
        f"&params[{iphone_params.MODEL_PARAM_WEB}][0]=1642359"
    )
    updated = iphone_params.retarget_web_model_params(web_style)
    assert f"params%5B{iphone_params.MODEL_PARAM_API}%5D%5B0%5D=1642359" in updated
    assert str(iphone_params.MODEL_PARAM_WEB) not in updated


def test_models_ignored_for_other_categories() -> None:
    """Фильтр моделей осмыслен только для смартфонов Apple."""
    plan = plan_search("", "moskva", "tablets", ["13-pro"])
    assert plan.iphone_models is None
    assert iphone_params.has_model_params(plan.web_url) is False
