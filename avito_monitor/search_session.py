"""Состояние текущего поиска — общая точка веб-интерфейса и цикла опроса.

Веб-интерфейс меняет состояние («искать вот это», «стоп»), а цикл опроса за
ним следит. Связка сделана через ``threading.Event``: цикл не опрашивает
состояние в занятом ожидании, а спит до изменения и потому реагирует на
кнопку «Стоп» мгновенно.

``generation`` — номер поискового запроса. Он растёт при каждом старте и
остановке, и цикл сравнивает его со своим: если номер изменился, текущую
итерацию нужно бросить и начать заново с новыми параметрами.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from loguru import logger

from avito_monitor.avito import catalog, iphone, iphone_params
from avito_monitor.avito.regions import default_region
from avito_monitor.avito.search import SearchPlan, plan_from_url, plan_search, resolve_api_url

MODE_QUERY = "query"
MODE_URL = "url"

_WAIT_STEP = 0.5
_SLEEP_STEP = 0.4


class SearchSession:
    """Потокобезопасное состояние одного поискового запроса."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._changed = threading.Event()
        self._generation = 0
        self._running = False
        self._query = ""
        self._region = default_region().as_dict()
        self._category = catalog.default_category().as_dict()
        self._web_url = ""
        self._api_url = ""
        self._error = ""
        self._seller_skip: tuple[str, ...] = ()
        self._mode = MODE_QUERY
        self._iphone_models: tuple[str, ...] | None = None
        self._iphone_models_in_url = False

    # ── Чтение ──────────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Состояние в виде, который отдаётся веб-интерфейсу."""
        with self._lock:
            return {
                "generation": self._generation,
                "running": self._running,
                "query": self._query,
                "region": dict(self._region),
                "category": dict(self._category),
                "web_url": self._web_url,
                "api_url": self._api_url,
                "error": self._error,
                "seller_skip": list(self._seller_skip),
                "search_mode": self._mode,
                "iphone_models": (
                    list(self._iphone_models) if self._iphone_models is not None else None
                ),
                "iphone_models_in_url": self._iphone_models_in_url,
            }

    @property
    def seller_skip(self) -> tuple[str, ...]:
        with self._lock:
            return self._seller_skip

    # ── Изменение ───────────────────────────────────────────────────────

    def set_seller_skip(self, sellers: list[Any]) -> dict[str, Any]:
        """Заменить чёрный список продавцов. Применяется со следующего цикла."""
        with self._lock:
            self._seller_skip = _unique_strings(sellers)
        self._changed.set()
        return self.snapshot()

    def start(
        self,
        plan: SearchPlan,
        api_url: str,
        *,
        mode: str,
        seller_skip: list[Any] | None,
        iphone_models: tuple[str, ...] | None,
        iphone_models_in_url: bool,
    ) -> dict[str, Any]:
        """Запустить новый поиск и разбудить цикл опроса."""
        with self._lock:
            self._generation += 1
            self._running = True
            self._mode = MODE_URL if mode == MODE_URL else MODE_QUERY
            self._query = plan.query
            self._region = plan.region.as_dict()
            self._category = plan.category.as_dict() if plan.category else dict(catalog.NO_CATEGORY)
            self._web_url = plan.web_url
            self._api_url = api_url
            self._error = ""
            if seller_skip is not None:
                self._seller_skip = _unique_strings(seller_skip)
            if iphone_models is not None:
                self._iphone_models = iphone_models
            self._iphone_models_in_url = iphone_models_in_url
        self._changed.set()
        return self.snapshot()

    def stop(self) -> dict[str, Any]:
        """Остановить поиск. Повторный вызов ничего не меняет."""
        with self._lock:
            if self._running:
                self._generation += 1
                self._running = False
                logger.info("Мониторинг остановлен")
        self._changed.set()
        return self.snapshot()

    # ── Ожидание из цикла опроса ────────────────────────────────────────

    def wait_for_start(self, last_generation: int = 0) -> dict[str, Any]:
        """Блокироваться, пока в интерфейсе не запустят новый поиск."""
        while True:
            with self._lock:
                if self._running and self._generation > last_generation:
                    return self.snapshot()
                self._changed.clear()
            self._changed.wait(timeout=_WAIT_STEP)

    def sleep_unless_changed(self, seconds: float, generation: int) -> bool:
        """Пауза между циклами.

        ``False`` — поиск остановили или заменили, текущий цикл продолжать
        не нужно.
        """
        deadline = time.time() + max(0.0, seconds)
        while True:
            left = deadline - time.time()
            if left <= 0:
                return True
            with self._lock:
                if self._generation != generation or not self._running:
                    return False
                self._changed.clear()
            self._changed.wait(timeout=min(_SLEEP_STEP, left))

    def is_current(self, generation: int) -> bool:
        """Идёт ли всё ещё тот же поиск."""
        with self._lock:
            return self._running and self._generation == generation


SESSION = SearchSession()
"""Единственная сессия на процесс: поиск в приложении всегда один."""


def _unique_strings(values: list[Any] | tuple[Any, ...] | None) -> tuple[str, ...]:
    """Непустые строки без повторов (без учёта регистра), порядок сохранён."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values or ():
        text = str(value).strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return tuple(out)


def start_search(
    *,
    mode: str = MODE_QUERY,
    query: str = "",
    web_url: str = "",
    region_slug: str = "",
    category_id: str = "",
    seller_skip: list[Any] | None = None,
    iphone_models: object = None,
) -> dict[str, Any]:
    """Подготовить и запустить поиск.

    Режим ``query`` собирает ссылку из региона и категории, режим ``url``
    берёт готовую ссылку Avito как есть.

    :raises ValueError: некорректные параметры поиска.
    :raises spfa.SpfaError: не удалось получить адрес API.
    """
    if mode == MODE_URL:
        plan = plan_from_url(web_url or query)
        # В своей ссылке пользователь мог уже задать фильтр моделей —
        # тогда по названию их фильтровать не нужно.
        models_in_url = iphone_params.has_model_params(plan.web_url) or "f=" in plan.web_url
        api_url = resolve_api_url(plan.web_url)
    else:
        plan = plan_search(query, region_slug, category_id, iphone_models)
        models_in_url = False
        api_url = resolve_api_url(
            plan.web_url,
            region_slug=plan.region.slug,
            category_id=plan.category.id if plan.category else "",
        )

    logger.info(f"Web URL: {plan.web_url}")
    if plan.iphone_models is not None:
        api_url, models_in_url = iphone_params.append_model_params(api_url, plan.iphone_models)
    logger.info(f"API URL: {api_url}")

    normalized_models = (
        iphone.normalize_models(iphone_models) if iphone_models is not None else None
    )
    return SESSION.start(
        plan,
        api_url,
        mode=mode,
        seller_skip=seller_skip,
        iphone_models=normalized_models,
        iphone_models_in_url=models_in_url,
    )
