"""Открывает выдачу Avito в видимом браузере через мобильный прокси."""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests
import tomllib
from loguru import logger
from playwright.sync_api import sync_playwright

STORAGE_DIR = Path("storage")
COOKIES_PATH = STORAGE_DIR / "cookies.json"
SPFA_COOKIES_URL = "https://spfa.pro/api/cookies/mobile/"

HIDE_PROMOTED_JS = """
() => {
  const hide = () => {
    document.querySelectorAll('[data-marker="item"]').forEach((el) => {
      const text = el.innerText || "";
      if (text.includes("Продвинуто")) {
        el.style.display = "none";
      }
    });
  };
  hide();
  new MutationObserver(hide).observe(document.body, { childList: true, subtree: true });
}
"""


def load_config() -> dict:
    with Path("config.toml").open("rb") as fh:
        data = tomllib.load(fh)
    cfg = data["avito"]
    cfg["proxy_string"] = (cfg.get("proxy_string") or "").strip()
    return cfg


def parse_proxy(proxy_string: str) -> dict:
    login, rest = proxy_string.split(":", 1)
    password, hostport = rest.rsplit("@", 1)
    return {
        "server": f"http://{hostport}",
        "username": login,
        "password": password,
    }


def require_proxy(proxy_string: str) -> str:
    proxy_url = f"http://{proxy_string}"
    response = requests.get(
        "https://api.ipify.org",
        proxies={"http": proxy_url, "https": proxy_url},
        timeout=20,
    )
    response.raise_for_status()
    ip = response.text.strip()
    logger.info(f"Мобильный прокси работает, IP {ip}")
    return ip


def load_saved_cookies() -> dict | None:
    if not COOKIES_PATH.exists():
        return None
    try:
        data = json.loads(COOKIES_PATH.read_text(encoding="utf-8"))
        if data.get("cookies") and data.get("user_agent"):
            logger.info(f"Загружаю сохранённые cookies id={data.get('id')}")
            return data
    except (OSError, ValueError) as err:
        logger.warning(f"Не удалось прочитать cookies: {err}")
    return None


def buy_cookies(api_key: str, proxy_string: str) -> dict:
    logger.info("Покупаю cookies на spfa.pro под текущий IP прокси...")
    response = requests.post(
        SPFA_COOKIES_URL,
        json={"api_key": api_key, "mobile": True, "proxy": proxy_string},
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=40,
    )
    if not response.ok:
        raise RuntimeError(f"spfa.pro вернул {response.status_code}: {response.text[:400]}")

    payload = response.json()
    results = payload.get("results") or {}
    cookies = results.get("cookies")
    fingerprint = results.get("fingerprint") or {}
    headers = fingerprint.get("headers") if isinstance(fingerprint, dict) else {}
    user_agent = results.get("user_agent") or (
        headers.get("user-agent") if isinstance(headers, dict) else None
    )

    if not payload.get("success") or not cookies or not user_agent:
        raise RuntimeError(f"Неполные cookies от spfa.pro: {payload}")

    data = {
        "id": results.get("id"),
        "cookies": cookies,
        "user_agent": user_agent,
        "fingerprint": fingerprint,
        "mobile": results.get("mobile", True),
        "saved_at": time.time(),
    }
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    COOKIES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Cookies получены, id={data['id']}")
    time.sleep(3)
    return data


def cookies_for_playwright(raw_cookies: dict) -> list[dict]:
    return [
        {
            "name": str(name),
            "value": str(value),
            "domain": ".avito.ru",
            "path": "/",
        }
        for name, value in raw_cookies.items()
    ]


def extra_headers(fingerprint: dict | None) -> dict:
    if not isinstance(fingerprint, dict):
        return {}
    headers = fingerprint.get("headers") or {}
    if not isinstance(headers, dict):
        return {}
    skip = {"host", "content-length", "connection", "proxy-authorization", "user-agent"}
    return {key: str(value) for key, value in headers.items() if key.lower() not in skip}


def open_avito() -> None:
    cfg = load_config()
    url = cfg["url"]
    proxy_string = cfg["proxy_string"]
    if not proxy_string:
        raise RuntimeError("В config.toml не указан proxy_string")

    require_proxy(proxy_string)
    proxy = parse_proxy(proxy_string)

    session = load_saved_cookies()
    if not session:
        session = buy_cookies(cfg["cookies_api_key"], proxy_string)

    user_agent = session["user_agent"]
    is_mobile = bool(session.get("mobile", True) or " Mobile " in user_agent)

    logger.info(f"Открываю браузер через мобильный прокси: {url}")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=False,
            proxy=proxy,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-http2",
                "--start-maximized",
            ],
        )
        context_kwargs = {
            "proxy": proxy,
            "user_agent": user_agent,
            "locale": "ru-RU",
            "extra_http_headers": extra_headers(session.get("fingerprint")),
            "ignore_https_errors": True,
        }
        if is_mobile:
            context_kwargs.update(
                {
                    "viewport": {"width": 390, "height": 844},
                    "is_mobile": True,
                    "has_touch": True,
                    "device_scale_factor": 3,
                }
            )
        else:
            context_kwargs["viewport"] = {"width": 1280, "height": 900}

        context = browser.new_context(**context_kwargs)
        context.add_cookies(cookies_for_playwright(session["cookies"]))
        page = context.new_page()

        def goto_avito() -> str:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(2500)
            return page.title() or ""

        title = goto_avito()
        blocked = "доступ ограничен" in title.lower()
        if blocked:
            logger.warning("Avito показал блок. Покупаю свежие cookies и обновляю страницу...")
            session = buy_cookies(cfg["cookies_api_key"], proxy_string)
            context.clear_cookies()
            context.add_cookies(cookies_for_playwright(session["cookies"]))
            title = goto_avito()
            blocked = "доступ ограничен" in title.lower()

        if blocked:
            logger.warning("Блок остался. Пройдите капчу в окне браузера, если она есть.")

        if cfg.get("ignore_promotion", True):
            try:
                page.evaluate(HIDE_PROMOTED_JS)
                logger.info("Продвинутые объявления скрыты на странице")
            except Exception as err:
                logger.warning(f"Не удалось скрыть продвинутые: {err}")

        logger.info(f"Страница открыта через прокси: {title}")
        logger.info("Закройте окно браузера, чтобы завершить скрипт")

        while browser.is_connected():
            time.sleep(1)


if __name__ == "__main__":
    from log_setup import setup_file_log

    setup_file_log("browser.log")
    open_avito()
