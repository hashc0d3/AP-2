"""HTTP-сервер веб-интерфейса.

Задач у сервера три: отдать собранный фронтенд из ``static/``, обслужить
JSON-эндпоинты из :mod:`~avito_monitor.web.routes` и держать поток событий
``/events``, по которому новые объявления приходят в браузер без опроса.

Взят стандартный ``ThreadingHTTPServer``: нагрузка — один-два браузера,
внешний веб-сервер и TLS обеспечивает nginx (см. ``docs/deployment.md``).
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import ParseResult, parse_qs, urlparse

import requests
from loguru import logger

from avito_monitor import auth
from avito_monitor.config import Settings
from avito_monitor.paths import STATIC_DIR
from avito_monitor.web import images
from avito_monitor.web.feed import FEED
from avito_monitor.web.routes import AUTH_REQUIRED_ERROR, Request, Response, dispatch

# Обрыв соединения браузером — обычное дело: пользователь обновил страницу
# или закрыл вкладку. Такие ошибки не нужно писать в лог.
_DISCONNECT_ERRORS = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, TimeoutError)
_WINSOCK_DISCONNECT = {10053, 10054}

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_BROWSER_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_OPEN_BROWSER_DELAY = 1.2
_STARTUP_GRACE = 0.2

# Как часто отправлять комментарий-пинг, чтобы прокси не закрыл SSE-поток.
SSE_PING_INTERVAL = 20.0

# Самое большое тело запроса — импорт cookies Avito, и он на два порядка
# меньше. Ограничение нужно, чтобы заявленный Content-Length не заставил
# сервер выделить память под что угодно.
MAX_BODY_BYTES = 1_048_576

CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".ico": "image/x-icon",
    ".js": "text/javascript; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".woff2": "font/woff2",
}
_NO_STORE_SUFFIXES = frozenset({".html", ".js"})
_STATIC_CACHE = "public, max-age=86400"

# Точные пути и префиксы, которые отдаются как файлы из static/.
_STATIC_PATHS = {
    "/": "index.html",
    "/index.html": "index.html",
    "/sw.js": "sw.js",
    "/manifest.webmanifest": "manifest.webmanifest",
}
_STATIC_PREFIXES = ("/assets/", "/icons/")


def _allowed_hosts() -> frozenset[str] | None:
    """Домены из ``ALLOWED_HOST``; ``None`` — проверка выключена."""
    raw = os.environ.get("ALLOWED_HOST", "").strip()
    if not raw:
        return None
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


class QuietServer(ThreadingHTTPServer):
    """Сервер, который не шумит в лог из-за отвалившихся браузеров."""

    daemon_threads = True
    # Порт не переиспользуем: иначе вторая копия parser молча «займёт»
    # порт первой, и пользователь будет смотреть в мёртвый интерфейс.
    allow_reuse_address = False

    def handle_error(self, request, client_address) -> None:
        err = sys.exc_info()[1]
        if isinstance(err, _DISCONNECT_ERRORS):
            return
        if isinstance(err, OSError) and getattr(err, "winerror", None) in _WINSOCK_DISCONNECT:
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    """Маршрутизация запроса: статика, поток событий, картинки, JSON API."""

    protocol_version = "HTTP/1.1"

    # ── Служебное ───────────────────────────────────────────────────────

    def handle(self) -> None:
        try:
            super().handle()
        except _DISCONNECT_ERRORS:
            return

    def finish(self) -> None:
        try:
            super().finish()
        except _DISCONNECT_ERRORS:
            return

    def log_message(self, fmt: str, *args) -> None:
        first = str(args[0]) if args else ""
        if "/events" in first:
            # Долгоживущий поток пишется в лог только при закрытии — толку нет.
            return
        logger.debug("web " + (fmt % args))

    # ── Отправка ответа ─────────────────────────────────────────────────

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        cache: str = "no-store",
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        for name, value in extra_headers or []:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _send_response(self, response: Response) -> None:
        body = json.dumps(response.payload, ensure_ascii=False).encode("utf-8")
        headers: list[tuple[str, str]] = []
        if response.session_token:
            headers.append(("Set-Cookie", self._session_cookie(response.session_token)))
        elif response.end_session:
            headers.append(("Set-Cookie", self._expired_cookie()))
        self._send_bytes(
            response.status,
            body,
            "application/json; charset=utf-8",
            extra_headers=headers,
        )

    # ── Cookie сессии ───────────────────────────────────────────────────

    @property
    def _is_https(self) -> bool:
        """Пришёл ли запрос по HTTPS (за nginx — по заголовку от него)."""
        return (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"

    def _session_cookie(self, token: str) -> str:
        """``HttpOnly``-cookie: JavaScript до токена не достаёт.

        ``SameSite=Lax`` защищает от CSRF, но не мешает открыть сайт по
        ссылке. Флаг ``Secure`` добавляем только для HTTPS — иначе локальный
        запуск по ``http://127.0.0.1`` не смог бы войти.
        """
        parts = [
            f"{auth.COOKIE_NAME}={token}",
            "Path=/",
            "HttpOnly",
            "SameSite=Lax",
            f"Max-Age={auth.session_ttl_seconds()}",
        ]
        if self._is_https:
            parts.append("Secure")
        return "; ".join(parts)

    def _expired_cookie(self) -> str:
        parts = [f"{auth.COOKIE_NAME}=", "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=0"]
        if self._is_https:
            parts.append("Secure")
        return "; ".join(parts)

    def _session_token(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            jar = SimpleCookie()
            jar.load(raw)
        except Exception:
            return None
        morsel = jar.get(auth.COOKIE_NAME)
        return morsel.value if morsel else None

    # ── Проверки ────────────────────────────────────────────────────────

    def _foreign_host(self) -> bool:
        """Запрос пришёл не на разрешённый домен.

        Защита от обращения по IP-адресу в обход nginx: без неё сайт
        открывался бы по ``http://<ip>:8765`` без TLS.
        """
        allowed = _allowed_hosts()
        if not allowed:
            return False
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        if host in LOCAL_HOSTS:
            return False
        if host not in allowed:
            self.send_error(403)
            return True
        return False

    def _read_body(self) -> bytes | None:
        """Считать тело запроса целиком.

        Читать нужно всегда, даже когда обработчику тело не нужно:
        непрочитанные байты остаются в сокете, и закрытие соединения
        превращается в RST — клиент теряет уже отправленный ответ.

        ``None`` — тело больше :data:`MAX_BODY_BYTES`, читать его мы не станем.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return b""
        if length <= 0:
            return b""
        if length > MAX_BODY_BYTES:
            return None
        return self.rfile.read(length)

    def _reject_large_body(self) -> None:
        # Тело осталось непрочитанным, поэтому соединение переиспользовать нельзя.
        self.close_connection = True
        self._send_response(Response.error(413, "Тело запроса слишком велико"))

    def _build_request(self, path: str, query: str, body: bytes) -> Request:
        return Request(
            path=path,
            query=parse_qs(query),
            body=body,
            token=self._session_token(),
        )

    # ── Статика ─────────────────────────────────────────────────────────

    def _serve_static(self, relative: str) -> bool:
        """Отдать файл из ``static/``. ``False`` — файла нет."""
        root = STATIC_DIR.resolve()
        path = (root / relative).resolve()
        # Защита от «../»: путь обязан остаться внутри static/.
        if path != root and root not in path.parents:
            return False
        if not path.is_file():
            return False

        suffix = path.suffix.lower()
        # HTML, JS и service worker не кэшируем: иначе после обновления
        # фронтенда браузер продолжит работать со старой сборкой.
        cache = (
            "no-store"
            if suffix in _NO_STORE_SUFFIXES or relative == "sw.js"
            else _STATIC_CACHE
        )
        self._send_bytes(
            200,
            path.read_bytes(),
            CONTENT_TYPES.get(suffix, "application/octet-stream"),
            cache=cache,
        )
        return True

    def _static_target(self, path: str) -> str | None:
        """Какой файл из ``static/`` соответствует пути запроса."""
        if path in _STATIC_PATHS:
            return _STATIC_PATHS[path]
        if path.startswith(_STATIC_PREFIXES):
            return path.lstrip("/")
        return None

    # ── Прокси картинок ─────────────────────────────────────────────────

    def _serve_image(self, query: str) -> None:
        url = (parse_qs(query).get("u") or [""])[0]
        try:
            body, content_type = images.fetch(url)
        except ValueError:
            self.send_error(400)
            return
        except requests.RequestException:
            self.send_error(502)
            return
        try:
            self._send_bytes(200, body, content_type, cache=images.CACHE_CONTROL)
        except _DISCONNECT_ERRORS:
            return

    # ── Поток событий ───────────────────────────────────────────────────

    def _serve_events(self) -> None:
        """SSE-поток: лента объявлений в реальном времени."""
        if not auth.is_authenticated(self._session_token()):
            self._send_response(Response(status=403, payload=dict(AUTH_REQUIRED_ERROR)))
            return

        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        # Просим nginx не буферизовать: иначе события копятся и приходят пачкой.
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        with FEED.subscription() as listener:
            try:
                self._write_sse(b": connected\n\n")
                initial = FEED.snapshot()
                if initial:
                    self._send_sse_data(initial)
                while True:
                    try:
                        batch = listener.get(timeout=SSE_PING_INTERVAL)
                    except queue.Empty:
                        self._write_sse(b": ping\n\n")
                        continue
                    if isinstance(batch, dict) and batch.get("reset"):
                        self._write_sse(b"event: reset\ndata: {}\n\n")
                    else:
                        self._send_sse_data(batch)
            except _DISCONNECT_ERRORS:
                return

    def _send_sse_data(self, payload: object) -> None:
        data = json.dumps(payload, ensure_ascii=False)
        self._write_sse(f"data: {data}\n\n".encode("utf-8"))

    def _write_sse(self, chunk: bytes) -> None:
        self.wfile.write(chunk)
        self.wfile.flush()

    # ── Точки входа ─────────────────────────────────────────────────────

    def do_GET(self) -> None:  # noqa: N802 — имя задаёт BaseHTTPRequestHandler
        body = self._read_body()
        if body is None:
            self._reject_large_body()
            return
        if self._foreign_host():
            return
        parsed = urlparse(self.path)

        target = self._static_target(parsed.path)
        if target is not None:
            if not self._serve_static(target):
                self.send_error(404)
            return
        if parsed.path == "/img":
            self._serve_image(parsed.query)
            return
        if parsed.path == "/events":
            self._serve_events()
            return

        self._dispatch("GET", parsed, body)

    def do_POST(self) -> None:  # noqa: N802 — имя задаёт BaseHTTPRequestHandler
        body = self._read_body()
        if body is None:
            self._reject_large_body()
            return
        if self._foreign_host():
            return
        self._dispatch("POST", urlparse(self.path), body)

    def _dispatch(self, method: str, parsed: ParseResult, body: bytes) -> None:
        response = dispatch(method, self._build_request(parsed.path, parsed.query, body))
        if response is None:
            self.send_error(404)
            return
        self._send_response(response)


