"""Поиск Avito: сбор web URL, регионы и преобразование в API URL через сервис ресурса."""

from __future__ import annotations

import threading
import time
from urllib.parse import quote

import requests
from loguru import logger

SPFA_AVITO_URL = "https://spfa.pro/api/avito-url/"

REGIONS: list[dict[str, str]] = [
    {"slug": "all", "name": "Вся Россия"},
    {"slug": "moskva", "name": "Москва"},
    {"slug": "moskovskaya_oblast", "name": "Московская область"},
    {"slug": "sankt-peterburg", "name": "Санкт-Петербург"},
    {"slug": "leningradskaya_oblast", "name": "Ленинградская область"},
    {"slug": "abakan", "name": "Абакан"},
    {"slug": "anapa", "name": "Анапа"},
    {"slug": "angarsk", "name": "Ангарск"},
    {"slug": "arkhangelsk", "name": "Архангельск"},
    {"slug": "armavir", "name": "Армавир"},
    {"slug": "astrakhan", "name": "Астрахань"},
    {"slug": "balashikha", "name": "Балашиха"},
    {"slug": "barnaul", "name": "Барнаул"},
    {"slug": "belgorod", "name": "Белгород"},
    {"slug": "biysk", "name": "Бийск"},
    {"slug": "blagoveshchensk", "name": "Благовещенск"},
    {"slug": "bratsk", "name": "Братск"},
    {"slug": "bryansk", "name": "Брянск"},
    {"slug": "velikiy_novgorod", "name": "Великий Новгород"},
    {"slug": "vidnoe", "name": "Видное"},
    {"slug": "vladivostok", "name": "Владивосток"},
    {"slug": "vladikavkaz", "name": "Владикавказ"},
    {"slug": "vladimir", "name": "Владимир"},
    {"slug": "volgograd", "name": "Волгоград"},
    {"slug": "volzhskiy", "name": "Волжский"},
    {"slug": "vologda", "name": "Вологда"},
    {"slug": "voronezh", "name": "Воронеж"},
    {"slug": "gorno-altaysk", "name": "Горно-Алтайск"},
    {"slug": "grozny", "name": "Грозный"},
    {"slug": "dzerzhinsk", "name": "Дзержинск"},
    {"slug": "domodedovo", "name": "Домодедово"},
    {"slug": "ekaterinburg", "name": "Екатеринбург"},
    {"slug": "essentuki", "name": "Ессентуки"},
    {"slug": "zheleznodorozhnyy", "name": "Железнодорожный"},
    {"slug": "zhukovskiy", "name": "Жуковский"},
    {"slug": "ivanovo", "name": "Иваново"},
    {"slug": "izhevsk", "name": "Ижевск"},
    {"slug": "irkutsk", "name": "Иркутск"},
    {"slug": "yoshkar-ola", "name": "Йошкар-Ола"},
    {"slug": "kazan", "name": "Казань"},
    {"slug": "kaliningrad", "name": "Калининград"},
    {"slug": "kaluga", "name": "Калуга"},
    {"slug": "kamensk-uralskiy", "name": "Каменск-Уральский"},
    {"slug": "kemerovo", "name": "Кемерово"},
    {"slug": "kerch", "name": "Керчь"},
    {"slug": "kislovodsk", "name": "Кисловодск"},
    {"slug": "klin", "name": "Клин"},
    {"slug": "kovrov", "name": "Ковров"},
    {"slug": "kolomna", "name": "Коломна"},
    {"slug": "komsomolsk-na-amure", "name": "Комсомольск-на-Амуре"},
    {"slug": "korolev", "name": "Королёв"},
    {"slug": "kostroma", "name": "Кострома"},
    {"slug": "krasnogorsk", "name": "Красногорск"},
    {"slug": "krasnodar", "name": "Краснодар"},
    {"slug": "krasnodarskiy_kray", "name": "Краснодарский край"},
    {"slug": "krasnoyarsk", "name": "Красноярск"},
    {"slug": "kurgan", "name": "Курган"},
    {"slug": "kursk", "name": "Курск"},
    {"slug": "lipeck", "name": "Липецк"},
    {"slug": "lyubertsy", "name": "Люберцы"},
    {"slug": "magnitogorsk", "name": "Магнитогорск"},
    {"slug": "maykop", "name": "Майкоп"},
    {"slug": "mahachkala", "name": "Махачкала"},
    {"slug": "mineralnye_vody", "name": "Минеральные Воды"},
    {"slug": "murmansk", "name": "Мурманск"},
    {"slug": "murom", "name": "Муром"},
    {"slug": "mytischi", "name": "Мытищи"},
    {"slug": "naberezhnye_chelny", "name": "Набережные Челны"},
    {"slug": "nalchik", "name": "Нальчик"},
    {"slug": "naro-fominsk", "name": "Наро-Фоминск"},
    {"slug": "nahodka", "name": "Находка"},
    {"slug": "nevinnomyssk", "name": "Невинномысск"},
    {"slug": "neftekamsk", "name": "Нефтекамск"},
    {"slug": "nefteyugansk", "name": "Нефтеюганск"},
    {"slug": "nizhnevartovsk", "name": "Нижневартовск"},
    {"slug": "nizhnekamsk", "name": "Нижнекамск"},
    {"slug": "nizhniy_novgorod", "name": "Нижний Новгород"},
    {"slug": "nizhniy_tagil", "name": "Нижний Тагил"},
    {"slug": "novokuznetsk", "name": "Новокузнецк"},
    {"slug": "novorossiysk", "name": "Новороссийск"},
    {"slug": "novosibirsk", "name": "Новосибирск"},
    {"slug": "novyy_urengoy", "name": "Новый Уренгой"},
    {"slug": "noginsk", "name": "Ногинск"},
    {"slug": "norilsk", "name": "Норильск"},
    {"slug": "noyabrsk", "name": "Ноябрьск"},
    {"slug": "obninsk", "name": "Обнинск"},
    {"slug": "odintsovo", "name": "Одинцово"},
    {"slug": "omsk", "name": "Омск"},
    {"slug": "orel", "name": "Орёл"},
    {"slug": "orenburg", "name": "Оренбург"},
    {"slug": "orehovo-zuevo", "name": "Орехово-Зуево"},
    {"slug": "orsk", "name": "Орск"},
    {"slug": "penza", "name": "Пенза"},
    {"slug": "perm", "name": "Пермь"},
    {"slug": "petrozavodsk", "name": "Петрозаводск"},
    {"slug": "petropavlovsk-kamchatskiy", "name": "Петропавловск-Камчатский"},
    {"slug": "podolsk", "name": "Подольск"},
    {"slug": "pskov", "name": "Псков"},
    {"slug": "pushkino", "name": "Пушкино"},
    {"slug": "pyatigorsk", "name": "Пятигорск"},
    {"slug": "ramenskoe", "name": "Раменское"},
    {"slug": "rostov-na-donu", "name": "Ростов-на-Дону"},
    {"slug": "rubtsovsk", "name": "Рубцовск"},
    {"slug": "ryazan", "name": "Рязань"},
    {"slug": "samara", "name": "Самара"},
    {"slug": "saransk", "name": "Саранск"},
    {"slug": "saratov", "name": "Саратов"},
    {"slug": "sevastopol", "name": "Севастополь"},
    {"slug": "severodvinsk", "name": "Северодвинск"},
    {"slug": "sergiev_posad", "name": "Сергиев Посад"},
    {"slug": "serpuhov", "name": "Серпухов"},
    {"slug": "simferopol", "name": "Симферополь"},
    {"slug": "smolensk", "name": "Смоленск"},
    {"slug": "sochi", "name": "Сочи"},
    {"slug": "stavropol", "name": "Ставрополь"},
    {"slug": "staryy_oskol", "name": "Старый Оскол"},
    {"slug": "sterlitamak", "name": "Стерлитамак"},
    {"slug": "surgut", "name": "Сургут"},
    {"slug": "syzran", "name": "Сызрань"},
    {"slug": "syktyvkar", "name": "Сыктывкар"},
    {"slug": "taganrog", "name": "Таганрог"},
    {"slug": "tambov", "name": "Тамбов"},
    {"slug": "tver", "name": "Тверь"},
    {"slug": "tolyatti", "name": "Тольятти"},
    {"slug": "tomsk", "name": "Томск"},
    {"slug": "tula", "name": "Тула"},
    {"slug": "tyumen", "name": "Тюмень"},
    {"slug": "ulan-ude", "name": "Улан-Удэ"},
    {"slug": "ulyanovsk", "name": "Ульяновск"},
    {"slug": "ussuriysk", "name": "Уссурийск"},
    {"slug": "ufa", "name": "Уфа"},
    {"slug": "khabarovsk", "name": "Хабаровск"},
    {"slug": "khanty-mansiysk", "name": "Ханты-Мансийск"},
    {"slug": "himki", "name": "Химки"},
    {"slug": "cheboksary", "name": "Чебоксары"},
    {"slug": "chelyabinsk", "name": "Челябинск"},
    {"slug": "cherepovets", "name": "Череповец"},
    {"slug": "cherkessk", "name": "Черкесск"},
    {"slug": "chita", "name": "Чита"},
    {"slug": "shchelkovo", "name": "Щёлково"},
    {"slug": "elektrostal", "name": "Электросталь"},
    {"slug": "engels", "name": "Энгельс"},
    {"slug": "yuzhno-sakhalinsk", "name": "Южно-Сахалинск"},
    {"slug": "yakutsk", "name": "Якутск"},
    {"slug": "yaroslavl", "name": "Ярославль"},
]

