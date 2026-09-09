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
  description?: string;
  /** Одноразовый ключ Avito для запроса номера, если он был в карточке. */
  phone_key?: string;
};
