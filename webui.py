"""Локальный веб-интерфейс ленты объявлений."""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from avito_search import list_categories, preview, search_regions, set_seller_skip, snapshot, start_search, stop_search
from avito_user import clear_user_session, fetch_user_phone, normalize_import, save_user_session, session_status
from resource_api import fetch_balance
from app_auth import auth_status, is_authenticated, login as app_login, logout as app_logout

_DISCONNECT = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, TimeoutError)

import requests
from loguru import logger

STATIC_DIR = Path(__file__).parent / "static"
ADS_PATH = Path("storage") / "ads.json"
MAX_ADS = 200

_lock = threading.Lock()
_ads: list[dict] = []
_listeners: list[queue.Queue] = []


def _load_disk() -> None:
    global _ads
    if not ADS_PATH.exists():
        return
    try:
        data = json.loads(ADS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            _ads = data[:MAX_ADS]
    except (OSError, ValueError):
        _ads = []


def _save_disk() -> None:
    ADS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ADS_PATH.write_text(json.dumps(_ads, ensure_ascii=False), encoding="utf-8")


def snapshot_ads() -> list[dict]:
    with _lock:
        return list(_ads)


def publish_ads(ads: list[dict]) -> None:
    if not ads:
        return
    with _lock:
        known = {item.get("id") for item in _ads}
        incoming = [ad for ad in ads if ad.get("id") not in known]
        if not incoming:
            return
        _ads[0:0] = incoming
        del _ads[MAX_ADS:]
        _save_disk()
        listeners = list(_listeners)
    for listener in listeners:
        listener.put(incoming)
    logger.info(f"В веб-ленту добавлено {len(incoming)} объявлений")


def clear_ads() -> int:
    with _lock:
        count = len(_ads)
        _ads.clear()
        _save_disk()
        listeners = list(_listeners)
    for listener in listeners:
        listener.put({"reset": True})
    logger.info(f"Лента очищена, удалено {count} объявлений")
    return count


def _allowed_image(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host.endswith("avito.st") or host.endswith("avito.ru") or "img.avito" in host


_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _allowed_hosts() -> frozenset[str] | None:
    raw = os.environ.get("ALLOWED_HOST", "").strip()
    if not raw:
        return None
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def handle_error(self, request, client_address) -> None:
        err = sys.exc_info()[1]
        if isinstance(err, _DISCONNECT) or (
            isinstance(err, OSError) and getattr(err, "winerror", None) in {10053, 10054}
        ):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def handle(self) -> None:
        try:
            super().handle()
        except _DISCONNECT:
            return

    def finish(self) -> None:
        try:
            super().finish()
        except _DISCONNECT:
            return

    def log_message(self, fmt: str, *args) -> None:
        first = str(args[0]) if args else ""
        if "/events" in first:
            return
        logger.debug("web " + (fmt % args))

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _json(self, code: int, data: dict | list) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _require_sub(self) -> bool:
        if is_authenticated():
            return True
        self._json(403, {"error": "Требуется вход", "code": "auth_required"})
        return False

    def _reject_foreign_host(self) -> bool:
        allowed = _allowed_hosts()
        if not allowed:
            return False
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        if host in _LOCAL_HOSTS:
            return False
        if host not in allowed:
            self.send_error(403)
            return True
        return False

    def _serve_static(self, rel: str) -> bool:
        path = (STATIC_DIR / rel).resolve()
        if STATIC_DIR.resolve() not in path.parents and path != STATIC_DIR.resolve():
            return False
        if not path.is_file():
            return False
        suffix = path.suffix.lower()
        types = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".svg": "image/svg+xml",
            ".map": "application/json; charset=utf-8",
            ".woff2": "font/woff2",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }
        cache = "no-store" if suffix == ".html" else "public, max-age=86400"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", types.get(suffix, "application/octet-stream"))
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True
        return True

    def do_GET(self) -> None:  # noqa: N802
        if self._reject_foreign_host():
            return
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            if not self._serve_static("index.html"):
                self.send_error(404)
            return
        if parsed.path.startswith("/assets/"):
            if not self._serve_static(parsed.path.lstrip("/")):
                self.send_error(404)
            return
        if parsed.path == "/api/auth/status":
            self._json(200, auth_status())
            return
        if parsed.path == "/api/billing/status":
            self._json(200, auth_status())
            return
        if parsed.path == "/api/ads":
            if not self._require_sub():
                return
            self._json(200, snapshot_ads())
            return
        if parsed.path == "/api/search":
            query = parse_qs(parsed.query)
            q = (query.get("q") or [""])[0]
            region = (query.get("region") or [""])[0]
            category = (query.get("category") or [""])[0]
            try:
                data = preview(q, region, category)
            except ValueError as err:
                self._send(400, json.dumps({"error": str(err)}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
                return
            self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            return
        if parsed.path == "/api/categories":
            body = json.dumps(list_categories(), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if parsed.path == "/api/regions":
            needle = (parse_qs(parsed.query).get("q") or [""])[0]
            body = json.dumps(search_regions(needle), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if parsed.path == "/api/status":
            self._json(200, snapshot() | {"auth": auth_status()})
            return
        if parsed.path == "/api/avito/session":
            if not self._require_sub():
                return
            self._json(200, session_status())
            return
        if parsed.path == "/api/resource/balance":
            if not self._require_sub():
                return
            try:
                data = fetch_balance()
            except ValueError as err:
                self._json(400, {"error": str(err)})
                return
            except RuntimeError as err:
                logger.warning(f"Resource balance: {err}")
                self._json(502, {"error": str(err)})
                return
            self._json(200, data)
            return
        if parsed.path == "/api/spfa/balance":
            if not self._require_sub():
                return
            try:
                data = fetch_balance()
            except ValueError as err:
                self._json(400, {"error": str(err)})
                return
            except RuntimeError as err:
                logger.warning(f"Resource balance: {err}")
                self._json(502, {"error": str(err)})
                return
            self._json(200, data)
            return
        if parsed.path == "/api/seller-blacklist":
            if not self._require_sub():
                return
            self._json(200, {"sellers": snapshot().get("seller_skip") or []})
            return
        if parsed.path == "/img":
            url = (parse_qs(parsed.query).get("u") or [""])[0]
            if not url or not _allowed_image(url):
                self.send_error(400)
                return
            try:
                response = requests.get(
                    url,
                    headers={
                        "Referer": "https://www.avito.ru/",
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/131.0.0.0 Safari/537.36"
                        ),
                    },
                    timeout=20,
                )
                response.raise_for_status()
            except requests.RequestException:
                self.send_error(502)
                return
            try:
                self.send_response(200)
                self.send_header("Content-Type", response.headers.get("Content-Type", "image/jpeg"))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("Content-Length", str(len(response.content)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(response.content)
            except _DISCONNECT:
                return
            self.close_connection = True
            return
        if parsed.path == "/events":
            if not is_authenticated():
                self._json(403, {"error": "Требуется вход", "code": "auth_required"})
                return
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            listener: queue.Queue = queue.Queue()
            with _lock:
                _listeners.append(listener)
            try:
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
                initial = snapshot_ads()
                if initial:
                    payload = json.dumps(initial, ensure_ascii=False)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                while True:
                    try:
                        batch = listener.get(timeout=20)
                        if isinstance(batch, dict) and batch.get("reset"):
                            self.wfile.write(b"event: reset\ndata: {}\n\n")
                        else:
                            payload = json.dumps(batch, ensure_ascii=False)
                            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except _DISCONNECT:
                return
            finally:
                with _lock:
                    if listener in _listeners:
                        _listeners.remove(listener)
            return
        self.send_error(404)

    def _read_json_body(self) -> dict | list:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8-sig"))

    def _read_json(self) -> dict:
        data = self._read_json_body()
        return data if isinstance(data, dict) else {}

    def do_POST(self) -> None:  # noqa: N802
        if self._reject_foreign_host():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/api/auth/login":
            try:
                payload = self._read_json()
                self._json(200, app_login(str(payload.get("login") or payload.get("username") or ""), str(payload.get("password") or "")))
            except ValueError as err:
                self._json(400, {"error": str(err)})
            return
        if parsed.path == "/api/auth/logout":
            self._json(200, app_logout())
            return
        if parsed.path == "/api/reset":
            if not self._require_sub():
                return
            count = clear_ads()
            self._json(200, {"ok": True, "cleared": count})
            return
        if parsed.path == "/api/seller-blacklist":
            if not self._require_sub():
                return
            try:
                payload = self._read_json()
            except ValueError:
                self._json(400, {"error": "Некорректный JSON"})
                return
            sellers = payload.get("sellers")
            if not isinstance(sellers, list):
                self._json(400, {"error": "Укажите sellers"})
                return
            data = set_seller_skip(sellers)
            self._json(200, {"ok": True, "sellers": data.get("seller_skip") or []})
            return
        if parsed.path == "/api/search":
            if not self._require_sub():
                return
            try:
                payload = self._read_json()
            except ValueError:
                self._json(400, {"error": "Некорректный JSON"})
                return
            query = str(payload.get("query") or "")
            region = str(payload.get("region") or payload.get("slug") or "")
            category = str(payload.get("category") or "")
            mode = str(payload.get("mode") or "query")
            url = str(payload.get("url") or "")
            seller_skip = payload.get("seller_skip")
            if seller_skip is not None and not isinstance(seller_skip, list):
                seller_skip = []
            iphone_models = payload.get("iphone_models")
            if iphone_models is not None and not isinstance(iphone_models, list):
                iphone_models = None
            try:
                if mode == "url":
                    data = start_search(
                        "",
                        "",
                        seller_skip=seller_skip,
                        iphone_models=iphone_models,
                        mode="url",
                        web_url=url or query,
                    )
                else:
                    data = start_search(
                        query,
                        region,
                        category,
                        seller_skip=seller_skip,
                        iphone_models=iphone_models,
                        mode="query",
                    )
            except ValueError as err:
                self._json(400, {"error": str(err)})
                return
            except RuntimeError as err:
                logger.warning(f"Старт поиска: {err}")
                self._json(502, {"error": str(err)})
                return
            clear_ads()
            self._json(200, {"ok": True, **data})
            return
        if parsed.path == "/api/search/stop":
            data = stop_search()
            self._json(200, {"ok": True, **data})
            return
        if parsed.path == "/api/avito/session/clear":
            if not self._require_sub():
                return
            clear_user_session()
            self._json(200, {"ok": True, **session_status()})
            return
        if parsed.path == "/api/avito/phone":
            if not self._require_sub():
                return
            try:
                payload = self._read_json()
            except ValueError:
                self._json(400, {"error": "Некорректный JSON"})
                return
            ad_id = payload.get("ad_id") or payload.get("id")
            if not ad_id:
                self._json(400, {"error": "Укажите ad_id"})
                return
            self._json(200, fetch_user_phone(ad_id))
            return
        if parsed.path == "/api/avito/session/import":
            if not self._require_sub():
                return
            try:
                payload = self._read_json_body()
                session = save_user_session(normalize_import(payload))
            except ValueError as err:
                self._json(400, {"error": str(err)})
                return
            self._json(200, {"ok": True, **session_status(), "label": session.get("label")})
            return
        self.send_error(404)


def start_server(port: int = 8765) -> None:
    _load_disk()
    host = os.environ.get("WEB_HOST", "127.0.0.1")
    try:
        httpd = QuietServer((host, port), Handler)
    except OSError as err:
        raise RuntimeError(f"Порт {port} занят — закройте старый parser.py и откройте http://{host}:{port}") from err
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    local_url = f"http://127.0.0.1:{port}"
    bind_url = local_url if host in {"0.0.0.0", "::"} else f"http://{host}:{port}"
    logger.info(f"Веб-интерфейс: {bind_url} (bind {host}:{port})")
    if os.environ.get("WEB_OPEN_BROWSER", "1") == "1" and host in {"127.0.0.1", "localhost", "::1"}:
        threading.Timer(1.2, lambda: webbrowser.open(local_url)).start()
    time.sleep(0.2)