_REGION_BY_SLUG = {item["slug"]: item for item in REGIONS}
DEFAULT_REGION = REGIONS[0]

CATEGORIES: list[dict] = [
    {"id": "none", "name": "Без категории", "path": "", "extra": []},
    {"id": "electronics", "name": "Электроника", "path": "bytovaya_elektronika", "extra": []},
    {"id": "phones", "name": "Смартфоны", "path": "telefony/mobile-ASgBAgICAUSwwQ2I_Dc", "extra": ["cd=1"]},
]
_CATEGORY_BY_ID = {item["id"]: item for item in CATEGORIES}
DEFAULT_CATEGORY = CATEGORIES[0]

_lock = threading.RLock()
_changed = threading.Event()
_state: dict = {
    "generation": 0,
    "running": False,
    "query": "",
    "region": dict(DEFAULT_REGION),
    "category": {"id": DEFAULT_CATEGORY["id"], "name": DEFAULT_CATEGORY["name"]},
    "web_url": "",
    "api_url": "",
    "error": "",
    "seller_skip": [],
    "search_mode": "query",
    "iphone_models": None,
}


def _public_category(item: dict) -> dict[str, str]:
    return {"id": item["id"], "name": item["name"]}


def snapshot() -> dict:
    with _lock:
        return {
            "generation": _state["generation"],
            "running": _state["running"],
            "query": _state["query"],
            "region": dict(_state["region"]),
            "category": dict(_state["category"]),
            "web_url": _state["web_url"],
            "api_url": _state["api_url"],
            "error": _state["error"],
            "seller_skip": list(_state["seller_skip"]),
            "search_mode": _state["search_mode"],
            "iphone_models": list(_state["iphone_models"]) if _state["iphone_models"] is not None else None,
        }


