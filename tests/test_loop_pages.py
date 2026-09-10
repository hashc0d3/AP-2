"""Вторая страница — только если на первой нечего показать."""

from avito_monitor.monitor.loop import FULL_PAGE_ITEMS, should_open_next_page


def test_does_not_open_next_page_when_first_has_suitable_ads() -> None:
    assert (
        should_open_next_page(
            suitable=[{"id": 1}],
            page_items=[{}] * FULL_PAGE_ITEMS,
            page=1,
            max_pages=2,
        )
        is False
    )


def test_opens_next_page_when_first_is_all_unsuitable() -> None:
    assert (
        should_open_next_page(
            suitable=[],
            page_items=[{}] * FULL_PAGE_ITEMS,
            page=1,
            max_pages=2,
        )
        is True
    )


def test_does_not_open_next_page_past_max() -> None:
    assert (
        should_open_next_page(
            suitable=[],
            page_items=[{}] * FULL_PAGE_ITEMS,
            page=2,
            max_pages=2,
        )
        is False
    )


def test_does_not_open_next_page_when_listing_ended() -> None:
    assert (
        should_open_next_page(
            suitable=[],
            page_items=[{}] * (FULL_PAGE_ITEMS - 1),
            page=1,
            max_pages=2,
        )
        is False
    )
