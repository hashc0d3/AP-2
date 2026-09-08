"""Замеры скорости парсера и разбор его логов.

Запуск (из корня проекта)::

    python -m avito_monitor.tools.bench_speed --probe 8      # keep-alive против нового клиента
    python -m avito_monitor.tools.bench_speed --api 5        # реальные запросы к API Avito
    python -m avito_monitor.tools.bench_speed --ipchange     # сколько занимает смена IP
    python -m avito_monitor.tools.bench_speed --log logs/parser.log

Режим ``--log`` работает без сети и прокси: он разбирает уже накопленный
лог и показывает период цикла, длительность запроса, долю сбоев и возраст
объявлений в момент попадания в ленту.
"""

from __future__ import annotations

import argparse
import re
import statistics
import time
from datetime import datetime
from itertools import pairwise
from pathlib import Path

import requests
from curl_cffi import requests as curl_requests

from avito_monitor.avito import catalog
from avito_monitor.avito.items import extract_items
from avito_monitor.avito.regions import default_region
from avito_monitor.config import Settings, load_settings
from avito_monitor.cookies.pool import next_cookie, usable_slots
from avito_monitor.net.client import build_client
from avito_monitor.net.proxy import PROBE_URL

IP_URL = "https://api.ipify.org/?format=text"
IMPERSONATE = "chrome131_android"
REQUEST_TIMEOUT = 15.0
IP_CHECK_TIMEOUT = 8.0
IP_WAIT_LIMIT = 30.0


def _proxies(proxy_string: str) -> dict[str, str]:
    url = f"http://{proxy_string}"
    return {"http": url, "https": url}


def _stats(values: list[float], unit: str = "мс") -> str:
    if not values:
        return "нет данных"
    scale = 1000 if unit == "мс" else 1
    data = sorted(value * scale for value in values)
    return (
        f"мин {data[0]:.0f} · медиана {statistics.median(data):.0f} · "
        f"среднее {statistics.fmean(data):.0f} · макс {data[-1]:.0f} {unit} (n={len(data)})"
    )


# ── Замеры с сетью ─────────────────────────────────────────────────────────


def probe(settings: Settings, count: int) -> None:
    """Сравнить новый клиент на каждый запрос с одним переиспользуемым.

    Разница показывает, сколько стоит установка TCP и TLS через мобильный
    прокси, — именно её экономит кольцо клиентов.
    """
    proxies = _proxies(settings.proxy_string)
    print(f"\n=== Прокси: новый клиент против переиспользуемого ({count} запросов) ===")

    fresh: list[float] = []
    for attempt in range(count):
        client = curl_requests.Session(impersonate=IMPERSONATE)
        client.proxies = proxies
        started = time.perf_counter()
        try:
            client.get(PROBE_URL, timeout=REQUEST_TIMEOUT)
            fresh.append(time.perf_counter() - started)
        except Exception as err:
            print(f"  новый клиент #{attempt + 1}: ошибка {err}")
        finally:
            client.close()

    reused: list[float] = []
    client = curl_requests.Session(impersonate=IMPERSONATE)
    client.proxies = proxies
    try:
        try:
            client.get(PROBE_URL, timeout=REQUEST_TIMEOUT)  # прогрев соединения
        except Exception as err:
            print(f"  прогрев не прошёл: {err}")
        for attempt in range(count):
            started = time.perf_counter()
            try:
                client.get(PROBE_URL, timeout=REQUEST_TIMEOUT)
                reused.append(time.perf_counter() - started)
            except Exception as err:
                print(f"  переиспользование #{attempt + 1}: ошибка {err}")
    finally:
        client.close()

    print(f"  новый клиент каждый раз: {_stats(fresh)}")
    print(f"  один клиент (keep-alive): {_stats(reused)}")
    if fresh and reused:
        saved = statistics.median(fresh) - statistics.median(reused)
        print(f"  экономия на переиспользовании: {saved * 1000:.0f} мс за цикл")


