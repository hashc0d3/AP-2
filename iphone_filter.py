"""Фильтр объявлений по поколению iPhone."""

from __future__ import annotations

import re

_IPHONE = r"(?:iphone|айфон|iphone'|айфон')"
_NUMBER = re.compile(rf"{_IPHONE}\s*(\d{{1,2}})(?:\s|$|[^0-9])", re.IGNORECASE)
_XS_MAX = re.compile(rf"{_IPHONE}\s*xs\s*max\b", re.IGNORECASE)
_XS = re.compile(rf"{_IPHONE}\s*xs\b", re.IGNORECASE)
_XR = re.compile(rf"{_IPHONE}\s*xr\b", re.IGNORECASE)
_X = re.compile(rf"{_IPHONE}\s*x(?:\s|$|[^a-zа-яs])", re.IGNORECASE)
_X_CYR = re.compile(rf"{_IPHONE}\s*х(?:\s|$|[^a-zа-яs])", re.IGNORECASE)
_SE = re.compile(rf"{_IPHONE}\s*se\b", re.IGNORECASE)


def _normalize(title: str) -> str:
    return (title or "").lower().replace("ё", "е")


IPHONE_MODEL_OPTIONS: list[dict[str, str | int]] = [
    {"id": "6", "gen": 6, "label": "6"},
    {"id": "7", "gen": 7, "label": "7"},
    {"id": "8", "gen": 8, "label": "8"},
    {"id": "10", "gen": 10, "label": "X"},
    {"id": "11", "gen": 11, "label": "11"},
    {"id": "12", "gen": 12, "label": "12"},
    {"id": "13", "gen": 13, "label": "13"},
    {"id": "14", "gen": 14, "label": "14"},
    {"id": "15", "gen": 15, "label": "15"},
    {"id": "16", "gen": 16, "label": "16"},
    {"id": "17", "gen": 17, "label": "17"},
]
_VALID_GENS = {int(item["gen"]) for item in IPHONE_MODEL_OPTIONS}


def detect_iphone_generation(title: str) -> int | None:
    """Номер модели: 6, 7, … 17; X/XS/XR → 10. Pro/Plus/mini того же номера."""
    text = _normalize(title)
    if not re.search(_IPHONE, text, re.IGNORECASE):
        return None

    if _has_6s(text):
        return 6
    if re.search(rf"{_IPHONE}\s*6\b", text) or re.search(r"\b6\s*plus\b", text):
        return 6

    match = _NUMBER.search(text)
    if match:
        return int(match.group(1))

    if _XS_MAX.search(text) or _XS.search(text) or _XR.search(text):
        return 10
    if _X.search(text) or _X_CYR.search(text):
        return 10

    if _SE.search(text):
        return None

    return None


def _has_6s(text: str) -> bool:
    return bool(re.search(r"\b6s\b", text))


def iphone_model_matches(title: str, min_gen: int, max_gen: int = 0) -> bool:
    if min_gen <= 0:
        return True
    gen = detect_iphone_generation(title)
    if gen is None:
        return False
    if gen < min_gen:
        return False
    if max_gen > 0 and gen > max_gen:
        return False
    return True


def _raw_to_gen(raw) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw in _VALID_GENS else None
    text = str(raw or "").strip().lower()
    if not text:
        return None
    if text.isdigit():
        gen = int(text)
        return gen if gen in _VALID_GENS else None
    if text in {"x", "xs", "xr", "xs-max", "10-x"}:
        return 10
    if text.startswith("6s") or text.startswith("6-"):
        return 6
    head = text.split("-", 1)[0]
    if head.isdigit():
        gen = int(head)
        return gen if gen in _VALID_GENS else None
    return None


def normalize_allowed_models(values) -> list[int] | None:
    if values is None:
        return None
    if not isinstance(values, list):
        return []
    out: list[int] = []
    seen: set[int] = set()
    for raw in values:
        gen = _raw_to_gen(raw)
        if gen is None or gen in seen:
            continue
        seen.add(gen)
        out.append(gen)
    return out


def iphone_model_allowed(
    title: str,
    allowed: list[int] | None,
    *,
    min_gen: int = 0,
    max_gen: int = 0,
) -> bool:
    if allowed is not None:
        if not allowed:
            return False
        gen = detect_iphone_generation(title)
        return gen is not None and gen in set(allowed)
    return iphone_model_matches(title, min_gen, max_gen)
