"""Тесты распознавания моделей iPhone."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from iphone_filter import detect_iphone_generation, iphone_model_allowed, iphone_model_matches, normalize_allowed_models


def test_detect_numbered_models() -> None:
    assert detect_iphone_generation("iPhone 13 Pro Max 256GB") == 13
    assert detect_iphone_generation("Apple iPhone 16 Pro") == 16
    assert detect_iphone_generation("айфон 14 про 128гб") == 14
    assert detect_iphone_generation("iPhone 17 Pro Max") == 17
    assert detect_iphone_generation("iPhone 8 Plus") == 8
    assert detect_iphone_generation("iPhone 7") == 7
    assert detect_iphone_generation("iPhone 6s Plus") == 6


def test_detect_x_series() -> None:
    assert detect_iphone_generation("iPhone X 64GB") == 10
    assert detect_iphone_generation("iPhone XS Max") == 10
    assert detect_iphone_generation("iPhone XR") == 10
    assert detect_iphone_generation("айфон XS") == 10


def test_detect_unknown_or_old() -> None:
    assert detect_iphone_generation("iPhone SE 2020") is None
    assert detect_iphone_generation("Samsung Galaxy S24") is None
    assert detect_iphone_generation("продам телефон") is None


def test_filter_from_ten_to_latest() -> None:
    assert iphone_model_matches("iPhone 11", 10) is True
    assert iphone_model_matches("iPhone X", 10) is True
    assert iphone_model_matches("iPhone 8", 10) is False
    assert iphone_model_matches("iPhone SE", 10) is False
    assert iphone_model_matches("iPhone 17 Pro", 10) is True


def test_allowed_models_from_ui() -> None:
    assert iphone_model_allowed("iPhone 13 Pro", [13, 14]) is True
    assert iphone_model_allowed("iPhone 12 mini", [13, 14]) is False
    assert iphone_model_allowed("iPhone 17 Pro", [17]) is True
    assert iphone_model_allowed("iPhone 17", [17]) is True
    assert iphone_model_allowed("iPhone SE", [13, 14]) is False
    assert iphone_model_allowed("Samsung S24", [17]) is False
    assert iphone_model_allowed("iPhone 13", []) is False


def test_normalize_legacy_variants() -> None:
    assert normalize_allowed_models(["17-pro", "17-pro-max", 13]) == [17, 13]
    assert normalize_allowed_models(["6s-plus", "x"]) == [6, 10]


if __name__ == "__main__":
    test_detect_numbered_models()
    test_detect_x_series()
    test_detect_unknown_or_old()
    test_filter_from_ten_to_latest()
    test_allowed_models_from_ui()
    test_normalize_legacy_variants()
    print("ok")
