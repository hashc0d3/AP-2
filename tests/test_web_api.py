"""Проверки веб-интерфейса через настоящий HTTP.

Сервер поднимается на свободном порту и опрашивается обычным ``requests`` —
так вместе с обработчиками проверяются маршрутизация, cookie сессии и коды
ответа, то есть именно то, что видит браузер.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from dataclasses import replace

import pytest
import requests

from avito_monitor import auth, spfa
from avito_monitor.config import Settings
from avito_monitor.web import server as server_module
from avito_monitor.web.feed import AdFeed
from avito_monitor.web.server import QuietServer, start_server


@pytest.fixture(scope="module")
def base_url(module_monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Один сервер на весь модуль.

    Обработчики читают пути к файлам и синглтоны в момент запроса, поэтому
    пофайловая изоляция из ``conftest`` продолжает работать и с общим
    сервером — а поднимать его на каждый тест дорого (bind делает обратный
    DNS-запрос).
    """
    module_monkeypatch.setenv("WEB_OPEN_BROWSER", "0")
    httpd: QuietServer = start_server(replace(Settings(), web_port=0))
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def client(base_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[requests.Session]:
    """Клиент без входа — хранит cookies между запросами, как браузер."""
    monkeypatch.delenv("ALLOWED_HOST", raising=False)
    with requests.Session() as session:
        yield session


@pytest.fixture
def signed_in(
    client: requests.Session, base_url: str, credentials: tuple[str, str]
) -> requests.Session:
    login, password = credentials
    response = client.post(
        f"{base_url}/api/auth/login", json={"login": login, "password": password}, timeout=5
    )
    assert response.status_code == 200
    return client


# ── Вход ───────────────────────────────────────────────────────────────────


def test_login_sets_httponly_cookie(
    client: requests.Session, base_url: str, credentials: tuple[str, str]
) -> None:
    login, password = credentials
    response = client.post(
        f"{base_url}/api/auth/login", json={"login": login, "password": password}, timeout=5
    )
    assert response.status_code == 200
    assert response.json()["logged_in"] is True

    cookie = response.headers["Set-Cookie"]
    assert auth.COOKIE_NAME in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_secure_flag_only_behind_https(
    client: requests.Session, base_url: str, credentials: tuple[str, str]
) -> None:
    """Локальный запуск идёт по http — с флагом Secure вход был бы невозможен."""
    login, password = credentials
    body = {"login": login, "password": password}

    plain = client.post(f"{base_url}/api/auth/login", json=body, timeout=5)
    assert "Secure" not in plain.headers["Set-Cookie"]

    behind_nginx = client.post(
        f"{base_url}/api/auth/login",
        json=body,
        headers={"X-Forwarded-Proto": "https"},
        timeout=5,
    )
    assert "Secure" in behind_nginx.headers["Set-Cookie"]


def test_wrong_password_gives_400(
    client: requests.Session, base_url: str, credentials: tuple[str, str]
) -> None:
    response = client.post(
        f"{base_url}/api/auth/login", json={"login": "tester", "password": "нет"}, timeout=5
    )
    assert response.status_code == 400
    assert "error" in response.json()
    assert "Set-Cookie" not in response.headers


def test_logout_clears_cookie(signed_in: requests.Session, base_url: str) -> None:
    response = signed_in.post(f"{base_url}/api/auth/logout", timeout=5)
    assert response.status_code == 200
    assert "Max-Age=0" in response.headers["Set-Cookie"]

    after = signed_in.get(f"{base_url}/api/ads", timeout=5)
    assert after.status_code == 403


# ── Доступ ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/ads"),
        ("GET", "/api/seller-blacklist"),
        ("GET", "/api/avito/session"),
        ("GET", "/api/resource/balance"),
        ("GET", "/events"),
        ("POST", "/api/search"),
        ("POST", "/api/search/stop"),
        ("POST", "/api/reset"),
        ("POST", "/api/seller-blacklist"),
        ("POST", "/api/avito/phone"),
        ("POST", "/api/push/subscribe"),
    ],
)
def test_closed_endpoints_require_login(
    client: requests.Session, base_url: str, method: str, path: str
) -> None:
    response = client.request(method, f"{base_url}{path}", json={}, timeout=5)
    assert response.status_code == 403
    assert response.json()["code"] == "auth_required"


@pytest.mark.parametrize(
    "path",
    ["/api/auth/status", "/api/status", "/api/categories", "/api/regions", "/api/push/vapid"],
)
def test_open_endpoints_work_without_login(
    client: requests.Session, base_url: str, path: str
) -> None:
    """Экрану входа нужны справочники и состояние — секретов там нет."""
    response = client.get(f"{base_url}{path}", timeout=5)
    assert response.status_code == 200


