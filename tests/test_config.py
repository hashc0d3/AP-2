"""Загрузка настроек из config.toml и .env."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from avito_monitor import config
from avito_monitor.config import Settings, load_settings

_ENV_KEYS = (
    "PROXY_STRING",
    "PROXY_CHANGE_URL",
    "PROXY_STRING_2",
    "PROXY_CHANGE_URL_2",
    "PROXY_STRING_3",
    "PROXY_CHANGE_URL_3",
    *(f"PROXY_STRING_{n}" for n in range(4, 13)),
    *(f"PROXY_CHANGE_URL_{n}" for n in range(4, 13)),
    "COOKIES_API_KEY",
    "WEB_PORT",
)


@pytest.fixture
def config_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Подменить config.toml и .env на временные файлы."""
    config_path = tmp_path / "config.toml"
    env_path = tmp_path / ".env"
    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(config, "ENV_PATH", env_path)
    for name in _ENV_KEYS:
        monkeypatch.delenv(name, raising=False)

    def _write(toml_text: str = "[avito]\n", env_text: str = "") -> None:
        config_path.write_text(textwrap.dedent(toml_text), encoding="utf-8")
        env_path.write_text(textwrap.dedent(env_text), encoding="utf-8")

    _write()
    return _write


# ── Обязательные файлы ─────────────────────────────────────────────────────