def start_server(settings: Settings) -> QuietServer:
    """Поднять веб-интерфейс в фоновом потоке.

    :raises RuntimeError: порт занят другой копией приложения.
    """
    FEED.set_max_age(settings.max_age)
    FEED.load_from_disk()
    host = os.environ.get("WEB_HOST", "127.0.0.1")
    port = settings.web_port

    try:
        httpd = QuietServer((host, port), Handler)
    except OSError as err:
        raise RuntimeError(
            f"Порт {port} занят — закройте старую копию приложения "
            f"и откройте http://{host}:{port}"
        ) from err

    threading.Thread(target=httpd.serve_forever, name="web-server", daemon=True).start()

    local_url = f"http://127.0.0.1:{port}"
    bind_url = local_url if host in {"0.0.0.0", "::"} else f"http://{host}:{port}"
    logger.info(f"Веб-интерфейс: {bind_url} (bind {host}:{port})")

    if os.environ.get("WEB_OPEN_BROWSER", "1") == "1" and host in _BROWSER_HOSTS:
        threading.Timer(_OPEN_BROWSER_DELAY, lambda: webbrowser.open(local_url)).start()
    # Небольшая пауза, чтобы строка про адрес интерфейса не потерялась
    # среди логов запуска мониторинга.
    time.sleep(_STARTUP_GRACE)
    return httpd
