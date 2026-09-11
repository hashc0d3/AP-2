"""Настройки приложения: ``config.toml`` (поведение) + ``.env`` (секреты).

Все значения по умолчанию живут в :class:`Settings` — это единственный
источник правды. ``config.toml`` нужен только чтобы что-то переопределить,
поэтому приложение запускается и с неполным файлом.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, replace
from typing import Any

from loguru import logger

from avito_monitor.avito import catalog, iphone
from avito_monitor.paths import CONFIG_PATH, ENV_PATH

# Секреты и доступы читаются только из окружения, не из config.toml.
_SECRET_ENV_KEYS = {
    "proxy_string": "PROXY_STRING",
    "proxy_change_url": "PROXY_CHANGE_URL",
    "proxy_string_2": "PROXY_STRING_2",
    "proxy_change_url_2": "PROXY_CHANGE_URL_2",
    "proxy_string_3": "PROXY_STRING_3",
    "proxy_change_url_3": "PROXY_CHANGE_URL_3",
    "cookies_api_key": "COOKIES_API_KEY",
}

# Поля Settings держат первые три канала; остальные читаются из
# PROXY_STRING_4 … PROXY_STRING_12, чтобы докупить прокси без правки кода.
_MAX_PROXY_SLOTS = 12

# Старое имя ключа -> актуальное поле, чтобы не ломать существующие config.toml.
_KEY_ALIASES = {"pause_general": "retry_pause"}

# Ключи, которые раньше были в примере конфига и больше ни на что не влияют.
_OBSOLETE_KEYS = frozenset(
    {"url", "api_url", "pause_max", "socks5_proxy", "max_age", "notify_max_age"}
)


@dataclass(frozen=True, slots=True)
class Settings:
    """Полный набор настроек парсера.

    Объект неизменяемый: рантайм-значения конкретного поиска накладываются
    через :meth:`for_search`, который возвращает копию.
    """

    # ── Доступы (только из .env) ────────────────────────────────────────
    proxy_string: str = ""
    proxy_change_url: str = ""
    proxy_string_2: str = ""
    proxy_change_url_2: str = ""
    proxy_string_3: str = ""
    proxy_change_url_3: str = ""
    cookies_api_key: str = ""

    # ── Что показывать ──────────────────────────────────────────────────
    ignore_promotion: bool = True
    private_only: bool = False
    seller_skip: tuple[str, ...] = ()
    title_must_contain: tuple[str, ...] = ()
    title_skip: tuple[str, ...] = ()

    # ── Фильтр моделей iPhone ───────────────────────────────────────────
    iphone_min_model: int = 0
    iphone_max_model: int = 0
    iphone_models: tuple[str, ...] | None = None
    """``None`` — фильтр по моделям выключен, пустой кортеж — не показывать ничего."""
    iphone_models_in_url: bool = False
    """Модели уже зашиты в API URL, повторно фильтровать по названию не нужно."""

    # ── Пагинация ───────────────────────────────────────────────────────
    pages: int = 2
    pause_between_pages: int = 0

    # ── Темп опроса ─────────────────────────────────────────────────────
    poll_interval: float = 3.0
    """Нижняя граница интервала между циклами."""
    poll_interval_max: float = 24.0
    """Верхняя граница: до неё замедляемся при серии отказов Avito."""
    per_cookie_interval: float = 12.0
    """Один набор cookies не бьёт Avito чаще, чем раз в столько секунд."""
    retry_pause: float = 12.0
    """Пауза после неудачного цикла."""
    request_timeout: float = 12.0
    ip_change_wait: float = 25.0

    # ── Пул cookies ─────────────────────────────────────────────────────
    cookie_pool_size: int = 24
    cookie_unblock_pause: int = 60

    # ── Веб-интерфейс ───────────────────────────────────────────────────
    web_port: int = 8765

    # ── Рантайм текущего поиска ─────────────────────────────────────────
    web_url: str = ""
    api_url: str = ""
    category_id: str = ""
    """Категория текущего поиска; фильтр iPhone действует только для смартфонов."""

    def __post_init__(self) -> None:
        # Значения из конфига могут быть любыми: приводим к разумным границам
        # один раз здесь, чтобы цикл опроса не пересчитывал их каждый раз.
        object.__setattr__(self, "pages", max(2, self.pages))
        object.__setattr__(self, "pause_between_pages", max(0, self.pause_between_pages))
        object.__setattr__(self, "poll_interval", max(1.0, self.poll_interval))
        object.__setattr__(self, "per_cookie_interval", max(3.0, self.per_cookie_interval))
        object.__setattr__(
            self, "poll_interval_max", max(self.poll_interval, self.poll_interval_max)
        )
        object.__setattr__(self, "retry_pause", max(3.0, self.retry_pause))
        object.__setattr__(
            self, "request_timeout", min(12.0, max(3.0, self.request_timeout))
        )
        object.__setattr__(self, "ip_change_wait", max(1.0, self.ip_change_wait))
        object.__setattr__(self, "cookie_pool_size", max(1, self.cookie_pool_size))
        object.__setattr__(self, "cookie_unblock_pause", max(30, self.cookie_unblock_pause))
        # Список моделей приходит и из config.toml, и из интерфейса. Приводим к
        # одному виду здесь, чтобы дальше по коду встречались только известные id.
        object.__setattr__(self, "iphone_models", iphone.normalize_models(self.iphone_models))

    def proxy_endpoints(self) -> tuple[tuple[str, str], ...]:
        """Пары (строка прокси, ссылка смены IP) для рабочего пула."""
        pairs: list[tuple[str, str]] = []

        def add(proxy_string: str, change_url: str) -> None:
            text = proxy_string.strip()
            if text:
                pairs.append((text, change_url.strip()))

        add(self.proxy_string, self.proxy_change_url)
        add(self.proxy_string_2, self.proxy_change_url_2)
        add(self.proxy_string_3, self.proxy_change_url_3)
        for n in range(4, _MAX_PROXY_SLOTS + 1):
            add(os.environ.get(f"PROXY_STRING_{n}", ""), os.environ.get(f"PROXY_CHANGE_URL_{n}", ""))
        return tuple(pairs)

    def for_search(
        self,
        *,
        web_url: str,
        api_url: str,
        seller_skip: tuple[str, ...] | None = None,
        iphone_models: tuple[str, ...] | None = None,
        iphone_models_in_url: bool = False,
        category_id: str = "",
    ) -> Settings:
        """Копия настроек с параметрами конкретного поиска из веб-интерфейса.

        Чёрный список продавцов объединяется: то, что задано в ``config.toml``,
        действует всегда, а UI только добавляет к нему.
        """
        merged_skip = (
            self.seller_skip if seller_skip is None else _unique((*self.seller_skip, *seller_skip))
        )
        return replace(
            self,
            web_url=web_url,
            api_url=api_url,
            category_id=category_id or self.category_id,
            seller_skip=merged_skip,
            iphone_models=self.iphone_models if iphone_models is None else iphone_models,
            iphone_models_in_url=iphone_models_in_url or self.iphone_models_in_url,
        )

    @property
    def filters_iphone_models(self) -> bool:
        """Нужно ли отсеивать модели iPhone по названию объявления.

        Только для категории смартфонов Apple. В планшетах, ноутбуках,
        приставках и по своей ссылке список моделей с прошлого поиска
        не должен ничего резать.
        """
        if self.category_id and self.category_id != catalog.IPHONE_CATEGORY_ID:
            return False
        if self.iphone_models_in_url:
            return False
        return self.iphone_models is not None or self.iphone_min_model > 0


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    """Убрать повторы без учёта регистра, сохранив порядок."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = value.strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return tuple(out)


