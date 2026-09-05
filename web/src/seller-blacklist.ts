import { api } from "./api";

const BLACKLIST_KEY = "parser1.sellerBlacklist";
export const BLACKLIST_EVENT = "parser1:seller-blacklist-changed";

export function normSellerText(value: string): string {
  return [...value.toLowerCase()].filter((ch) => /\p{L}|\p{N}/u.test(ch)).join("");
}

export function loadSellerBlacklist(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(BLACKLIST_KEY) || "[]") as unknown;
    if (!Array.isArray(raw)) return [];
    const seen = new Set<string>();
    const out: string[] = [];
    for (const item of raw) {
      const text = String(item).trim();
      if (!text) continue;
      const key = text.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(text);
    }
    return out;
  } catch {
    return [];
  }
}

export function saveSellerBlacklist(items: string[]): void {
  const seen = new Set<string>();
  const cleaned: string[] = [];
  for (const item of items) {
    const text = item.trim();
    if (!text) continue;
    const key = text.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    cleaned.push(text);
  }
  try {
    localStorage.setItem(BLACKLIST_KEY, JSON.stringify(cleaned));
  } catch {
    /* empty */
  }
  notifyBlacklistChanged();
}

export function sellerMatchesBlacklist(seller: string, list = loadSellerBlacklist()): boolean {
  const blob = normSellerText(seller);
  if (!blob) return false;
  return list.some((entry) => {
    const needle = normSellerText(entry);
    return needle && blob.includes(needle);
  });
}

export function addSellerToBlacklist(seller: string): boolean {
  const text = seller.trim();
  if (!text || sellerMatchesBlacklist(text)) return false;
  saveSellerBlacklist([...loadSellerBlacklist(), text]);
  return true;
}

export function removeSellerFromBlacklist(seller: string): void {
  const key = seller.trim().toLowerCase();
  if (!key) return;
  saveSellerBlacklist(loadSellerBlacklist().filter((item) => item.toLowerCase() !== key));
}

export function notifyBlacklistChanged(): void {
  window.dispatchEvent(new CustomEvent(BLACKLIST_EVENT));
}

export async function syncSellerBlacklist(): Promise<void> {
  await api.updateSellerBlacklist(loadSellerBlacklist());
}
