import type { Ad, AuthStatus, AvitoPhoneResult, AvitoSession, Category, Region, SearchMode, SearchState, SpfaBalance } from "./types";

async function readJson<T>(res: Response): Promise<T> {
  const data = (await res.json()) as T & { error?: string };
  if (!res.ok) {
    throw new Error(data.error || `Ошибка ${res.status}`);
  }
  return data;
}

async function fetchJson<T>(url: string, init?: RequestInit, timeoutMs = 45000): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { ...init, signal: controller.signal });
    return await readJson<T>(response);
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error("Сервер не ответил вовремя — проверьте, что parser.py запущен");
    }
    if (err instanceof TypeError) {
      throw new Error("Нет связи с сервером — запустите parser.py");
    }
    throw err;
  } finally {
    window.clearTimeout(timer);
  }
}

export const api = {
  authStatus(): Promise<AuthStatus> {
    return fetch("/api/billing/status").then((r) => readJson<AuthStatus>(r));
  },
  login(login: string, password: string): Promise<AuthStatus> {
    return fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ login, password }),
    }).then((r) => readJson<AuthStatus>(r));
  },
  logout(): Promise<AuthStatus> {
    return fetch("/api/auth/logout", { method: "POST" }).then((r) => readJson<AuthStatus>(r));
  },
  status(): Promise<SearchState> {
    return fetch("/api/status").then((r) => readJson<SearchState>(r));
  },
  categories(): Promise<Category[]> {
    return fetch("/api/categories").then((r) => readJson<Category[]>(r));
  },
  regions(q: string): Promise<Region[]> {
    return fetch("/api/regions?q=" + encodeURIComponent(q)).then((r) => readJson<Region[]>(r));
  },
  startSearch(payload: {
    mode: SearchMode;
    query?: string;
    url?: string;
    region?: string;
    category?: string;
    seller_skip?: string[];
    iphone_models?: string[] | null;
  }): Promise<SearchState> {
    const sellerSkip = payload.seller_skip ?? [];
    const iphoneModels = payload.iphone_models;
    const body = payload.mode === "url"
      ? {
        mode: "url",
        url: payload.url || payload.query || "",
        seller_skip: sellerSkip,
        ...(iphoneModels !== undefined ? { iphone_models: iphoneModels } : {}),
      }
      : {
        mode: "query",
        query: payload.query || "",
        region: payload.region || "all",
        category: payload.category || "apple_phones",
        seller_skip: sellerSkip,
        ...(iphoneModels !== undefined ? { iphone_models: iphoneModels } : {}),
      };
    return fetchJson<SearchState>("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  },
  stopSearch(): Promise<SearchState> {
    return fetch("/api/search/stop", { method: "POST" }).then((r) => readJson<SearchState>(r));
  },
  ads(): Promise<Ad[]> {
    return fetch("/api/ads").then((r) => readJson<Ad[]>(r));
  },
  reset(): Promise<void> {
    return fetch("/api/reset", { method: "POST" }).then((r) => readJson(r)).then(() => undefined);
  },
  avitoSession(): Promise<AvitoSession> {
    return fetch("/api/avito/session").then((r) => readJson<AvitoSession>(r));
  },
  importAvitoSession(raw: string): Promise<AvitoSession> {
    const trimmed = raw.trim();
    const body = trimmed.startsWith("[") ? trimmed : JSON.stringify({ cookies: JSON.parse(trimmed) });
    return fetch("/api/avito/session/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    }).then((r) => readJson<AvitoSession>(r));
  },
  clearAvitoSession(): Promise<AvitoSession> {
    return fetch("/api/avito/session/clear", { method: "POST" }).then((r) => readJson<AvitoSession>(r));
  },
  avitoPhone(adId: string | number): Promise<AvitoPhoneResult> {
    return fetch("/api/avito/phone", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ad_id: String(adId) }),
    }).then((r) => readJson<AvitoPhoneResult>(r));
  },
  resourceBalance(): Promise<SpfaBalance> {
    return fetch("/api/resource/balance").then((r) => readJson<SpfaBalance>(r));
  },
  updateSellerBlacklist(sellers: string[]): Promise<{ sellers: string[] }> {
    return fetch("/api/seller-blacklist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sellers }),
    }).then((r) => readJson<{ sellers: string[] }>(r));
  },
};
