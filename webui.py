"""Локальный веб-интерфейс ленты объявлений."""

from __future__ import annotations

import json
import queue
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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


def snapshot() -> list[dict]:
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

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            path = STATIC_DIR / "index.html"
            self._send(200, path.read_bytes(), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/ads":
            body = json.dumps(snapshot(), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
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
                initial = snapshot()
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

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/reset":
            count = clear_ads()
            body = json.dumps({"ok": True, "cleared": count}, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        self.send_error(404)


def start_server(port: int = 8765) -> None:
    _load_disk()
    try:
        httpd = QuietServer(("127.0.0.1", port), Handler)
    except OSError as err:
        raise RuntimeError(f"Порт {port} занят — закройте старый parser.py и откройте http://127.0.0.1:{port}") from err
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    logger.info(f"Веб-интерфейс: {url}")
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    time.sleep(0.2)
