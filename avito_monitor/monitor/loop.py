"""Цикл мониторинга: опрос Avito и публикация новых объявлений в ленту.

Схема одного цикла:

1. взять следующий набор cookies из кольца;
2. запросить страницы выдачи;
3. при отказе Avito — увести набор на соседний прокси или взять другой набор;
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
from avito_monitor.net.proxy import ensure_proxy_bypasses_vpn
from avito_monitor.net.proxies import PROXY_POOL
from avito_monitor.paths import ensure_runtime_dirs
from avito_monitor.search_session import SESSION

FULL_PAGE_ITEMS = 10
"""Меньше объявлений на странице — значит, выдача закончилась."""


def should_open_next_page(
    *,
    page_items: list,
    page: int,
    max_pages: int,
) -> bool:
    """Следующая страница — отдельный запрос ``p=N``, если эта ещё полная."""
    if page >= max_pages:
        return False
    return len(page_items) >= FULL_PAGE_ITEMS


@dataclass(slots=True)
class CycleResult:
    """Итог одного обращения к Avito."""

    status: int = 0
    items: list[dict] = field(default_factory=list)
    failed: bool = False
    """Выдачу получить не удалось."""
    throttled: bool = False
    """Avito ограничивал запросы — стоит сбавить темп."""


def _fetch_page(client: object, url: str, timeout: float):
    """Страница выдачи: при двух прокси не повторяем запрос в мёртвый туннель."""
    attempts = 1 if PROXY_POOL.size >= 2 else net_client.DEFAULT_ATTEMPTS
    return net_client.fetch_page(client, url, timeout=timeout, attempts=attempts)


def _recover_empty_pool(settings: Settings, ring: CookieRing, reason: str) -> tuple[dict | None, object]:
    """Нет рабочих cookies — докупить набор.

    IP при этом не трогаем: сгоревшие cookies — не проблема адреса, а
    покупка нового набора как раз идёт через живой прокси.
    """
    from avito_monitor.cookies import pool

    logger.warning(reason)
    try:
        pool.replenish_if_empty(settings)
    except Exception as err:
        logger.error(f"Не удалось докупить cookies: {err}")
    if not ring.refresh():
        return None, None
    return ring.next()


def fetch_items(settings: Settings, ring: CookieRing) -> CycleResult:
    """Забрать выдачу Avito, восстанавливаясь после отказов.

    Страницы ``p=1`` и ``p=2`` — отдельные запросы. Дальше не идём, если
    текущая страница короче полной выдачи.
    """
    result = CycleResult()
    slot, client = ring.next()
    if slot is None or client is None:
        slot, client = _recover_empty_pool(settings, ring, "Нет рабочих cookies — докупаю")
    if slot is None or client is None:
        logger.error("Нет готового cookie — не бью Avito заблокированным набором")
        return CycleResult(failed=True)

    proxy = ring.proxy_of(slot).rsplit("@", 1)[-1] or "без прокси"
    logger.info(f"Цикл на cookie id={slot.get('id')} [{ring.position()}] через {proxy}")
    collected: dict[int, dict] = {}
    rotated = False

    def rotate(reason: str) -> bool:
        """Увести набор с забаненного канала; тот меняет IP в фоне."""
        nonlocal rotated, client
        if rotated:
            logger.warning(f"{reason}: в этом цикле уже уходили на соседний, оставляю собранное")
            return False
        PROXY_POOL.ban(ring.proxy_of(slot), reason)
        rotated = True
        client = ring.client_for(slot)
        return True

    for page in range(1, settings.pages + 1):
        url = catalog.with_page(settings.api_url, page)
        try:
            status, payload = _fetch_page(client, url, settings.request_timeout)
        except RequestException:
            if not rotate("Прокси сбросил соединение, переключаюсь"):
                result.failed = True
                break
            try:
                status, payload = _fetch_page(client, url, settings.request_timeout)
            except RequestException:
                result.failed = True
                logger.warning("Соседний прокси тоже сбросил соединение — отдаю цикл")
                break

        if status == net_client.RATE_LIMITED:
            result.throttled = True
            if PROXY_POOL.size < 2:
                rotate("429: бан по IP, меняю адрес")
                result.failed = True
                break
            if not rotate("429: бан по IP, переключаюсь на соседний прокси"):
                result.failed = True
                break
            status, payload = _fetch_page(client, url, settings.request_timeout)
            if status == net_client.RATE_LIMITED:
                result.failed = True
                logger.warning("429 и на соседнем прокси — меняю его IP и отдаю цикл")
                PROXY_POOL.ban(ring.proxy_of(slot), "429 на соседнем канале", wait=False)
                break

        if status in net_client.COOKIE_BLOCKED:
            result.throttled = True
            burned = slot.get("id")
            ring.burn(burned)
            logger.warning(f"{status}: cookie id={burned} сгорел, IP не меняю, беру другой набор")
            slot, client = ring.next()
            if slot is None or client is None:
                slot, client = _recover_empty_pool(settings, ring, "Все cookies сгорели — докупаю")
            if slot is None or client is None:
                logger.error("Пул не дал готовый набор после блокировки")
                return CycleResult(status=status, failed=True, throttled=True)
            logger.info(f"Продолжаю на cookie id={slot.get('id')}")
            status, payload = _fetch_page(client, url, settings.request_timeout)

        if not payload and status == 200:
            result.throttled = True
            if not rotate("200 без JSON — антибот вместо API, переключаюсь"):
                result.failed = True
                break
            status, payload = _fetch_page(client, url, settings.request_timeout)

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
        logger.info(f"p={page}: {len(page_items)} объявлений")
        if not should_open_next_page(
            page_items=page_items,
            page=page,
            max_pages=settings.pages,
        ):
            break
        logger.info(f"Беру p={page + 1} отдельным запросом")
        if settings.pause_between_pages:
            time.sleep(settings.pause_between_pages)

    result.items = list(collected.values())
    return result


def run_cycle(
    settings: Settings,
    ring: CookieRing,
    seen: SeenStore,
    *,
    first_run: bool,
    started_at: float = 0.0,
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
    selected, stats = filters.select_new_ads(
        result.items, settings, seen.ids, first_run=first_run, started_at=started_at
    )
    seen.save()

    summary = stats.summary()
    if first_run:
        extra = f" ({summary})" if summary else ""
        logger.info(f"Старт: запомнил выдачу{extra}, в ленту не кладу — дальше только новые")
        if stats.promotion_badge_ignored:
            logger.info(f"API URL: {settings.api_url}")
        return [], result.failed, result.throttled

    logger.info(f"Подходящих: {len(selected)}" + (f" ({summary})" if summary else ""))
    if not selected:
        logger.info("Новых объявлений нет")
        return [], result.failed, result.throttled

    ages = [age for age in (items_mod.age_seconds(item) for item in selected) if age is not None]
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
    web_url = search.get("web_url") or ""
    raw_api = search.get("api_url") or ""
    api_url = catalog.normalize_items_api_url(raw_api, prefer_s=catalog.DATE_SORT) if raw_api else ""
    if raw_api and api_url != raw_api and (
        "presentationType" in raw_api or "sort=date" in raw_api
    ):
        logger.info("API URL очищен от платной SERP — иначе вся страница «Продвинуто»")
        logger.info(f"API URL: {api_url}")
    return settings.for_search(
        web_url=web_url,
        api_url=api_url,
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
            selected, failed, throttled = run_cycle(
                runtime,
                ring,
                seen,
                first_run=first_run,
                started_at=float(search.get("started_at") or 0.0),
            )
            if selected:
                publish_ads([items_mod.serialize_ad(item, tz_name=tz_name) for item in selected])
            first_run = False
        except Exception as err:
            # Любая неожиданная ошибка не должна останавливать мониторинг.
            # IP не меняем: он тут обычно ни при чём, а вот соединения после
            # такого сбоя лучше считать мёртвыми.
            failed = True
            logger.error(f"Ошибка цикла: {err}")
            ring.reset_clients()

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

    PROXY_POOL.change_wait = settings.ip_change_wait
    PROXY_POOL.configure(settings.proxy_endpoints())
    service.start_background(settings)
    ensure_proxy_bypasses_vpn()
    start_server(settings)
    clear_ads()

    ring = CookieRing(settings)
    ready = ring.refresh()
    if not ready:
        pool.wait_ready_cookie(settings=settings)
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
