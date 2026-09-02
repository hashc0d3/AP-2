"""Парсер Avito: JSON API через мобильный прокси и cookies, без браузера."""

from __future__ import annotations

import json
import socket
import subprocess
import time
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests as std_requests
import tomllib
from curl_cffi import requests as curl_requests
from loguru import logger

STORAGE_DIR = Path("storage")
COOKIES_PATH = STORAGE_DIR / "cookies.json"
SEEN_PATH = STORAGE_DIR / "seen.json"
SPFA_COOKIES_URL = "https://spfa.pro/api/cookies/mobile/"
SPFA_UNBLOCK_URL = "https://spfa.pro/api/unblock/"
PROXY_HOSTS = ("mproxy.site", "fproxy.site", "bproxy.site", "gproxy.site")


def load_config() -> dict:
    with Path("config.toml").open("rb") as fh:
        return tomllib.load(fh)["avito"]


def load_session() -> dict:
    if not COOKIES_PATH.exists():
        raise RuntimeError("Нет storage/cookies.json — сначала получите cookies через spfa")
    return json.loads(COOKIES_PATH.read_text(encoding="utf-8"))


def save_session(session: dict) -> None:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    COOKIES_PATH.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")


def load_seen() -> set[int]:
    if not SEEN_PATH.exists():
        return set()
    try:
        return {int(x) for x in json.loads(SEEN_PATH.read_text(encoding="utf-8"))}
    except (OSError, ValueError, TypeError):
        return set()


def save_seen(seen: set[int]) -> None:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(json.dumps(sorted(seen)), encoding="utf-8")


def build_client(session: dict, proxy_string: str) -> curl_requests.Session:
    fingerprint = session.get("fingerprint") or {}
    headers = dict(fingerprint.get("headers") or {})
    user_agent = session.get("user_agent") or headers.get("user-agent")
    impersonate = fingerprint.get("impersonate") or "chrome131_android"
    if user_agent:
        headers["user-agent"] = user_agent
    headers.setdefault("referer", "https://www.avito.ru/")
    headers.setdefault("accept", "application/json, text/plain, */*")

    client = curl_requests.Session(impersonate=impersonate)
    client.headers.update(headers)
    client.cookies.update(session.get("cookies") or {})
    proxy_url = f"http://{proxy_string}"
    client.proxies = {"http": proxy_url, "https": proxy_url}
    return client


