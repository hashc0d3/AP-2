"""Разбор объявлений из ответа API Avito."""

from __future__ import annotations

import time

import pytest

from avito_monitor.avito import items


def _item(**overrides: object) -> dict:
    base: dict = {
        "id": 42,
        "title": "iPhone 13 Pro 256GB",
        "priceDetailed": {"string": "55 000 ₽", "value": 55000},
        "urlPath": "/moskva/telefony/iphone_13_pro_1234?src=search",
        "sortTimeStamp": int(time.time() * 1000),
    }
    base.update(overrides)
    return base


# ── Поиск списка объявлений ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "payload",
    [
        {"items": [{"id": 1}]},
        {"catalog": {"items": [{"id": 1}]}},
        {"result": {"items": [{"id": 1}]}},
        {"result": {"catalog": {"items": [{"id": 1}]}}},
    ],
)
def test_finds_items_in_known_shapes(payload: dict) -> None:
    """Avito кладёт список в разные места — проверяем все известные."""
    assert items.extract_items(payload) == [{"id": 1}]


@pytest.mark.parametrize("payload", [None, "строка", {}, {"items": "не список"}, []])
def test_unknown_shape_gives_empty_list(payload: object) -> None:
    assert items.extract_items(payload) == []


def test_entries_without_id_are_dropped() -> None:
    payload = {"items": [{"id": 1}, {"title": "без id"}, "мусор", {"id": 0}]}
    assert items.extract_items(payload) == [{"id": 1}]


def test_item_id_parses_strings() -> None:
    assert items.item_id({"id": "123"}) == 123
    assert items.item_id({"id": "abc"}) is None
    assert items.item_id({}) is None


# ── Время ──────────────────────────────────────────────────────────────────


def test_age_from_publish_timestamp() -> None:
    item = _item(sortTimeStamp=int((time.time() - 120) * 1000))
    age = items.age_seconds(item)
    assert age is not None
    assert 118 <= age <= 125


@pytest.mark.parametrize("raw", [None, 0, "", "нет"])
def test_missing_timestamp_gives_no_age(raw: object) -> None:
    assert items.age_seconds(_item(sortTimeStamp=raw)) is None


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (None, ""),
        (0, "0 сек назад"),
        (59, "59 сек назад"),
        (60, "1 мин назад"),
        (3599, "59 мин назад"),
        (3600, "1 ч назад"),
        (7200, "2 ч назад"),
    ],
)
def test_age_formatting(seconds: int | None, expected: str) -> None:
    assert items.format_age(seconds) == expected


def test_published_uses_region_timezone() -> None:
    """Одно и то же время показывается по-разному в разных поясах."""
    ts = 1_700_000_000
    moscow = items.format_published(ts, "Europe/Moscow")
    vladivostok = items.format_published(ts, "Asia/Vladivostok")
    assert moscow != vladivostok


# ── Продвижение ────────────────────────────────────────────────────────────


def test_detects_promoted_ad() -> None:
    item = _item(iva={"DateInfoStep": [{"payload": {"vas": [{"title": "Продвинуто"}]}}]})
    assert items.is_promoted(item) is True


def test_ordinary_ad_is_not_promoted() -> None:
    assert items.is_promoted(_item()) is False
    assert items.is_promoted(_item(iva={"DateInfoStep": [{"payload": {"vas": []}}]})) is False


# ── Продавец ───────────────────────────────────────────────────────────────


def _with_profile(link: str, **extra: object) -> dict:
    item = _item(iva={"UserInfoStep": [{"payload": {"profile": {"title": "Иван", "link": link}}}]})
    item.update(extra)
    return item


def test_private_seller_from_user_link() -> None:
    item = _with_profile("/user/abc/profile?src=search")
    assert items.is_private_seller(item) is True
    assert items.is_company_seller(item) is False


@pytest.mark.parametrize("link", ["/brands/vnk?src=x", "/shop/foo", "/company/bar"])
def test_company_seller_from_link(link: str) -> None:
    item = _with_profile(link)
    assert items.is_company_seller(item) is True
    assert items.is_private_seller(item) is False


def test_shop_id_does_not_override_private_profile() -> None:
    """Корзина и доставка проставляют shopId даже частникам."""
    assert items.is_company_seller(_with_profile("/user/abc/profile", shopId=99)) is False
    assert items.is_private_seller(_with_profile("/user/abc/profile", shopId=99)) is True


def test_seller_name_prefers_readable_text() -> None:
    assert items.seller_name(_with_profile("/user/abc/profile")) == "Иван"


def test_seller_name_missing_gives_empty_string() -> None:
    assert items.seller_name(_item()) == ""


