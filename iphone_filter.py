"""Фильтр объявлений по модели iPhone."""

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
_PRO_MAX = re.compile(r"(?:pro\s*max|promax|про\s*макс|промакс)\b", re.IGNORECASE)
_PRO = re.compile(r"(?:\bpro\b|\bпро\b)", re.IGNORECASE)
_PLUS = re.compile(r"(?:\bplus\b|\bплюс\b)", re.IGNORECASE)
_MINI = re.compile(r"(?:\bmini\b|\bмини\b)", re.IGNORECASE)
_AIR = re.compile(r"\bair\b", re.IGNORECASE)
_16E = re.compile(r"(?:\b16\s*e\b|\b16e\b)", re.IGNORECASE)


def _normalize(title: str) -> str:
    return (title or "").lower().replace("ё", "е")


IPHONE_MODEL_OPTIONS: list[dict[str, str | int]] = [
    {"id": "11", "gen": 11, "label": "iPhone 11"},
    {"id": "11-pro", "gen": 11, "label": "iPhone 11 Pro"},
    {"id": "11-pro-max", "gen": 11, "label": "iPhone 11 Pro Max"},
    {"id": "12", "gen": 12, "label": "iPhone 12"},
    {"id": "12-mini", "gen": 12, "label": "iPhone 12 mini"},
    {"id": "12-pro", "gen": 12, "label": "iPhone 12 Pro"},
    {"id": "12-pro-max", "gen": 12, "label": "iPhone 12 Pro Max"},
    {"id": "13", "gen": 13, "label": "iPhone 13"},
    {"id": "13-mini", "gen": 13, "label": "iPhone 13 mini"},
    {"id": "13-pro", "gen": 13, "label": "iPhone 13 Pro"},
    {"id": "13-pro-max", "gen": 13, "label": "iPhone 13 Pro Max"},
    {"id": "14", "gen": 14, "label": "iPhone 14"},
    {"id": "14-plus", "gen": 14, "label": "iPhone 14 Plus"},
    {"id": "14-pro", "gen": 14, "label": "iPhone 14 Pro"},
    {"id": "14-pro-max", "gen": 14, "label": "iPhone 14 Pro Max"},
    {"id": "15", "gen": 15, "label": "iPhone 15"},
    {"id": "15-plus", "gen": 15, "label": "iPhone 15 Plus"},
    {"id": "15-pro", "gen": 15, "label": "iPhone 15 Pro"},
    {"id": "15-pro-max", "gen": 15, "label": "iPhone 15 Pro Max"},
    {"id": "16", "gen": 16, "label": "iPhone 16"},
    {"id": "16-plus", "gen": 16, "label": "iPhone 16 Plus"},
    {"id": "16-pro", "gen": 16, "label": "iPhone 16 Pro"},
    {"id": "16-pro-max", "gen": 16, "label": "iPhone 16 Pro Max"},
    {"id": "16-e", "gen": 16, "label": "iPhone 16e"},
    {"id": "17", "gen": 17, "label": "iPhone 17"},
    {"id": "17-pro", "gen": 17, "label": "iPhone 17 Pro"},
    {"id": "17-pro-max", "gen": 17, "label": "iPhone 17 Pro Max"},
    {"id": "17-air", "gen": 17, "label": "iPhone Air"},
]

_VALID_IDS = {str(item["id"]) for item in IPHONE_MODEL_OPTIONS}
_VALID_GENS = {int(item["gen"]) for item in IPHONE_MODEL_OPTIONS}
_IDS_BY_GEN: dict[int, list[str]] = {}
for item in IPHONE_MODEL_OPTIONS:
    gen = int(item["gen"])
    _IDS_BY_GEN.setdefault(gen, []).append(str(item["id"]))


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


def _variant_suffix(text: str, gen: int) -> str:
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
    return "base"


def detect_iphone_model_id(title: str) -> str | None:
    gen = detect_iphone_generation(title)
    if gen is None or gen < 11:
        return None
    suffix = _variant_suffix(_normalize(title), gen)
    if suffix == "base":
        model_id = str(gen)
    elif suffix == "e":
        model_id = f"{gen}-e"
    else:
        model_id = f"{gen}-{suffix}"
    return model_id if model_id in _VALID_IDS else None


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


def _expand_legacy_gen(gen: int) -> list[str]:
    return list(_IDS_BY_GEN.get(gen, []))


def _raw_to_model_id(raw) -> str | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        if raw in _VALID_GENS:
            expanded = _expand_legacy_gen(raw)
            return expanded[0] if len(expanded) == 1 else None
        return None
    text = str(raw or "").strip().lower()
    if not text:
        return None
    if text in _VALID_IDS:
        return text
    if text.isdigit():
        return text if text in _VALID_IDS else None
    if text in {"16e", "16-e"}:
        return "16-e"
    if text.endswith("-pro-max") and text in _VALID_IDS:
        return text
    if text.endswith("-pro") and text in _VALID_IDS:
        return text
    if text.endswith("-plus") and text in _VALID_IDS:
        return text
    if text.endswith("-mini") and text in _VALID_IDS:
        return text
    if text.endswith("-air") and text in _VALID_IDS:
        return text
    if text.endswith("-e") and text in _VALID_IDS:
        return text
    head = text.split("-", 1)[0]
    if head.isdigit():
        gen = int(head)
        if gen in _VALID_GENS and text in _VALID_IDS:
            return text
    return None


def normalize_allowed_models(values) -> list[str] | None:
    if values is None:
        return None
    if not isinstance(values, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        if isinstance(raw, int):
            for model_id in _expand_legacy_gen(int(raw)):
                if model_id in seen:
                    continue
                seen.add(model_id)
                out.append(model_id)
            continue
        model_id = _raw_to_model_id(raw)
        if model_id is None or model_id in seen:
            continue
        seen.add(model_id)
        out.append(model_id)
    return out


def iphone_model_allowed(
    title: str,
    allowed: list[str] | None,
    *,
    min_gen: int = 0,
    max_gen: int = 0,
) -> bool:
    if allowed is not None:
        if not allowed:
            return False
        model_id = detect_iphone_model_id(title)
        return model_id is not None and model_id in set(allowed)
    return iphone_model_matches(title, min_gen, max_gen)
