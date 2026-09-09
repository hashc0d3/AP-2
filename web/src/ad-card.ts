/**
 * Карточка объявления: сборка разметки и обработчики кнопок.
 *
 * Карточки собираются строками HTML, а не через `document.createElement`:
 * при активном поиске они приходят пачками, и один `innerHTML` на карточку
 * заметно дешевле десятков вставок в DOM. Плата за это — обязательное
 * экранирование любых данных объявления через `escapeHtml`.
 */

import { itemWebUrl } from "./avito-links";
import { ICON_CLOSE, ICON_EXT, ICON_PHONE, ICON_STAR, ICON_STAR_OUTLINE } from "./card-icons";
import { isFavorite, toggleFavorite } from "./favorites";
import { collapseDescText, displayPrice, escapeHtml, formatAddedAt, imgSrc } from "./format";
import { openImageLightbox } from "./image-lightbox";
import { regionTimezone } from "./region-timezones";
import type { Ad } from "./types";

export type AdCardRenderer = {
  /** Готовый узел карточки. */
  create: (ad: Ad, fresh: boolean) => HTMLElement;
  /** Пересчитать «сколько назад» у всех карточек в ленте. */
  refreshTimes: () => void;
  /** Применить режим «без фотографий» к ленте и уже показанным карточкам. */
  applyHideImages: () => void;
};

export function createAdCardRenderer(opts: {
  feed: HTMLElement;
  /** Текущий регион: задаёт часовой пояс времени публикации. */
  regionSlug: () => string;
  hideImages: () => boolean;
  onCall: (ad: Ad, btn: HTMLButtonElement) => void;
  onBlockSeller: (seller: string) => void;
}): AdCardRenderer {
  const create = (ad: Ad, fresh: boolean): HTMLElement => {
    const wrap = document.createElement("div");
    wrap.innerHTML = cardHtml(ad, {
      hideImages: opts.hideImages(),
      timeZone: regionTimezone(opts.regionSlug()),
    });
    const card = wrap.firstElementChild as HTMLElement;
    // Класс включает подсветку: объявление пришло только что.
    if (fresh) card.classList.add("fresh");
    bindActions(card, ad, opts);
    return card;
  };

  const refreshTimes = (): void => {
    const timeZone = regionTimezone(opts.regionSlug());
    opts.feed.querySelectorAll<HTMLElement>(".card-meta[data-ts]").forEach((meta) => {
      const ts = Number(meta.dataset.ts);
      if (!ts) return;
      const parts = [formatAddedAt(ts, timeZone), meta.dataset.address || ""];
      meta.textContent = parts.filter(Boolean).join(" · ");
    });
  };

  const applyHideImages = (): void => {
    const hidden = opts.hideImages();
    opts.feed.classList.toggle("no-images", hidden);
    opts.feed.querySelectorAll<HTMLElement>(".card").forEach((card) => {
      card.classList.toggle("card--no-media", hidden);
      card.querySelector(".card-media")?.classList.toggle("hidden", hidden);
    });
  };

  return { create, refreshTimes, applyHideImages };
}

function cardHtml(ad: Ad, view: { hideImages: boolean; timeZone: string }): string {
  const web = escapeHtml(itemWebUrl(ad));
  const fav = isFavorite(String(ad.id));
  const favTitle = fav ? "Убрать из избранного" : "В избранное";
  const title = escapeHtml(ad.title || "");

  return `<article class="card${fav ? " is-fav" : ""}${view.hideImages ? " card--no-media" : ""}" data-id="${escapeHtml(String(ad.id))}"${
    ad.ts ? ` data-ts="${ad.ts}"` : ""
  }${ad.received_at ? ` data-received-at="${ad.received_at}"` : ""}${ad.seller ? ` data-seller="${escapeHtml(ad.seller)}"` : ""}">
      ${view.hideImages ? "" : mediaHtml(ad)}
      <div class="card-body">
        <div class="card-price-block">
          <div class="card-price">${escapeHtml(displayPrice(ad.price || "—"))}</div>
          <div class="card-toolbar">
            <a class="card-open" href="${web}" target="_blank" rel="noopener" title="Открыть на Avito" aria-label="Открыть на Avito">${ICON_EXT}</a>
            <button type="button" class="card-fav${fav ? " on" : ""}" data-action="fav" aria-label="${favTitle}" title="${favTitle}">
              ${fav ? ICON_STAR : ICON_STAR_OUTLINE}
            </button>
          </div>
        </div>
        <a class="card-title" href="${web}" target="_blank" rel="noopener">${title}</a>
        ${metaHtml(ad, view.timeZone)}
        ${sellerHtml(ad)}
        <div class="card-actions">${actionHtml(ad)}</div>
        ${descriptionHtml(ad)}
      </div>
    </article>`;
}

