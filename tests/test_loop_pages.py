"""Страницы выдачи — отдельные запросы ``p=1``, ``p=2``."""

from avito_monitor.config import Settings
from avito_monitor.monitor.loop import FULL_PAGE_ITEMS, _runtime_settings, should_open_next_page


def test_opens_next_page_when_current_is_full() -> None:
    assert (
        should_open_next_page(
            page_items=[{}] * FULL_PAGE_ITEMS,
            page=1,
            max_pages=2,
        )
        is True
    )


def test_does_not_open_next_page_past_max() -> None:
    assert (
        should_open_next_page(
            page_items=[{}] * FULL_PAGE_ITEMS,
            page=2,
            max_pages=2,
        )
        is False
    )


def test_does_not_open_next_page_when_listing_ended() -> None:
    assert (
        should_open_next_page(
            page_items=[{}] * (FULL_PAGE_ITEMS - 1),
            page=1,
            max_pages=2,
        )
        is False
    )


def test_runtime_settings_strip_paid_serp() -> None:
    """Старый адрес из spfa чистим на каждом цикле, без перезапуска поиска."""
    runtime = _runtime_settings(
        Settings(),
        {
            "web_url": "https://www.avito.ru/moskva/telefony?s=1",
            "api_url": (
                "https://www.avito.ru/web/1/js/items?locationId=637640"
                "&presentationType=serp&sort=date&s=1&owner[]=private"
            ),
            "category": {},
        },
    )
    assert "presentationType" not in runtime.api_url
    assert "sort=" not in runtime.api_url
    assert "s=104" in runtime.api_url
