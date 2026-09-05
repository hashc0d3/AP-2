import type { Ad, AvitoConnectStatus, AvitoPhoneResult, AvitoSession, BillingStatus, Category, PromoQuote, Region, SearchState } from "./types";

async function readJson<T>(res: Response): Promise<T> {
  const data = (await res.json()) as T & { error?: string };
  if (!res.ok) {
    throw new Error(data.error || `Ошибка ${res.status}`);
  }
  return data;
}

export const api = {
  billing(): Promise<BillingStatus> {
    return fetch("/api/billing/status").then((r) => readJson<BillingStatus>(r));
  },
  quotePromo(code: string): Promise<PromoQuote> {
    return fetch("/api/billing/promo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    }).then((r) => readJson<PromoQuote>(r));
  },
  sendSms(phone: string): Promise<{ ok: boolean; phone: string; message: string }> {
    return fetch("/api/billing/sms", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone }),
    }).then((r) => readJson(r));
  },
  verify(phone: string, code: string): Promise<BillingStatus> {
    return fetch("/api/auth/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone, code }),
    }).then((r) => readJson<BillingStatus>(r));
  },
  logout(): Promise<BillingStatus> {
    return fetch("/api/auth/logout", { method: "POST" }).then((r) => readJson<BillingStatus>(r));
  },
  pay(promo: string): Promise<BillingStatus> {
    return fetch("/api/billing/pay", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ promo }),
    }).then((r) => readJson<BillingStatus>(r));
  },
  trial(): Promise<BillingStatus> {
    return fetch("/api/billing/trial", { method: "POST" }).then((r) => readJson<BillingStatus>(r));
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
  preview(q: string, region: string, category: string): Promise<{ web_url?: string }> {
    const params = new URLSearchParams({ q, region, category });
    return fetch("/api/search?" + params).then((r) => readJson(r));
  },
  startSearch(query: string, region: string, category: string): Promise<SearchState> {
    return fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, region, category }),
    }).then((r) => readJson<SearchState>(r));
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
};
