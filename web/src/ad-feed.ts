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

/** Как часто пересчитывать «сколько назад» в карточках. */
const CLOCK_INTERVAL_MS = 1000;

/** Совпадает с `max_age` в config.toml: старше этого в ленте не держим. */
const MAX_AGE_SEC = 300;

const EMPTY_TEXT = "Жду новые объявления…";

export type AdFeed = {
  /** Добавить объявления. `fresh` — пришли только что, а не опросом. */
  add: (ads: Ad[], fresh: boolean) => void;
  /** Очистить ленту (новый поиск). */
  clear: (message?: string) => void;
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

  const mount = (ad: Ad, fresh: boolean): HTMLElement | null => {
    const id = String(ad.id);
    if (shown.has(id)) return null;
    // Заблокированного продавца запоминаем как показанного: иначе он будет
    // приходить снова каждым опросом.
    shown.add(id);
    if (ad.seller && sellerMatchesBlacklist(ad.seller)) return null;
    return opts.cards.create(ad, fresh);
  };

  const add = (ads: Ad[], fresh: boolean): void => {
    if (!Array.isArray(ads) || !ads.length) {
      syncEmptyClass();
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
    syncEmptyClass();
  };

  const clear = (message?: string): void => {
    shown.clear();
    opts.feed.innerHTML = `<div class="empty">${message || EMPTY_TEXT}</div>`;
    syncEmptyClass();
  };

  const showEmpty = (): void => {
    if (opts.feed.querySelector(".card") || opts.feed.querySelector(".empty")) return;
    opts.feed.innerHTML = `<div class="empty">${EMPTY_TEXT}</div>`;
  };

  const purgeStale = (): void => {
    const now = Date.now() / 1000;
    opts.feed.querySelectorAll<HTMLElement>(".card[data-ts]").forEach((card) => {
      const ts = Number(card.dataset.ts);
      if (!ts || now - ts <= MAX_AGE_SEC) return;
      if (card.dataset.id) shown.delete(card.dataset.id);
      card.remove();
    });
    showEmpty();
    syncEmptyClass();
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
    syncEmptyClass();
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
      purgeStale();
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

  return { add, clear, purgeBlacklisted, start };
}
