/** Формы данных, которые приходят от сервера. Совпадают с ответами API. */

export type AuthStatus = {
  logged_in: boolean;
  username: string;
  active: boolean;
};

export type Region = { slug: string; name: string };
export type Category = { id: string; name: string };

/** «query» — поиск по региону и категории, «url» — по готовой ссылке Avito. */
export type SearchMode = "query" | "url";

/** Состояние текущего поиска: ответ GET /api/status и POST /api/search. */
export type SearchState = {
  running: boolean;
  query: string;
  search_mode?: SearchMode;
  region?: Region;
  category?: Category;
  web_url?: string;
  api_url?: string;
  error?: string;
  auth?: AuthStatus;
  iphone_models?: string[] | null;
};

/** Сессия Avito, сохранённая пользователем для кнопки «Позвонить». */
export type AvitoSession = {
  connected: boolean;
  logged_in: boolean;
  label?: string;
  saved_at?: number;
  error?: string;
};

export type AvitoPhoneResult = {
  ok: boolean;
  phone?: string;
  error?: string;
  /** Причина отказа: no_session, not_logged_in, auth_required. */
  code?: string;
  /** Имена полей ответа Avito, если номер не разобрали. */
  fields?: string[];
};

/** Снимок GET /api/metrics: счётчики с запуска процесса и живое состояние. */
export type SystemMetrics = {
  started_at: number;
  now: number;
  requests: Record<string, number>;
  cycles: number;
  cycles_ok: number;
  cycles_failed: number;
  cycles_throttled: number;
  new_ads: number;
  pace_sec: number;
  cookie_sets: number;
  last_cycle_sec: number | null;
  cycle_median_sec: number | null;
  json_age_min_sec: number | null;
  json_age_max_sec: number | null;
  events: { at: number; proxy: string; kind: string; text: string }[];
  proxies: {
    label: string;
    ip: string;
    prefix: string;
    changing: boolean;
    available: boolean;
    cooldown_sec: number;
    strikes: number;
    bans: number;
    hangs: number;
    leases: number;
    requests: Record<string, number>;
  }[];
  cookies: {
    counts: { ready: number; in_use: number; blocked: number; dead: number };
    usable: number;
    oldest_hours: number | null;
    youngest_hours: number | null;
    blocked: { id: string | number; blocked_sec: number | null; age_hours: number | null }[];
  };
  search: {
    running: boolean;
    query: string;
    region: string;
    category: string;
    error: string;
    started_at: number;
  };
};

/** Баланс сервиса cookies. */
export type SpfaBalance = {
  success?: boolean;
  balance: number;
  error?: string;
};

/** Объявление в ленте. Собирается сервером в avito_monitor/avito/items.py. */
export type Ad = {
  id: string | number;
  title: string;
  price: string;
  address: string;
  url: string;
  images: string[];
  can_call: boolean;
  can_message: boolean;
  seller?: string;
  published?: string;
  /** Unix-время публикации в секундах. */
  ts?: number;
  /** Когда объявление попало в нашу ленту, unix-секунды. */
  received_at?: number;
  description?: string;
  /** Одноразовый ключ Avito для запроса номера, если он был в карточке. */
  phone_key?: string;
};