def test_unknown_endpoint_gives_404(client: requests.Session, base_url: str) -> None:
    assert client.get(f"{base_url}/api/нет-такого", timeout=5).status_code == 404


def test_body_on_get_does_not_break_the_answer(client: requests.Session, base_url: str) -> None:
    """Тело у GET встречается редко, но ответ должно доходить.

    Непрочитанное сервером тело оставалось в сокете, и закрытие соединения
    превращалось в сброс: клиент терял уже отправленный ответ.
    """
    for _ in range(5):
        response = client.request("GET", f"{base_url}/events", json={"x": "y"}, timeout=5)
        assert response.status_code == 403


def test_huge_body_is_refused(base_url: str) -> None:
    """Заявленный Content-Length не должен заставлять сервер выделять память.

    Запрос отправляется сокетом: ``requests`` считает ``Content-Length`` сам
    и подделать его не даёт.
    """
    host, port = base_url.removeprefix("http://").split(":")
    request = (
        "POST /api/auth/login HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Content-Length: {server_module.MAX_BODY_BYTES + 1}\r\n"
        "\r\n"
    ).encode("ascii")

    with socket.create_connection((host, int(port)), timeout=5) as sock:
        sock.sendall(request)
        answer = sock.recv(4096)

    assert b"413" in answer.split(b"\r\n", 1)[0]


def test_vapid_never_exposes_private_key(client: requests.Session, base_url: str) -> None:
    payload = client.get(f"{base_url}/api/push/vapid", timeout=5).json()
    assert set(payload) == {"configured", "publicKey"}
    assert "PRIVATE" not in payload["publicKey"]


# ── Справочники ────────────────────────────────────────────────────────────


def test_categories_have_id_and_name(client: requests.Session, base_url: str) -> None:
    categories = client.get(f"{base_url}/api/categories", timeout=5).json()
    assert categories
    assert all({"id", "name"} <= set(item) for item in categories)


def test_region_search_is_case_insensitive(client: requests.Session, base_url: str) -> None:
    found = client.get(f"{base_url}/api/regions", params={"q": "МОСК"}, timeout=5).json()
    assert any(item["slug"] == "moskva" for item in found)


def test_region_search_without_query_returns_list(client: requests.Session, base_url: str) -> None:
    assert isinstance(client.get(f"{base_url}/api/regions", timeout=5).json(), list)


# ── Поиск ──────────────────────────────────────────────────────────────────


def test_status_reports_stopped_search(client: requests.Session, base_url: str) -> None:
    payload = client.get(f"{base_url}/api/status", timeout=5).json()
    assert payload["running"] is False
    assert payload["auth"]["logged_in"] is False


def test_start_search_requires_known_region(signed_in: requests.Session, base_url: str) -> None:
    response = signed_in.post(
        f"{base_url}/api/search",
        json={"query": "iphone", "region": "нет-такого", "category": "apple_phones"},
        timeout=10,
    )
    assert response.status_code == 400
    assert "регион" in response.json()["error"].lower()


def test_start_search_all_categories_requires_query(
    signed_in: requests.Session, base_url: str
) -> None:
    response = signed_in.post(
        f"{base_url}/api/search",
        json={"region": "moskva", "category": "all"},
        timeout=10,
    )
    assert response.status_code == 400
    assert "запрос" in response.json()["error"].lower()


def test_start_search_all_categories_builds_links(
    signed_in: requests.Session, base_url: str
) -> None:
    response = signed_in.post(
        f"{base_url}/api/search",
        json={"query": "iphone", "region": "moskva", "category": "all"},
        timeout=10,
    )
    assert response.status_code == 200
    payload = response.json()
    web_url = payload["web_url"]
    path = web_url.split("?", 1)[0]
    assert "q=iphone" in web_url
    assert "s=104" in web_url
    assert "owner" in web_url
    assert "/telefony" not in path
    signed_in.post(f"{base_url}/api/search/stop", timeout=5)


