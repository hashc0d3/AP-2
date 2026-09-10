"""Отбор объявлений для ленты."""

from __future__ import annotations

import time
from dataclasses import replace

import pytest

from avito_monitor.avito import filters
from avito_monitor.config import Settings


def _ad(ad_id: int = 1, title: str = "iPhone 13 Pro", age: int = 10, **extra: object) -> dict:
    item: dict = {
        "id": ad_id,
        "title": title,
        "sortTimeStamp": int((time.time() - age) * 1000),
    }
    item.update(extra)
    return item


def _with_seller(link: str, name: str = "Иван", **extra: object) -> dict:
    return _ad(
        iva={"UserInfoStep": [{"payload": {"profile": {"title": name, "link": link}}}]},
        **extra,
    )


PRIVATE = "/user/abc/profile"
COMPANY = "/brands/vnk"


# ── Отдельные правила ──────────────────────────────────────────────────────


def test_title_must_contain() -> None:
    assert filters.title_matches(_ad(title="iPhone 13"), ("iphone",), ()) is True
    assert filters.title_matches(_ad(title="Samsung"), ("iphone",), ()) is False


def test_title_skip_words() -> None:
    assert filters.title_matches(_ad(title="Чехол на iPhone"), (), ("чехол",)) is False
    assert filters.title_matches(_ad(title="iPhone 13"), (), ("чехол",)) is True


def test_title_filters_are_case_insensitive() -> None:
    assert filters.title_matches(_ad(title="ЧЕХОЛ"), (), ("чехол",)) is False


def test_no_title_filters_pass_everything() -> None:
    assert filters.title_matches(_ad(title="что угодно"), (), ()) is True


def test_seller_blacklist_ignores_punctuation_and_case() -> None:
    """Продавцы разбавляют имена символами — сравниваем «скелеты» строк."""
    item = _with_seller(PRIVATE, name="П Р Е М И У М . Store")
    assert filters.seller_is_skipped(item, ("премиум",)) is True


def test_seller_blacklist_empty_passes() -> None:
    assert filters.seller_is_skipped(_with_seller(PRIVATE), ()) is False


def test_private_only_blocks_companies() -> None:
    assert filters.seller_is_allowed(_with_seller(COMPANY), private_only=True) is False
    assert filters.seller_is_allowed(_with_seller(PRIVATE), private_only=True) is True


def test_private_only_disabled_allows_companies() -> None:
    assert filters.seller_is_allowed(_with_seller(COMPANY), private_only=False) is True


def test_unknown_seller_is_allowed() -> None:
    """Avito отдаёт профиль не всегда: молча терять частников хуже."""
    assert filters.seller_is_allowed(_ad(), private_only=True) is True


def test_private_seller_with_delivery_shop_is_allowed() -> None:
    """Корзина даёт /shop/, но продавец — частное лицо."""
    item = _with_seller(PRIVATE, shopId=99)
    item["iva"]["ShopInfoStep"] = [{"payload": {"link": "/shop/delivery"}}]
    assert filters.seller_is_allowed(item, private_only=True) is True


def test_delivery_shop_without_user_profile_is_allowed() -> None:
    """Иначе новые частные объявления пропадали как «компания»."""
    item = _ad(iva={"ShopInfoStep": [{"payload": {"link": "/shop/delivery"}}]}, shopId=99)
    assert filters.seller_is_allowed(item, private_only=True) is True


# ── Пайплайн отбора ────────────────────────────────────────────────────────


@pytest.fixture
def base_settings() -> Settings:
    return Settings(private_only=False, ignore_promotion=True)


def test_first_run_remembers_without_showing(base_settings: Settings) -> None:
    ads = [_ad(1), _ad(2), _ad(3)]
    seen: set[int] = set()
    selected, stats = filters.select_new_ads(ads, base_settings, seen, first_run=True)
    assert selected == []
    assert stats.baseline == 3
    assert seen == {1, 2, 3}


def test_later_runs_show_only_unseen(base_settings: Settings) -> None:
    seen = {1, 2}
    selected, stats = filters.select_new_ads(
        [_ad(1), _ad(2), _ad(3)], base_settings, seen, first_run=False
    )
    assert [ad["id"] for ad in selected] == [3]
    assert stats.already_seen == 2


def test_rejected_non_promo_ads_can_surface_later(base_settings: Settings) -> None:
    """Ложный «магазин» не должен навсегда закрыть карточку."""
    settings = replace(base_settings, private_only=True)
    company = _with_seller(COMPANY)
    seen: set[int] = set()
    selected, stats = filters.select_new_ads([company], settings, seen, first_run=False)
    assert selected == []
    assert stats.company == 1
    assert seen == set()

    private = _with_seller(PRIVATE, **{"id": 1})
    selected, _ = filters.select_new_ads([private], settings, seen, first_run=False)
    assert [ad["id"] for ad in selected] == [1]
    assert seen == {1}


