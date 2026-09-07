export function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch] || ch));
}

export function displayPrice(price: string): string {
  return price.replace(/[\u00a0\u202f\u2009]/g, " ").trim();
}

export function formatLeft(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const days = Math.floor(s / 86400);
  const hours = Math.floor((s % 86400) / 3600);
  const mins = Math.floor((s % 3600) / 60);
  if (days > 0) return `${days} дн. ${hours} ч`;
  if (hours > 0) return `${hours} ч ${mins} мин`;
  return `${mins} мин`;
}

function formatAge(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s} сек назад`;
  const minutes = Math.floor(s / 60);
  if (minutes < 60) return `${minutes} мин назад`;
  return `${Math.floor(minutes / 60)} ч назад`;
}

export function formatAddedAt(ts: number, timeZone: string): string {
  const addedMs = ts * 1000;
  const nowMs = Date.now();
  const seconds = Math.max(0, Math.floor((nowMs - addedMs) / 1000));
  const age = formatAge(seconds);
  const clock = new Intl.DateTimeFormat("ru-RU", {
    timeZone,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(addedMs);
  if (age) return `${clock} · ${age}`;
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone,
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(addedMs);
}

export function imgSrc(url: string): string {
  return "/img?u=" + encodeURIComponent(url);
}

/** Avito CDN: поддомен — условный размер; 128 — максимальное качество. */
export function imgSrcLarge(url: string): string {
  const large = url.replace(/^https:\/\/(\d+)\.img\.avito\.st\//, "https://128.img.avito.st/");
  return imgSrc(large);
}