def api_url_for_page(api_url: str, page: int) -> str:
    parts = urlsplit(api_url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in {"p", "page"}]
    query.append(("page", str(page)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def extract_items(payload: dict) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    for candidate in (
        payload.get("catalog"),
        result.get("catalog") if isinstance(result, dict) else None,
        result,
        payload,
    ):
        if isinstance(candidate, dict) and isinstance(candidate.get("items"), list):
            return [item for item in candidate["items"] if isinstance(item, dict) and item.get("id")]
    return []


def is_promoted(item: dict) -> bool:
    iva = item.get("iva") or {}
    steps = iva.get("DateInfoStep") or []
    for step in steps:
        payload = (step or {}).get("payload") or {}
        for vas in payload.get("vas") or []:
            if (vas or {}).get("title") == "Продвинуто":
                return True
    return False


def published_at(item: dict) -> datetime | None:
    ts = item.get("sortTimeStamp")
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def age_seconds(item: dict) -> int | None:
    published = published_at(item)
    if not published:
        return None
    return int((datetime.now(timezone.utc) - published).total_seconds())


def is_fresh(item: dict, max_age: int) -> bool:
    if not max_age:
        return True
    age = age_seconds(item)
    return age is not None and age <= max_age


def format_age(seconds: int | None) -> str:
    if seconds is None:
        return ""
    if seconds < 60:
        return f"{seconds} сек назад"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} мин назад"
    return f"{minutes // 60} ч назад"


def title_matches(item: dict, must_contain: list, skip: list) -> bool:
    title = (item.get("title") or "").lower()
    if must_contain and not any(word.lower() in title for word in must_contain if word):
        return False
    if any(word.lower() in title for word in skip if word):
        return False
    return True


def ad_address(item: dict) -> str:
    geo = item.get("geo") or {}
    if isinstance(geo, dict):
        for key in ("formattedAddress", "address", "city"):
            val = geo.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        refs = geo.get("geoReferences") or []
        names = [
            (ref.get("content") or "").strip()
            for ref in refs
            if isinstance(ref, dict) and (ref.get("content") or "").strip()
        ]
        if names:
            return ", ".join(names)
    loc = item.get("location")
    if isinstance(loc, dict):
        name = (loc.get("name") or loc.get("title") or "").strip()
        if name:
            return name
    if isinstance(loc, str) and loc.strip():
        return loc.strip()
    return ""


def ad_url(item: dict) -> str:
    path = (item.get("urlPath") or "").split("?", 1)[0]
    if path:
        return f"https://www.avito.ru{path}"
    ad_id = item.get("id")
    return f"https://www.avito.ru/{ad_id}" if ad_id else ""


def _image_urls(value, out: list[str]) -> None:
    if isinstance(value, str) and value.startswith("http"):
        if value not in out:
            out.append(value)
        return
    if isinstance(value, dict):
        sized = []
        for key, val in value.items():
            if isinstance(val, str) and val.startswith("http"):
                area = 0
                if "x" in str(key):
                    try:
                        width, height = str(key).split("x", 1)
                        area = int(width) * int(height.split("/")[0])
                    except ValueError:
                        area = 0
                sized.append((area, val))
        if sized:
            sized.sort(reverse=True)
            _image_urls(sized[0][1], out)
            return
        for val in value.values():
            _image_urls(val, out)
        return
    if isinstance(value, list):
        for val in value:
            _image_urls(val, out)


def serialize_ad(item: dict) -> dict:
    images: list[str] = []
    _image_urls(item.get("gallery"), images)
    _image_urls(item.get("images"), images)
    phone = item.get("phone")
    if not isinstance(phone, str) or not phone.strip():
        phone = None
    contacts = item.get("contacts") or {}
    published = published_at(item)
    age = age_seconds(item)
    published_text = format_age(age)
    if published and not published_text:
        published_text = published.astimezone().strftime("%d.%m.%Y %H:%M")
    elif published:
        published_text = f"{published.astimezone().strftime('%H:%M:%S')} · {published_text}"
    return {
        "id": item.get("id"),
        "title": item.get("title") or "без названия",
        "price": ((item.get("priceDetailed") or {}).get("string"))
        or str((item.get("priceDetailed") or {}).get("value") or "—"),
        "address": ad_address(item),
        "url": ad_url(item),
        "images": images[:12],
        "phone": phone,
        "can_call": bool(phone or contacts.get("phone")),
        "published": published_text,
        "ts": int(time.time()),
    }


def format_ad(item: dict) -> str:
    title = item.get("title") or "без названия"
    price = ((item.get("priceDetailed") or {}).get("string")) or str(
        (item.get("priceDetailed") or {}).get("value") or "?"
    )
    address = ad_address(item)
    url = ad_url(item)
    age = format_age(age_seconds(item))
    age_part = f"{age}  |  " if age else ""
    return f"{title}\n{age_part}{price}  |  {address}\n{url}"


def wifi_gateway() -> str | None:
    ps = (
        "Get-NetRoute -DestinationPrefix '0.0.0.0/0' | "
        "Where-Object { $_.NextHop -ne '0.0.0.0' -and $_.InterfaceAlias -notmatch 'Amnezia|WireGuard|TAP|OpenVPN' } | "
        "Sort-Object RouteMetric | Select-Object -First 1 -ExpandProperty NextHop"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    gateway = (result.stdout or "").strip()
    return gateway or None


def ensure_proxy_bypasses_vpn() -> None:
    """VPN оставляем для смены IP; трафик к шлюзу мобильного прокси идёт напрямую по Wi‑Fi."""
    gateway = wifi_gateway()
    if not gateway:
        logger.warning("Не нашёл Wi‑Fi шлюз, split-tunnel для прокси не настроен")
        return
    for host in PROXY_HOSTS:
        try:
            ip = socket.gethostbyname(host)
        except OSError as err:
            logger.warning(f"Не резолвится {host}: {err}")
            continue
        added = subprocess.run(
            ["route", "add", ip, "mask", "255.255.255.255", gateway],
            capture_output=True,
            text=True,
            encoding="cp866",
            errors="replace",
        )
        if added.returncode == 0:
            logger.info(f"Маршрут мимо VPN: {host} ({ip}) → {gateway}")
        else:
            logger.debug(f"Маршрут {ip} уже есть или нет прав: {added.stdout.strip() or added.stderr.strip()}")


def change_ip(change_url: str) -> None:
    logger.info(f"Меняю IP: {change_url.split('?')[0]}")
    try:
        response = std_requests.get(change_url, params={"format": "json"}, timeout=20)
        response.raise_for_status()
    except std_requests.RequestException as err:
        raise RuntimeError(f"Смена IP не прошла: {err}") from err

    payload = {}
    content_type = response.headers.get("content-type", "")
    if "json" in content_type or response.text.strip().startswith("{"):
        try:
            payload = response.json()
        except ValueError:
            payload = {}
    new_ip = payload.get("new_ip") if isinstance(payload, dict) else None
    logger.info(f"Новый IP: {new_ip or payload or 'ок'}")
    time.sleep(8)


def buy_cookies(api_key: str, proxy_string: str) -> dict:
    logger.info("Покупаю новые cookies на spfa.pro...")
    response = std_requests.post(
        SPFA_COOKIES_URL,
        json={"api_key": api_key, "mobile": True, "proxy": proxy_string},
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=40,
    )
    if not response.ok:
        raise RuntimeError(f"spfa.pro {response.status_code}: {response.text[:300]}")
    payload = response.json()
    results = payload.get("results") or {}
    cookies = results.get("cookies")
    fingerprint = results.get("fingerprint") or {}
    headers = fingerprint.get("headers") if isinstance(fingerprint, dict) else {}
    user_agent = results.get("user_agent") or (headers.get("user-agent") if isinstance(headers, dict) else None)
    if not payload.get("success") or not cookies or not user_agent:
        raise RuntimeError(f"Неполные cookies: {payload}")
    session = {
        "id": results.get("id"),
        "cookies": cookies,
        "user_agent": user_agent,
        "fingerprint": fingerprint,
        "mobile": results.get("mobile", True),
        "saved_at": time.time(),
    }
    save_session(session)
    logger.info(f"Cookies получены, id={session['id']}")
    time.sleep(3)
    return session


def unblock_cookies(session: dict, api_key: str, proxy_string: str) -> None:
    cookie_id = session.get("id")
    if not cookie_id:
        return
    logger.info(f"Пробую разблокировать cookies id={cookie_id}")
    std_requests.post(
        SPFA_UNBLOCK_URL,
        json={"id": cookie_id, "api_key": api_key, "proxy": proxy_string},
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=30,
    )
    time.sleep(5)


def fetch_page(client: curl_requests.Session, url: str, attempts: int = 3) -> tuple[int, dict | None]:
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            response = client.get(url, timeout=30)
            if response.status_code in (403, 429, 439):
                return response.status_code, None
            response.raise_for_status()
            try:
                return response.status_code, response.json()
            except ValueError:
                logger.warning(
                    f"Ответ не JSON, status={response.status_code}, начало={response.text[:180]!r}"
                )
                return response.status_code, None
        except curl_requests.exceptions.RequestException as err:
            last_error = err
            logger.warning(f"Сбой сети ({attempt}/{attempts}): {err}")
            time.sleep(2 * attempt)
    raise last_error


def rotate_ip(cfg: dict, session: dict) -> dict:
    logger.warning("Меняю только IP, cookies не трогаю")
    change_ip(cfg["proxy_change_url"])
    return session


def refresh_cookies(cfg: dict, session: dict) -> dict:
    logger.warning("403/439: обновляю cookies, IP не меняю")
    unblock_cookies(session, cfg["cookies_api_key"], cfg["proxy_string"])
    return buy_cookies(cfg["cookies_api_key"], cfg["proxy_string"])


def recover_connection(cfg: dict, session: dict) -> dict:
    logger.warning("Прокси сбросил соединение, меняю только IP")
    try:
        return rotate_ip(cfg, session)
    except Exception as err:
        logger.warning(f"Не удалось сменить IP: {err}")
        return session


def fetch_items(cfg: dict, session: dict) -> tuple[dict, int, list[dict], bool]:
    client = build_client(session, cfg["proxy_string"])
    pages = max(1, int(cfg.get("pages") or 1))
    items: list[dict] = []
    seen_ids: set[int] = set()
    status = 0
    blocked = False

    for page in range(1, pages + 1):
        url = api_url_for_page(cfg["api_url"], page)
        try:
            status, payload = fetch_page(client, url)
        except curl_requests.exceptions.RequestException:
            session = recover_connection(cfg, session)
            client = build_client(session, cfg["proxy_string"])
            status, payload = fetch_page(client, url)

        if status == 429:
            logger.warning("429: меняю только IP")
            session = rotate_ip(cfg, session)
            client = build_client(session, cfg["proxy_string"])
            status, payload = fetch_page(client, url)

        if status in (403, 439):
            logger.warning(f"{status}: беру новые cookies")
            session = refresh_cookies(cfg, session)
            client = build_client(session, cfg["proxy_string"])
            status, payload = fetch_page(client, url)

        if not payload:
            logger.error(f"Не удалось получить JSON, status={status}, page={page}")
            blocked = True
            break

        page_items = extract_items(payload)
        for item in page_items:
            ad_id = int(item["id"])
            if ad_id in seen_ids:
                continue
            seen_ids.add(ad_id)
            items.append(item)
        if len(page_items) < 10:
            break

    return session, status, items, blocked


def parse_once(cfg: dict, session: dict, seen: set[int], first: bool) -> tuple[dict, set[int], list, bool]:
    session, status, items, blocked = fetch_items(cfg, session)
    if not items:
        if status:
            logger.error(f"Не удалось получить JSON, status={status}")
        return session, seen, [], True

    logger.info(f"Получено объявлений: {len(items)}")

    ordinary = []
    promoted = 0
    skipped_title = 0
    too_late = 0
    max_age = int(cfg.get("max_age") or 0)
    notify_max_age = int(cfg.get("notify_max_age") or 0)
    must_contain = cfg.get("title_must_contain") or []
    skip = cfg.get("title_skip") or []
    for item in items:
        try:
            ad_id = int(item["id"])
        except (TypeError, ValueError, KeyError):
            continue
        # Запоминаем всё, что уже было в выдаче, иначе «Продвинуто»
        # позже всплывёт как будто только что появилось.
        already_seen = ad_id in seen
        seen.add(ad_id)
        if cfg.get("ignore_promotion", True) and is_promoted(item):
            promoted += 1
            continue
        if not is_fresh(item, max_age):
            continue
        if not title_matches(item, must_contain, skip):
            skipped_title += 1
            continue
        if notify_max_age and not is_fresh(item, notify_max_age):
            too_late += 1
            continue
        if already_seen and not first:
            continue
        ordinary.append(item)
    ordinary.sort(key=lambda item: item.get("sortTimeStamp") or 0, reverse=True)
    logger.info(
        f"Свежих: {len(ordinary)}, продвинутых скрыто: {promoted}"
        + (f", не iPhone: {skipped_title}" if skipped_title else "")
        + (f", поздно в выдаче: {too_late}" if too_late else "")
    )

    if first:
        logger.info(f"Старт: в ленту {len(ordinary)} объявлений, дальше только новые")
        to_show = ordinary
    elif ordinary:
        ages = [age_seconds(item) for item in ordinary]
        ages = [age for age in ages if age is not None]
        freshness = f", {min(ages)}–{max(ages)} сек" if ages else ""
        logger.info(f"Новых объявлений: {len(ordinary)}{freshness}")
        to_show = ordinary
    else:
        logger.info("Новых объявлений нет")
        to_show = []

    for item in to_show:
        print("\n" + "=" * 40)
        print(format_ad(item))
        print("=" * 40)

    save_seen(seen)
    return session, seen, to_show, blocked


def main() -> None:
    from webui import clear_ads, publish_ads, start_server

    Path("logs").mkdir(exist_ok=True)
    logger.add("logs/parser.log", rotation="2 MB", retention="3 days")
    cfg = load_config()
    ensure_proxy_bypasses_vpn()
    start_server(int(cfg.get("web_port") or 8765))
    clear_ads()
    session = load_session()
    seen = set()
    save_seen(seen)
    pause_min = max(3, int(cfg.get("pause_general") or 5))
    logger.info(f"Новая сессия, мониторю {cfg['api_url']}")
    first = True
    while True:
        blocked = False
        try:
            session, seen, shown, blocked = parse_once(cfg, session, seen, first)
            if shown:
                publish_ads([serialize_ad(item) for item in shown])
            first = False
        except Exception as err:
            blocked = True
            logger.error(f"Ошибка цикла: {err}")
            try:
                session = rotate_ip(cfg, session)
            except Exception as rec_err:
                logger.warning(f"Не удалось сменить IP: {rec_err}")
        if blocked:
            logger.info("После блока сразу следующий запрос, без паузы")
            continue
        logger.info(f"Пауза {pause_min} сек.")
        time.sleep(pause_min)


if __name__ == "__main__":
    main()
