export type SavedUrl = { id: string; name: string; url: string };

const STORAGE_KEY = "parser1.saved-urls";
export const MAX_SAVED_URLS = 5;

function newId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

export function normalizeUrl(url: string): string {
  return url.trim();
}

export function urlsMatch(a: string, b: string): boolean {
  return normalizeUrl(a) === normalizeUrl(b);
}

export function defaultUrlName(url: string, fallbackIndex: number): string {
  try {
    const u = new URL(url);
    const parts = u.pathname.split("/").filter(Boolean);
    if (parts.length >= 2) {
      const tail = parts.slice(-2).join(" · ");
      return tail.length > 48 ? `${tail.slice(0, 47)}…` : tail;
    }
    if (parts.length === 1) return parts[0];
    return u.hostname.replace(/^www\./, "");
  } catch {
    return `Ссылка ${fallbackIndex + 1}`;
  }
}

export function loadSavedUrls(): SavedUrl[] {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]") as unknown;
    if (!Array.isArray(raw)) return [];
    return raw
      .map((item) => {
        if (!item || typeof item !== "object") return null;
        const rec = item as Record<string, unknown>;
        const url = normalizeUrl(String(rec.url || ""));
        if (!url) return null;
        return {
          id: String(rec.id || newId()),
          name: String(rec.name || defaultUrlName(url, 0)).trim() || defaultUrlName(url, 0),
          url,
        } satisfies SavedUrl;
      })
      .filter((item): item is SavedUrl => Boolean(item))
      .slice(0, MAX_SAVED_URLS);
  } catch {
    return [];
  }
}

export function saveSavedUrls(urls: SavedUrl[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(urls.slice(0, MAX_SAVED_URLS)));
  } catch {
    /* empty */
  }
}

export function findSavedUrlByUrl(urls: SavedUrl[], url: string): SavedUrl | undefined {
  return urls.find((item) => urlsMatch(item.url, url));
}

export type UpsertResult = {
  urls: SavedUrl[];
  item: SavedUrl;
  added: boolean;
  limitReached: boolean;
};

export function addSavedUrl(urls: SavedUrl[], name: string, url: string): UpsertResult {
  const normalized = normalizeUrl(url);
  const trimmedName = name.trim();
  if (!normalized || !trimmedName) {
    return {
      urls,
      item: { id: "", name: trimmedName, url: normalized },
      added: false,
      limitReached: false,
    };
  }
  const existing = findSavedUrlByUrl(urls, normalized);
  if (existing) {
    const item = { ...existing, name: trimmedName, url: normalized };
    const next = urls.map((entry) => (entry.id === existing.id ? item : entry));
    saveSavedUrls(next);
    return { urls: next, item, added: false, limitReached: false };
  }
  if (urls.length >= MAX_SAVED_URLS) {
    return {
      urls,
      item: { id: "", name: trimmedName, url: normalized },
      added: false,
      limitReached: true,
    };
  }
  const item: SavedUrl = { id: newId(), name: trimmedName, url: normalized };
  const next = [item, ...urls].slice(0, MAX_SAVED_URLS);
  saveSavedUrls(next);
  return { urls: next, item, added: true, limitReached: false };
}

export function updateSavedUrl(
  urls: SavedUrl[],
  id: string,
  name: string,
  url: string,
): SavedUrl[] {
  const trimmedName = name.trim();
  const normalized = normalizeUrl(url);
  if (!trimmedName || !normalized) return urls;
  const duplicate = urls.find((item) => item.id !== id && urlsMatch(item.url, normalized));
  if (duplicate) return urls;
  const next = urls.map((item) => (
    item.id === id ? { ...item, name: trimmedName, url: normalized } : item
  ));
  saveSavedUrls(next);
  return next;
}

export function upsertSavedUrl(urls: SavedUrl[], url: string, name?: string): UpsertResult {
  const normalized = normalizeUrl(url);
  const existing = findSavedUrlByUrl(urls, normalized);
  if (existing) {
    const item = { ...existing, name: name?.trim() || existing.name, url: normalized };
    const next = urls.map((entry) => (entry.id === existing.id ? item : entry));
    saveSavedUrls(next);
    return { urls: next, item, added: false, limitReached: false };
  }
  if (urls.length >= MAX_SAVED_URLS) {
    return {
      urls,
      item: { id: "", name: "", url: normalized },
      added: false,
      limitReached: true,
    };
  }
  const item: SavedUrl = {
    id: newId(),
    name: name?.trim() || defaultUrlName(normalized, urls.length),
    url: normalized,
  };
  const next = [item, ...urls].slice(0, MAX_SAVED_URLS);
  saveSavedUrls(next);
  return { urls: next, item, added: true, limitReached: false };
}

export function removeSavedUrl(urls: SavedUrl[], id: string): SavedUrl[] {
  const next = urls.filter((item) => item.id !== id);
  saveSavedUrls(next);
  return next;
}

export function renameSavedUrl(urls: SavedUrl[], id: string, name: string): SavedUrl[] {
  const trimmed = name.trim();
  if (!trimmed) return urls;
  const next = urls.map((item) => (item.id === id ? { ...item, name: trimmed } : item));
  saveSavedUrls(next);
  return next;
}

export function migrateLegacySavedUrl(savedSearchUrl: string): SavedUrl[] {
  const urls = loadSavedUrls();
  const legacy = normalizeUrl(savedSearchUrl);
  if (!legacy || urls.length) return urls;
  const next = [{ id: newId(), name: defaultUrlName(legacy, 0), url: legacy }];
  saveSavedUrls(next);
  return next;
}
