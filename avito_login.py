"""Вход в Avito по телефону + SMS (Playwright, ввод из приложения)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from loguru import logger

from avito_connect import BrowserClosedError, PROFILE_DIR, STEALTH_JS, collect_session
from avito_user import save_user_session, session_status
from subscription import normalize_phone

STATUS_PATH = Path("storage") / "avito_login.json"
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
LOGIN_URL = "https://www.avito.ru/#login?authsrc=h"
IMPERSONATE = "chrome131"
WAIT_COMMAND_SEC = 600
_NOISE_HOSTS = (
    "sentry",
    "pixel",
    "metric.js",
    "adhigh",
    "gonet-ads",
    "simbad.pro",
    "doubleclick",
    "googlesyndication",
    "mc.yandex",
)


def _mask_phone(phone: str) -> str:
    digits = re.sub(r"\D+", "", phone)
    if len(digits) < 6:
        return phone
    return f"+{digits[0]} {digits[1:4]} ***-**-{digits[-2:]}"


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
    stale = (time.time() - updated) > WAIT_COMMAND_SEC

    if running:
        alive = bool(pid) and _pid_alive(int(pid))
        if not alive or stale:
            running = False
            state["running"] = False
            state["pid"] = None
            if not connected and state.get("step") not in {"ready", "error"}:
                state["step"] = "idle"
                state["error"] = state.get("error") or ("Таймаут входа" if stale else "")
            _write_status(**state)

    if not running and not connected:
        step = state.get("step") or "idle"
        if step in {"starting", "browser", "sending_sms", "verifying", "need_code"}:
            state["step"] = "idle"
            state["error"] = ""
            _write_status(**state)

    return state


def login_status() -> dict:
    state = _normalize_state(_read_status())
    live = session_status()
    connected = bool(live.get("connected"))
    step = state.get("step") or ("ready" if connected else "idle")
    if connected and not state.get("running"):
        step = "ready"
    phone = state.get("phone") or ""
    return {
        "running": bool(state.get("running")),
        "step": step,
        "error": state.get("error") or "",
        "connected": connected,
        "label": live.get("label") or state.get("label") or "",
        "phone": phone,
        "pid": state.get("pid"),
    }


def reset_login() -> dict:
    live = session_status()
    connected = bool(live.get("connected"))
    _write_status(
        running=False,
        pid=None,
        step="ready" if connected else "idle",
        error="",
        connected=connected,
        label=live.get("label") or "",
        phone="",
        pending={},
    )
    return login_status()


def _ensure_pending() -> dict:
    state = _read_status()
    pending = state.get("pending")
    if not isinstance(pending, dict):
        pending = {}
    return pending


def _set_pending(**fields) -> None:
    pending = _ensure_pending()
    pending.update(fields)
    _write_status(pending=pending)


def _pop_pending(key: str) -> str | None:
    pending = _ensure_pending()
    value = pending.pop(key, None)
    _write_status(pending=pending)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def queue_phone(phone: str) -> dict:
    number = normalize_phone(phone)
    state = login_status()
    if state["connected"]:
        return {"ok": True, "already_connected": True, **login_status()}
    if state["running"] and state["step"] in {"sending_sms", "verifying"}:
        raise ValueError("Подождите завершения текущего шага")
    if not state["running"]:
        start_login()
    _write_status(
        step="phone_queued",
        error="",
        phone=_mask_phone(number),
        pending={"phone": number},
    )
    return {"ok": True, "phone": _mask_phone(number), **login_status()}


def queue_code(code: str) -> dict:
    digits = re.sub(r"\D+", "", code or "")
    if len(digits) < 4:
        raise ValueError("Введите код из SMS")
    state = login_status()
    if state["connected"]:
        return {"ok": True, "already_connected": True, **login_status()}
    if not state["running"]:
        raise ValueError("Сначала запросите код на номер телефона")
    if state["step"] not in {"need_code", "phone_queued", "sending_sms"}:
        raise ValueError("Сначала дождитесь SMS-кода")
    pending = _ensure_pending()
    pending["code"] = digits
    _write_status(pending=pending, step="code_queued", error="")
    return {"ok": True, **login_status()}


def start_login() -> dict:
    status = login_status()
    if status["running"]:
        return {"ok": True, "already_running": True, **status}

    script = Path(__file__).resolve().parent / "connect_avito_sms.py"
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc = subprocess.Popen(
        [sys.executable, str(script)],
        cwd=str(Path.cwd()),
        creationflags=creationflags,
    )
    _write_status(
        running=True,
        pid=proc.pid,
        step="starting",
        error="",
        connected=False,
        label="",
        phone="",
        pending={},
    )
    time.sleep(0.4)
    return {"ok": True, "pid": proc.pid, **login_status()}


def _should_block(url: str, resource_type: str) -> bool:
    if resource_type in {"image", "media", "font"}:
        return True
    lower = url.lower()
    return any(host in lower for host in _NOISE_HOSTS)


def _launch_login_context(playwright):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR.resolve()),
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        ignore_default_args=["--enable-automation"],
        user_agent=DESKTOP_UA,
        locale="ru-RU",
        viewport={"width": 1280, "height": 800},
    )
    context.add_init_script(STEALTH_JS)

    def _route(route) -> None:
        req = route.request
        if _should_block(req.url, req.resource_type):
            route.abort()
        else:
            route.continue_()

    context.route("**/*", _route)
    logger.info("Браузер: Chromium для SMS-входа (desktop)")
    return context


def _wait_pending(key: str, timeout: float = WAIT_COMMAND_SEC) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = _pop_pending(key)
        if value:
            return value
        time.sleep(0.4)
    raise TimeoutError(f"Не получен {key} из приложения")


def _first_visible(page, selectors: list[str], timeout: float = 8000):
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=timeout)
            return loc
        except PlaywrightTimeout:
            continue
    return None


def _click_text(page, patterns: list[str]) -> bool:
    for text in patterns:
        try:
            loc = page.get_by_role("button", name=re.compile(text, re.I)).first
            if loc.is_visible(timeout=1500):
                loc.click(timeout=5000)
                return True
        except Exception:
            pass
        try:
            loc = page.locator(f"text=/{text}/i").first
            if loc.is_visible(timeout=1500):
                loc.click(timeout=5000)
                return True
        except Exception:
            pass
    return False


def _open_login(page) -> None:
    if page.is_closed():
        raise BrowserClosedError("Окно браузера закрыто")
    _write_status(step="browser")
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=90_000)
    page.wait_for_timeout(3500)
    title = (page.title() or "").lower()
    body = (page.content() or "").lower()
    if "доступ ограничен" in title or "too many requests" in body:
        raise RuntimeError("Avito ограничил доступ с вашего IP — попробуйте позже или вставьте cookies")
    _click_text(page, [r"телефон", r"по номеру", r"войти", r"продолжить"])


def _fill_phone(page, phone: str) -> None:
    digits = re.sub(r"\D+", "", phone)
    local = digits[1:] if digits.startswith("7") else digits
    input_loc = _first_visible(
        page,
        [
            'input[type="tel"]',
            'input[inputmode="tel"]',
            'input[name*="phone" i]',
            'input[autocomplete="tel"]',
            'input[placeholder*="телефон" i]',
            'input[placeholder*="phone" i]',
        ],
        timeout=12_000,
    )
    if not input_loc:
        raise RuntimeError("Не найдено поле телефона на странице Avito")
    input_loc.click(timeout=5000)
    input_loc.fill("")
    for variant in (phone, digits, local, f"+7{local}"):
        input_loc.fill(variant)
        if input_loc.input_value(timeout=2000):
            break
    if not _click_text(page, [r"получить код", r"продолжить", r"далее", r"войти"]):
        input_loc.press("Enter")
    page.wait_for_timeout(2500)


def _fill_code(page, code: str) -> None:
    code_loc = _first_visible(
        page,
        [
            'input[autocomplete="one-time-code"]',
            'input[name*="code" i]',
            'input[placeholder*="код" i]',
            'input[inputmode="numeric"]',
            'input[type="tel"]',
        ],
        timeout=20_000,
    )
    if code_loc:
        code_loc.click(timeout=5000)
        code_loc.fill(code)
    else:
        boxes = page.locator('input[inputmode="numeric"], input[type="tel"]').all()
        visible = [box for box in boxes if box.is_visible()]
        if len(visible) >= len(code):
            for idx, digit in enumerate(code):
                visible[idx].click(timeout=3000)
                visible[idx].fill(digit)
        else:
            raise RuntimeError("Не найдено поле для SMS-кода")
    if not _click_text(page, [r"подтвердить", r"войти", r"продолжить", r"готово"]):
        page.keyboard.press("Enter")
    page.wait_for_timeout(3000)


def _captcha_visible(page) -> bool:
    try:
        if page.locator("text=/капч/i").first.is_visible(timeout=500):
            return True
    except Exception:
        pass
    try:
        if page.locator('[class*="captcha" i]').first.is_visible(timeout=500):
            return True
    except Exception:
        pass
    return False


def run_sms_login() -> None:
    from playwright.sync_api import sync_playwright

    _write_status(
        running=True,
        step="starting",
        error="",
        connected=False,
        label="",
        pid=os.getpid(),
        pending={},
    )

    with sync_playwright() as playwright:
        context = _launch_login_context(playwright)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            _open_login(page)
            phone = _wait_pending("phone")
            _write_status(step="sending_sms", phone=_mask_phone(phone))
            _fill_phone(page, phone)
            if _captcha_visible(page):
                raise RuntimeError("Avito запросил капчу — войдите через cookies или обычный браузер")
            _write_status(step="need_code")
            code = _wait_pending("code")
            _write_status(step="verifying")
            _fill_code(page, code)
            if _captcha_visible(page):
                raise RuntimeError("Avito запросил капчу — войдите через cookies или обычный браузер")

            saved = False
            browser = context.browser
            for _ in range(45):
                try:
                    payload = collect_session(context, DESKTOP_UA, IMPERSONATE)
                except Exception as err:
                    if "has been closed" in str(err).lower():
                        raise BrowserClosedError("Окно браузера закрыто") from err
                    logger.warning(f"Чтение cookies: {err}")
                    time.sleep(2)
                    continue
                if payload:
                    saved_session = save_user_session(payload)
                    saved = True
                    _write_status(
                        connected=True,
                        label=saved_session.get("label") or "",
                        step="ready",
                        error="",
                    )
                    logger.info("Avito SMS login: сессия сохранена")
                    break
                time.sleep(2)
                if browser and not browser.is_connected():
                    break

            if not saved:
                raise RuntimeError("Вход не завершён — проверьте код или попробуйте снова")
        finally:
            try:
                context.close()
            except Exception:
                pass

    _write_status(running=False, pid=None)


def finish_login(ok: bool, error: str = "") -> None:
    live = session_status()
    connected = bool(live.get("connected"))
    if ok or connected:
        _write_status(running=False, pid=None, step="ready", error="", connected=True, label=live.get("label") or "")
    else:
        _write_status(running=False, pid=None, step="error", error=error or "Ошибка входа")
