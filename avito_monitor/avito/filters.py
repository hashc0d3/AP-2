"""Отбор объявлений, которые стоит показать.

Порядок проверок выбран так, чтобы дешёвые отсекали большинство объявлений
раньше дорогих, а счётчики :class:`FilterStats` объясняли в логе, почему из
сотни объявлений в ленту попало три.
"""

from __future__ import annotations

from dataclasses import dataclass

from avito_monitor.avito import iphone
from avito_monitor.avito import items as items_mod
from avito_monitor.config import Settings


def title_matches(item: dict, must_contain: tuple[str, ...], skip: tuple[str, ...]) -> bool:
    """Проходит ли название объявления по словам-фильтрам."""
    title = (item.get("title") or "").lower()
    if must_contain and not any(word.lower() in title for word in must_contain if word):
        return False
    return not any(word.lower() in title for word in skip if word)


def is_fresh(item: dict, max_age: int) -> bool:
    """Не старше ``max_age`` секунд. ``0`` — ограничения нет."""
    if not max_age:
        return True
    age = items_mod.age_seconds(item)
    return age is not None and age <= max_age


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

    Если ссылок на профиль нет вовсе, объявление пропускаем: Avito отдаёт
    профиль не всегда, и молча терять частников хуже, чем изредка показать
    магазин.
    """
    if not private_only:
        return True
    if items_mod.is_company_seller(item):
        return False
    if not items_mod.seller_profile_links(item):
        return True
    return items_mod.is_private_seller(item)


@dataclass(slots=True)
class FilterStats:
    """Сколько объявлений отсеяла каждая проверка."""

    total: int = 0
    promoted: int = 0
    seller_skipped: int = 0
    company: int = 0
    stale: int = 0
    title: int = 0
    iphone_model: int = 0
    too_late: int = 0
    already_seen: int = 0

    def summary(self) -> str:
        """Строка для лога: перечислены только сработавшие фильтры."""
        reasons = (
            ("продвинутых скрыто", self.promoted),
            ("продавец скрыт", self.seller_skipped),
            ("компания", self.company),
            ("старее лимита", self.stale),
            ("не подходит название", self.title),
            ("не та модель iPhone", self.iphone_model),
            ("поздно в выдаче", self.too_late),
            ("уже показывали", self.already_seen),
        )
        parts = [f"{label}: {count}" for label, count in reasons if count]
        return ", ".join(parts)


def select_new_ads(
    items: list[dict],
    settings: Settings,
    seen: set[int],
    *,
    first_run: bool,
) -> tuple[list[dict], FilterStats]:
    """Выбрать объявления для ленты.

    ``seen`` пополняется на месте **всеми** объявлениями выдачи, даже
    отфильтрованными: иначе платно продвинутое объявление всплывёт позже как
    новое. На первом цикле показываем всё подходящее, дальше — только то,
    чего в выдаче ещё не было.

    Возвращает объявления от свежих к старым и статистику отбора.
    """
    stats = FilterStats(total=len(items))
    selected: list[dict] = []

    for item in items:
        ad_id = items_mod.item_id(item)
        if ad_id is None:
            continue
        already_seen = ad_id in seen
        seen.add(ad_id)

        if settings.ignore_promotion and items_mod.is_promoted(item):
            stats.promoted += 1
            continue
        if seller_is_skipped(item, settings.seller_skip):
            stats.seller_skipped += 1
            continue
        if not seller_is_allowed(item, private_only=settings.private_only):
            stats.company += 1
            continue
        if not is_fresh(item, settings.max_age):
            stats.stale += 1
            continue
        if not title_matches(item, settings.title_must_contain, settings.title_skip):
            stats.title += 1
            continue
        if settings.filters_iphone_models and not iphone.model_allowed(
            item.get("title") or "",
            settings.iphone_models,
            min_gen=settings.iphone_min_model,
            max_gen=settings.iphone_max_model,
        ):
            stats.iphone_model += 1
            continue
        if settings.notify_max_age and not is_fresh(item, settings.notify_max_age):
            stats.too_late += 1
            continue
        if already_seen and not first_run:
            stats.already_seen += 1
            continue
        selected.append(item)

    selected.sort(key=lambda item: item.get("sortTimeStamp") or 0, reverse=True)
    return selected, stats