def test_missing_config_is_explained(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Сообщение должно подсказывать, что делать, а не показывать трейсбек."""
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "нет.toml")
    with pytest.raises(FileNotFoundError, match=r"config\.toml\.example"):
        load_settings()


def test_config_without_section_is_rejected(config_files) -> None:
    config_files(toml_text="[other]\nquery = 1\n")
    with pytest.raises(ValueError, match=r"нет секции \[avito\]"):
        load_settings()


def test_empty_section_gives_defaults(config_files) -> None:
    """Неполный конфиг — не ошибка: значения по умолчанию живут в Settings."""
    assert load_settings() == Settings()


# ── Чтение значений ────────────────────────────────────────────────────────


def test_values_are_read_from_toml(config_files) -> None:
    config_files(
        toml_text="""
        [avito]
        cookie_pool_size = 7
        private_only = false
        poll_interval = 5
        poll_interval_max = 30
        pages = 3
        title_must_contain = ["iphone"]
        """
    )
    settings = load_settings()
    assert settings.cookie_pool_size == 7
    assert settings.private_only is False
    assert settings.poll_interval == 5.0
    assert settings.poll_interval_max == 30.0
    assert settings.pages == 3
    assert settings.title_must_contain == ("iphone",)


def test_legacy_key_name_still_works(config_files) -> None:
    """Старые config.toml у заказчика не должны ломаться после переименования."""
    config_files(toml_text="[avito]\npause_general = 30\n")
    assert load_settings().retry_pause == 30.0


def test_obsolete_keys_are_ignored_silently(config_files) -> None:
    config_files(
        toml_text="""
        [avito]
        url = "https://www.avito.ru/moskva"
        api_url = "https://www.avito.ru/web/1/js/items"
        pause_max = 60
        socks5_proxy = "socks5://1.2.3.4:1080"
        max_age = 1200
        notify_max_age = 60
        """
    )
    assert load_settings() == Settings()


def test_unknown_key_is_skipped(config_files) -> None:
    """Опечатка попадает в лог, но сервер всё равно поднимается."""
    config_files(toml_text="[avito]\npoll_intrval = 9\n")
    assert load_settings().poll_interval == Settings().poll_interval


def test_wrong_type_is_skipped(config_files) -> None:
    config_files(toml_text='[avito]\npoll_interval = "не число"\npages = 2\n')
    settings = load_settings()
    assert settings.poll_interval == Settings().poll_interval
    assert settings.pages == 2, "остальные ключи должны примениться"


# ── Секреты ────────────────────────────────────────────────────────────────


def test_secrets_come_from_env(config_files) -> None:
    config_files(
        env_text="""
        PROXY_STRING=http://user:pass@1.2.3.4:8000
        PROXY_STRING_2=http://user:pass@5.6.7.8:9000
        PROXY_CHANGE_URL_2=https://aproxy.site/?proxy_key=second
        PROXY_STRING_3=http://user:pass@9.9.9.9:10000
        PROXY_CHANGE_URL_3=https://aproxy.site/?proxy_key=third
        PROXY_STRING_4=http://user:pass@4.4.4.4:14000
        PROXY_CHANGE_URL_4=https://aproxy.site/?proxy_key=fourth
        COOKIES_API_KEY=abc123
        """
    )
    settings = load_settings()
    assert settings.proxy_string == "http://user:pass@1.2.3.4:8000"
    assert settings.proxy_string_2 == "http://user:pass@5.6.7.8:9000"
    assert settings.proxy_change_url_2 == "https://aproxy.site/?proxy_key=second"
    assert settings.proxy_string_3 == "http://user:pass@9.9.9.9:10000"
    assert settings.cookies_api_key == "abc123"
    assert settings.proxy_endpoints() == (
        ("http://user:pass@1.2.3.4:8000", ""),
        ("http://user:pass@5.6.7.8:9000", "https://aproxy.site/?proxy_key=second"),
        ("http://user:pass@9.9.9.9:10000", "https://aproxy.site/?proxy_key=third"),
        ("http://user:pass@4.4.4.4:14000", "https://aproxy.site/?proxy_key=fourth"),
    )


def test_secret_in_toml_is_refused(config_files) -> None:
    """Иначе ключ утечёт вместе с конфигом, который принято коммитить."""
    config_files(toml_text='[avito]\ncookies_api_key = "из конфига"\n')
    assert load_settings().cookies_api_key == ""


def test_environment_wins_over_env_file(config_files, monkeypatch: pytest.MonkeyPatch) -> None:
    """В Docker переменные приходят снаружи и должны иметь приоритет."""
    config_files(env_text="COOKIES_API_KEY=from-file\n")
    monkeypatch.setenv("COOKIES_API_KEY", "from-environment")
    assert load_settings().cookies_api_key == "from-environment"


def test_env_file_comments_and_quotes(config_files) -> None:
    config_files(
        env_text="""
        # комментарий
        COOKIES_API_KEY="в кавычках"
        PROXY_CHANGE_URL='в апострофах'
        строка без знака равно
        """
    )
    settings = load_settings()
    assert settings.cookies_api_key == "в кавычках"
    assert settings.proxy_change_url == "в апострофах"


def test_web_port_from_env(config_files, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_PORT", "9999")
    assert load_settings().web_port == 9999


def test_invalid_web_port_falls_back(config_files, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_PORT", "не порт")
    assert load_settings().web_port == Settings().web_port


# ── Нормализация ───────────────────────────────────────────────────────────


def test_list_values_are_trimmed(config_files) -> None:
    config_files(toml_text='[avito]\ntitle_skip = ["Чехол", " стекло ", ""]\n')
    assert load_settings().title_skip == ("Чехол", "стекло")


def test_iphone_models_are_normalized(config_files) -> None:
    config_files(toml_text='[avito]\niphone_models = ["13-pro", "13-pro", "неизвестно"]\n')
    assert load_settings().iphone_models == ("13-pro",)


def test_bare_generation_expands_to_whole_line(config_files) -> None:
    """Так выглядел формат до появления вариантов Pro/Max."""
    config_files(toml_text="[avito]\niphone_models = [16]\n")
    assert load_settings().iphone_models == ("16", "16-plus", "16-pro", "16-pro-max", "16-e")


def test_interval_max_is_never_below_interval(config_files) -> None:
    config_files(toml_text="[avito]\npoll_interval = 20\npoll_interval_max = 5\n")
    settings = load_settings()
    assert settings.poll_interval_max == settings.poll_interval


@pytest.mark.parametrize(
    ("field", "given", "expected"),
    [
        ("pages", 0, 2),
        ("pages", 1, 2),
        ("poll_interval", 0.1, 1.0),
        ("per_cookie_interval", 1, 3.0),
        ("request_timeout", 0, 3.0),
        ("request_timeout", 20, 12.0),
        ("cookie_pool_size", 0, 1),
        ("cookie_unblock_pause", 5, 30),
    ],
)
def test_unsafe_values_are_clamped(field: str, given: float, expected: float) -> None:
    """Слишком агрессивные настройки быстро сожгут пул cookies."""
    assert getattr(Settings(**{field: given}), field) == expected


# ── Неизменяемость и рантайм-копии ─────────────────────────────────────────


def test_settings_are_immutable(settings: Settings) -> None:
    """Настройки читаются из нескольких потоков — менять их на ходу нельзя."""
    with pytest.raises(AttributeError):
        settings.poll_interval = 99  # type: ignore[misc]


def test_for_search_returns_a_copy(settings: Settings) -> None:
    updated = settings.for_search(web_url="https://avito.ru/x", api_url="https://api/x")
    assert updated.web_url == "https://avito.ru/x"
    assert settings.web_url == ""
    assert updated.poll_interval == settings.poll_interval


def test_for_search_merges_seller_blacklist() -> None:
    """Список из config.toml действует всегда, интерфейс только добавляет."""
    base = Settings(seller_skip=("из конфига",))
    updated = base.for_search(web_url="", api_url="", seller_skip=("из конфига", "из интерфейса"))
    assert updated.seller_skip == ("из конфига", "из интерфейса")


def test_for_search_keeps_config_models_when_ui_is_silent() -> None:
    base = Settings(iphone_models=("13-pro",))
    assert base.for_search(web_url="", api_url="").iphone_models == ("13-pro",)


def test_url_filter_disables_title_filter() -> None:
    """Avito уже отфильтровал выдачу — второй проход по названию лишний."""
    base = Settings(iphone_models=("13-pro",))
    assert base.filters_iphone_models is True
    assert (
        base.for_search(
            web_url="", api_url="", iphone_models=("13-pro",), iphone_models_in_url=True
        ).filters_iphone_models
        is False
    )


def test_generation_filter_also_enables_title_filter() -> None:
    assert Settings(iphone_min_model=14).filters_iphone_models is True
    assert Settings().filters_iphone_models is False


def test_iphone_title_filter_stays_on_phones() -> None:
    """Список моделей с прошлого поиска не режет планшеты и приставки."""
    leftover = Settings(iphone_models=("13-pro",), category_id="tablets")
    assert leftover.filters_iphone_models is False
    consoles = leftover.for_search(web_url="", api_url="", category_id="game_consoles")
    assert consoles.filters_iphone_models is False
    phones = leftover.for_search(web_url="", api_url="", category_id="apple_phones")
    assert phones.filters_iphone_models is True
