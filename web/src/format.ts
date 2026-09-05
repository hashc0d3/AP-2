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

export function imgSrc(url: string): string {
  return "/img?u=" + encodeURIComponent(url);
}