def api(settings: Settings, count: int, api_url: str = "") -> None:
    """Реальные запросы к API Avito текущим набором cookies из пула."""
    url = api_url or catalog.build_api_url(default_region().slug, catalog.default_category().id)
    print(f"\n=== Запросы к API Avito ({count}) ===")
    if not url:
        print("  не удалось собрать API URL — передайте его через --api-url")
        return

    slots = usable_slots()
    print(f"  готовых cookies в пуле: {len(slots)}")
    slot = next_cookie() if slots else None
    if not slot:
        print("  нет готового набора — дождитесь сервиса пула")
        return

    client = build_client(slot, settings.proxy_string)
    times: list[float] = []
    try:
        for attempt in range(count):
            started = time.perf_counter()
            try:
                response = client.get(url, timeout=REQUEST_TIMEOUT)
                elapsed = time.perf_counter() - started
                items = extract_items(response.json()) if response.status_code == 200 else []
                times.append(elapsed)
                print(
                    f"  #{attempt + 1}: {response.status_code}, "
                    f"объявлений {len(items)}, {elapsed * 1000:.0f} мс"
                )
            except Exception as err:
                print(f"  #{attempt + 1}: ошибка {err}")
            if attempt + 1 < count:
                time.sleep(1)
    finally:
        client.close()
    print(f"  итог: {_stats(times)}")


def _current_ip(proxies: dict[str, str], timeout: float = IP_CHECK_TIMEOUT) -> str:
    try:
        return requests.get(IP_URL, proxies=proxies, timeout=timeout).text.strip()
    except requests.RequestException:
        return ""


def ipchange(settings: Settings) -> None:
    """Сколько реально занимает смена IP: до готовности туннеля и до нового адреса."""
    proxies = _proxies(settings.proxy_string)
    print("\n=== Смена IP ===")
    before = _current_ip(proxies)
    print(f"  IP до смены: {before or 'не определился'}")

    started = time.perf_counter()
    try:
        requests.get(settings.proxy_change_url, params={"format": "json"}, timeout=20)
    except requests.RequestException as err:
        print(f"  запрос смены не прошёл: {err}")
        return
    print(f"  ответ на запрос смены: {(time.perf_counter() - started) * 1000:.0f} мс")

    usable_at: float | None = None
    changed_at: float | None = None
    deadline = time.perf_counter() + IP_WAIT_LIMIT
    while time.perf_counter() < deadline:
        now = _current_ip(proxies, timeout=4)
        elapsed = time.perf_counter() - started
        if now and usable_at is None:
            usable_at = elapsed
            print(f"  туннель отвечает: {usable_at:.2f} с (IP {now})")
        if now and before and now != before:
            changed_at = elapsed
            print(f"  IP сменился на {now}: {changed_at:.2f} с")
            break
        time.sleep(0.4)

    if changed_at is None:
        print(
            f"  новый IP за {IP_WAIT_LIMIT:.0f} с не подтвердился "
            "(возможно, провайдер выдал тот же адрес)"
        )
    measured = changed_at or usable_at
    if measured:
        print(
            f"  ip_change_wait в config.toml сейчас {settings.ip_change_wait:.0f} с — "
            f"запас {settings.ip_change_wait - measured:.1f} с"
        )


# ── Разбор лога ────────────────────────────────────────────────────────────

_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)")
_CYCLE_RE = re.compile(r"Цикл на cookie id=(\S+)")
_RECEIVED_RE = re.compile(r"Получено объявлений: (\d+)")
_AGE_RE = re.compile(r"Новых объявлений: (\d+), возраст (\d+)[–-](\d+) сек")
_IP_RE = re.compile(r"Новый IP: (\d+\.\d+\.\d+\.\d+)")

# Метка в отчёте -> фрагмент строки лога.
_EVENTS = {
    "смена IP": "Меняю IP",
    "429 (бан по IP)": "429: бан по IP",
    "403/439 (cookie сгорел)": "сгорел, беру другой набор",
    "200 без JSON (антибот)": "200 без JSON",
    "сбой сети": "Сбой сети",
    "страница без JSON": "Не удалось получить JSON",
    "циклов без данных": "Цикл без объявлений",
    "пул без готовых cookies": "Пул без готовых cookies",
}

# Пауза длиннее означает перезапуск парсера, а не медленный цикл.
_MAX_CYCLE_GAP = 600


def _timestamp(line: str) -> float | None:
    match = _TIMESTAMP_RE.match(line)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S.%f").timestamp()


