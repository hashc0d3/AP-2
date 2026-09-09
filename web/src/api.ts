/**
 * Единственная точка обращения к серверу.
 *
 * Все запросы идут через `request`, поэтому таймаут, разбор ошибок и
 * понятные сообщения про обрыв связи описаны один раз. Пути эндпоинтов
 * не должны встречаться больше нигде в коде.
 */

import type {
  Ad,
  AuthStatus,
  AvitoPhoneResult,
  AvitoSession,
  Category,
  Region,
  SearchMode,
  SearchState,
  SpfaBalance,
} from "./types";

/** Обычный запрос: сервер отвечает из памяти. */
const DEFAULT_TIMEOUT_MS = 15000;

/**
 * Запуск поиска: сервер может обращаться к внешнему сервису за адресом
 * API Avito, и это заметно дольше остальных запросов.
 */
const START_SEARCH_TIMEOUT_MS = 45000;

const JSON_HEADERS = { "Content-Type": "application/json" };

type RequestOptions = {
  method?: "GET" | "POST";
  body?: unknown;
  timeoutMs?: number;
};

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, timeoutMs = DEFAULT_TIMEOUT_MS } = options;
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(path, {
      method,
      signal: controller.signal,
      ...(body === undefined ? {} : { headers: JSON_HEADERS, body: JSON.stringify(body) }),
    });
    return await readJson<T>(response);
  } catch (err) {
    throw asFriendlyError(err);
  } finally {
    window.clearTimeout(timer);
  }
}

async function readJson<T>(response: Response): Promise<T> {
  const payload = (await response.json().catch(() => null)) as (T & { error?: string }) | null;
  if (!response.ok) {
    throw new Error(payload?.error || `Ошибка ${response.status}`);
  }
  if (payload === null) {
    throw new Error("Сервер вернул неожиданный ответ");
  }
  return payload;
}

function asFriendlyError(err: unknown): Error {
  if (err instanceof DOMException && err.name === "AbortError") {
    return new Error("Сервер не ответил вовремя — проверьте, что приложение запущено");
  }
  // fetch бросает TypeError, когда до сервера вообще не дошли.
  if (err instanceof TypeError) {
    return new Error("Нет связи с сервером — проверьте, что приложение запущено");
  }
  return err instanceof Error ? err : new Error(String(err));
}

export type StartSearchPayload = {
  mode: SearchMode;
  query?: string;
  url?: string;
  region?: string;
  category?: string;
  seller_skip?: string[];
  iphone_models?: string[] | null;
};

function startSearchBody(payload: StartSearchPayload): Record<string, unknown> {
  const sellerSkip = payload.seller_skip ?? [];
  // `null` выключает фильтр моделей (планшеты, ноутбуки, приставки).
  // Ключ пропускаем только если поле не передали вовсе.
  const models =
    payload.iphone_models !== undefined ? { iphone_models: payload.iphone_models } : {};
  if (payload.mode === "url") {
    return {
      mode: "url",
      url: payload.url || payload.query || "",
      seller_skip: sellerSkip,
      ...models,
    };
  }
  return {
    mode: "query",
    query: payload.query || "",
    region: payload.region || "all",
    category: payload.category || "apple_phones",
    seller_skip: sellerSkip,
    ...models,
  };
}

/**
 * Разбор вставленного JSON cookies: массив из Cookie-Editor или готовый
 * объект. Ошибку разбора превращаем в понятный текст — пользователь
 * вставляет JSON руками, и опечатка здесь дело обычное.
 */
function avitoSessionBody(raw: string): Record<string, unknown> {
  const trimmed = raw.trim();
  const userAgent = typeof navigator !== "undefined" ? navigator.userAgent : "";
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    throw new Error("Не удалось разобрать JSON — скопируйте его из Cookie-Editor целиком");
  }
  if (Array.isArray(parsed)) {
    return { cookies: parsed, user_agent: userAgent };
  }
  if (!parsed || typeof parsed !== "object") {
    throw new Error("Ожидается список cookies или объект с полем cookies");
  }
  const fields = parsed as Record<string, unknown>;
  return { ...fields, user_agent: fields.user_agent || userAgent };
}

export const api = {
  // ── Вход ────────────────────────────────────────────────────────────
  authStatus: (): Promise<AuthStatus> => request("/api/auth/status"),

  login: (login: string, password: string): Promise<AuthStatus> =>
    request("/api/auth/login", { method: "POST", body: { login, password } }),

  logout: (): Promise<AuthStatus> => request("/api/auth/logout", { method: "POST" }),

  // ── Поиск ───────────────────────────────────────────────────────────
  status: (): Promise<SearchState> => request("/api/status"),

  categories: (): Promise<Category[]> => request("/api/categories"),

  regions: (query: string): Promise<Region[]> =>
    request(`/api/regions?q=${encodeURIComponent(query)}`),

  startSearch: (payload: StartSearchPayload): Promise<SearchState> =>
    request("/api/search", {
      method: "POST",
      body: startSearchBody(payload),
      timeoutMs: START_SEARCH_TIMEOUT_MS,
    }),

  stopSearch: (): Promise<SearchState> => request("/api/search/stop", { method: "POST" }),

  // ── Лента ───────────────────────────────────────────────────────────
  ads: (): Promise<Ad[]> => request("/api/ads"),

  updateSellerBlacklist: (sellers: string[]): Promise<{ sellers: string[] }> =>
    request("/api/seller-blacklist", { method: "POST", body: { sellers } }),

  // ── Сессия Avito (кнопка «Позвонить») ───────────────────────────────
  avitoSession: (): Promise<AvitoSession> => request("/api/avito/session"),

  // async, чтобы ошибка разбора JSON пришла в .catch() вызывающего, а не
  // выбросилась синхронно из обработчика клика.
  importAvitoSession: async (raw: string): Promise<AvitoSession> =>
    request("/api/avito/session/import", { method: "POST", body: avitoSessionBody(raw) }),

  clearAvitoSession: (): Promise<AvitoSession> =>
    request("/api/avito/session/clear", { method: "POST" }),

  avitoPhone: (adId: string | number): Promise<AvitoPhoneResult> =>
    request("/api/avito/phone", { method: "POST", body: { ad_id: String(adId) } }),

  // ── Баланс сервиса cookies ──────────────────────────────────────────
  resourceBalance: (): Promise<SpfaBalance> => request("/api/resource/balance"),
};
