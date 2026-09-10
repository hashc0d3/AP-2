"""Фильтр моделей iPhone в ссылках Avito.

Avito принимает выбранные модели как повторяющийся параметр
``params[<код>][<индекс>]``. Код параметра различается: у веб-поиска один,
у внутреннего JSON API — другой, хотя значения моделей совпадают.

Фильтровать в ссылке выгоднее, чем по названию: Avito сам не отдаёт лишние
объявления, и в выдаче остаётся больше нужных.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from avito_monitor.avito import iphone

# Код параметра «модель»: чекбоксы веб-поиска и JSON API нумеруются по-разному.
MODEL_PARAM_WEB = 121588
MODEL_PARAM_API = 110617
# «Тип товара: телефон» — без него Avito игнорирует фильтр моделей.
TYPE_PARAM = 110618
TYPE_VALUE = 469735

_API_PATH_MARKER = "/web/1/js/items"


def _model_param(url: str) -> int:
    return MODEL_PARAM_API if _API_PATH_MARKER in url else MODEL_PARAM_WEB


def models_to_avito_values(model_ids: tuple[str, ...] | list[str] | None) -> list[int]:
    """Коды моделей для ссылки Avito.

    Пустой список — фильтр не нужен: либо моделей не задано, либо выбраны
    все, и Avito отдаст ту же выдачу без лишних параметров.
    """
    normalized = iphone.normalize_models(model_ids)
    if not normalized or iphone.is_full_selection(normalized):
        return []
    values = []
    for model_id in normalized:
        model = iphone.find_model(model_id)
        if model is not None:
            values.append(model.avito_value)
    return values


def _without_model_params(query: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Убрать из запроса все параметры фильтра моделей (и веб-, и API-код)."""
    prefixes = (f"params[{MODEL_PARAM_WEB}]", f"params[{MODEL_PARAM_API}]")
    type_key = f"params[{TYPE_PARAM}]"
    return [
        (key, value) for key, value in query if key != type_key and not key.startswith(prefixes)
    ]


def _rebuild(url: str, query: list[tuple[str, str]]) -> str:
    split = urlsplit(url)
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


def strip_model_params(url: str) -> str:
    """Ссылка без фильтра моделей.

    Нужна как ключ кэша: внешний сервис конвертации всё равно не различает
    ссылки по набору моделей, а платить за повторный запрос не хочется.
    """
    query = parse_qsl(urlsplit(url).query, keep_blank_values=True)
    return _rebuild(url, _without_model_params(query))


def append_model_params(
    url: str, model_ids: tuple[str, ...] | list[str] | None
) -> tuple[str, bool]:
    """Добавить фильтр моделей к ссылке.

    Возвращает ``(ссылка, применён ли фильтр)``. Если фильтр применён,
    отсеивать модели по названию объявления уже не нужно.
    """
    values = models_to_avito_values(model_ids)
    if not values:
        return url, False

    param = _model_param(url)
    query = _without_model_params(parse_qsl(urlsplit(url).query, keep_blank_values=True))
    query.extend((f"params[{param}][{index}]", str(value)) for index, value in enumerate(values))
    query.append((f"params[{TYPE_PARAM}]", str(TYPE_VALUE)))
    return _rebuild(url, query), True


def has_model_params(url: str) -> bool:
    """Есть ли в ссылке фильтр моделей — например, вставленной пользователем."""
    query = urlsplit(url).query
    return any(
        f"params%5B{param}%5D" in query or f"params[{param}]" in query
        for param in (MODEL_PARAM_WEB, MODEL_PARAM_API)
    )


def retarget_web_model_params(url: str) -> str:
    """Переписать веб-коды моделей в коды JSON API.

    Вставленная ссылка несёт ``params[121588]``, а ``/web/1/js/items``
    понимает ``params[110617]``. Без замены фильтр моделей на API молчит.
    """
    if _API_PATH_MARKER not in url or not has_model_params(url):
        return url
    query = parse_qsl(urlsplit(url).query, keep_blank_values=True)
    values: list[str] = []
    kept: list[tuple[str, str]] = []
    type_key = f"params[{TYPE_PARAM}]"
    prefixes = (f"params[{MODEL_PARAM_WEB}]", f"params[{MODEL_PARAM_API}]")
    for key, value in query:
        if key == type_key or any(key.startswith(prefix) for prefix in prefixes):
            if key != type_key:
                values.append(value)
            continue
        kept.append((key, value))
    if not values:
        return url
    kept.extend(
        (f"params[{MODEL_PARAM_API}][{index}]", value) for index, value in enumerate(values)
    )
    kept.append((type_key, str(TYPE_VALUE)))
    return _rebuild(url, kept)