def log_report(path: str, since: str = "") -> None:
    """Разобрать лог парсера: период цикла, сбои, возраст объявлений."""
    file = Path(path)
    print(f"\n=== Разбор лога {file} ===")
    if not file.exists():
        print("  файл не найден")
        return

    lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
    if since:
        # Лог пишется несколько дней подряд: без отсечки прошлые запуски
        # смешаются с текущим и медианы будут ни о чём.
        lines = [line for line in lines if line[: len(since)] >= since]
        print(f"  только записи с {since}: строк {len(lines)}")
        if not lines:
            print("  за этот период записей нет")
            return

    cycle_starts: list[float] = []
    request_times: list[float] = []
    proxy_ips: list[str] = []
    ages_min: list[float] = []
    ages_max: list[float] = []
    ads_total = 0
    counters = dict.fromkeys(_EVENTS, 0)
    pending_cycle: float | None = None

    for line in lines:
        ts = _timestamp(line)
        if ts is None:
            continue
        if _CYCLE_RE.search(line):
            cycle_starts.append(ts)
            pending_cycle = ts
        elif _RECEIVED_RE.search(line) and pending_cycle is not None:
            request_times.append(ts - pending_cycle)
            pending_cycle = None

        age_match = _AGE_RE.search(line)
        if age_match:
            ads_total += int(age_match.group(1))
            ages_min.append(float(age_match.group(2)))
            ages_max.append(float(age_match.group(3)))

        ip_match = _IP_RE.search(line)
        if ip_match:
            proxy_ips.append(ip_match.group(1))

        for name, needle in _EVENTS.items():
            if needle in line:
                counters[name] += 1

    periods = [
        later - earlier
        for earlier, later in pairwise(cycle_starts)
        if 0 < later - earlier < _MAX_CYCLE_GAP
    ]

    print(f"  циклов опроса: {len(cycle_starts)}")
    if periods:
        print(f"  период между циклами: {_stats(periods, 'с')}")
    if request_times:
        print(f"  запрос к API Avito: {_stats(request_times)}")

    print("\n  Сбои и потери:")
    triggered = {name: count for name, count in counters.items() if count}
    if not triggered:
        print("    не зафиксировано")
    for name, count in triggered.items():
        share = count / max(1, len(cycle_starts)) * 100
        print(f"    {name}: {count} раз ({share:.1f}% циклов)")

    if proxy_ips:
        _report_subnets(proxy_ips, len(cycle_starts))

    print("\n  Возраст объявлений в момент попадания в ленту:")
    if not ages_min:
        print("    строк с возрастом не найдено")
        return
    print(f"    циклов с новыми: {len(ages_min)}, объявлений: {ads_total}")
    print(f"    самое свежее в цикле: {_stats(ages_min, 'с')}")
    print(f"    самое старое в цикле: {_stats(ages_max, 'с')}")
    for limit in (15, 30, 60):
        share = sum(1 for value in ages_min if value <= limit) / len(ages_min) * 100
        print(f"    быстрее {limit} с: {share:.0f}% циклов")


def _report_subnets(proxy_ips: list[str], cycles: int) -> None:
    """Из скольких подсетей прокси выдал адреса.

    Avito считает лимит по подсети, а не по отдельному адресу: ротация
    внутри одного ``/21`` счётчик не сбрасывает. Если все адреса из одной
    подсети — смена IP не помогает, и нужен другой тариф прокси.
    """
    blocks = {
        ".".join(ip.split(".")[:2]) + f".{int(ip.split('.')[2]) // 8 * 8}.0/21" for ip in proxy_ips
    }
    print("\n  Адреса от прокси:")
    print(f"    смен IP: {len(proxy_ips)}, уникальных адресов: {len(set(proxy_ips))}")
    print(f"    подсетей /21: {len(blocks)} — {', '.join(sorted(blocks))}")
    if len(blocks) == 1:
        per_ip = cycles / max(1, len(proxy_ips))
        print(
            "    все адреса из одной подсети: смена IP лимит Avito не сбрасывает "
            f"(в среднем {per_ip:.1f} запроса на адрес)"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Замеры скорости парсера Avito")
    parser.add_argument("--probe", type=int, metavar="N", help="сравнить keep-alive и новый клиент")
    parser.add_argument("--api", type=int, metavar="N", help="замерить запросы к API Avito")
    parser.add_argument("--api-url", default="", help="свой API URL для режима --api")
    parser.add_argument("--ipchange", action="store_true", help="замерить смену IP")
    parser.add_argument("--log", metavar="PATH", help="разобрать лог парсера")
    parser.add_argument(
        "--since",
        metavar="'YYYY-MM-DD HH:MM'",
        default="",
        help="учитывать записи с этого момента: можно только дату, можно с временем",
    )
    args = parser.parse_args()

    if not any([args.probe, args.api, args.ipchange, args.log]):
        parser.print_help()
        return

    # Разбор лога сети не требует, поэтому настройки читаем только при нужде.
    if args.log:
        log_report(args.log, args.since)
    if not any([args.probe, args.api, args.ipchange]):
        return

    settings = load_settings()
    if args.probe:
        probe(settings, args.probe)
    if args.api:
        api(settings, args.api, args.api_url)
    if args.ipchange:
        ipchange(settings)


if __name__ == "__main__":
    main()
