"""Таблица JSON-эндпоинтов веб-интерфейса.

Каждый обработчик получает :class:`Request` и возвращает :class:`Response` —
он не знает про сокеты, заголовки и cookie. Проверка входа и превращение
исключений в коды ответа вынесены в :func:`dispatch`, поэтому обработчики
состоят из одной-двух строк по делу.

Потоковые эндпоинты (``/events``), статика и прокси картинок сюда не входят:
им нужен прямой доступ к соединению, и живут они в
:mod:`avito_monitor.web.server`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger

from avito_monitor import auth, spfa
from avito_monitor.avito import catalog, regions, user_session
from avito_monitor.config import load_settings
from avito_monitor.search_session import MODE_QUERY, MODE_URL, SESSION, start_search
from avito_monitor.web import feed, push

AUTH_REQUIRED_ERROR = {"error": "Требуется вход", "code": "auth_required"}
_TRUE_VALUES = frozenset({"1", "true", "yes"})


@dataclass(frozen=True, slots=True)
class Request:
    """Разобранный HTTP-запрос в том объёме, который нужен обработчикам."""

    path: str
    query: dict[str, list[str]]
    body: bytes
    token: str | None

    def param(self, name: str, default: str = "") -> str:
        """Первое значение параметра строки запроса."""
        values = self.query.get(name)
        return values[0] if values else default

    def flag(self, name: str) -> bool:
        """Параметр-переключатель: ``?test=1``."""
        return self.param(name).lower() in _TRUE_VALUES

    def json_any(self) -> Any:
        """Тело запроса как JSON любого типа.

        :raises ValueError: тело не является корректным JSON.
        """
        if not self.body:
            return {}
        try:
            # ``utf-8-sig``: некоторые редакторы cookies отдают JSON с BOM.
            return json.loads(self.body.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError) as err:
            raise ValueError("Некорректный JSON в теле запроса") from err

    def json(self) -> dict[str, Any]:
        """Тело запроса как JSON-объект; иное считаем пустым объектом."""
        data = self.json_any()
        return data if isinstance(data, dict) else {}


@dataclass(frozen=True, slots=True)
class Response:
    """Ответ обработчика."""

    status: int = 200
    payload: Any = None
    session_token: str | None = None
    """Выдать браузеру cookie сессии."""
    end_session: bool = False
    """Удалить cookie сессии."""

    @classmethod
    def error(cls, status: int, message: str, code: str = "") -> Response:
        payload: dict[str, Any] = {"error": message}
        if code:
            payload["code"] = code
        return cls(status=status, payload=payload)


Handler = Callable[[Request], Response]


@dataclass(frozen=True, slots=True)
class Route:
    handler: Handler
    requires_auth: bool = True
    """По умолчанию эндпоинт закрыт: открытость задаётся явно."""


# ── Вход ───────────────────────────────────────────────────────────────────


def _auth_status(request: Request) -> Response:
    return Response(payload=auth.auth_status(request.token))


def _login(request: Request) -> Response:
    payload = request.json()
    username = str(payload.get("login") or payload.get("username") or "")
    token = auth.login(username, str(payload.get("password") or ""))
    return Response(payload=auth.auth_status(token), session_token=token)


def _logout(request: Request) -> Response:
    auth.logout(request.token)
    return Response(payload=auth.auth_status(None), end_session=True)


# ── Поиск ──────────────────────────────────────────────────────────────────


def _status(request: Request) -> Response:
    return Response(payload=SESSION.snapshot() | {"auth": auth.auth_status(request.token)})


def _categories(_: Request) -> Response:
    return Response(payload=catalog.list_categories())


def _regions(request: Request) -> Response:
    return Response(payload=regions.search_regions(request.param("q")))


def _start_search(request: Request) -> Response:
    payload = request.json()
    seller_skip = payload.get("seller_skip")
    if seller_skip is not None and not isinstance(seller_skip, list):
        seller_skip = []
    iphone_models = payload.get("iphone_models")
    if iphone_models is not None and not isinstance(iphone_models, list):
        iphone_models = None

    mode = MODE_URL if str(payload.get("mode") or MODE_QUERY) == MODE_URL else MODE_QUERY
    state = start_search(
        mode=mode,
        query=str(payload.get("query") or ""),
        web_url=str(payload.get("url") or ""),
        region_slug=str(payload.get("region") or payload.get("slug") or ""),
        category_id=str(payload.get("category") or ""),
        seller_skip=seller_skip,
        iphone_models=iphone_models,
    )
    feed.clear_ads()
    return Response(payload={"ok": True, **state})


def _stop_search(_: Request) -> Response:
    return Response(payload={"ok": True, **SESSION.stop()})


# ── Лента ──────────────────────────────────────────────────────────────────


def _ads(_: Request) -> Response:
    return Response(payload=feed.snapshot_ads())


def _reset_feed(_: Request) -> Response:
    return Response(payload={"ok": True, "cleared": feed.clear_ads()})


def _get_seller_blacklist(_: Request) -> Response:
    return Response(payload={"sellers": list(SESSION.seller_skip)})


def _set_seller_blacklist(request: Request) -> Response:
    sellers = request.json().get("sellers")
    if not isinstance(sellers, list):
        raise ValueError("Укажите sellers списком")
    state = SESSION.set_seller_skip(sellers)
    return Response(payload={"ok": True, "sellers": state.get("seller_skip") or []})


# ── Сессия Avito ───────────────────────────────────────────────────────────


def _avito_session(_: Request) -> Response:
    return Response(payload=user_session.session_status())


def _import_avito_session(request: Request) -> Response:
    session = user_session.save_session(request.json_any())
    return Response(
        payload={"ok": True, **user_session.session_status(), "label": session.get("label")}
    )


def _clear_avito_session(_: Request) -> Response:
    user_session.clear_session()
    return Response(payload={"ok": True, **user_session.session_status()})


def _avito_phone(request: Request) -> Response:
    payload = request.json()
    ad_id = payload.get("ad_id") or payload.get("id")
    if not ad_id:
        raise ValueError("Укажите ad_id")
    phone_key = str(payload.get("phone_key") or payload.get("key") or "").strip()
    return Response(payload=user_session.fetch_phone(ad_id, phone_key=phone_key or None))


# ── Баланс сервиса ─────────────────────────────────────────────────────────


def _resource_balance(_: Request) -> Response:
    balance = spfa.fetch_balance(load_settings().cookies_api_key)
    logger.debug(f"Баланс ресурса: {balance}")
    return Response(payload={"success": True, "balance": balance})


# ── Push ───────────────────────────────────────────────────────────────────


def _vapid(_: Request) -> Response:
    return Response(
        payload={"configured": push.is_configured(), "publicKey": push.vapid_public_key() or ""}
    )


def _push_subscribe(request: Request) -> Response:
    if not push.is_configured():
        return Response.error(503, "Web Push не настроен на сервере (VAPID)")
    total = push.subscribe(request.json())
    # Проверочное уведомление отправляем только при включении тумблера,
    # чтобы обычное переподписывание не будило пользователя.
    if request.flag("test"):
        push.send_test_push()
    return Response(payload={"ok": True, "subscriptions": total})


def _push_unsubscribe(request: Request) -> Response:
    total = push.unsubscribe(str(request.json().get("endpoint") or ""))
    return Response(payload={"ok": True, "subscriptions": total})


# ── Таблица ────────────────────────────────────────────────────────────────

# Открытые эндпоинты: состояние входа нужно экрану логина, справочники и
# публичный VAPID-ключ секретов не содержат.
ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/auth/status"): Route(_auth_status, requires_auth=False),
    ("GET", "/api/status"): Route(_status, requires_auth=False),
    ("GET", "/api/categories"): Route(_categories, requires_auth=False),
    ("GET", "/api/regions"): Route(_regions, requires_auth=False),
    ("GET", "/api/push/vapid"): Route(_vapid, requires_auth=False),
    ("POST", "/api/auth/login"): Route(_login, requires_auth=False),
    ("POST", "/api/auth/logout"): Route(_logout, requires_auth=False),
    ("GET", "/api/ads"): Route(_ads),
    ("GET", "/api/seller-blacklist"): Route(_get_seller_blacklist),
    ("GET", "/api/avito/session"): Route(_avito_session),
    ("GET", "/api/resource/balance"): Route(_resource_balance),
    ("POST", "/api/search"): Route(_start_search),
    ("POST", "/api/search/stop"): Route(_stop_search),
    ("POST", "/api/reset"): Route(_reset_feed),
    ("POST", "/api/seller-blacklist"): Route(_set_seller_blacklist),
    ("POST", "/api/avito/session/import"): Route(_import_avito_session),
    ("POST", "/api/avito/session/clear"): Route(_clear_avito_session),
    ("POST", "/api/avito/phone"): Route(_avito_phone),
    ("POST", "/api/push/subscribe"): Route(_push_subscribe),
    ("POST", "/api/push/unsubscribe"): Route(_push_unsubscribe),
}


def dispatch(method: str, request: Request) -> Response | None:
    """Найти обработчик и выполнить его.

    ``None`` — такого эндпоинта нет, вызывающий отдаст 404.

    Ошибки переводятся в коды ответа здесь, а не в каждом обработчике:
    :class:`ValueError` — вина запроса (400), :class:`~avito_monitor.spfa.SpfaError`
    — внешний сервис недоступен (502), остальное — наша ошибка (500).
    """
    route = ROUTES.get((method, request.path))
    if route is None:
        return None
    if route.requires_auth and not auth.is_authenticated(request.token):
        return Response(status=403, payload=dict(AUTH_REQUIRED_ERROR))

    try:
        return route.handler(request)
    except ValueError as err:
        return Response.error(400, str(err))
    except spfa.SpfaError as err:
        logger.warning(f"{method} {request.path}: внешний сервис — {err}")
        return Response.error(502, str(err))
    except Exception as err:
        logger.exception(f"{method} {request.path}: {err}")
        return Response.error(500, "Внутренняя ошибка сервера")
