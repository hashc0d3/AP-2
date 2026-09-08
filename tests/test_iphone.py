"""Распознавание моделей iPhone по названию объявления."""

from __future__ import annotations

import pytest

from avito_monitor.avito import iphone


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("iPhone 13 Pro Max 256GB", 13),
        ("Apple iPhone 16 Pro", 16),
        ("айфон 14 про 128гб", 14),
        ("iPhone 17 Pro Max", 17),
        ("iPhone 8 Plus", 8),
        ("iPhone 7", 7),
        ("iPhone 6s Plus", 6),
        ("iPhone X 64GB", 10),
        ("iPhone XS Max", 10),
        ("iPhone XR", 10),
        ("айфон XS", 10),
    ],
)
def test_detects_generation(title: str, expected: int) -> None:
    assert iphone.detect_generation(title) == expected


@pytest.mark.parametrize(
    "title",
    [
        "iPhone SE 2020",  # у SE номер линейки не совпадает с поколением
        "Samsung Galaxy S24",
        "продам телефон",
        "",
    ],
)
def test_ignores_unknown_titles(title: str) -> None:
    assert iphone.detect_generation(title) is None


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("iPhone 13 Pro Max 256GB", "13-pro-max"),
        ("Apple iPhone 16 Pro", "16-pro"),
        ("айфон 14 про 128гб", "14-pro"),
        ("iPhone 14 Plus 128GB", "14-plus"),
        ("iPhone 12 mini 64GB", "12-mini"),
        ("iPhone 16e 128GB", "16-e"),
        ("iPhone 17 Air 256GB", "17-air"),
        ("iPhone 13 128GB", "13"),
    ],
)
def test_detects_model_id(title: str, expected: str) -> None:
    assert iphone.detect_model_id(title) == expected


@pytest.mark.parametrize("title", ["iPhone 8 Plus", "iPhone X 64GB", "iPhone SE"])
def test_models_below_eleventh_generation_are_not_selectable(title: str) -> None:
    """Avito различает варианты Pro/Plus только с 11-го поколения."""
    assert iphone.detect_model_id(title) is None


@pytest.mark.parametrize(
    ("title", "allowed"),
    [
        ("iPhone 11", True),
        ("iPhone X", True),
        ("iPhone 8", False),
        ("iPhone SE", False),
        ("iPhone 17 Pro", True),
    ],
)
def test_generation_range(title: str, allowed: bool) -> None:
    assert iphone.generation_in_range(title, 10) is allowed


def test_generation_range_disabled_passes_everything() -> None:
    assert iphone.generation_in_range("Samsung Galaxy", 0) is True


@pytest.mark.parametrize(
    ("title", "allowed", "expected"),
    [
        ("iPhone 13 Pro", ["13-pro"], True),
        ("iPhone 13 Pro Max", ["13-pro"], False),
        ("iPhone 13", ["13-pro"], False),
        ("iPhone 12 mini", ["13-pro", "14-pro"], False),
        ("iPhone 17 Pro", ["17-pro"], True),
        ("iPhone 17", ["17-pro"], False),
        ("iPhone SE", ["13-pro"], False),
        ("Samsung S24", ["17-pro"], False),
    ],
)
def test_model_allowed(title: str, allowed: list[str], expected: bool) -> None:
    assert iphone.model_allowed(title, allowed) is expected


def test_empty_selection_shows_nothing() -> None:
    """Снятые все галочки — это «не показывать ничего», а не «показать всё»."""
    assert iphone.model_allowed("iPhone 13", []) is False


def test_no_selection_falls_back_to_range() -> None:
    assert iphone.model_allowed("iPhone 13", None, min_gen=14) is False
    assert iphone.model_allowed("iPhone 15", None, min_gen=14) is True


def test_normalize_keeps_order_and_drops_duplicates() -> None:
    assert iphone.normalize_models(["17-pro", "17-pro-max", "13", "17-pro"]) == (
        "17-pro",
        "17-pro-max",
        "13",
    )


def test_normalize_expands_bare_generation() -> None:
    """Число в настройках означает всю линейку — так выглядел старый формат."""
    assert iphone.normalize_models([13, 14]) == (
        "13",
        "13-mini",
        "13-pro",
        "13-pro-max",
        "14",
        "14-plus",
        "14-pro",
        "14-pro-max",
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (["6s-plus", "x"], ()),
        (["16e"], ("16-e",)),
        ([True, None, ""], ()),
        ("не список", ()),
    ],
)
def test_normalize_rejects_garbage(raw: object, expected: tuple[str, ...]) -> None:
    assert iphone.normalize_models(raw) == expected


def test_normalize_passes_none_through() -> None:
    """``None`` значит «фильтр не задан» и должен таким и остаться."""
    assert iphone.normalize_models(None) is None


def test_full_selection_detected() -> None:
    every_id = [model.id for model in iphone.all_models()]
    assert iphone.is_full_selection(every_id) is True
    assert iphone.is_full_selection(every_id[:-1]) is False
    assert iphone.is_full_selection(None) is True


def test_catalog_is_consistent() -> None:
    """Каталог не должен содержать дублей и пустых полей."""
    models = iphone.all_models()
    assert len(models) == len({model.id for model in models})
    assert len(models) == len({model.avito_value for model in models})
    assert all(model.label and model.gen >= 11 for model in models)
