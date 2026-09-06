"""Замеры скорости парсера: прокси, переиспользование клиента, смена IP, задержка выдачи.

Примеры:
    python bench_speed.py --probe 8
    python bench_speed.py --api 5
    python bench_speed.py --ipchange
    python bench_speed.py --log logs/parser.log
"""

from __future__ import annotations

import argparse
import re
import statistics
import time
from pathlib import Path

import requests as std_requests
import tomllib
from curl_cffi import requests as curl_requests

PROBE_URL = "https://www.avito.ru/robots.txt"
IP_URL = "https://api.ipify.org/?format=text"


def load_config() -> dict:
    with Path("config.toml").open("rb") as fh:
        return tomllib.load(fh)["avito"]


def proxies_for(proxy_string: str) -> dict:
    url = f"http://{proxy_string}"
    return {"http": url, "https": url}


def fmt(values: list[float], unit: str = "мс") -> str:
    if not values:
        return "нет данных"
    scale = 1000 if unit == "мс" else 1
    data = sorted(v * scale for v in values)
    median = statistics.median(data)
    return (
        f"мин {data[0]:.0f} · медиана {median:.0f} · "
        f"среднее {statistics.fmean(data):.0f} · макс {data[-1]:.0f} {unit} "
        f"(n={len(data)})"
    )


def probe(cfg: dict, count: int) -> None:
    """Сравнение: новый клиент на каждый запрос против одного переиспользуемого."""
    proxies = proxies_for(cfg["proxy_string"])
    print(f"\n=== Прокси: новый клиент против переиспользуемого ({count} запросов) ===")

    fresh: list[float] = []
    for i in range(count):
        started = time.perf_counter()
        try:
            client = curl_requests.Session(impersonate="chrome131_android")
            client.proxies = proxies
            client.get(PROBE_URL, timeout=15)
            fresh.append(time.perf_counter() - started)
        except Exception as err:
            print(f"  новый клиент #{i + 1}: ошибка {err}")
        finally:
            try:
                client.close()
            except Exception:
                pass

    reused: list[float] = []
    client = curl_requests.Session(impersonate="chrome131_android")
    client.proxies = proxies
    try:
        try:
            client.get(PROBE_URL, timeout=15)  # прогрев соединения
        except Exception as err:
            print(f"  прогрев не прошёл: {err}")
        for i in range(count):
            started = time.perf_counter()
            try:
                client.get(PROBE_URL, timeout=15)
                reused.append(time.perf_counter() - started)
            except Exception as err:
                print(f"  переиспользование #{i + 1}: ошибка {err}")
    finally:
        client.close()

    print(f"  новый клиент каждый раз: {fmt(fresh)}")
    print(f"  один клиент (keep-alive): {fmt(reused)}")
    if fresh and reused:
        saved = statistics.median(fresh) - statistics.median(reused)
        print(f"  экономия на переиспользовании: {saved * 1000:.0f} мс за цикл")


def api(cfg: dict, count: int) -> None:
    """Реальные запросы к API Avito текущим cookie из пула."""
    from cookie_pool import next_cookie, usable_slots
    from parser import build_client, extract_items

    slots = usable_slots()
    print(f"\n=== Запросы к API Avito ({count}) ===")
    print(f"  готовых cookies в пуле: {len(slots)}")
    session = next_cookie() if slots else None
    if not session:
        print("  нет готового cookie — сначала дождитесь сервиса пула")
        return

    client = build_client(session, cfg["proxy_string"])
    times: list[float] = []
    try:
        for i in range(count):
            started = time.perf_counter()
            try:
                response = client.get(cfg["api_url"], timeout=15)
                elapsed = time.perf_counter() - started
                items = extract_items(response.json()) if response.status_code == 200 else []
                times.append(elapsed)
                print(f"  #{i + 1}: {response.status_code}, объявлений {len(items)}, {elapsed * 1000:.0f} мс")
            except Exception as err:
                print(f"  #{i + 1}: ошибка {err}")
            if i + 1 < count:
                time.sleep(1)
    finally:
        client.close()
    print(f"  итог: {fmt(times)}")


def current_ip(proxies: dict, timeout: float = 8.0) -> str:
    try:
        response = std_requests.get(IP_URL, proxies=proxies, timeout=timeout)
        return response.text.strip()
    except std_requests.RequestException:
        return ""


def ipchange(cfg: dict) -> None:
    """Сколько реально занимает смена IP: до готовности туннеля и до нового адреса."""
    proxies = proxies_for(cfg["proxy_string"])
    print("\n=== Смена IP ===")
    before = current_ip(proxies)
    print(f"  IP до смены: {before or 'не определился'}")

    started = time.perf_counter()
    try:
        std_requests.get(cfg["proxy_change_url"], params={"format": "json"}, timeout=20)
    except std_requests.RequestException as err:
        print(f"  запрос смены не прошёл: {err}")
        return
    triggered = time.perf_counter() - started
    print(f"  ответ на запрос смены: {triggered * 1000:.0f} мс")

    usable_at = None
    changed_at = None
    deadline = time.perf_counter() + 30
    while time.perf_counter() < deadline:
        now = current_ip(proxies, timeout=4)
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
        print("  новый IP за 30 с не подтвердился (возможно, провайдер выдал тот же адрес)")
    print(f"  сейчас в коде фиксированная пауза 8 с — запас {8 - (changed_at or usable_at or 8):.1f} с")