def test_tablet_search_clears_leftover_iphone_models(
    signed_in: requests.Session, base_url: str
) -> None:
    """После поиска iPhone фильтр моделей не должен переехать на планшеты."""
    first = signed_in.post(
        f"{base_url}/api/search",
        json={
            "region": "all",
            "category": "apple_phones",
            "iphone_models": ["13-pro"],
        },
        timeout=10,
    )
    assert first.status_code == 200
    assert first.json()["iphone_models"] == ["13-pro"]

    second = signed_in.post(
        f"{base_url}/api/search",
        json={"region": "all", "category": "tablets"},
        timeout=10,
    )
    assert second.status_code == 200
    payload = second.json()
    assert payload["category"]["id"] == "tablets"
    assert payload["iphone_models"] is None
    signed_in.post(f"{base_url}/api/search/stop", timeout=5)


def test_start_search_returns_links(signed_in: requests.Session, base_url: str) -> None:
    response = signed_in.post(
        f"{base_url}/api/search",
        json={"query": "iphone 13", "region": "moskva", "category": "apple_phones"},
        timeout=10,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["running"] is True
    assert "avito.ru" in payload["web_url"]

    stopped = signed_in.post(f"{base_url}/api/search/stop", timeout=5).json()
    assert stopped["running"] is False


def test_search_by_url_mode(signed_in: requests.Session, base_url: str) -> None:
    """Узкий путь без хеша фильтра по-прежнему нельзя собрать без сервиса."""
    response = signed_in.post(
        f"{base_url}/api/search",
        json={"mode": "url", "url": "https://www.avito.ru/moskva/telefony"},
        timeout=10,
    )
    assert response.status_code == 502


def test_search_by_url_builds_local_api(signed_in: requests.Session, base_url: str) -> None:
    """Знакомый регион и категория в ссылке — без обращения к spfa.pro."""
    response = signed_in.post(
        f"{base_url}/api/search",
        json={
            "mode": "url",
            "url": (
                "https://www.avito.ru/moskva/telefony/mobilnye_telefony/"
                "apple-ASgBAgICAkS0wA3OqzmwwQ2I_Dc?s=104&owner[]=private"
            ),
        },
        timeout=10,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["running"] is True
    assert "presentationType=serp" in payload["api_url"]
    assert "sort=date" in payload["api_url"]
    assert "s=104" not in payload["api_url"]
    assert payload["region"]["slug"] == "moskva"
    signed_in.post(f"{base_url}/api/search/stop", timeout=5)


def test_empty_url_is_rejected(signed_in: requests.Session, base_url: str) -> None:
    response = signed_in.post(f"{base_url}/api/search", json={"mode": "url", "url": ""}, timeout=5)
    assert response.status_code == 400


def test_broken_json_body_gives_400(signed_in: requests.Session, base_url: str) -> None:
    response = signed_in.post(
        f"{base_url}/api/seller-blacklist",
        data="{не json".encode(),
        headers={"Content-Type": "application/json"},
        timeout=5,
    )
    assert response.status_code == 400


# ── Лента ──────────────────────────────────────────────────────────────────


def test_ads_start_empty(signed_in: requests.Session, base_url: str) -> None:
    assert signed_in.get(f"{base_url}/api/ads", timeout=5).json() == []


def test_published_ads_are_served(
    signed_in: requests.Session, base_url: str, fresh_feed: AdFeed
) -> None:
    fresh_feed.publish([{"id": 1, "title": "iPhone 13"}])
    ads = signed_in.get(f"{base_url}/api/ads", timeout=5).json()
    assert [ad["id"] for ad in ads] == [1]


def test_reset_clears_feed(signed_in: requests.Session, base_url: str, fresh_feed: AdFeed) -> None:
    fresh_feed.publish([{"id": 1}, {"id": 2}])
    response = signed_in.post(f"{base_url}/api/reset", timeout=5).json()
    assert response == {"ok": True, "cleared": 2}
    assert signed_in.get(f"{base_url}/api/ads", timeout=5).json() == []


# ── Чёрный список продавцов ────────────────────────────────────────────────


def test_seller_blacklist_round_trip(signed_in: requests.Session, base_url: str) -> None:
    saved = signed_in.post(
        f"{base_url}/api/seller-blacklist", json={"sellers": ["Премиум", "Магазин"]}, timeout=5
    )
    assert saved.status_code == 200
    assert saved.json()["sellers"] == ["Премиум", "Магазин"]

    read_back = signed_in.get(f"{base_url}/api/seller-blacklist", timeout=5).json()
    assert read_back == {"sellers": ["Премиум", "Магазин"]}


def test_seller_blacklist_needs_a_list(signed_in: requests.Session, base_url: str) -> None:
    response = signed_in.post(
        f"{base_url}/api/seller-blacklist", json={"sellers": "Премиум"}, timeout=5
    )
    assert response.status_code == 400


# ── Сессия Avito ───────────────────────────────────────────────────────────


def test_avito_session_absent_by_default(signed_in: requests.Session, base_url: str) -> None:
    status = signed_in.get(f"{base_url}/api/avito/session", timeout=5).json()
    assert status["connected"] is False
    assert status["label"] == ""


def test_avito_session_import_and_clear(signed_in: requests.Session, base_url: str) -> None:
    cookies = [{"name": "sessid", "value": "abc", "domain": ".avito.ru"}]
    imported = signed_in.post(
        f"{base_url}/api/avito/session/import", json=cookies, timeout=5
    ).json()
    assert imported["ok"] is True

    status = signed_in.get(f"{base_url}/api/avito/session", timeout=5).json()
    assert status["connected"] is True
    assert status["saved_at"] > 0

    signed_in.post(f"{base_url}/api/avito/session/clear", timeout=5)
    cleared = signed_in.get(f"{base_url}/api/avito/session", timeout=5).json()
    assert cleared["connected"] is False


def test_phone_request_needs_ad_id(signed_in: requests.Session, base_url: str) -> None:
    response = signed_in.post(f"{base_url}/api/avito/phone", json={}, timeout=5)
    assert response.status_code == 400
    assert "ad_id" in response.json()["error"]


# ── Внешний сервис ─────────────────────────────────────────────────────────


def test_balance_is_returned(
    signed_in: requests.Session, base_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spfa, "fetch_balance", lambda key: 123.45)
    payload = signed_in.get(f"{base_url}/api/resource/balance", timeout=5).json()
    assert payload == {"success": True, "balance": 123.45}


def test_service_failure_maps_to_502(
    signed_in: requests.Session, base_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Чужая недоступность — не наша 500-я: интерфейс должен это различать."""

    def _fail(_: str) -> float:
        raise spfa.SpfaError("сервис недоступен")

    monkeypatch.setattr(spfa, "fetch_balance", _fail)
    response = signed_in.get(f"{base_url}/api/resource/balance", timeout=5)
    assert response.status_code == 502
    assert response.json()["error"] == "сервис недоступен"


def test_unexpected_error_hides_details(
    signed_in: requests.Session, base_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Наружу не должны попадать трейсбеки и пути к файлам."""

    def _boom(_: str) -> float:
        raise RuntimeError("пароль в тексте ошибки")

    monkeypatch.setattr(spfa, "fetch_balance", _boom)
    response = signed_in.get(f"{base_url}/api/resource/balance", timeout=5)
    assert response.status_code == 500
    assert response.json() == {"error": "Внутренняя ошибка сервера"}


# ── Статика и картинки ─────────────────────────────────────────────────────


def test_index_is_served(client: requests.Session, base_url: str) -> None:
    response = client.get(f"{base_url}/", timeout=5)
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/html")
    assert response.headers["Cache-Control"] == "no-store"


def test_service_worker_is_never_cached(client: requests.Session, base_url: str) -> None:
    """Закэшированный sw.js оставил бы пользователя на старой сборке."""
    response = client.get(f"{base_url}/sw.js", timeout=5)
    if response.status_code == 200:
        assert response.headers["Cache-Control"] == "no-store"


def test_directory_traversal_is_blocked(client: requests.Session, base_url: str) -> None:
    response = client.get(f"{base_url}/assets/../../.env", timeout=5, allow_redirects=False)
    assert response.status_code in {400, 404}
    assert "APP_PASSWORD" not in response.text


def test_image_proxy_rejects_foreign_hosts(client: requests.Session, base_url: str) -> None:
    """Иначе прокси стал бы открытым ретранслятором запросов."""
    response = client.get(f"{base_url}/img", params={"u": "http://example.com/a.jpg"}, timeout=5)
    assert response.status_code == 400


def test_image_proxy_needs_url(client: requests.Session, base_url: str) -> None:
    assert client.get(f"{base_url}/img", timeout=5).status_code == 400


# ── Проверка домена ────────────────────────────────────────────────────────


def test_foreign_host_is_refused(
    client: requests.Session, base_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALLOWED_HOST", "monitor.example.com")
    response = client.get(f"{base_url}/api/status", headers={"Host": "evil.example"}, timeout=5)
    assert response.status_code == 403


def test_local_host_is_always_allowed(
    client: requests.Session, base_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Иначе после настройки домена перестал бы работать доступ с самой машины."""
    monkeypatch.setenv("ALLOWED_HOST", "monitor.example.com")
    assert client.get(f"{base_url}/api/status", timeout=5).status_code == 200
