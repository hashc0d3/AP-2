"""Фоновое подключение Avito через Playwright (без прокси — только вход пользователя)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from loguru import logger

from avito_user import is_logged_in, save_user_session, session_status

STATUS_PATH = Path("storage") / "avito_connect.json"
PROFILE_DIR = Path("storage") / "avito_login_profile"
LOGIN_URLS = (
    "https://m.avito.ru/profile/login",
    "https://www.avito.ru/",
)
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
)
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || { runtime: {} };
"""


class BrowserClosedError(Exception):
    pass


def _read_status() -> dict[str, Any]:
    if not STATUS_PATH.exists():
        return {}
    for attempt in range(3):
        try:
            data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, json.JSONDecodeError):
            time.sleep(0.05 * (attempt + 1))
    return {}


def _write_status(**fields) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = _read_status()
    data.update(fields)
    data["updated_at"] = time.time()
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    for attempt in range(8):
        try:
            with STATUS_PATH.open("w", encoding="utf-8") as fh:
                fh.write(payload)
            return
        except OSError as err:
            if attempt == 7:
                raise err
            time.sleep(0.08 * (attempt + 1))


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def _normalize_state(state: dict) -> dict:
    running = bool(state.get("running"))
    pid = state.get("pid")
    connected = bool(session_status().get("connected"))
    updated = float(state.get("updated_at") or 0)
    stale = (time.time() - updated) > 180

    if running:
        alive = bool(pid) and _pid_alive(int(pid))
        if not alive or stale:
            running = False
            state["running"] = False
            state["pid"] = None
            if not connected:
                state["step"] = "idle"
                if not alive:
                    state["error"] = ""
            _write_status(**state)

    if not running and not connected:
        step = state.get("step") or "idle"
        if step in {"wait_login", "login", "browser", "starting", "proxy", "cookies"}:
            state["step"] = "idle"
            state["error"] = ""
            _write_status(**state)

    return state


def connect_status() -> dict:
    state = _normalize_state(_read_status())
    pid = state.get("pid")
    running = bool(state.get("running"))

    live = session_status()
    connected = bool(live.get("connected"))
    label = live.get("label") or state.get("label") or ""
    step = state.get("step") or ("ready" if connected else "idle")
    if connected and not running:
        step = "ready"
    elif not running and not connected and step not in {"error", "idle"}:
        step = "idle"
    return {
        "running": running,
        "step": step,
        "error": state.get("error") or "",
        "connected": connected,
        "label": label,
        "pid": pid,
    }


def reset_connect() -> dict:
    live = session_status()
    connected = bool(live.get("connected"))
    _write_status(
        running=False,
        pid=None,
        step="ready" if connected else "idle",
        error="",
        connected=connected,
        label=live.get("label") or "",
    )
    return connect_status()


def collect_session(context, user_agent: str, impersonate: str) -> dict | None:
    cookies = {
        item["name"]: item["value"]
        for item in context.cookies()
        if "avito.ru" in item.get("domain", "")
    }
    if not is_logged_in(cookies):
        return None
    return {
        "cookies": cookies,
        "user_agent": user_agent,
        "fingerprint": {
            "impersonate": impersonate,
            "headers": {
                "user-agent": user_agent,
                "accept": "application/json, text/plain, */*",
                "accept-language": "ru-RU,ru;q=0.9",
            },
        },
    }


def _launch_context(playwright):
    """Встроенный Chromium без расширений — меньше ERR_BLOCKED_BY_CLIENT."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR.resolve()),
        headless=False,
        args=args,
        ignore_default_args=["--enable-automation"],
        user_agent=MOBILE_UA,
        locale="ru-RU",
        viewport={"width": 390, "height": 844},
        is_mobile=True,
        has_touch=True,
        device_scale_factor=3,
        channel=None,
    )
    context.add_init_script(STEALTH_JS)
    logger.info("Браузер: встроенный Chromium (профиль avito_login_profile)")
    return context


def _open_login(page) -> None:
    if page.is_closed():
        raise BrowserClosedError("Окно браузера закрыто")
    _write_status(step="login")
    last_err = None
    for url in LOGIN_URLS:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(2000)
            title = (page.title() or "").lower()
            if "доступ ограничен" in title:
                raise RuntimeError("Avito ограничил доступ с вашего IP")
            return
        except BrowserClosedError:
            raise
        except Exception as err:
            last_err = err
            logger.warning(f"goto {url}: {err}")
    raise RuntimeError(f"Не открылся Avito: {last_err}")


def run_connect() -> None:
    from playwright.sync_api import sync_playwright

    _write_status(
        running=True,
        step="starting",
        error="",
        connected=False,
        label="",
        pid=os.getpid(),
    )

    user_agent = MOBILE_UA
    impersonate = "chrome131_android"

    _write_status(step="browser")
    with sync_playwright() as playwright:
        context = _launch_context(playwright)
        page = context.pages[0] if context.pages else context.new_page()
        _open_login(page)
        _write_status(step="wait_login")

        saved = False
        browser = context.browser
        while browser and browser.is_connected():
            time.sleep(3)
            try:
                payload = collect_session(context, user_agent, impersonate)
            except Exception as err:
                if "has been closed" in str(err).lower():
                    raise BrowserClosedError("Окно браузера закрыто") from err
                logger.warning(f"Чтение cookies: {err}")
                continue
            if not payload:
                continue
            saved_session = save_user_session(payload)
            saved = True
            _write_status(
                connected=True,
                label=saved_session.get("label") or "",
                step="ready",
                error="",
            )
            logger.info("Avito: вход выполнен, сессия сохранена")

        if not saved:
            raise RuntimeError(
                "Вход не выполнен. Если в консоли ERR_BLOCKED_BY_CLIENT — отключите AdGuard/блокировщик "
                "для avito.ru или войдите через обычный браузер и вставьте cookies."
            )


def start_connect() -> dict:
    status = connect_status()
    if status["running"]:
        return {"ok": True, "already_running": True, **status}

    script = Path(__file__).resolve().parent / "connect_avito.py"
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc = subprocess.Popen(
        [sys.executable, str(script)],
        cwd=str(Path.cwd()),
        creationflags=creationflags,
    )
    time.sleep(0.5)
    return {"ok": True, "pid": proc.pid, **connect_status()}


def _finish_connect(ok: bool, error: str = "") -> None:
    if ok:
        _write_status(running=False, step="ready", error="")
    else:
        _write_status(running=False, step="error", error=error)
