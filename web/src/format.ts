/** Подготовка текста и ссылок к выводу в карточке. */

const HTML_ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

/**
 * Экранирование для вставки в HTML.
 *
 * Карточки собираются как строки и присваиваются через innerHTML, поэтому
 * любые данные объявления — название, имя продавца, описание — обязаны
 * пройти через эту функцию.
 */
export function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (char) => HTML_ESCAPES[char] || char);
}

/**
 * Описание в одну строку для свёрнутой карточки.
 *
 * Safari на iOS не обрезает многострочный текст по `line-clamp`, поэтому
 * переносы убираем заранее.
 */
export function collapseDescText(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

/** Avito разделяет разряды неразрывными пробелами разной ширины. */
export function displayPrice(price: string): string {
  return price.replace(/[\u00a0\u202f\u2009]/g, " ").trim();
}

function formatAge(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  if (total < 60) return `${total} сек назад`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes} мин назад`;
  return `${Math.floor(minutes / 60)} ч назад`;
}

/**
 * Время публикации: часы по времени выбранного региона плюс «сколько назад».
 *
 * Часовой пояс приходит извне, а не берётся из браузера: пользователь может
 * следить за объявлениями Владивостока, сидя в Москве.
 */
export function formatAddedAt(ts: number, timeZone: string): string {
  const addedMs = ts * 1000;
  const clock = new Intl.DateTimeFormat("ru-RU", {
    timeZone,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(addedMs);
  return `${clock} · ${formatAge((Date.now() - addedMs) / 1000)}`;
}

/**
 * Ссылка на картинку через свой прокси.
 *
 * Avito отдаёт изображения только со своим `Referer`, поэтому напрямую из
 * браузера они не загружаются (см. avito_monitor/web/images.py).
 */
export function imgSrc(url: string): string {
  return `/img?u=${encodeURIComponent(url)}`;
}

/** То же, но в максимальном качестве: у Avito размер задаётся поддоменом. */
export function imgSrcLarge(url: string): string {
  return imgSrc(url.replace(/^https:\/\/(\d+)\.img\.avito\.st\//, "https://128.img.avito.st/"));
}
