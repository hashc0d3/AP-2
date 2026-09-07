"""Avito URL filters for iPhone models."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from iphone_filter import IPHONE_MODEL_OPTIONS, normalize_allowed_models

# Web UI checkboxes use 121588; items API expects 110617 (same value IDs).
IPHONE_MODEL_PARAM_WEB = 121588
IPHONE_MODEL_PARAM_API = 110617
IPHONE_TYPE_PARAM = 110618
IPHONE_TYPE_VALUE = 469735

AVITO_IPHONE_MODEL_VALUES: dict[str, int] = {
    "11": 468690,
    "11-pro": 461016,
    "11-pro-max": 468037,
    "12": 491645,
    "12-mini": 491622,
    "12-pro": 491594,
    "12-pro-max": 491590,
    "13": 1642358,
    "13-mini": 1642360,
    "13-pro": 1642359,
    "13-pro-max": 1642361,
    "14": 17605542,
    "14-plus": 17605549,
    "14-pro": 17605543,
    "14-pro-max": 17605548,
    "15": 18720777,
    "15-plus": 18720870,
    "15-pro": 18720713,
    "15-pro-max": 18720875,
    "16": 22121571,
    "16-plus": 22121572,
    "16-pro": 22121573,
    "16-pro-max": 22121574,
    "16-e": 22313350,
    "17": 22446489,
    "17-pro": 22446491,
    "17-pro-max": 22446492,
    "17-air": 22446490,
}

_ALL_MODEL_IDS = {str(item["id"]) for item in IPHONE_MODEL_OPTIONS}


def _model_param_for_url(url: str) -> int:
    if "/web/1/js/items" in url:
        return IPHONE_MODEL_PARAM_API
    return IPHONE_MODEL_PARAM_WEB


def is_full_iphone_selection(model_ids: list[str] | None) -> bool:
    if not model_ids:
        return True
    return set(model_ids) == _ALL_MODEL_IDS


def models_to_avito_values(model_ids: list[str] | None) -> list[int]:
    normalized = normalize_allowed_models(model_ids)
    if not normalized or is_full_iphone_selection(normalized):
        return []
    order = {model_id: index for index, model_id in enumerate(normalized)}
    pairs: list[tuple[int, str]] = []
    for model_id in normalized:
        value = AVITO_IPHONE_MODEL_VALUES.get(model_id)
        if value is None:
            continue
        pairs.append((order[model_id], model_id))
    pairs.sort(key=lambda item: item[0])
    return [AVITO_IPHONE_MODEL_VALUES[model_id] for _, model_id in pairs]


def _strip_model_query(query_items: list[tuple[str, str]], model_param: int | None = None) -> list[tuple[str, str]]:
    prefixes = {f"params[{IPHONE_MODEL_PARAM_WEB}]", f"params[{IPHONE_MODEL_PARAM_API}]"}
    if model_param is not None:
        prefixes = {f"params[{model_param}]"}
    type_key = f"params[{IPHONE_TYPE_PARAM}]"
    return [
        (key, value)
        for key, value in query_items
        if not any(key.startswith(prefix) for prefix in prefixes) and key != type_key
    ]


def strip_iphone_model_params(url: str) -> str:
    split = urlsplit(url)
    query_items = _strip_model_query(parse_qsl(split.query, keep_blank_values=True))
    return urlunsplit(
        (split.scheme, split.netloc, split.path, urlencode(query_items), split.fragment)
    )


def append_iphone_model_params(url: str, model_ids: list[str] | None) -> tuple[str, bool]:
    """Append Avito model filters to a web or API URL. Returns (url, applied)."""
    values = models_to_avito_values(model_ids)
    if not values:
        return url, False

    model_param = _model_param_for_url(url)
    split = urlsplit(url)
    query_items = _strip_model_query(parse_qsl(split.query, keep_blank_values=True))
    for index, value in enumerate(values):
        query_items.append((f"params[{model_param}][{index}]", str(value)))
    query_items.append((f"params[{IPHONE_TYPE_PARAM}]", str(IPHONE_TYPE_VALUE)))
    updated = urlunsplit(
        (split.scheme, split.netloc, split.path, urlencode(query_items), split.fragment)
    )
    return updated, True


def iphone_model_params_present(url: str) -> bool:
    query = urlsplit(url).query
    for param in (IPHONE_MODEL_PARAM_WEB, IPHONE_MODEL_PARAM_API):
        if f"params%5B{param}%5D" in query or f"params[{param}]" in query:
            return True
    return False