AGE_RE = re.compile(r"Новых объявлений: (\d+), (\d+)[–-](\d+) сек")
TS_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)")
CYCLE_RE = re.compile(r"Цикл на cookie id=(\S+)")
GOT_RE = re.compile(r"Получено объявлений: (\d+)")

EVENTS = {
    "смена IP": "Меняю IP",
    "429 (бан по IP)": "429: бан по IP",
    "403/439 (cookie сгорел)": "cookie или IP сгорели",
    "200 без JSON (антибот)": "200 без JSON",
    "сбой сети": "Сбой сети",
    "JSON не получен": "Не удалось получить JSON",
    "пул без готовых cookies": "Пул без готовых cookies",
    "повтор после сбоя": "Запрос не прошёл",
}


def stamp(line: str) -> float | None:
    match = TS_RE.match(line)
    if not match:
        return None
    from datetime import datetime

    return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S.%f").timestamp()


def log_report(path: str, since: str = "") -> None:
    """Разбор лога: период цикла, длительность запроса, сбои, возраст объявлений."""
    file = Path(path)
    print(f"\n=== Разбор лога {file} ===")
    if not file.exists():
        print("  файл не найден")
        return

    lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
    if since:
        # Лог пишется в один файл несколько дней подряд, иначе прошлые
        # запуски смешаются с текущим и медианы будут ни о чём.
        lines = [line for line in lines if line[: len(since)] >= since]
        print(f"  только записи с {since}: строк {len(lines)}")
        if not lines:
            print("  за этот период записей нет")
            return

    cycle_starts: list[float] = []
    request_times: list[float] = []
    ages_min: list[float] = []
    ages_max: list[float] = []
    ads_total = 0
    counters = dict.fromkeys(EVENTS, 0)
    pending_cycle: float | None = None

    for line in lines:
        ts = stamp(line)
        if ts is None:
            continue
        if CYCLE_RE.search(line):
            cycle_starts.append(ts)
            pending_cycle = ts
        elif GOT_RE.search(line) and pending_cycle is not None:
            request_times.append(ts - pending_cycle)
            pending_cycle = None
        match = AGE_RE.search(line)
        if match:
            ads_total += int(match.group(1))
            ages_min.append(float(match.group(2)))
            ages_max.append(float(match.group(3)))
        for name, needle in EVENTS.items():
            if needle in line:
                counters[name] += 1

    periods = [
        b - a
        for a, b in zip(cycle_starts, cycle_starts[1:])
        if 0 < b - a < 600  # выкидываем перезапуски парсера
    ]

    print(f"  циклов опроса: {len(cycle_starts)}")
    if periods:
        print(f"  период между циклами: {fmt(periods, 'с')}")
    if request_times:
        print(f"  запрос к API Avito: {fmt(request_times)}")

    print("\n  Сбои и потери:")
    any_event = False
    for name, count in counters.items():
        if count:
            any_event = True
            share = count / max(1, len(cycle_starts)) * 100
            print(f"    {name}: {count} раз ({share:.1f}% циклов)")
    if not any_event:
        print("    не зафиксировано")

    print("\n  Возраст объявлений в момент попадания в ленту:")
    if not ages_min:
        print("    строк с возрастом не найдено")
        return
    print(f"    циклов с новыми: {len(ages_min)}, объявлений: {ads_total}")
    print(f"    самое свежее в цикле: {fmt(ages_min, 'с')}")
    print(f"    самое старое в цикле: {fmt(ages_max, 'с')}")
    for limit in (15, 30, 60):
        share = sum(1 for v in ages_min if v <= limit) / len(ages_min) * 100
        print(f"    быстрее {limit} с: {share:.0f}% циклов")


def main() -> None:
    parser = argparse.ArgumentParser(description="Замеры скорости парсера Avito")
    parser.add_argument("--probe", type=int, metavar="N", help="сравнить новый клиент и keep-alive")
    parser.add_argument("--api", type=int, metavar="N", help="замерить запросы к API Avito")
    parser.add_argument("--ipchange", action="store_true", help="замерить смену IP")
    parser.add_argument("--log", metavar="PATH", help="разобрать лог парсера")
    parser.add_argument("--since", metavar="YYYY-MM-DD", default="", help="учитывать записи с этой даты")
    args = parser.parse_args()

    if not any([args.probe, args.api, args.ipchange, args.log]):
        parser.print_help()
        return

    cfg = load_config()
    if args.probe:
        probe(cfg, args.probe)
    if args.api:
        api(cfg, args.api)
    if args.ipchange:
        ipchange(cfg)
    if args.log:
        log_report(args.log, args.since)


if __name__ == "__main__":
    main()
