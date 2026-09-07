"""Tests for private vs company seller detection."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parser import is_company_seller, is_private_seller, seller_is_allowed


def _item(profile_link: str, *, shop_id=None) -> dict:
    payload = {"profile": {"title": "Test", "link": profile_link}}
    item = {"id": 1, "iva": {"UserInfoStep": [{"payload": payload}]}}
    if shop_id is not None:
        item["shopId"] = shop_id
    return item


def test_private_user_link() -> None:
    item = _item("/user/abc/profile?src=search")
    assert is_private_seller(item) is True
    assert is_company_seller(item) is False
    assert seller_is_allowed(item, private_only=True) is True


def test_company_brand_link() -> None:
    item = _item("/brands/vnk?src=search_seller_info")
    assert is_company_seller(item) is True
    assert is_private_seller(item) is False
    assert seller_is_allowed(item, private_only=True) is False


def test_shop_id_marks_company() -> None:
    item = _item("/user/abc/profile", shop_id=99)
    assert seller_is_allowed(item, private_only=True) is False


if __name__ == "__main__":
    test_private_user_link()
    test_company_brand_link()
    test_shop_id_marks_company()
    print("ok")