def set_seller_skip(sellers: list) -> dict:
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in sellers or []:
        text = str(item).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    with _lock:
        _state["seller_skip"] = cleaned
    _changed.set()
    return snapshot()


def find_category(category_id: str) -> dict:
    key = (category_id or DEFAULT_CATEGORY["id"]).strip().lower()
    if key in _CATEGORY_BY_ID:
        return dict(_CATEGORY_BY_ID[key])
    raise ValueError(f"Неизвестная категория: {category_id}")


def list_categories() -> list[dict[str, str]]:
    return [_public_category(item) for item in CATEGORIES]


def find_region(slug: str) -> dict[str, str]:
    key = (slug or "").strip().lower()
    if key in _REGION_BY_SLUG:
        return dict(_REGION_BY_SLUG[key])
    raise ValueError(f"Неизвестный регион: {slug}")


def search_regions(query: str, limit: int = 20) -> list[dict[str, str]]:
    needle = (query or "").strip().lower().replace("ё", "е")
    if not needle:
        return [dict(item) for item in REGIONS[:limit]]
    scored: list[tuple[int, dict[str, str]]] = []
    for item in REGIONS:
        name = item["name"].lower().replace("ё", "е")
        slug = item["slug"].lower()
        if name.startswith(needle):
            scored.append((0, item))
        elif needle in name:
            scored.append((1, item))
        elif needle in slug:
            scored.append((2, item))
    scored.sort(key=lambda pair: (pair[0], pair[1]["name"]))
    return [dict(item) for _, item in scored[:limit]]


def build_web_url(query: str, region_slug: str, category_id: str = "") -> str:
    slug = (region_slug or DEFAULT_REGION["slug"]).strip("/") or DEFAULT_REGION["slug"]
    category = find_category(category_id) if category_id else dict(DEFAULT_CATEGORY)
    path = "/".join(part for part in (slug, category.get("path") or "") if part)
    parts = list(category.get("extra") or [])
    parts.extend(["s=104", "owner[]=private"])
    text = (query or "").strip()
    if text:
        parts.insert(0, f"q={quote(text)}")
    return f"https://www.avito.ru/{path}?{'&'.join(parts)}"