function mediaHtml(ad: Ad): string {
  const photo = ad.images?.[0];
  if (!photo) {
    return '<div class="card-media"><div class="ph">нет фото</div></div>';
  }
  return `<button type="button" class="card-media" data-action="zoom-photo" aria-label="Увеличить фото">
      <img src="${imgSrc(photo)}" alt="" loading="lazy" />
    </button>`;
}

function metaHtml(ad: Ad, timeZone: string): string {
  const added = ad.ts ? formatAddedAt(ad.ts, timeZone) : ad.published || "";
  const text = [added, ad.address].filter(Boolean).join(" · ");
  if (!text) return "";
  // data-ts и data-address нужны, чтобы обновлять «сколько назад» без
  // перерисовки карточки целиком.
  const attrs = [
    ad.ts ? ` data-ts="${ad.ts}"` : "",
    ad.address ? ` data-address="${escapeHtml(ad.address)}"` : "",
  ].join("");
  return `<div class="card-meta"${attrs}>${escapeHtml(text)}</div>`;
}

function sellerHtml(ad: Ad): string {
  if (!ad.seller) return "";
  return `<div class="card-seller-row">
      <span class="card-seller">Продавец: ${escapeHtml(ad.seller)}</span>
      <button type="button" class="card-seller-block" data-action="block-seller" aria-label="В чёрный список" title="В чёрный список">${ICON_CLOSE}</button>
    </div>`;
}

function actionHtml(ad: Ad): string {
  if (ad.can_call) {
    return `<button class="card-btn call" type="button" data-action="call">${ICON_PHONE}<span>Позвонить</span></button>`;
  }
  const web = escapeHtml(itemWebUrl(ad));
  return `<a class="card-btn open-ad" href="${web}" target="_blank" rel="noopener">${ICON_EXT}<span>Открыть объявление</span></a>`;
}

function descriptionHtml(ad: Ad): string {
  const text = (ad.description || "").trim();
  if (!text) return "";
  return `<div class="card-desc" data-action="toggle-desc" role="button" tabindex="0" aria-expanded="false">
      <span class="card-desc-text">${escapeHtml(collapseDescText(text))}</span>
    </div>`;
}

function bindActions(
  card: HTMLElement,
  ad: Ad,
  opts: {
    onCall: (ad: Ad, btn: HTMLButtonElement) => void;
    onBlockSeller: (seller: string) => void;
  },
): void {
  const action = (name: string) => card.querySelector<HTMLButtonElement>(`[data-action="${name}"]`);

  const favBtn = action("fav");
  favBtn?.addEventListener("click", (ev) => {
    stop(ev);
    const on = toggleFavorite(ad.id);
    card.classList.toggle("is-fav", on);
    favBtn.classList.toggle("on", on);
    favBtn.innerHTML = on ? ICON_STAR : ICON_STAR_OUTLINE;
    favBtn.title = on ? "Убрать из избранного" : "В избранное";
    favBtn.setAttribute("aria-label", favBtn.title);
  });

  const callBtn = action("call");
  callBtn?.addEventListener("click", (ev) => {
    ev.preventDefault();
    opts.onCall(ad, callBtn);
  });

  action("zoom-photo")?.addEventListener("click", (ev) => {
    stop(ev);
    const photo = card.querySelector<HTMLImageElement>(".card-media img");
    if (photo) openImageLightbox(photo);
  });

  const blockBtn = action("block-seller");
  blockBtn?.addEventListener("click", (ev) => {
    stop(ev);
    if (ad.seller) opts.onBlockSeller(ad.seller);
  });

  bindDescriptionToggle(card, ad);
}

/** Разворот описания по клику и с клавиатуры (карточка — не настоящая кнопка). */
function bindDescriptionToggle(card: HTMLElement, ad: Ad): void {
  const box = card.querySelector<HTMLElement>('[data-action="toggle-desc"]');
  const text = box?.querySelector<HTMLElement>(".card-desc-text");
  const full = (ad.description || "").trim();
  if (!box || !text || !full) return;

  const toggle = (): void => {
    const open = box.classList.toggle("open");
    text.textContent = open ? full : collapseDescText(full);
    box.setAttribute("aria-expanded", open ? "true" : "false");
  };

  box.addEventListener("click", (ev) => {
    ev.preventDefault();
    toggle();
  });
  box.addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter" && ev.key !== " ") return;
    ev.preventDefault();
    toggle();
  });
}

/** Не дать клику по кнопке уйти на ссылку-карточку под ней. */
function stop(ev: Event): void {
  ev.preventDefault();
  ev.stopPropagation();
}
