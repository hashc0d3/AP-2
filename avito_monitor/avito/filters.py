"""Отбор объявлений, которые стоит показать.

Порядок проверок выбран так, чтобы дешёвые отсекали большинство объявлений
раньше дорогих, а счётчики :class:`FilterStats` объясняли в логе, почему из
сотни объявлений в ленту попало три.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from avito_monitor.avito import iphone  # noqa: F401 — вернём вместе с фильтром моделей
from avito_monitor.avito import items as items_mod
from avito_monitor.config import Settings


def title_matches(item: dict, must_contain: tuple[str, ...], skip: tuple[str, ...]) -> bool:
    """Проходит ли название объявления по словам-фильтрам."""
    title = (item.get("title") or "").lower()
    if must_contain and not any(word.lower() in title for word in must_contain if word):
        return False
    return not any(word.lower() in title for word in skip if word)


def _normalized(value: str) -> str:
    """Только буквы и цифры в нижнем регистре.

    Продавцы разбавляют имена пробелами, точками и эмодзи, поэтому чёрный
    список сравниваем по «скелету» строки.
    """
    return "".join(ch for ch in value.lower() if ch.isalnum())


def seller_is_skipped(item: dict, skip: tuple[str, ...]) -> bool:
    """Попал ли продавец в чёрный список."""
    if not skip:
        return False
    blob = "".join(_normalized(text) for text in items_mod.seller_texts(item))
    if not blob:
        return False
    return any(_normalized(word) in blob for word in skip if word)


def seller_is_allowed(item: dict, *, private_only: bool = True) -> bool:
    """Подходит ли продавец под режим «только частные объявления».

    Магазин режем только по профилю в карточке. Корзина ``/shop/`` и
    отсутствие ``/user/`` частника не прячут: Avito так размечает доставку,
    и из‑за этого лента пустела, хотя на сайте те же объявления — частные.
    """
    if not private_only:
        return True
    return not items_mod.is_company_seller(item)


@dataclass(slots=True)
class FilterStats:
    """Сколько объявлений отсеяла каждая проверка."""

    total: int = 0
    promoted: int = 0
    seller_skipped: int = 0
    company: int = 0
    title: int = 0
    iphone_model: int = 0
    already_seen: int = 0
    baseline: int = 0
    """Сколько объявлений положили в ленту на первом цикле."""
    before_start: int = 0
    """Опубликованы до нажатия «Начать поиск»."""
    promotion_badge_ignored: bool = False
    company_filter_ignored: bool = False
    company_hints: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """Строка для лога: перечислены только сработавшие фильтры."""
        reasons = (
            ("продвинутых скрыто", self.promoted),
            ("продавец скрыт", self.seller_skipped),
            ("компания", self.company),
            ("не подходит название", self.title),
            ("не та модель iPhone", self.iphone_model),
            ("уже показывали", self.already_seen),
            ("на старте в ленту", self.baseline),
            ("раньше старта", self.before_start),
        )
        parts = [f"{label}: {count}" for label, count in reasons if count]
        if self.promotion_badge_ignored:
            parts.append("бейдж «Продвинуто» на всех, не прячу")
        if self.company_filter_ignored:
            parts.append("все как магазин, не прячу")
        return ", ".join(parts)


def select_new_ads(
    items: list[dict],
    settings: Settings,
    seen: set[int],
    *,
    first_run: bool,
    started_at: float = 0.0,
) -> tuple[list[dict], FilterStats]:
    """Выбрать объявления для ленты.

    Первый цикл кладёт текущую выдачу в ленту. Дальше — любой новый ID.
    Платное продвижение скрываем. Магазины отдельно не режем: частные
    уже в запросе Avito (``owner[0]=private``), как 6 сентября.

    Возвращает объявления от свежих к старым и статистику отбора.
    """
    stats = FilterStats(total=len(items))
    selected: list[dict] = []
    _ = started_at
    hide_promoted = settings.ignore_promotion and not items_mod.all_items_promoted(items)
    if settings.ignore_promotion and not hide_promoted and items:
        stats.promotion_badge_ignored = True
    hide_companies = settings.private_only

    for item in items:
        ad_id = items_mod.item_id(item)
        if ad_id is None:
            continue
        already_seen = ad_id in seen

        if hide_promoted and items_mod.is_promoted(item):
            seen.add(ad_id)
            stats.promoted += 1
            continue
        # if seller_is_skipped(item, settings.seller_skip):
        #     stats.seller_skipped += 1
        #     continue
        if hide_companies and not seller_is_allowed(item, private_only=True):
            stats.company += 1
            if len(stats.company_hints) < 3:
                links = items_mod.seller_profile_links(item)
                stats.company_hints.append(f"{ad_id} {links[0] if links else 'без ссылки'}")
            continue
        # if not title_matches(item, settings.title_must_contain, settings.title_skip):
        #     stats.title += 1
        #     continue
        # if settings.filters_iphone_models and not iphone.model_allowed(
        #     item.get("title") or "",
        #     settings.iphone_models,
        #     min_gen=settings.iphone_min_model,
        #     max_gen=settings.iphone_max_model,
        # ):
        #     stats.iphone_model += 1
        #     continue
        if already_seen:
            stats.already_seen += 1
            continue
        seen.add(ad_id)
        selected.append(item)
        if first_run:
            stats.baseline += 1

    selected.sort(key=lambda item: item.get("sortTimeStamp") or 0, reverse=True)
    return selected, stats


START_GRACE_SEC = 180
"""Запас на рассинхрон часов Avito и момент нажатия «Начать поиск»."""


def _published_after(item: dict, started_at: float) -> bool:
    """Опубликовано ли объявление после старта поиска (с небольшим запасом)."""
    published = items_mod.published_at(item)
    if published is None:
        return False
    return published.timestamp() > started_at - START_GRACE_SEC
