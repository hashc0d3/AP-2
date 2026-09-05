"""Парсер Avito: JSON API через мобильный прокси и cookies, без браузера."""

from __future__ import annotations

import json
import os
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
_CALL_KEYS = (
    "canCall",
    "can_call",
    "hasPhone",
    "has_phone",
    "isPhoneVisible",
    "phoneAvailable",
    "isAvailableForCalls",
    "callAvailable",
)
_MESSAGE_KEYS = (
    "canWrite",
    "can_write",
    "canMessage",
    "hasMessenger",
    "has_messenger",
    "messengerAvailable",
    "isAvailableForMessages",
    "messageAvailable",
    "chatAvailable",
)
_HIDDEN_PHONE_KEYS = ("isPhoneHidden", "phoneHidden", "isPhoneDisabled")


def load_config() -> dict:
    with Path("config.toml").open("rb") as fh:
        return tomllib.load(fh)["avito"]


def load_session() -> dict:
    from cookie_pool import current, wait_ready_cookie

    session = current() or wait_ready_cookie()
    if not session:
        raise RuntimeError("Пул cookies пуст — нет готового набора")
    return session


def save_session(session: dict) -> None:
    from cookie_pool import save_slot

    save_slot(session)


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
    if proxy_string:
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


def _norm_text(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _add_text(out: list[str], value) -> None:
    if isinstance(value, str) and value.strip():
        out.append(value.strip())


def seller_texts(item: dict) -> list[str]:
    found: list[str] = []
    for key in ("sellerName", "userName", "shopName", "companyName"):
        _add_text(found, item.get(key))
    logo = item.get("userLogo")
    if isinstance(logo, dict):
        for key in ("link", "slug"):
            _add_text(found, logo.get(key))
    for blob in (item.get("user"), item.get("seller"), item.get("shop"), item.get("profile")):
        if not isinstance(blob, dict):
            continue
        for key in ("title", "name", "text", "value", "link", "slug"):
            _add_text(found, blob.get(key))
        profile = blob.get("profile")
        if isinstance(profile, dict):
            for key in ("title", "name", "link", "slug"):
                _add_text(found, profile.get(key))
    iva = item.get("iva")
    if not isinstance(iva, dict):
        return found
    for name, steps in iva.items():
        name_l = str(name).lower()
        if not any(part in name_l for part in ("user", "seller", "shop", "profile")):
            continue
        if not isinstance(steps, list):
            steps = [steps]
        for step in steps:
            if not isinstance(step, dict):
                continue
            payload = step.get("payload") if isinstance(step.get("payload"), dict) else step
            for key in ("title", "name", "text", "link", "slug"):
                _add_text(found, payload.get(key))
            profile = payload.get("profile") or payload.get("user") or {}
            if not isinstance(profile, dict):
                continue
            for key in ("title", "name", "text", "link", "slug"):
                _add_text(found, profile.get(key))
            badge = profile.get("badge") or profile.get("badges") or payload.get("badge")
            if isinstance(badge, dict):
                _add_text(found, badge.get("title") or badge.get("name"))
            elif isinstance(badge, str):
                _add_text(found, badge)
            elif isinstance(badge, list):
                for entry in badge:
                    if isinstance(entry, dict):
                        _add_text(found, entry.get("title") or entry.get("name"))
                    else:
                        _add_text(found, entry)
    return found


def seller_name(item: dict) -> str:
    texts = seller_texts(item)
    for text in texts:
        if "://" not in text and not text.startswith("/"):
            return text
    return texts[0] if texts else ""


def seller_is_skipped(item: dict, skip: list) -> bool:
    if not skip:
        return False
    blob = "".join(_norm_text(text) for text in seller_texts(item))
    if not blob:
        return False
    return any(_norm_text(word) in blob for word in skip if word)


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


def _as_bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _first_bool(obj: dict, keys: tuple[str, ...]) -> bool | None:
    for key in keys:
        if key in obj:
            found = _as_bool(obj.get(key))
            if found is not None:
                return found
    return None


def _iva_has_action(item: dict, *needles: str) -> bool:
    iva = item.get("iva")
    if not isinstance(iva, dict):
        return False
    wanted = tuple(n.lower() for n in needles)
    stack: list = list(iva.values())
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            title = str(cur.get("title") or cur.get("type") or cur.get("id") or "").lower()
            if any(word in title for word in wanted):
                return True
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return False


def contact_flags(item: dict) -> tuple[bool, bool]:
    blobs: list[dict] = [item]
    for key in ("contacts", "contact", "seller", "user"):
        blob = item.get(key)
        if isinstance(blob, dict):
            blobs.append(blob)

    can_call = None
    can_message = None
    for blob in blobs:
        if can_call is None:
            hidden = _first_bool(blob, _HIDDEN_PHONE_KEYS)
            if hidden is True:
                can_call = False
            elif hidden is False:
                can_call = True
        if can_call is None:
            can_call = _first_bool(blob, _CALL_KEYS)
        if can_call is None:
            can_call = _as_bool(blob.get("phone"))
        if can_message is None:
            can_message = _first_bool(blob, _MESSAGE_KEYS)
        if can_message is None:
            for key in ("messenger", "message", "chat"):
                can_message = _as_bool(blob.get(key))
                if can_message is not None:
                    break

    if can_call is None:
        can_call = _iva_has_action(item, "call", "phone", "позвон")
    if can_message is None:
        can_message = _iva_has_action(item, "message", "messenger", "write", "chat", "написа")
    return bool(can_call), bool(can_message)


def serialize_ad(item: dict) -> dict:
    images: list[str] = []
    _image_urls(item.get("gallery"), images)
    _image_urls(item.get("images"), images)
    can_call, can_message = contact_flags(item)
    published = published_at(item)
    age = age_seconds(item)
    published_text = format_age(age)
    if published and not published_text:
        published_text = published.astimezone().strftime("%d.%m.%Y %H:%M")
    elif published:
        published_text = f"{published.astimezone().strftime('%H:%M:%S')} · {published_text}"
    seller = seller_name(item)
    return {
        "id": item.get("id"),
        "title": item.get("title") or "без названия",
        "price": ((item.get("priceDetailed") or {}).get("string"))
        or str((item.get("priceDetailed") or {}).get("value") or "—"),
        "address": ad_address(item),
        "url": ad_url(item),
        "images": images[:12],
        "can_call": can_call,
        "can_message": can_message,
        "seller": seller,
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
    seller = seller_name(item)
    seller_part = f"  |  {seller}" if seller else ""
    can_call, can_message = contact_flags(item)
    contact_part = f"\nзвонок: {'да' if can_call else 'нет'}  |  сообщение: {'да' if can_message else 'нет'}"
    return f"{title}\n{age_part}{price}  |  {address}{seller_part}{contact_part}\n{url}"


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
    if os.name != "nt" or os.environ.get("SKIP_VPN_BYPASS") == "1":
        return
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
    from cookie_pool import buy_one, load_config

    return buy_one(load_config())


def unblock_cookies(session: dict, api_key: str, proxy_string: str) -> dict | None:
    from cookie_pool import load_config, unblock_one

    return unblock_one(session, load_config())


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
    logger.warning("Меняю IP")
    change_ip(cfg["proxy_change_url"])
    return session


def session_is_ready(session: dict | None) -> bool:
    if not session or not session.get("id"):
        return False
    from cookie_pool import load_slot

    fresh = load_slot(session["id"])
    if not fresh:
        return False
    if fresh.get("status") in {"blocked", "dead"}:
        return False
    return bool(fresh.get("unblock_ok"))


def refresh_cookies(cfg: dict, session: dict) -> dict:
    from cookie_pool import mark_blocked, wait_ready_cookie

    current_id = session.get("id")
    logger.warning(f"403/439: cookie id={current_id} сгорел, меняю IP и беру другой набор")
    mark_blocked(current_id)
    try:
        rotate_ip(cfg, session)
    except Exception as err:
        logger.warning(f"Не удалось сменить IP: {err}")
    nxt = wait_ready_cookie(exclude=current_id)
    if nxt:
        return nxt
    logger.warning("Пул не дал готовый набор, покупаю новый")
    return buy_cookies(cfg["cookies_api_key"], cfg["proxy_string"])


def recover_connection(cfg: dict, session: dict) -> dict:
    logger.warning("Прокси сбросил соединение, меняю только IP")
    try:
        return rotate_ip(cfg, session)
    except Exception as err:
        logger.warning(f"Не удалось сменить IP: {err}")
        return session


def fetch_items(cfg: dict, session: dict) -> tuple[dict, int, list[dict], bool]:
    from cookie_pool import wait_ready_cookie

    if not session_is_ready(session):
        session = wait_ready_cookie()
        if not session:
            logger.error("Нет готового cookie — не бью Avito заблокированным набором")
            return session, 0, [], True

    pages = max(1, int(cfg.get("pages") or 1))
    page_pause = max(0, int(cfg.get("pause_between_pages") or 2))
    items: list[dict] = []
    seen_ids: set[int] = set()
    status = 0
    blocked = False
    client = build_client(session, cfg["proxy_string"])
    logger.info(f"Цикл на cookie id={session.get('id')}")

    for page in range(1, pages + 1):
        url = api_url_for_page(cfg["api_url"], page)
        try:
            status, payload = fetch_page(client, url)
        except curl_requests.exceptions.RequestException:
            session = recover_connection(cfg, session)
            client = build_client(session, cfg["proxy_string"])
            status, payload = fetch_page(client, url)

        if status == 429:
            logger.warning("429: бан по IP, меняю IP, cookie оставляю")
            session = rotate_ip(cfg, session)
            client = build_client(session, cfg["proxy_string"])
            status, payload = fetch_page(client, url)

        if status in (403, 439):
            logger.warning(f"{status}: cookie или IP сгорели")
            session = refresh_cookies(cfg, session)
            client = build_client(session, cfg["proxy_string"])
            status, payload = fetch_page(client, url)

        if not payload and status == 200:
            logger.warning("200 без JSON — антибот или HTML вместо API, меняю IP")
            session = rotate_ip(cfg, session)
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
        if page < pages and page_pause:
            time.sleep(page_pause)

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
    skipped_seller = 0
    too_late = 0
    max_age = int(cfg.get("max_age") or 0)
    notify_max_age = int(cfg.get("notify_max_age") or 0)
    must_contain = cfg.get("title_must_contain") or []
    skip = cfg.get("title_skip") or []
    seller_skip = cfg.get("seller_skip") or []
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
        if seller_is_skipped(item, seller_skip):
            skipped_seller += 1
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
        + (f", продавец скрыт: {skipped_seller}" if skipped_seller else "")
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

    if to_show:
        flags = [contact_flags(item) for item in to_show]
        logger.info(
            f"Контакты: звонок {sum(c for c, _ in flags)}/{len(to_show)}, "
            f"сообщение {sum(m for _, m in flags)}/{len(to_show)}"
        )

    save_seen(seen)
    return session, seen, to_show, blocked


def main() -> None:
    from avito_search import apply_runtime, sleep_or_restart, snapshot as search_snapshot, wait_for_search
    from webui import clear_ads, publish_ads, start_server

    from log_setup import setup_file_log

    setup_file_log("parser.log")
    cfg = load_config()
    from cookie_service import start_background

    start_background()
    ensure_proxy_bypasses_vpn()
    start_server(int(cfg.get("web_port") or 8765))
    clear_ads()
    session = load_session()
    pause_min = max(3, int(cfg.get("pause_general") or 5))
    generation = 0
    logger.info(f"Новая сессия, cookies id={session.get('id')}. Жду «Начать поиск» в веб-интерфейсе")
    while True:
        search = wait_for_search(generation)
        generation = int(search["generation"])
        runtime = apply_runtime(cfg, search)
        seen = set()
        save_seen(seen)
        first = True
        query = search.get("query") or "все объявления"
        region_name = (search.get("region") or {}).get("name") or ""
        logger.info(f"Старт мониторинга: {query} · {region_name}")
        logger.info(f"Web URL: {runtime['url']}")
        logger.info(f"API URL: {runtime['api_url']}")
        while True:
            blocked = False
            try:
                session, seen, shown, blocked = parse_once(runtime, session, seen, first)
                if shown:
                    publish_ads([serialize_ad(item) for item in shown])
                first = False
            except Exception as err:
                blocked = True
                logger.error(f"Ошибка цикла: {err}")
                try:
                    session = rotate_ip(runtime, session)
                except Exception as rec_err:
                    logger.warning(f"Не удалось сменить IP: {rec_err}")
            from subscription import is_active

            if not is_active():
                logger.info("Подписка неактивна, останавливаю мониторинг")
                from avito_search import stop_search
                stop_search()
                break
            state = search_snapshot()
            if not state["running"] or state["generation"] != generation:
                if not state["running"]:
                    logger.info("Мониторинг остановлен, жду новый запуск")
                else:
                    logger.info("Поисковый запрос обновлён, перезапускаю мониторинг")
                break
            if blocked:
                logger.info(f"Запрос не прошёл, пауза {pause_min} сек. перед повтором")
                if not sleep_or_restart(pause_min, generation):
                    state = search_snapshot()
                    if not state["running"]:
                        logger.info("Мониторинг остановлен, жду новый запуск")
                    else:
                        logger.info("Поисковый запрос обновлён, перезапускаю мониторинг")
                    break
                continue
            logger.info(f"Пауза {pause_min} сек.")
            if not sleep_or_restart(pause_min, generation):
                state = search_snapshot()
                if not state["running"]:
                    logger.info("Мониторинг остановлен, жду новый запуск")
                else:
                    logger.info("Поисковый запрос обновлён, перезапускаю мониторинг")
                break


if __name__ == "__main__":
    main()
