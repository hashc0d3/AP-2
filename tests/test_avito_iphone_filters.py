"""Tests for Avito iPhone model URL filters."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from avito_iphone_filters import (
    append_iphone_model_params,
    iphone_model_params_present,
    is_full_iphone_selection,
    models_to_avito_values,
)
from avito_search import build_web_url


def test_full_selection_skips_params() -> None:
    all_ids = [str(i) for i in range(11, 18)]
    assert is_full_iphone_selection(["13-pro", "13-pro-max"]) is False
    assert models_to_avito_values(["13-pro", "13-pro-max"]) == [1642359, 1642361]


def test_build_web_url_with_models() -> None:
    url = build_web_url("", "moskva", "apple_phones", ["13-pro", "13-pro-max"])
    assert "1642359" in url
    assert "1642361" in url
    assert "469735" in url
    assert "localPriority=0" in url
    assert iphone_model_params_present(url) is True


def test_build_web_url_all_models_without_params() -> None:
    from iphone_filter import IPHONE_MODEL_OPTIONS

    all_models = [str(item["id"]) for item in IPHONE_MODEL_OPTIONS]
    url = build_web_url("", "moskva", "apple_phones", all_models)
    assert "params[121588]" not in url


def test_append_to_api_url() -> None:
    api = (
        "https://www.avito.ru/web/1/js/items?categoryId=84&locationId=637640"
        "&owner%5B0%5D=private&sort=date"
    )
    updated, applied = append_iphone_model_params(api, ["13-pro"])
    assert applied is True
    assert "params%5B110617%5D%5B0%5D=1642359" in updated
    assert iphone_model_params_present(updated) is True


def test_strip_model_params() -> None:
    from avito_iphone_filters import strip_iphone_model_params

    url = build_web_url("", "moskva", "apple_phones", ["13-pro"])
    stripped = strip_iphone_model_params(url)
    assert "1642359" not in stripped
    assert iphone_model_params_present(stripped) is False


def test_build_api_url_local() -> None:
    from avito_search import build_api_url_local

    api = build_api_url_local("all", "apple_phones")
    assert api
    assert "categoryId=84" in api
    assert "locationId=621540" in api

    game = build_api_url_local("moskva", "game_consoles")
    assert game
    assert "categoryId=97" in game
    assert "locationId=107620" in game
    assert "params%5B137%5D=613" in game
    assert "sort=date" in game
    assert "privateOnly=1" in game

    laptops = build_api_url_local("moskva", "laptops_apple")
    assert laptops
    assert "categoryId=98" in laptops
    assert "params%5B112916%5D=841338" in laptops

    tablets = build_api_url_local("all", "tablets")
    assert tablets
    assert "categoryId=96" in tablets
    assert "params%5B140%5D=4995" in tablets
    assert "params%5B122400%5D=15993277" in tablets


def test_build_web_url_new_categories() -> None:
    game = build_web_url("", "kazan", "game_consoles")
    assert "igrovye_pristavki/igrovye_pristavki-ASgBAgICAkSSAsoJ9M0UmsqPAw" in game
    assert game.startswith("https://www.avito.ru/kazan/")
    assert "localPriority=0" in game
    assert "s=104" in game
    assert "owner[]=private" in game

    game_msk = build_web_url("", "moskva", "game_consoles")
    assert "https://www.avito.ru/moskva_i_mo/" in game_msk
    assert "igrovye_pristavki_i_aksessuary" not in game_msk

    laptops = build_web_url("", "sankt-peterburg", "laptops_apple")
    assert "noutbuki/apple-ASgBAgICAUSo5A302WY" in laptops
    assert "https://www.avito.ru/sankt-peterburg/" in laptops

    tablets = build_web_url("", "moskva", "tablets")
    assert "planshety-ASgBAgICAUSYAoZO" in tablets
    assert "f=ASgBAgICAkSYAoZOwPgO~qagDw" in tablets


if __name__ == "__main__":
    test_full_selection_skips_params()
    test_build_web_url_with_models()
    test_build_web_url_all_models_without_params()
    test_append_to_api_url()
    test_strip_model_params()
    test_build_api_url_local()
    test_build_web_url_new_categories()
    print("ok")
