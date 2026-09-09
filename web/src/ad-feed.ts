/**
 * Лента объявлений в браузере.
 *
 * Объявления приходят двумя путями: основной — поток событий `/events`,
 * запасной — периодический опрос `/api/ads` (нужен, когда соединение
 * обрывается на мобильной сети). Поэтому одно и то же объявление приходит
 * не раз, и лента ведёт список показанных ID.
 */

import { api } from "./api";
import type { AdCardRenderer } from "./ad-card";
import { notifyNewAds } from "./push-notify";
import { sellerMatchesBlacklist } from "./seller-blacklist";
import type { Ad } from "./types";

/** Запасной опрос: реже — можно пропустить объявление, чаще — лишняя нагрузка. */
const POLL_INTERVAL_MS = 4000;

/** Карточка живёт в ленте 20 минут с момента, как попала к нам. */
const FEED_KEEP_SEC = 20 * 60;

/** Как часто пересчитывать «сколько назад» в карточках. */
const CLOCK_INTERVAL_MS = 1000;

const IDLE_TEXT = "Настройте фильтр для поиска";
const SEARCHING_TEXT = "Идет поиск";

export type AdFeed = {
  /** Добавить объявления. `fresh` — пришли только что, а не опросом. */
  add: (ads: Ad[], fresh: boolean) => void;
  /** Очистить ленту (новый поиск). */
  clear: () => void;
  /** Обновить заглушку: простой поиск или «Идет поиск» с точками. */
  syncEmpty: () => void;
  /** Убрать карточки продавцов, попавших в чёрный список. */
  purgeBlacklisted: () => void;
  /** Открыть поток событий и запустить запасной опрос. */
  start: () => void;
};

export function createAdFeed(opts: {
  feed: HTMLElement;
  cards: AdCardRenderer;
  isMonitoring: () => boolean;
  isPushEnabled: () => boolean;
  /** Поток событий оборвался — стоит сказать об этом пользователю. */
  onReconnecting: () => void;
}): AdFeed {
  const shown = new Set<string>();
  let events: EventSource | null = null;

  const syncEmptyClass = (): void => {
    opts.feed.classList.toggle("feed-empty", shown.size === 0);
  };

  const hasCards = (): boolean => Boolean(opts.feed.querySelector(".card"));

  const emptyMarkup = (searching: boolean): string => {
    if (!searching) {
      return `<div class="empty">${IDLE_TEXT}</div>`;
    }
    return `<div class="empty empty--searching" role="status" aria-label="${SEARCHING_TEXT}">${SEARCHING_TEXT}<span class="empty-dots" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span></div>`;
  };

  const renderEmpty = (): void => {
    opts.feed.innerHTML = emptyMarkup(opts.isMonitoring());
    syncEmptyClass();
  };

  const syncEmpty = (): void => {
    if (hasCards()) {
      syncEmptyClass();
      return;
    }
    const searching = opts.isMonitoring();
    const current = opts.feed.querySelector(".empty");
    if (current && current.classList.contains("empty--searching") === searching) {
      syncEmptyClass();
      return;
    }
    renderEmpty();
  };

  const mount = (ad: Ad, fresh: boolean): HTMLElement | null => {
    const id = String(ad.id);
    if (shown.has(id)) return null;
    // Заблокированного продавца запоминаем как показанного: иначе он будет
    // приходить снова каждым опросом.
    shown.add(id);
    if (ad.seller && sellerMatchesBlacklist(ad.seller)) return null;
    const card = opts.cards.create(ad, fresh);
    if (!card.dataset.receivedAt) {
      card.dataset.receivedAt = String(ad.received_at || Date.now() / 1000);
    }
    return card;
  };

  const add = (ads: Ad[], fresh: boolean): void => {
    if (!Array.isArray(ads) || !ads.length) {
      syncEmpty();
      return;
    }
    opts.feed.querySelector(".empty")?.remove();
    // Вставляем перед первым существующим узлом: свежие объявления сверху.
    const marker = opts.feed.firstChild;
    const added: Ad[] = [];
    ads.forEach((ad) => {
      const card = mount(ad, fresh);
      if (!card) return;
      opts.feed.insertBefore(card, marker);
      if (fresh) added.push(ad);
    });
    if (fresh && added.length && opts.isPushEnabled()) {
      notifyNewAds(added);
    }
    syncEmpty();
  };

  const clear = (): void => {
    shown.clear();
    renderEmpty();
  };

  const purgeExpired = (): void => {
    const cutoff = Date.now() / 1000 - FEED_KEEP_SEC;
    opts.feed.querySelectorAll<HTMLElement>(".card").forEach((card) => {
      const received = Number(card.dataset.receivedAt);
      if (!received || received >= cutoff) return;
      if (card.dataset.id) shown.delete(card.dataset.id);
      card.remove();
    });
    syncEmpty();
  };

  const purgeBlacklisted = (): void => {
    opts.feed.querySelectorAll<HTMLElement>(".card").forEach((card) => {
      const seller = card.dataset.seller || "";
      if (!seller || !sellerMatchesBlacklist(seller)) return;
      // Забываем ID: если продавца потом уберут из списка, объявление
      // должно снова попасть в ленту.
      if (card.dataset.id) shown.delete(card.dataset.id);
      card.remove();
    });
    syncEmpty();
  };

  const start = (): void => {
    connect();
    window.setInterval(() => {
      if (!opts.isMonitoring()) return;
      void api
        .ads()
        .then((ads) => add(ads, false))
        .catch(() => undefined);
    }, POLL_INTERVAL_MS);
    window.setInterval(() => {
      opts.cards.refreshTimes();
      purgeExpired();
    }, CLOCK_INTERVAL_MS);
  };

  const connect = (): void => {
    if (events) return;
    events = new EventSource("/events");
    events.onmessage = (ev: MessageEvent<string>) => {
      try {
        add(JSON.parse(ev.data) as Ad[], true);
      } catch {
        // Битое сообщение пропускаем: следующее придёт целым.
      }
    };
    events.addEventListener("reset", () => clear());
    events.onerror = () => opts.onReconnecting();
  };

  return { add, clear, syncEmpty, purgeBlacklisted, start };
}