def convert_to_api_url(web_url: str) -> str:
    try:
        response = requests.post(
            SPFA_AVITO_URL,
            json={"url": web_url},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=40,
        )
    except requests.RequestException as err:
        raise RuntimeError(f"Не удалось связаться с ресурсом: {err}") from err
    if response.status_code == 429:
        raise RuntimeError("Лимит ресурса: подождите минуту и нажмите ещё раз (2 запроса в минуту)")
    if response.status_code == 400:
        raise RuntimeError("Сервис не принял ссылку Avito")
    if not response.ok:
        raise RuntimeError(f"Сервис avito-url {response.status_code}: {response.text[:240]}")
    try:
        payload = response.json()
    except ValueError as err:
        raise RuntimeError("Сервис вернул не JSON") from err
    api_url = payload.get("api_url") if isinstance(payload, dict) else None
    if not payload.get("success") or not isinstance(api_url, str) or not api_url.startswith("http"):
        raise RuntimeError(f"Сервис не вернул api_url: {payload}")
    return api_url


def preview(query: str, region_slug: str, category_id: str = "") -> dict:
    region = find_region(region_slug) if region_slug else dict(DEFAULT_REGION)
    category = find_category(category_id) if category_id else dict(DEFAULT_CATEGORY)
    web_url = build_web_url(query, region["slug"], category["id"])
    return {
        "query": (query or "").strip(),
        "region": region,
        "category": _public_category(category),
        "web_url": web_url,
    }


def start_search(
    query: str,
    region_slug: str,
    category_id: str = "",
    seller_skip: list | None = None,
    iphone_models: list | None = None,
    *,
    mode: str = "query",
    web_url: str = "",
) -> dict:
    if mode == "url":
        link = (web_url or query).strip()
        if not link:
            raise ValueError("Укажите ссылку Avito")
        preview_data = {
            "query": link,
            "region": dict(DEFAULT_REGION),
            "category": {"id": "none", "name": "Без категории"},
            "web_url": link,
        }
    else:
        preview_data = preview(query, region_slug, category_id)
    web_url = preview_data["web_url"]
    logger.info(f"Преобразую web URL в API: {web_url}")
    api_url = convert_to_api_url(web_url)
    logger.info(f"API URL: {api_url}")
    with _lock:
        _state["generation"] += 1
        _state["running"] = True
        _state["search_mode"] = "url" if mode == "url" else "query"
        _state["query"] = preview_data["query"]
        _state["region"] = preview_data["region"]
        _state["category"] = preview_data["category"]
        _state["web_url"] = web_url
        _state["api_url"] = api_url
        _state["error"] = ""
        if seller_skip is not None:
            _state["seller_skip"] = [
                str(item).strip() for item in seller_skip if str(item).strip()
            ]
        if iphone_models is not None:
            from iphone_filter import normalize_allowed_models

            _state["iphone_models"] = normalize_allowed_models(iphone_models)
        generation = _state["generation"]
    _changed.set()
    return snapshot() | {"generation": generation}


def stop_search() -> dict:
    with _lock:
        if _state["running"]:
            _state["generation"] += 1
            _state["running"] = False
            logger.info("Мониторинг остановлен")
    _changed.set()
    return snapshot()


def wait_for_search(last_generation: int = 0) -> dict:
    while True:
        with _lock:
            if _state["running"] and _state["generation"] > last_generation:
                return snapshot()
            _changed.clear()
        _changed.wait(timeout=0.5)


def sleep_or_restart(seconds: float, generation: int) -> bool:
    """Пауза. False — пришёл новый поиск, текущий цикл нужно бросить."""
    deadline = time.time() + max(0.0, seconds)
    while True:
        left = deadline - time.time()
        if left <= 0:
            return True
        with _lock:
            if _state["generation"] != generation or not _state["running"]:
                return False
            _changed.clear()
        _changed.wait(timeout=min(0.4, left))


def apply_runtime(cfg: dict, search: dict) -> dict:
    runtime = dict(cfg)
    runtime["url"] = search["web_url"]
    runtime["api_url"] = search["api_url"]
    runtime["title_must_contain"] = list(cfg.get("title_must_contain") or [])
    runtime["title_skip"] = list(cfg.get("title_skip") or [])
    merged: list[str] = []
    seen: set[str] = set()
    for item in (cfg.get("seller_skip") or []) + (search.get("seller_skip") or []):
        text = str(item).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(text)
    runtime["seller_skip"] = merged
    if search.get("iphone_models") is not None:
        runtime["iphone_models"] = list(search["iphone_models"])
    return runtime
