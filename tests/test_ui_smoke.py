"""Проверка интерфейса в настоящем браузере.

Собранный фронтенд открывается в headless-браузере поверх реального
сервера. Тесты умышленно поверхностные: они не проверяют вид страницы, а
ловят поломки, которые не видны ни типам, ни линтеру, — отсутствующий id
в разметке, исключение при сборке экрана, неработающий вход.

Тесты пропускаются, если не установлен playwright или браузер к нему
(``python -m playwright install chromium``), а также если фронтенд не
собран, — чтобы это не мешало запуску остальных проверок.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import pytest

from avito_monitor.config import Settings
from avito_monitor.paths import STATIC_DIR
from avito_monitor.web.server import start_server

playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright не установлен")

pytestmark = pytest.mark.skipif(
    not (STATIC_DIR / "index.html").is_file(),
    reason="фронтенд не собран: npm --prefix web run build",
)

# Ошибки в консоли, не относящиеся к работе интерфейса: в headless-браузере
# нет иконок, разрешений на уведомления и рабочего service worker.
IGNORED_CONSOLE = ("favicon", "manifest", "service worker", "serviceworker", "notification", "push")


@pytest.fixture(scope="module")
def browser() -> Iterator[object]:
    with playwright_api.sync_playwright() as engine:
        try:
            instance = engine.chromium.launch()
        except Exception as err:  # pragma: no cover — браузер не установлен
            pytest.skip(f"браузер для playwright недоступен: {err}")
        try:
            yield instance
        finally:
            instance.close()


@pytest.fixture(scope="module")
def server_url(module_monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    module_monkeypatch.setenv("WEB_OPEN_BROWSER", "0")
    httpd = start_server(replace(Settings(), web_port=0))
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


class Ui:
    """Страница приложения и накопленные ошибки консоли."""

    #: Форма поиска живёт в выдвижной панели, закрытой при старте.
    DRAWER = "#filters-drawer-back"

    def __init__(self, page, credentials: tuple[str, str]) -> None:
        self.page = page
        self.login, self.password = credentials
        self.console_errors: list[str] = []
        page.on(
            "console",
            lambda msg: self.console_errors.append(msg.text) if msg.type == "error" else None,
        )
        page.on("pageerror", lambda err: self.console_errors.append(str(err)))

    def errors(self) -> list[str]:
        """Ошибки консоли без заведомо посторонних."""
        return [
            text
            for text in self.console_errors
            if not any(marker in text.lower() for marker in IGNORED_CONSOLE)
        ]

    def sign_in(self) -> None:
        self.page.fill("#auth-login", self.login)
        self.page.fill("#auth-password", self.password)
        self.page.click("#auth-submit")
        self.page.wait_for_selector("#app:not(.hidden)", timeout=15000)

    def open_search_form(self) -> None:
        self.page.click("#filters-open-btn")
        self.page.wait_for_selector("#filters-drawer-back.open", timeout=5000)
        self.page.wait_for_selector("#search-form", state="visible", timeout=5000)

    def reload(self) -> None:
        """Перезагрузка с ожиданием готового приложения.

        Ждать ``networkidle`` здесь нельзя: после входа страница держит
        открытым поток событий, и «тишины в сети» уже не наступает.
        """
        self.page.reload(wait_until="load")
        self.page.wait_for_selector("#app:not(.hidden)", timeout=15000)

    def open_user_menu(self) -> None:
        self.page.click("#user-menu-btn")
        self.page.wait_for_selector("#user-drawer-back.open", timeout=5000)


@pytest.fixture
def ui(browser, server_url: str, credentials: tuple[str, str], monkeypatch) -> Iterator[Ui]:
    monkeypatch.delenv("ALLOWED_HOST", raising=False)
    context = browser.new_context()
    page = context.new_page()
    screen = Ui(page, credentials)
    page.goto(server_url, wait_until="networkidle")
    try:
        yield screen
    finally:
        context.close()


# ── Вход ───────────────────────────────────────────────────────────────────


def test_page_loads_without_errors(ui: Ui) -> None:
    assert ui.errors() == []


def test_login_screen_is_shown_first(ui: Ui) -> None:
    """Без входа интерфейс поиска показывать нельзя."""
    assert ui.page.is_visible("#auth-screen")
    assert "hidden" in (ui.page.get_attribute("#app", "class") or "")


def test_wrong_password_shows_message(ui: Ui) -> None:
    ui.page.fill("#auth-login", "tester")
    ui.page.fill("#auth-password", "неверный")
    ui.page.click("#auth-submit")
    ui.page.wait_for_function("document.querySelector('#auth-hint').textContent.length > 0")
    assert ui.page.inner_text("#auth-hint")
    assert "hidden" in (ui.page.get_attribute("#app", "class") or "")


def test_app_appears_after_login(ui: Ui) -> None:
    """Главная проверка: экран собирается целиком и без исключений."""
    ui.sign_in()
    assert ui.page.is_visible("#feed")
    assert ui.page.is_visible("#filters-open-btn")
    assert ui.page.is_visible("#search-status-pill")
    assert ui.errors() == []


def test_session_survives_reload(ui: Ui) -> None:
    """Cookie сессии должна возвращать в приложение без повторного входа."""
    ui.sign_in()
    ui.reload()
    assert ui.page.is_visible("#feed")
    assert ui.errors() == []


def test_logout_returns_to_login_screen(ui: Ui) -> None:
    ui.sign_in()
    ui.open_user_menu()
    ui.page.click("#user-menu-logout")
    ui.page.wait_for_selector("#auth-screen:not(.hidden)", timeout=10000)
    assert "hidden" in (ui.page.get_attribute("#app", "class") or "")


# ── Форма поиска ───────────────────────────────────────────────────────────


def test_search_form_opens(ui: Ui) -> None:
    ui.sign_in()
    ui.open_search_form()
    assert ui.page.is_visible("#start")
    assert ui.errors() == []


def test_search_form_closes(ui: Ui) -> None:
    ui.sign_in()
    ui.open_search_form()
    ui.page.click("#filters-close")
    ui.page.wait_for_selector("#search-form", state="hidden", timeout=5000)
    assert ui.errors() == []


def test_categories_come_from_server(ui: Ui) -> None:
    ui.sign_in()
    ui.open_search_form()
    ui.page.click("#category-trigger")
    ui.page.wait_for_selector("#cats button.multi-select-choice", state="visible", timeout=10000)
    assert ui.page.locator("#cats button.multi-select-choice").count() >= 4


def test_iphone_models_are_listed(ui: Ui) -> None:
    """Список моделей строится из того же каталога, что и на сервере."""
    ui.sign_in()
    ui.open_search_form()
    assert ui.page.locator('#iphone-models input[type="checkbox"]').count() >= 20


def test_iphone_selection_can_be_cleared_and_restored(ui: Ui) -> None:
    ui.sign_in()
    ui.open_search_form()
    ui.page.click("#iphone-models-trigger")
    ui.page.click("#iphone-models-none")
    ui.page.wait_for_function(
        "document.querySelector('#iphone-models-label').textContent.includes('Не выбрано')",
        timeout=5000,
    )
    ui.page.click("#iphone-models-all")
    ui.page.wait_for_function(
        "document.querySelector('#iphone-models-label').textContent.includes('Все модели')",
        timeout=5000,
    )
    assert ui.errors() == []


def test_all_categories_shows_query_field(ui: Ui) -> None:
    """В «Все категории» появляется поле запроса; без текста поиск не стартует."""
    ui.sign_in()
    ui.open_search_form()
    assert not ui.page.is_visible("#query")

    ui.page.click("#category-trigger")
    ui.page.click('#cats button[data-id="all"]')
    ui.page.wait_for_selector("#query", state="visible", timeout=5000)
    ui.page.click("#start")
    ui.page.wait_for_selector(".toast", timeout=5000)
    assert "запрос" in ui.page.inner_text(".toast").lower()
    assert ui.page.is_visible("#search-status-pill.is-idle")
    assert ui.errors() == []


def test_url_mode_shows_link_field(ui: Ui) -> None:
    """В режиме «по ссылке» появляется поле ввода, в обычном оно скрыто."""
    ui.sign_in()
    ui.open_search_form()
    assert not ui.page.is_visible("#query")

    ui.page.click("#mode-url")
    ui.page.wait_for_selector("#query", state="visible", timeout=5000)

    ui.page.click("#mode-query")
    ui.page.wait_for_selector("#query", state="hidden", timeout=5000)
    assert ui.errors() == []


def test_saved_url_can_be_added(ui: Ui) -> None:
    ui.sign_in()
    ui.open_search_form()
    ui.page.click("#mode-url")
    ui.page.click("#saved-url-add-btn")
    ui.page.fill("#saved-url-add-name", "Тестовая ссылка")
    ui.page.fill("#saved-url-add-url", "https://www.avito.ru/moskva/telefony")
    ui.page.click("#saved-url-add-confirm")
    ui.page.wait_for_selector("#saved-urls-list .saved-url-item", timeout=5000)
    assert ui.page.locator("#saved-urls-list .saved-url-item").count() == 1
    assert ui.errors() == []


def test_region_picker_loads_list(ui: Ui) -> None:
    ui.sign_in()
    ui.open_search_form()
    ui.page.click("#region-btn")
    ui.page.wait_for_selector("#region-list button[data-slug]", timeout=10000)
    assert ui.page.locator("#region-list button[data-slug]").count() > 0


def test_region_search_narrows_list(ui: Ui) -> None:
    ui.sign_in()
    ui.open_search_form()
    ui.page.click("#region-btn")
    ui.page.fill("#region-query", "казань")
    ui.page.wait_for_function(
        "document.querySelectorAll('#region-list button[data-slug]').length === 1",
        timeout=10000,
    )
    assert "Казан" in ui.page.inner_text("#region-list")


def test_chosen_region_is_remembered_after_reload(ui: Ui) -> None:
    """Настройки формы хранятся в браузере и должны переживать перезагрузку."""
    ui.sign_in()
    ui.open_search_form()
    ui.page.click("#region-btn")
    ui.page.fill("#region-query", "казань")
    ui.page.click("#region-list button[data-slug]")
    ui.page.wait_for_function(
        "document.querySelector('#region-label').textContent.includes('Казан')",
        timeout=5000,
    )

    ui.reload()
    ui.open_search_form()
    assert "Казан" in ui.page.inner_text("#region-label")


# ── Запуск поиска ──────────────────────────────────────────────────────────


def test_starting_search_locks_the_form(ui: Ui) -> None:
    """Во время поиска условия менять нельзя — иначе они разойдутся с сервером."""
    ui.sign_in()
    ui.open_search_form()
    ui.page.click("#start")
    ui.page.wait_for_selector("#search-status-pill.is-active", timeout=20000)

    # Панель закрывается сама, поэтому для проверок открываем её заново.
    ui.open_search_form()
    assert ui.page.is_disabled("#start")
    assert ui.page.is_disabled("#mode-url")
    assert ui.page.is_disabled("#category-trigger")
    assert ui.page.is_disabled("#region-btn")

    ui.page.click("#stop")
    ui.page.wait_for_selector("#search-status-pill.is-idle", timeout=10000)
    assert not ui.page.is_disabled("#start")
    assert ui.errors() == []


def test_url_mode_requires_a_link(ui: Ui) -> None:
    ui.sign_in()
    ui.open_search_form()
    ui.page.click("#mode-url")
    ui.page.click("#start")
    ui.page.wait_for_selector(".toast", timeout=5000)
    assert "ссылк" in ui.page.inner_text(".toast").lower()
    assert ui.page.is_visible("#search-status-pill.is-idle")


# ── Сессия Avito ───────────────────────────────────────────────────────────


def test_avito_modal_opens_from_user_menu(ui: Ui) -> None:
    ui.sign_in()
    ui.open_user_menu()
    ui.page.click("#user-menu-avito")
    ui.page.wait_for_selector("#avito-modal:not(.hidden)", timeout=5000)
    assert ui.page.is_visible("#avito-cookies")
    assert ui.errors() == []


def test_avito_modal_reports_broken_json(ui: Ui) -> None:
    """Ошибка разбора должна доходить до пользователя, а не в консоль."""
    ui.sign_in()
    ui.open_user_menu()
    ui.page.click("#user-menu-avito")
    ui.page.wait_for_selector("#avito-modal:not(.hidden)", timeout=5000)
    ui.page.fill("#avito-cookies", "{не json")
    ui.page.click("#avito-import")
    ui.page.wait_for_function(
        "document.querySelector('#avito-hint').textContent.length > 0",
        timeout=5000,
    )
    assert ui.page.inner_text("#avito-hint")
