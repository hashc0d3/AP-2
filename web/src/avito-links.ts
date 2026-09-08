import type { Ad } from "./types";

/**
 * Ссылка на объявление на Avito.
 *
 * Сервер отдаёт готовый путь, но не всегда: если его нет, собираем адрес
 * по ID — Avito сам развернёт его в полную ссылку.
 */
export function itemWebUrl(ad: Ad): string {
  if (ad.url && ad.url.includes("avito.ru")) {
    // Параметры запроса — это метки поиска, в ссылке они не нужны.
    return ad.url.split("?")[0] ?? ad.url;
  }
  return `https://www.avito.ru/items/${String(ad.id)}`;
}