def _is_secret_toml_key(key: str) -> bool:
    """Секреты не должны жить в config.toml — даже с номером слота прокси."""
    if key in _SECRET_ENV_KEYS:
        return True
    return key.startswith("proxy_string") or key.startswith("proxy_change_url")


def load_env() -> None:
    """Подтянуть ``.env`` в ``os.environ`` (существующие переменные важнее)."""
    if not ENV_PATH.is_file():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


_FIELD_TYPES = {
    field: Settings.__dataclass_fields__[field].type for field in Settings.__dataclass_fields__
}


def _coerce(field: str, value: Any) -> Any:
    """Привести значение из TOML к типу поля настроек."""
    if field == "iphone_models":
        # Разбираем до приведения к строкам: число здесь значит линейку целиком
        # (``13`` -> все варианты 13-й), а «13» — конкретную базовую модель.
        return iphone.normalize_models(value)
    declared = str(_FIELD_TYPES[field])
    if declared.startswith("tuple"):
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"{field}: ожидается список строк")
        return tuple(str(item).strip() for item in value if str(item).strip())
    if declared.startswith("bool"):
        return bool(value)
    if declared.startswith("int"):
        return int(value)
    if declared.startswith("float"):
        return float(value)
    return str(value)


def _read_toml() -> dict[str, Any]:
    """Прочитать секцию ``[avito]`` из config.toml."""
    if not CONFIG_PATH.is_file():
        raise FileNotFoundError(
            f"Не найден {CONFIG_PATH.name}. Скопируйте config.toml.example "
            "и заполните .env (см. README)."
        )
    with CONFIG_PATH.open("rb") as fh:
        document = tomllib.load(fh)
    section = document.get("avito")
    if not isinstance(section, dict):
        raise ValueError(f"{CONFIG_PATH.name}: нет секции [avito]")
    return section


def load_settings() -> Settings:
    """Собрать настройки из ``config.toml`` и ``.env``.

    Непонятные ключи не роняют запуск, а попадают в лог: опечатку в конфиге
    видно сразу, но рабочий сервер из-за неё не встанет.
    """
    load_env()
    values: dict[str, Any] = {}
    for raw_key, raw_value in _read_toml().items():
        key = _KEY_ALIASES.get(raw_key, raw_key)
        if key in _OBSOLETE_KEYS:
            continue
        if _is_secret_toml_key(key):
            logger.warning(f"config.toml: «{raw_key}» — секрет, задайте его в .env")
            continue
        if key not in _FIELD_TYPES:
            logger.warning(f"config.toml: неизвестный ключ «{raw_key}» — пропущен")
            continue
        try:
            values[key] = _coerce(key, raw_value)
        except (TypeError, ValueError) as err:
            logger.warning(f"config.toml: ключ «{raw_key}» пропущен — {err}")

    for field, env_key in _SECRET_ENV_KEYS.items():
        values[field] = os.environ.get(env_key, "").strip()

    web_port = os.environ.get("WEB_PORT", "").strip()
    if web_port.isdigit():
        values["web_port"] = int(web_port)

    return Settings(**values)
