import type { Ad } from "./types";

export const isMobileDevice = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);

const isAndroid = /Android/i.test(navigator.userAgent);

export function itemWebUrl(ad: Ad): string {
  const id = String(ad.id);
  if (ad.url && ad.url.includes("avito.ru")) return ad.url;
  return `https://www.avito.ru/items/${id}`;
}

/** Открыть объявление в приложении Avito (сессия пользователя — в приложении). */
export function openInAvitoApp(ad: Ad): void {
  const id = String(ad.id);
  const web = itemWebUrl(ad);
  if (isAndroid) {
    const intent =
      `intent://www.avito.ru/items/${id}#Intent;scheme=https;package=com.avito.android;` +
      `S.browser_fallback_url=${encodeURIComponent(web)};end`;
    window.location.assign(intent);
    return;
  }
  window.open(web, "_blank", "noopener");
}
