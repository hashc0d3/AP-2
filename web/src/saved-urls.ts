/**
 * Сохранённые ссылки Avito (режим поиска «по ссылке»).
 *
 * Хранятся в localStorage браузера, а не на сервере: это личные закладки
 * пользователя, и серверу для поиска они не нужны — он получает готовую
 * ссылку в запросе.
 *
 * Функции не меняют переданный список, а возвращают новый и сразу пишут
 * его на диск: вызывающему достаточно присвоить результат.
 */

import { asText } from "./parse";

export type SavedUrl = { id: string; name: string; url: string };

const STORAGE_KEY = "parser1.saved-urls";

/** Больше пяти закладок в выпадающий список уже не помещается. */
export const MAX_SAVED_URLS = 5;

const MAX_NAME_LENGTH = 48;

export type SaveResult = {
  urls: SavedUrl[];
  item: SavedUrl;
  /** Ссылка добавлена как новая (иначе обновлена существующая). */
  added: boolean;
  /** Достигнут предел MAX_SAVED_URLS — ничего не сохранено. */
  limitReached: boolean;
};

function newId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function sameUrl(left: string, right: string): boolean {
  return left.trim() === right.trim();
}

/** Название по ссылке: два последних участка пути обычно узнаваемы. */
function nameFromUrl(url: string, fallbackIndex: number): string {
  try {
    const parsed = new URL(url);
    const parts = parsed.pathname.split("/").filter(Boolean);
    if (parts.length >= 2) {
      const tail = parts.slice(-2).join(" · ");
      return tail.length > MAX_NAME_LENGTH ? `${tail.slice(0, MAX_NAME_LENGTH - 1)}…` : tail;
    }
    return parts[0] || parsed.hostname.replace(/^www\./, "");
  } catch {
    return `Ссылка ${fallbackIndex + 1}`;
  }
}

function read(): SavedUrl[] {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]") as unknown;
    if (!Array.isArray(raw)) return [];
    return raw
      .map(toSavedUrl)
      .filter((item): item is SavedUrl => item !== null)
      .slice(0, MAX_SAVED_URLS);
  } catch {
    return [];
  }
}

function toSavedUrl(raw: unknown): SavedUrl | null {
  if (!raw || typeof raw !== "object") return null;
  const fields = raw as Record<string, unknown>;
  const url = asText(fields.url);
  if (!url) return null;
  return {
    id: asText(fields.id) || newId(),
    name: asText(fields.name) || nameFromUrl(url, 0),
    url,
  };
}

function write(urls: SavedUrl[]): SavedUrl[] {
  const capped = urls.slice(0, MAX_SAVED_URLS);
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(capped));
  } catch {
    // Приватный режим запрещает запись — закладки просто не сохранятся.
  }
  return capped;
}

export function findSavedUrlByUrl(urls: SavedUrl[], url: string): SavedUrl | undefined {
  return urls.find((item) => sameUrl(item.url, url));
}

/** Добавить ссылку или обновить название уже сохранённой. */
export function addSavedUrl(urls: SavedUrl[], name: string, url: string): SaveResult {
  const trimmedUrl = url.trim();
  const trimmedName = name.trim();
  const rejected = (limitReached: boolean): SaveResult => ({
    urls,
    item: { id: "", name: trimmedName, url: trimmedUrl },
    added: false,
    limitReached,
  });

  if (!trimmedUrl || !trimmedName) return rejected(false);

  const existing = findSavedUrlByUrl(urls, trimmedUrl);
  if (existing) {
    const item = { ...existing, name: trimmedName, url: trimmedUrl };
    const next = write(urls.map((entry) => (entry.id === existing.id ? item : entry)));
    return { urls: next, item, added: false, limitReached: false };
  }

  if (urls.length >= MAX_SAVED_URLS) return rejected(true);

  const item: SavedUrl = { id: newId(), name: trimmedName, url: trimmedUrl };
  return { urls: write([item, ...urls]), item, added: true, limitReached: false };
}

/**
 * Правка названия и ссылки из списка закладок.
 *
 * Возвращает тот же массив, если менять нечего: пустое поле или такая
 * ссылка уже есть под другим названием. Вызывающий сравнивает по ссылке и
 * понимает, что перерисовывать список не нужно.
 */
export function updateSavedUrl(
  urls: SavedUrl[],
  id: string,
  name: string,
  url: string,
): SavedUrl[] {
  const trimmedName = name.trim();
  const trimmedUrl = url.trim();
  if (!trimmedName || !trimmedUrl) return urls;
  if (urls.some((item) => item.id !== id && sameUrl(item.url, trimmedUrl))) return urls;
  return write(
    urls.map((item) => (item.id === id ? { ...item, name: trimmedName, url: trimmedUrl } : item)),
  );
}

export function removeSavedUrl(urls: SavedUrl[], id: string): SavedUrl[] {
  return write(urls.filter((item) => item.id !== id));
}

/**
 * Перенос единственной ссылки из старого формата настроек.
 *
 * До появления списка закладок ссылка хранилась одним полем `savedSearchUrl`
 * в настройках поиска. Переносим её, только если список ещё пуст.
 */
export function migrateLegacySavedUrl(legacyUrl: string): SavedUrl[] {
  const urls = read();
  const legacy = legacyUrl.trim();
  if (!legacy || urls.length) return urls;
  return write([{ id: newId(), name: nameFromUrl(legacy, 0), url: legacy }]);
}
