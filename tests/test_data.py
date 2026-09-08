"""Целостность справочников и их согласованность с фронтендом.

Регионы, категории и модели iPhone заданы в JSON внутри пакета. Часть тех же
данных нужна браузеру, поэтому во фронтенде есть их копия. Эти проверки
ловят расхождение копий и явные ошибки в самих справочниках — без них
рассинхрон замечается только по неверной выдаче у заказчика.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from avito_monitor.avito import catalog, iphone, regions
from avito_monitor.paths import DATA_DIR, PROJECT_ROOT

WEB_SRC = PROJECT_ROOT / "web" / "src"


# ── Регионы ────────────────────────────────────────────────────────────────


def test_regions_data_is_well_formed() -> None:
    raw = json.loads((DATA_DIR / "regions.json").read_text(encoding="utf-8"))
    assert isinstance(raw, list)
    assert len(raw) > 100
    for item in raw:
        assert item["slug"], item
        assert item["name"], item


def test_region_slugs_are_unique() -> None:
    slugs = [region.slug for region in regions.all_regions()]
    assert len(slugs) == len(set(slugs))


def test_every_timezone_is_real() -> None:
    """Опечатка в имени зоны выяснилась бы только при показе времени."""
    for region in regions.all_regions():
        try:
            ZoneInfo(region.timezone)
        except ZoneInfoNotFoundError as err:  # pragma: no cover — защита от опечаток
            pytest.fail(f"{region.slug}: неизвестная зона {region.timezone} ({err})")


def test_default_region_exists() -> None:
    assert regions.default_region().slug
    assert regions.region_or_default("").slug == regions.default_region().slug


def test_known_regions_resolve() -> None:
    assert regions.region_or_default("moskva").name
    assert regions.region_or_default("vladivostok").timezone == "Asia/Vladivostok"


def test_unknown_region_is_rejected() -> None:
    with pytest.raises(ValueError, match=r"[Рр]егион"):
        regions.region_or_default("нет-такого-города")


def test_region_search_finds_by_prefix() -> None:
    found = regions.search_regions("новосиб")
    assert any(item["slug"] == "novosibirsk" for item in found)


def test_region_search_ignores_case_and_spaces() -> None:
    assert regions.search_regions("  КАЗАНЬ  ") == regions.search_regions("казань")


# ── Категории ──────────────────────────────────────────────────────────────


def test_categories_data_is_well_formed() -> None:
    raw = json.loads((DATA_DIR / "categories.json").read_text(encoding="utf-8"))
    assert isinstance(raw, list)
    assert raw
    for item in raw:
        assert item["id"], item
        assert item["name"], item
        assert item["path"], item
        assert item["api_category_id"].isdigit(), item
        assert isinstance(item["api_params"], dict), item


def test_category_ids_are_unique() -> None:
    ids = [item["id"] for item in catalog.list_categories()]
    assert len(ids) == len(set(ids))


def test_iphone_category_is_present() -> None:
    """К этой категории привязан фильтр моделей — без неё он не работает."""
    assert catalog.find_category(catalog.IPHONE_CATEGORY_ID).id == catalog.IPHONE_CATEGORY_ID


def test_default_category_is_valid() -> None:
    assert catalog.find_category(catalog.default_category().id)


def test_every_category_builds_a_web_url() -> None:
    for item in catalog.list_categories():
        url = catalog.build_web_url("", "moskva", item["id"])
        assert url.startswith("https://www.avito.ru/"), item["id"]


def test_every_category_builds_an_api_url() -> None:
    """Для известного региона адрес API должен собираться без внешнего сервиса."""
    for item in catalog.list_categories():
        assert catalog.build_api_url("moskva", item["id"]), item["id"]


# ── Модели iPhone ──────────────────────────────────────────────────────────


def test_iphone_data_is_well_formed() -> None:
    raw = json.loads((DATA_DIR / "iphone_models.json").read_text(encoding="utf-8"))
    assert isinstance(raw, list)
    assert raw
    for item in raw:
        assert item["id"], item
        assert item["label"], item
        assert isinstance(item["gen"], int), item
        assert isinstance(item["avito_value"], int), item


def test_every_model_is_findable_by_id() -> None:
    for model in iphone.all_models():
        assert iphone.find_model(model.id) == model


def test_model_ids_match_the_catalog() -> None:
    assert iphone.model_ids() == {model.id for model in iphone.all_models()}


def test_models_are_ordered_by_generation() -> None:
    """Порядок задаёт вид списка в интерфейсе."""
    generations = [model.gen for model in iphone.all_models()]
    assert generations == sorted(generations)


def test_every_model_label_is_recognized_back() -> None:
    """Название из каталога должно определяться нашим же разбором.

    Исключение — «iPhone Air»: номера линейки в названии нет, и по заголовку
    объявления такую модель не отличить. Фильтр по ней работает только через
    параметры ссылки Avito.
    """
    for model in iphone.all_models():
        if model.id == "17-air":
            continue
        assert iphone.detect_model_id(f"{model.label} 128GB") == model.id, model.label


# ── Согласованность с фронтендом ───────────────────────────────────────────


def _parse_ts_models(source: str) -> list[tuple[str, int, str]]:
    pattern = re.compile(r'\{\s*id:\s*"([^"]+)",\s*gen:\s*(\d+),\s*label:\s*"([^"]+)"\s*\}')
    return [(mid, int(gen), label) for mid, gen, label in pattern.findall(source)]


def test_frontend_model_list_matches_backend() -> None:
    """Расхождение = пользователь выбирает модель, которую сервер не знает."""
    source = (WEB_SRC / "iphone-models.ts").read_text(encoding="utf-8")
    frontend = _parse_ts_models(source)
    backend = [(model.id, model.gen, model.label) for model in iphone.all_models()]

    assert frontend, "не удалось разобрать IPHONE_MODELS из iphone-models.ts"
    assert frontend == backend


def _parse_ts_timezones(source: str) -> dict[str, str]:
    body = source.split("OVERRIDES: Record<string, string> = {", 1)[1].split("};", 1)[0]
    pattern = re.compile(r'"?([\w-]+)"?:\s*"([\w/+-]+)"')
    return dict(pattern.findall(body))


def test_frontend_timezones_match_backend() -> None:
    """Иначе время публикации в браузере разойдётся с серверным."""
    source = (WEB_SRC / "region-timezones.ts").read_text(encoding="utf-8")
    frontend = _parse_ts_timezones(source)
    assert frontend, "не удалось разобрать OVERRIDES из region-timezones.ts"

    mismatched = {
        slug: (zone, regions.region_or_default(slug).timezone)
        for slug, zone in frontend.items()
        if regions.region_or_default(slug).timezone != zone
    }
    assert mismatched == {}


def test_frontend_knows_every_non_default_timezone() -> None:
    """Регион с особой зоной, забытый во фронтенде, покажет московское время."""
    source = (WEB_SRC / "region-timezones.ts").read_text(encoding="utf-8")
    frontend = _parse_ts_timezones(source)
    default = regions.DEFAULT_TIMEZONE

    missing = sorted(
        region.slug
        for region in regions.all_regions()
        if region.timezone != default and region.slug not in frontend
    )
    assert missing == []


def test_data_files_are_utf8_without_bom() -> None:
    """BOM ломает разбор JSON частью инструментов."""
    for path in sorted(DATA_DIR.glob("*.json")):
        assert not path.read_bytes().startswith(b"\xef\xbb\xbf"), path.name


def test_data_files_are_packaged() -> None:
    """Справочники обязаны попадать в дистрибутив вместе с кодом."""
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "data/*.json" in pyproject or "avito_monitor.data" in pyproject


def test_no_stray_files_in_data_dir() -> None:
    unexpected = [path.name for path in DATA_DIR.iterdir() if path.suffix != ".json"]
    assert unexpected == []


def test_web_src_has_no_leftover_scratch_files() -> None:
    leftovers = [
        path.name
        for path in Path(WEB_SRC).iterdir()
        if path.suffix in {".bak", ".orig", ".tmp"} or path.name.endswith(".old.ts")
    ]
    assert leftovers == []
