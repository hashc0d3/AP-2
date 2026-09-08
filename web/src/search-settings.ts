/**
 * Настройки поиска, запомненные в браузере.
 *
 * Хранятся локально: это выбор конкретного пользователя (регион, категория,
 * режим), а не состояние сервера. После перезагрузки страницы форма должна
 * выглядеть так же, как её оставили.
 *
 * Формат читается «мягко»: любое поле может отсутствовать или оказаться
 * мусором после обновления версии, и это не должно мешать запуску.
 */

import { asText } from "./parse";
import type { Category, Region, SearchMode } from "./types";

const STORAGE_KEY = "parser1.search";

export type SearchSettings = {
  region: Region | null;
  category: Category | null;
  searchMode: SearchMode | null;
  selectedSavedUrlId: string | null;
  hideImages: boolean;
  /** Текст поиска для категории «Все категории». */
  query: string;
  /** Единственная ссылка из формата до появления списка закладок. */
  legacySavedUrl: string;
};

const EMPTY: SearchSettings = {
  region: null,
  category: null,
  searchMode: null,
  selectedSavedUrlId: null,
  hideImages: false,
  query: "",
  legacySavedUrl: "",
};

export function loadSearchSettings(): SearchSettings {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}") as unknown;
    if (!raw || typeof raw !== "object") return { ...EMPTY };
    const fields = raw as Record<string, unknown>;
    return {
      region: toRegion(fields.region),
      category: toCategory(fields.category),
      searchMode: toSearchMode(fields.searchMode),
      selectedSavedUrlId: asText(fields.selectedSavedUrlId) || null,
      hideImages: fields.hideImages === true,
      query: asText(fields.query),
      legacySavedUrl: asText(fields.savedSearchUrl),
    };
  } catch {
    return { ...EMPTY };
  }
}

export function saveSearchSettings(settings: {
  region: Region;
  category: Category;
  searchMode: SearchMode;
  selectedSavedUrlId: string | null;
  hideImages: boolean;
  query: string;
}): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch {
    // Приватный режим запрещает запись — настройки просто не сохранятся.
  }
}

function toRegion(raw: unknown): Region | null {
  if (!raw || typeof raw !== "object") return null;
  const fields = raw as Record<string, unknown>;
  const slug = asText(fields.slug);
  const name = asText(fields.name);
  return slug && name ? { slug, name } : null;
}

function toCategory(raw: unknown): Category | null {
  if (!raw || typeof raw !== "object") return null;
  const fields = raw as Record<string, unknown>;
  const id = asText(fields.id);
  return id ? { id, name: asText(fields.name) } : null;
}

function toSearchMode(raw: unknown): SearchMode | null {
  return raw === "query" || raw === "url" ? raw : null;
}
