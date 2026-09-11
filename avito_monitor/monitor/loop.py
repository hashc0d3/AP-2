"""Цикл мониторинга: опрос Avito и публикация новых объявлений в ленту.

Схема одного цикла:

1. взять следующий набор cookies из кольца — или несколько на разных каналах;
2. запросить выдачу и разобрать JSON;
3. при отказе Avito — увести набор на соседний прокси или взять другой набор;
4. отфильтровать выдачу и отдать новое в веб-ленту;
5. выдержать паузу, размер которой определяет :mod:`~.pacer`.

Цикл не запускается сам: он ждёт, пока в веб-интерфейсе нажмут «Начать
поиск», и останавливается, когда поиск сняли или заменили.

При четырёх и больше живых прокси запрос идёт сразу по нескольким каналам:
разные cookies видят разные снимки SERP. Каждый успешный ответ сразу
отбирается и уходит в ленту, не дожидаясь самого медленного канала;
уникальные id соседей догоняют. До трёх каналов опрос остаётся
последовательным.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from curl_cffi.requests.exceptions import RequestException
from loguru import logger

from avito_monitor.avito import catalog, filters
from avito_monitor.avito import items as items_mod
from avito_monitor.avito.regions import region_timezone
from avito_monitor.config import Settings, load_settings
from avito_monitor.cookies.ring import CookieRing
from avito_monitor.monitor.pacer import PollPacer, next_interval, parallel_width
from avito_monitor.monitor.seen import SeenStore
from avito_monitor.net import client as net_client
from avito_monitor.net.proxy import ensure_proxy_bypasses_vpn
from avito_monitor.net.proxies import PROXY_POOL
from avito_monitor.paths import ensure_runtime_dirs
from avito_monitor.search_session import SESSION

ItemsCallback = Callable[[list[dict]], None]


@dataclass(slots=True)
class CycleResult:
    """Итог одного обращения к Avito."""

    status: int = 0
    items: list[dict] = field(default_factory=list)
    failed: bool = False
    """Выдачу получить не удалось."""
    throttled: bool = False
    """Avito ограничивал запросы — стоит сбавить темп."""


@dataclass(slots=True)
class _Probe:
    """Ответ одного канала до слияния. Кольцо и пул трогает главный поток."""

    status: int = 0
    items: list[dict] = field(default_factory=list)
    failed: bool = False
    throttled: bool = False
    cookie_id: object = None
    proxy: str = ""
    cookie_blocked: bool = False
    dropped: bool = False
    antibot: bool = False


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


def _unique_items(groups: list[list[dict]]) -> list[dict]:
    """Слить выдачи каналов: один id — одно объявление, порядок первого появления."""
    seen: set[object] = set()
    merged: list[dict] = []
    for items in groups:
        for item in items:
            key = item.get("id")
            if key is None:
                merged.append(item)
                continue
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def _probe_listing(settings: Settings, client: object, proxy: str, cookie_id: object) -> _Probe:
    """Один запрос выдачи без пересадки: сбой разберёт вызывающий поток."""
    probe = _Probe(cookie_id=cookie_id, proxy=proxy)
    try:
        status, payload = _fetch_page(client, settings.api_url, settings.request_timeout)
    except RequestException:
        probe.failed = True
        probe.dropped = True
        return probe

    probe.status = status
    if status == net_client.RATE_LIMITED:
        probe.failed = True
        probe.throttled = True
        return probe
    if status in net_client.COOKIE_BLOCKED:
        probe.failed = True
        probe.throttled = True
        probe.cookie_blocked = True
        return probe
    if not payload:
        probe.failed = True
        if status == 200:
            probe.throttled = True
            probe.antibot = True
        return probe
    probe.items = items_mod.extract_items(payload)
    return probe


def _apply_probe_failure(ring: CookieRing, probe: _Probe) -> None:
    """Увести сгоревший набор или забаненный канал после параллельного запроса."""
    if probe.cookie_blocked:
        ring.burn(probe.cookie_id)
        logger.warning(f"{probe.status}: cookie id={probe.cookie_id} сгорел, IP не меняю")
        return
    if probe.dropped:
        PROXY_POOL.ban(probe.proxy, "Прокси сбросил соединение", wait=False)
        return
    if probe.antibot:
        PROXY_POOL.ban(probe.proxy, "200 без JSON — антибот вместо API", wait=False)
        return
    if probe.status == net_client.RATE_LIMITED:
        PROXY_POOL.ban(probe.proxy, "429: бан по IP", wait=False)


def _fetch_items_parallel(
    settings: Settings,
    ring: CookieRing,
    width: int,
    on_items: ItemsCallback | None = None,
) -> CycleResult:
    """Снять несколько каналов сразу и слить уникальные объявления.

    Успешный канал отдаёт выдачу в ``on_items`` сразу, не дожидаясь
    остальных: кольцо и пул по-прежнему трогает только этот поток.
    """
    batch = ring.next_many(width)
    if not batch:
        slot, client = _recover_empty_pool(settings, ring, "Нет рабочих cookies — докупаю")
        if slot is None or client is None:
            logger.error("Нет готового cookie — не бью Avito заблокированным набором")
            return CycleResult(failed=True)
        batch = [(slot, client)]

    labels = ", ".join(
        f"id={slot.get('id')} через {ring.proxy_of(slot).rsplit('@', 1)[-1] or 'без прокси'}"
        for slot, _ in batch
    )
    logger.info(f"Цикл параллельно ×{len(batch)}: {labels}")
    logger.info("Запрашиваю выдачу, presentationType=serp, sort=date")

    groups: list[list[dict]] = []
    throttled = False
    last_status = 0
    any_ok = False
    with ThreadPoolExecutor(max_workers=len(batch), thread_name_prefix="avito-fetch") as pool:
        futures = {
            pool.submit(
                _probe_listing,
                settings,
                client,
                ring.proxy_of(slot),
                slot.get("id"),
            ): index
            for index, (slot, client) in enumerate(batch)
        }
        for future in as_completed(futures):
            probe = future.result()
            slot, _ = batch[futures[future]]
            last_status = probe.status or last_status
            if probe.failed:
                throttled = throttled or probe.throttled
                _apply_probe_failure(ring, probe)
                logger.warning(f"id={slot.get('id')}: отказ status={probe.status or 'сеть'}")
                continue
            any_ok = True
            groups.append(probe.items)
            logger.info(f"id={slot.get('id')}: {len(probe.items)} объявлений")
            if on_items and probe.items:
                on_items(probe.items)

    if not any_ok:
        return CycleResult(status=last_status, failed=True, throttled=throttled)

    items = _unique_items(groups)
    raw = sum(len(group) for group in groups)
    logger.info(f"Получено из JSON: {raw} объявлений, уникальных {len(items)}")
    return CycleResult(status=last_status, items=items, throttled=throttled)


def fetch_items(
    settings: Settings,
    ring: CookieRing,
    on_items: ItemsCallback | None = None,
) -> CycleResult:
    """Забрать выдачу Avito и разобрать JSON как есть.

    Запрос как 6 сентября: ``presentationType=serp`` и ``sort=date``,
    без ``p=1`` / ``p=2`` и без принудительного ``s=104``.
    При четырёх и больше живых каналах опрос идёт сразу по нескольким.
    """
    width = parallel_width(PROXY_POOL.live_size)
    if width > 1:
        return _fetch_items_parallel(settings, ring, width, on_items=on_items)

    result = CycleResult()
    slot, client = ring.next()
    if slot is None or client is None:
        slot, client = _recover_empty_pool(settings, ring, "Нет рабочих cookies — докупаю")
    if slot is None or client is None:
        logger.error("Нет готового cookie — не бью Avito заблокированным набором")
        return CycleResult(failed=True)

    proxy = ring.proxy_of(slot).rsplit("@", 1)[-1] or "без прокси"
    logger.info(f"Цикл на cookie id={slot.get('id')} [{ring.position()}] через {proxy}")
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

    url = settings.api_url
    logger.info("Запрашиваю выдачу, presentationType=serp, sort=date")
    status, payload = 0, None
    try:
        status, payload = _fetch_page(client, url, settings.request_timeout)
    except RequestException:
        if rotate("Прокси сбросил соединение, переключаюсь"):
            try:
                status, payload = _fetch_page(client, url, settings.request_timeout)
            except RequestException:
                logger.warning("Соседний прокси тоже сбросил соединение — отдаю цикл")
                return CycleResult(failed=True)
        else:
            return CycleResult(failed=True)

    if status == net_client.RATE_LIMITED:
        result.throttled = True
        if PROXY_POOL.size < 2:
            rotate("429: бан по IP, меняю адрес")
            return CycleResult(status=status, failed=True, throttled=True)
        if not rotate("429: бан по IP, переключаюсь на соседний прокси"):
            return CycleResult(status=status, failed=True, throttled=True)
        status, payload = _fetch_page(client, url, settings.request_timeout)
        if status == net_client.RATE_LIMITED:
            logger.warning("429 и на соседнем прокси — меняю его IP и отдаю цикл")
            PROXY_POOL.ban(ring.proxy_of(slot), "429 на соседнем канале", wait=False)
            return CycleResult(status=status, failed=True, throttled=True)

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
            return CycleResult(status=status, failed=True, throttled=True)
        status, payload = _fetch_page(client, url, settings.request_timeout)

    result.status = status
    if not payload:
        logger.error(f"Не удалось получить JSON, status={status}")
        return CycleResult(status=status, failed=True, throttled=result.throttled)

    items = items_mod.extract_items(payload)
    logger.info(f"Получено из JSON: {len(items)} объявлений")
    result.items = items
    if on_items and items:
        on_items(items)
    return result


def _log_selection(selected: list[dict], stats: filters.FilterStats, *, first_run: bool, immediate: bool) -> None:
    """Строка отбора: сразу после канала или итог цикла."""
    summary = stats.summary()
    extra = f" ({summary})" if summary else ""
    if immediate:
        logger.info(f"В ленту сразу {len(selected)} объявлений{extra}")
        return
    if first_run:
        logger.info(f"Старт: кладу в ленту {len(selected)} объявлений{extra}")
        return
    logger.info(f"Подходящих: {len(selected)}{extra}")


def run_cycle(
    settings: Settings,
    ring: CookieRing,
    seen: SeenStore,
    *,
    first_run: bool,
    started_at: float = 0.0,
    on_selected: ItemsCallback | None = None,
) -> tuple[list[dict], bool, bool]:
    """Один цикл: опрос, фильтрация, отчёт в лог.

    ``on_selected`` вызывается по мере ответа канала, чтобы лента не ждала
    самый медленный прокси. Возвращает ``(объявления для ленты, цикл
    провалился, Avito ограничивает)``.
    """
    selected: list[dict] = []
    last_stats = filters.FilterStats()

    def consume(raw: list[dict]) -> None:
        nonlocal last_stats
        batch, stats = filters.select_new_ads(
            raw, settings, seen.ids, first_run=first_run, started_at=started_at
        )
        seen.save()
        last_stats = stats
        if not batch:
            return
        selected.extend(batch)
        if on_selected:
            _log_selection(batch, stats, first_run=first_run, immediate=True)
            on_selected(batch)

    result = fetch_items(settings, ring, on_items=consume)
    if not result.items:
        if result.status:
            logger.error(f"Цикл без объявлений, status={result.status}")
        return selected, True, result.throttled

    logger.info(f"Получено объявлений: {len(result.items)}")
    json_ages = [age for age in (items_mod.age_seconds(item) for item in result.items) if age is not None]
    if json_ages:
        logger.info(f"В JSON свежее {min(json_ages)} сек, старше {max(json_ages)} сек")
    if on_selected is None:
        _log_selection(selected, last_stats, first_run=first_run, immediate=False)
    if last_stats.promotion_badge_ignored and first_run:
        logger.info(f"API URL: {settings.api_url}")
    if last_stats.company_hints:
        logger.info("Пример «компания»: " + "; ".join(last_stats.company_hints))
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
    web_url = catalog.with_date_sort(search.get("web_url") or "")
    raw_api = search.get("api_url") or ""
    api_url = catalog.normalize_items_api_url(raw_api) if raw_api else ""
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
            _, failed, throttled = run_cycle(
                runtime,
                ring,
                seen,
                first_run=first_run,
                started_at=float(search.get("started_at") or 0.0),
                on_selected=lambda items: publish_ads(
                    [items_mod.serialize_ad(item, tz_name=tz_name) for item in items]
                ),
            )
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

        target = next_interval(
            pacer.interval,
            runtime.per_cookie_interval,
            ring.size(),
            failed=failed,
            throttled=throttled,
            retry_pause=runtime.retry_pause,
        )
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
        f"Готовых cookies: {ready}, прокси {PROXY_POOL.size}, "
        f"параллельно ×{parallel_width(PROXY_POOL.live_size)}, "
        f"опрос от {settings.poll_interval:.0f} с "
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
