"""Цикл мониторинга: опрос Avito и публикация новых объявлений в ленту.

Схема одного цикла:

1. взять следующий набор cookies из кольца;
2. запросить страницы выдачи;
3. при отказе Avito — сменить IP или набор и попробовать снова;
4. отфильтровать выдачу и отдать новое в веб-ленту;
5. выдержать паузу, размер которой определяет :mod:`~.pacer`.

Цикл не запускается сам: он ждёт, пока в веб-интерфейсе нажмут «Начать
поиск», и останавливается, когда поиск сняли или заменили.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from curl_cffi.requests.exceptions import RequestException
from loguru import logger

from avito_monitor.avito import catalog, filters
from avito_monitor.avito import items as items_mod
from avito_monitor.avito.regions import region_timezone
from avito_monitor.config import Settings, load_settings
from avito_monitor.cookies.ring import CookieRing
from avito_monitor.monitor.pacer import PollPacer, poll_delay
from avito_monitor.monitor.seen import SeenStore
from avito_monitor.net import client as net_client
from avito_monitor.net.proxy import change_ip, ensure_proxy_bypasses_vpn
from avito_monitor.paths import ensure_runtime_dirs
from avito_monitor.search_session import SESSION

FULL_PAGE_ITEMS = 10
"""Меньше объявлений на странице — значит, выдача закончилась."""


@dataclass(slots=True)
class CycleResult:
    """Итог одного обращения к Avito."""

    status: int = 0
    items: list[dict] = field(default_factory=list)
    failed: bool = False
    """Выдачу получить не удалось."""
    throttled: bool = False
    """Avito ограничивал запросы — стоит сбавить темп."""


def _rotate_ip(settings: Settings, ring: CookieRing, reason: str) -> None:
    """Сменить IP и забыть соединения: после смены они мертвы."""
    logger.warning(reason)
    try:
        change_ip(
            settings.proxy_change_url, settings.proxy_string, wait_max=settings.ip_change_wait
        )
    except RuntimeError as err:
        logger.warning(f"Не удалось сменить IP: {err}")
    ring.reset_clients()


def fetch_items(settings: Settings, ring: CookieRing) -> CycleResult:
    """Забрать выдачу Avito, восстанавливаясь после отказов."""
    result = CycleResult()
    slot, client = ring.next()
    if slot is None or client is None:
        logger.error("Нет готового cookie — не бью Avito заблокированным набором")
        return CycleResult(failed=True)

    logger.info(f"Цикл на cookie id={slot.get('id')} [{ring.position()}]")
    collected: dict[int, dict] = {}

    for page in range(1, settings.pages + 1):
        url = catalog.with_page(settings.api_url, page)
        try:
            status, payload = net_client.fetch_page(client, url, timeout=settings.request_timeout)
        except RequestException:
            _rotate_ip(settings, ring, "Прокси сбросил соединение, меняю только IP")
            client = ring.client_for(slot)
            status, payload = net_client.fetch_page(client, url, timeout=settings.request_timeout)

        if status == net_client.RATE_LIMITED:
            # На 135 циклах повтор сразу после смены IP помог лишь 6 раз из
            # 32: свежий IP мобильного прокси обычно тоже под лимитом.
            # Дешевле отдать цикл и зайти новым IP в следующем.
            result.throttled = True
            result.failed = True
            _rotate_ip(settings, ring, "429: бан по IP, меняю IP и жду следующий цикл")
            break

        if status in net_client.COOKIE_BLOCKED:
            result.throttled = True
            burned = slot.get("id")
            ring.burn(burned)
            _rotate_ip(settings, ring, f"{status}: cookie id={burned} сгорел, беру другой набор")
            slot, client = ring.next()
            if slot is None or client is None:
                logger.error("Пул не дал готовый набор после блокировки")
                return CycleResult(status=status, failed=True, throttled=True)
            logger.info(f"Продолжаю на cookie id={slot.get('id')}")
            status, payload = net_client.fetch_page(client, url, timeout=settings.request_timeout)

        if not payload and status == 200:
            result.throttled = True
            _rotate_ip(settings, ring, "200 без JSON — антибот вместо API, меняю IP")
            client = ring.client_for(slot)
            status, payload = net_client.fetch_page(client, url, timeout=settings.request_timeout)

        result.status = status
        if not payload:
            logger.error(f"Не удалось получить JSON, status={status}, page={page}")
            result.failed = True
            break

        page_items = items_mod.extract_items(payload)
        for item in page_items:
            ad_id = items_mod.item_id(item)
            if ad_id is not None:
                collected.setdefault(ad_id, item)
        if len(page_items) < FULL_PAGE_ITEMS:
            break
        if page < settings.pages and settings.pause_between_pages:
            time.sleep(settings.pause_between_pages)

    result.items = list(collected.values())
    return result


def run_cycle(
    settings: Settings,
    ring: CookieRing,
    seen: SeenStore,
    *,
    first_run: bool,
) -> tuple[list[dict], bool, bool]:
    """Один цикл: опрос, фильтрация, отчёт в лог.

    Возвращает ``(объявления для ленты, цикл провалился, Avito ограничивает)``.
    """
    result = fetch_items(settings, ring)
    if not result.items:
        if result.status:
            logger.error(f"Цикл без объявлений, status={result.status}")
        return [], True, result.throttled

    logger.info(f"Получено объявлений: {len(result.items)}")
    selected, stats = filters.select_new_ads(result.items, settings, seen.ids, first_run=first_run)
    seen.save()

    summary = stats.summary()
    logger.info(f"Подходящих: {len(selected)}" + (f" ({summary})" if summary else ""))

    if not selected:
        logger.info("Новых объявлений нет")
        return [], result.failed, result.throttled

    if first_run:
        logger.info(f"Старт: в ленту {len(selected)} объявлений, дальше только новые")
    else:
        ages = [
            age for age in (items_mod.age_seconds(item) for item in selected) if age is not None
        ]
        freshness = f", возраст {min(ages)}–{max(ages)} сек" if ages else ""
        logger.info(f"Новых объявлений: {len(selected)}{freshness}")

    contacts = [items_mod.contact_flags(item) for item in selected]
    logger.info(
        f"Контакты: звонок {sum(call for call, _ in contacts)}/{len(selected)}, "
        f"сообщение {sum(msg for _, msg in contacts)}/{len(selected)}"
    )
    return selected, result.failed, result.throttled


def _runtime_settings(settings: Settings, search: dict) -> Settings:
    """Настройки для конкретного поиска из веб-интерфейса.

    Пересобираются на каждом цикле: чёрный список продавцов можно поменять,
    не перезапуская поиск, и он должен подействовать сразу.
    """
    models = search.get("iphone_models")
    category_id = str((search.get("category") or {}).get("id") or "")
    if category_id != catalog.IPHONE_CATEGORY_ID:
        models = None
    return settings.for_search(
        web_url=search.get("web_url") or "",
        api_url=search.get("api_url") or "",
        seller_skip=tuple(search.get("seller_skip") or ()),
        iphone_models=tuple(models) if models is not None else None,
        iphone_models_in_url=bool(search.get("iphone_models_in_url")),
        category_id=category_id,
    )


def _monitor_search(settings: Settings, ring: CookieRing, seen: SeenStore, generation: int) -> None:
    """Опрашивать Avito, пока поиск не остановят или не заменят."""
    from avito_monitor.web.feed import publish_ads

    pacer = PollPacer(settings.poll_interval, settings.poll_interval_max)
    first_run = True

    while True:
        search = SESSION.snapshot()
        if search["generation"] != generation:
            logger.info("Поисковый запрос обновлён, перезапускаю мониторинг")
            return
        if not search["running"]:
            logger.info("Мониторинг остановлен, жду новый запуск")
            return

        runtime = _runtime_settings(settings, search)
        tz_name = region_timezone((search.get("region") or {}).get("slug", ""))
        started = time.monotonic()
        failed = False
        throttled = False

        try:
            selected, failed, throttled = run_cycle(runtime, ring, seen, first_run=first_run)
            if selected:
                publish_ads([items_mod.serialize_ad(item, tz_name=tz_name) for item in selected])
            first_run = False
        except Exception as err:
            # Любая неожиданная ошибка не должна останавливать мониторинг:
            # меняем IP и пробуем следующий цикл.
            failed = True
            logger.error(f"Ошибка цикла: {err}")
            _rotate_ip(runtime, ring, "Восстанавливаюсь после ошибки цикла")

        if failed or throttled:
            pacer.on_throttle()
        else:
            pacer.on_ok()

        target = poll_delay(pacer.interval, runtime.per_cookie_interval, ring.size())
        if failed:
            target = max(target, runtime.retry_pause)
        wait = max(0.0, target - (time.monotonic() - started))
        logger.info(
            f"Цикл {time.monotonic() - started:.1f} с, пауза {wait:.1f} с, "
            f"темп {pacer.interval:.1f} с, наборов {ring.size()}"
        )
        if wait:
            # Ложное пробуждение разберёт проверка в начале цикла.
            SESSION.sleep_unless_changed(wait, generation)


def main() -> None:
    """Точка входа: поднять веб-интерфейс и ждать команду «Начать поиск»."""
    from avito_monitor.auth import migrate_legacy_session, warn_about_weak_password
    from avito_monitor.cookies import pool, service
    from avito_monitor.logging_setup import setup_file_log
    from avito_monitor.web.feed import clear_ads
    from avito_monitor.web.server import start_server

    setup_file_log("parser.log")
    ensure_runtime_dirs()
    settings = load_settings()
    migrate_legacy_session()
    warn_about_weak_password()

    service.start_background(settings)
    ensure_proxy_bypasses_vpn()
    start_server(settings)
    clear_ads()

    ring = CookieRing(settings)
    ready = ring.refresh()
    if not ready:
        pool.wait_ready_cookie()
        ready = ring.refresh()

    logger.info(
        f"Готовых cookies: {ready}, опрос от {settings.poll_interval:.0f} с "
        f"(на набор не чаще {settings.per_cookie_interval:.0f} с, "
        f"откат при банах до {settings.poll_interval_max:.0f} с). "
        "Жду «Начать поиск» в веб-интерфейсе"
    )

    seen = SeenStore()
    generation = 0
    while True:
        search = SESSION.wait_for_start(generation)
        generation = int(search["generation"])
        seen.reset()
        region_name = (search.get("region") or {}).get("name") or ""
        logger.info(f"Старт мониторинга: {search.get('query') or 'все объявления'} · {region_name}")
        logger.info(f"Web URL: {search.get('web_url')}")
        logger.info(f"API URL: {search.get('api_url')}")
        _monitor_search(settings, ring, seen, generation)