def test_promoted_ads_are_remembered_so_they_dont_resurface(base_settings: Settings) -> None:
    """Иначе продвинутое объявление всплывёт позже как новое."""
    promoted = _ad(9, iva={"DateInfoStep": [{"payload": {"vas": [{"title": "Продвинуто"}]}}]})
    ordinary = _ad(8)
    seen: set[int] = set()
    selected, stats = filters.select_new_ads(
        [promoted, ordinary], base_settings, seen, first_run=True
    )
    assert selected == []
    assert stats.promoted == 1
    assert stats.baseline == 1
    assert seen == {8, 9}


def test_all_promoted_page_is_not_hidden(base_settings: Settings) -> None:
    """Бейдж на каждой карточке JSON — ложный, на сайте те же объявления обычные."""
    badge = {"DateInfoStep": [{"payload": {"vas": [{"title": "Продвинуто"}]}}]}
    selected, stats = filters.select_new_ads(
        [_ad(1, iva=badge), _ad(2, iva=badge)], base_settings, set(), first_run=False
    )
    assert {ad["id"] for ad in selected} == {1, 2}
    assert stats.promoted == 0
    assert stats.promotion_badge_ignored is True


def test_promoted_can_be_allowed(base_settings: Settings) -> None:
    promoted = _ad(9, iva={"DateInfoStep": [{"payload": {"vas": [{"title": "Продвинуто"}]}}]})
    settings = replace(base_settings, ignore_promotion=False)
    selected, _ = filters.select_new_ads([promoted], settings, set(), first_run=False)
    assert len(selected) == 1


def test_results_are_sorted_newest_first(base_settings: Settings) -> None:
    ads = [_ad(1, age=200), _ad(2, age=10), _ad(3, age=100)]
    selected, _ = filters.select_new_ads(ads, base_settings, set(), first_run=False)
    assert [ad["id"] for ad in selected] == [2, 3, 1]


def test_old_ads_are_not_dropped_by_age(base_settings: Settings) -> None:
    """Возраст на Avito больше не режет: новое — то, чего не было на старте."""
    selected, stats = filters.select_new_ads(
        [_ad(1, age=10), _ad(2, age=9000)], base_settings, set(), first_run=False
    )
    assert {ad["id"] for ad in selected} == {1, 2}
    assert stats.baseline == 0


def test_iphone_model_filter_applies(base_settings: Settings) -> None:
    settings = replace(base_settings, iphone_models=("13-pro",), category_id="apple_phones")
    ads = [_ad(1, title="iPhone 13 Pro"), _ad(2, title="iPhone 14 Pro")]
    selected, stats = filters.select_new_ads(ads, settings, set(), first_run=False)
    assert [ad["id"] for ad in selected] == [1]
    assert stats.iphone_model == 1


def test_iphone_model_filter_ignored_for_tablets(base_settings: Settings) -> None:
    """Даже если в сессии остались модели iPhone, планшеты они не режут."""
    settings = replace(base_settings, iphone_models=("13-pro",), category_id="tablets")
    ads = [_ad(1, title="iPad Air 13 m3 256gb Purple")]
    selected, stats = filters.select_new_ads(ads, settings, set(), first_run=False)
    assert [ad["id"] for ad in selected] == [1]
    assert stats.iphone_model == 0


def test_model_filter_skipped_when_already_in_url(base_settings: Settings) -> None:
    """Avito уже отфильтровал выдачу — повторно проверять название незачем."""
    settings = replace(base_settings, iphone_models=("13-pro",), iphone_models_in_url=True)
    ads = [_ad(1, title="Совсем другое название")]
    selected, _ = filters.select_new_ads(ads, settings, set(), first_run=False)
    assert len(selected) == 1


def test_ads_without_id_are_skipped(base_settings: Settings) -> None:
    selected, stats = filters.select_new_ads(
        [{"title": "без id"}, _ad(1)], base_settings, set(), first_run=False
    )
    assert [ad["id"] for ad in selected] == [1]
    assert stats.total == 2


def test_stats_summary_lists_only_triggered_filters() -> None:
    stats = filters.FilterStats(total=10, promoted=3, title=1)
    summary = stats.summary()
    assert "продвинутых скрыто: 3" in summary
    assert "не подходит название: 1" in summary
    assert "компания" not in summary


def test_empty_summary_when_nothing_filtered() -> None:
    assert filters.FilterStats(total=5).summary() == ""