def test_seller_texts_collect_names_and_links() -> None:
    texts = items.seller_texts(_with_profile("/user/abc/profile", sellerName="Магазин Плюс"))
    assert "Магазин Плюс" in texts
    assert "/user/abc/profile" in texts


# ── Прочие поля ────────────────────────────────────────────────────────────


def test_address_prefers_exact_value() -> None:
    item = _item(geo={"formattedAddress": "ул. Ленина, 1", "city": "Москва"})
    assert items.ad_address(item) == "ул. Ленина, 1"


def test_address_falls_back_to_geo_references() -> None:
    item = _item(geo={"geoReferences": [{"content": "Тверская"}, {"content": "5 мин"}]})
    assert items.ad_address(item) == "Тверская, 5 мин"


def test_address_missing_gives_empty_string() -> None:
    assert items.ad_address(_item(geo={})) == ""


def test_url_drops_query_string() -> None:
    assert items.ad_url(_item()) == ("https://www.avito.ru/moskva/telefony/iphone_13_pro_1234")


def test_url_falls_back_to_id() -> None:
    assert items.ad_url({"id": 77}) == "https://www.avito.ru/77"


@pytest.mark.parametrize(
    ("detailed", "expected"),
    [
        ({"string": "55 000 ₽"}, "55 000 ₽"),
        ({"value": 55000}, "55000"),
        ({"string": "", "value": 0}, items.NO_PRICE),
        ({}, items.NO_PRICE),
    ],
)
def test_price_formats(detailed: dict, expected: str) -> None:
    assert items.ad_price(_item(priceDetailed=detailed)) == expected


def test_images_pick_largest_size() -> None:
    item = _item(gallery=[{"208x156": "http://a/small.jpg", "640x480": "http://a/big.jpg"}])
    assert items.ad_images(item) == ["http://a/big.jpg"]


def test_images_are_deduplicated_and_capped() -> None:
    gallery = [{"640x480": f"http://a/{index}.jpg"} for index in range(20)]
    gallery.append({"640x480": "http://a/0.jpg"})
    assert len(items.ad_images(_item(gallery=gallery))) == items.MAX_IMAGES


def test_no_images_gives_empty_list() -> None:
    assert items.ad_images(_item()) == []


def test_description_from_card_block() -> None:
    item = _item(iva={"DescriptionStep": [{"payload": {"description": "  Как новый  "}}]})
    assert items.ad_description(item) == "Как новый"


def test_description_prefers_root_field() -> None:
    item = _item(
        description="Из корня", iva={"DescriptionStep": [{"payload": {"description": "Из блока"}}]}
    )
    assert items.ad_description(item) == "Из корня"


# ── Контакты ───────────────────────────────────────────────────────────────


def test_explicit_call_flag_wins() -> None:
    assert items.contact_flags(_item(canCall=True))[0] is True
    assert items.contact_flags(_item(canCall=False))[0] is False


def test_hidden_phone_flag_is_inverted() -> None:
    assert items.contact_flags(_item(isPhoneHidden=True))[0] is False
    assert items.contact_flags(_item(isPhoneHidden=False))[0] is True


def test_flags_are_read_from_nested_blocks() -> None:
    assert items.contact_flags(_item(contacts={"canCall": True, "canWrite": True})) == (True, True)


def test_flags_fall_back_to_card_actions() -> None:
    """Явных флагов нет — смотрим, есть ли в карточке кнопка звонка."""
    item = _item(iva={"ContactBarStep": [{"payload": {"title": "Позвонить"}}]})
    assert items.contact_flags(item)[0] is True


def test_missing_contacts_default_to_false() -> None:
    assert items.contact_flags(_item()) == (False, False)


# ── Итоговая карточка ──────────────────────────────────────────────────────


def test_serialize_ad_has_frontend_contract() -> None:
    """Набор ключей должен совпадать с типом ``Ad`` во фронтенде."""
    ad = items.serialize_ad(_item(), tz_name="Europe/Moscow")
    assert set(ad) == {
        "id",
        "title",
        "price",
        "address",
        "url",
        "images",
        "can_call",
        "can_message",
        "seller",
        "published",
        "ts",
        "description",
        "phone_key",
    }
    assert ad["id"] == 42
    assert ad["title"] == "iPhone 13 Pro 256GB"
    assert ad["price"] == "55 000 ₽"
    assert isinstance(ad["images"], list)
    assert isinstance(ad["ts"], int)


def test_serialize_ad_without_title() -> None:
    ad = items.serialize_ad({"id": 1}, tz_name="Europe/Moscow")
    assert ad["title"] == items.NO_TITLE
    assert ad["ts"] == pytest.approx(time.time(), abs=5)
