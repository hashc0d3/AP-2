"""Распознавание модели iPhone по названию объявления.

Продавцы пишут название как угодно: «iPhone 13 Pro Max», «айфон 14 про»,
«iPhone XS Max». Модуль сводит это к идентификатору из каталога
``avito_monitor/data/iphone_models.json`` — того же, по которому строятся
фильтры в ссылке Avito и чекбоксы в интерфейсе.

Термины:

* **поколение** (``gen``) — номер линейки: 13, 14, …; X/XS/XR считаем 10-м;
* **идентификатор модели** (``model id``) — поколение с вариантом:
  ``13``, ``13-pro``, ``13-pro-max``, ``16-e``, ``17-air``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache

from avito_monitor.paths import DATA_DIR

DATA_PATH = DATA_DIR / "iphone_models.json"

# Первое поколение, для которого Avito различает варианты в фильтрах.
MIN_SELECTABLE_GEN = 11

_IPHONE = r"(?:iphone|айфон|iphone'|айфон')"
_NUMBER = re.compile(rf"{_IPHONE}\s*(\d{{1,2}})(?:\s|$|[^0-9])", re.IGNORECASE)
_SIX = re.compile(rf"{_IPHONE}\s*6\b|\b6\s*plus\b", re.IGNORECASE)
_SIX_S = re.compile(r"\b6s\b", re.IGNORECASE)
_X_SERIES = re.compile(rf"{_IPHONE}\s*(?:xs\s*max|xs|xr)\b", re.IGNORECASE)
# Латинская и кириллическая «X»: «iPhone X 64GB», «айфон х».
_X_BARE = re.compile(rf"{_IPHONE}\s*[xх](?:\s|$|[^a-zа-яs])", re.IGNORECASE)
_SE = re.compile(rf"{_IPHONE}\s*se\b", re.IGNORECASE)

_PRO_MAX = re.compile(r"(?:pro\s*max|promax|про\s*макс|промакс)\b", re.IGNORECASE)
_PRO = re.compile(r"(?:\bpro\b|\bпро\b)", re.IGNORECASE)
_PLUS = re.compile(r"(?:\bplus\b|\bплюс\b)", re.IGNORECASE)
_MINI = re.compile(r"(?:\bmini\b|\bмини\b)", re.IGNORECASE)
_AIR = re.compile(r"\bair\b", re.IGNORECASE)
_16E = re.compile(r"(?:\b16\s*e\b|\b16e\b)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class IphoneModel:
    id: str
    gen: int
    label: str
    avito_value: int
    """Значение параметра модели в ссылке Avito."""

    def as_dict(self) -> dict[str, str | int]:
        return {"id": self.id, "gen": self.gen, "label": self.label}


@lru_cache(maxsize=1)
def _models() -> tuple[IphoneModel, ...]:
    rows = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return tuple(
        IphoneModel(
            id=str(row["id"]),
            gen=int(row["gen"]),
            label=str(row["label"]),
            avito_value=int(row["avito_value"]),
        )
        for row in rows
    )


@lru_cache(maxsize=1)
def _ids() -> frozenset[str]:
    return frozenset(model.id for model in _models())


@lru_cache(maxsize=1)
def _ids_by_gen() -> dict[int, tuple[str, ...]]:
    grouped: dict[int, list[str]] = {}
    for model in _models():
        grouped.setdefault(model.gen, []).append(model.id)
    return {gen: tuple(ids) for gen, ids in grouped.items()}


def all_models() -> tuple[IphoneModel, ...]:
    return _models()


def model_ids() -> frozenset[str]:
    return _ids()


def find_model(model_id: str) -> IphoneModel | None:
    return next((model for model in _models() if model.id == model_id), None)


def _normalize(title: str) -> str:
    return (title or "").lower().replace("ё", "е")


def detect_generation(title: str) -> int | None:
    """Поколение iPhone из названия; ``None`` — не iPhone или модель не понятна.

    SE сознательно не поддерживается: у него номер линейки не совпадает с
    поколением железа, и в фильтрах Avito он отдельная позиция.
    """
    text = _normalize(title)
    if not re.search(_IPHONE, text, re.IGNORECASE):
        return None

    # 6 и 6s проверяем отдельно: в этих названиях цифра часто отделена от
    # слова «iPhone» запятой, и общий шаблон её не видит.
    if _SIX_S.search(text) or _SIX.search(text):
        return 6

    match = _NUMBER.search(text)
    if match:
        return int(match.group(1))

    if _X_SERIES.search(text) or _X_BARE.search(text):
        return 10

    if _SE.search(text):
        return None
    return None


def _variant(text: str, gen: int) -> str:
    """Приставка варианта модели: ``pro-max``, ``pro``, ``plus``, …"""
    if _PRO_MAX.search(text):
        return "pro-max"
    if _PRO.search(text):
        return "pro"
    if gen >= 14 and _PLUS.search(text):
        return "plus"
    if gen in {12, 13} and _MINI.search(text):
        return "mini"
    if gen == 16 and _16E.search(text):
        return "e"
    if gen == 17 and _AIR.search(text):
        return "air"
    return ""


def detect_model_id(title: str) -> str | None:
    """Идентификатор модели из каталога; ``None`` — нет в каталоге."""
    gen = detect_generation(title)
    if gen is None or gen < MIN_SELECTABLE_GEN:
        return None
    variant = _variant(_normalize(title), gen)
    model_id = f"{gen}-{variant}" if variant else str(gen)
    return model_id if model_id in _ids() else None


def generation_in_range(title: str, min_gen: int, max_gen: int = 0) -> bool:
    """Попадает ли поколение в диапазон. ``min_gen <= 0`` — фильтр выключен."""
    if min_gen <= 0:
        return True
    gen = detect_generation(title)
    if gen is None or gen < min_gen:
        return False
    return not (max_gen > 0 and gen > max_gen)


def _to_model_id(raw: object) -> str | None:
    text = str(raw or "").strip().lower()
    if not text:
        return None
    if text in _ids():
        return text
    # Единственное написание, которое встречается в старых настройках.
    return "16-e" if text == "16e" else None


def normalize_models(values: object) -> tuple[str, ...] | None:
    """Привести выбор моделей к идентификаторам каталога.

    ``None`` на входе означает «фильтр по моделям не задан» и возвращается
    как есть. Число трактуется как поколение целиком: ``13`` разворачивается
    во все варианты 13-й линейки — так выглядели настройки до появления
    вариантов Pro/Plus.
    """
    if values is None:
        return None
    if not isinstance(values, (list, tuple)):
        return ()

    out: list[str] = []
    seen: set[str] = set()

    def add(model_id: str) -> None:
        if model_id not in seen:
            seen.add(model_id)
            out.append(model_id)

    for raw in values:
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int):
            for model_id in _ids_by_gen().get(raw, ()):
                add(model_id)
            continue
        model_id = _to_model_id(raw)
        if model_id:
            add(model_id)
    return tuple(out)


def model_allowed(
    title: str,
    allowed: tuple[str, ...] | list[str] | None,
    *,
    min_gen: int = 0,
    max_gen: int = 0,
) -> bool:
    """Проходит ли объявление фильтр моделей.

    Пустой (но заданный) список моделей означает «ничего не показывать» —
    в интерфейсе это состояние «снял все галочки».
    """
    if allowed is None:
        return generation_in_range(title, min_gen, max_gen)
    if not allowed:
        return False
    model_id = detect_model_id(title)
    return model_id is not None and model_id in set(allowed)


def is_full_selection(model_ids: tuple[str, ...] | list[str] | None) -> bool:
    """Выбраны ли все модели каталога (11 и новее).

    Это не «любой iPhone на Avito»: без параметров в ссылке туда попадают
    X, XS, SE и старше.
    """
    if not model_ids:
        return True
    return set(model_ids) == _ids()
