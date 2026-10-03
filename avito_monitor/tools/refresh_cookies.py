"""Принудительно обновить пул cookies.

Докупает наборы, не дожидаясь, пока старые сгорят, и по желанию выводит
старые из работы. Каждый набор стоит денег на spfa.pro.

Примеры::

    python -m avito_monitor.tools.refresh_cookies --count 6
    python -m avito_monitor.tools.refresh_cookies --count 24 --retire-old --port 23650
"""

from __future__ import annotations

import argparse
import time

from avito_monitor import spfa
from avito_monitor.config import load_settings
from avito_monitor.cookies import pool
from avito_monitor.net.proxies import PROXY_POOL


RATE_LIMIT_WAIT = 65.0
"""spfa.pro отвечает 429 на частые покупки; через минуту снова продаёт."""
RATE_LIMIT_RETRIES = 3


def _buy_with_wait(settings, proxy_string: str, label: str) -> dict | None:
    """Купить набор, переждав лимит сервиса, а не пропуская покупку."""
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            return pool.buy_one(settings, proxy_string=proxy_string)
        except spfa.SpfaError as err:
            if "Лимит" not in str(err) or attempt == RATE_LIMIT_RETRIES:
                print(f"  {label}: не купил — {err}")
                return None
            print(f"  {label}: лимит сервиса, жду {RATE_LIMIT_WAIT:.0f} с")
            time.sleep(RATE_LIMIT_WAIT)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Принудительно обновить пул cookies")
    parser.add_argument("--count", type=int, help="сколько купить; по умолчанию — размер пула")
    parser.add_argument(
        "--retire-old",
        action="store_true",
        help="после покупки вывести из работы все прежние наборы",
    )
    parser.add_argument("--port", type=int, help="оформлять наборы через этот порт прокси")
    args = parser.parse_args()

    settings = load_settings()
    PROXY_POOL.configure(settings.proxy_endpoints())
    proxy_string = ""
    if args.port:
        matches = [p for p, _ in settings.proxy_endpoints() if p.endswith(f":{args.port}")]
        if not matches:
            print(f"Порта {args.port} нет в PROXY_STRING")
            return
        proxy_string = matches[0]

    old_ids = [slot["id"] for slot in pool.alive_slots()]
    count = args.count or pool.pool_size(settings)
    print(f"Сейчас в пуле: {len(old_ids)}, рабочих {len(pool.usable_slots())}. Покупаю {count}.")

    bought = 0
    for number in range(1, count + 1):
        slot = _buy_with_wait(settings, proxy_string, f"{number}/{count}")
        if slot is None:
            continue
        bought += 1
        print(f"  {number}/{count}: id={slot['id']}")

    if args.retire_old and bought:
        for cookie_id in old_ids:
            pool.retire(cookie_id)
        print(f"Старые наборы выведены из работы: {len(old_ids)}")
    elif args.retire_old:
        print("Ни одного не купил — старые наборы оставляю в работе")

    print(f"Готово: куплено {bought}, рабочих сейчас {len(pool.usable_slots())}")


if __name__ == "__main__":
    main()
