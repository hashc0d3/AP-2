import type { Ad } from "./types";

export function itemWebUrl(ad: Ad): string {
  const id = String(ad.id);
  if (ad.url && ad.url.includes("avito.ru")) return ad.url.split("?")[0];
  return `https://www.avito.ru/items/${id}`;
}

/** Открыть чат Avito по объявлению (в приложении на Android или в браузере). */
export function openMessenger(ad: Ad): void {
  const id = String(ad.id);
  const web = `https://www.avito.ru/profile/messenger?itemId=${id}`;
  const isAndroid = /Android/i.test(navigator.userAgent);
  if (isAndroid) {
    const intent =
      `intent://www.avito.ru/profile/messenger?itemId=${id}#Intent;scheme=https;package=com.avito.android;` +
      `S.browser_fallback_url=${encodeURIComponent(web)};end`;
    window.location.assign(intent);
    return;
  }
  window.open(web, "_blank", "noopener");
}
