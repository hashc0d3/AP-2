export type AuthStatus = {
  logged_in: boolean;
  username: string;
  active: boolean;
};

export type Region = { slug: string; name: string };
export type Category = { id: string; name: string };
export type SearchMode = "query" | "url";

export type Account = {
  id: string;
  phone: string;
  phone_label: string;
  created_at: number;
};

export type BillingStatus = {
  logged_in: boolean;
  account: Account | null;
  created?: boolean;
  active: boolean;
  plan: "none" | "trial" | "paid" | "expired" | string;
  trial_used: boolean;
  trial_available: boolean;
  expires_at: number;
  seconds_left: number;
  phone: string;
  price: number;
  currency: string;
  paid_days: number;
  trial_hours: number;
};

export type PromoQuote = {
  code: string;
  price: number;
  discount: number;
  note: string;
  extra_days?: number;
};

export type SearchState = {
  running: boolean;
  query: string;
  search_mode?: SearchMode;
  region?: Region;
  category?: Category;
  web_url?: string;
  api_url?: string;
  error?: string;
  subscription?: AuthStatus;
};

export type AvitoConnectStatus = {
  running: boolean;
  step: string;
  error?: string;
  connected: boolean;
  label?: string;
  phone?: string;
};

export type AvitoSession = AvitoConnectStatus & {
  logged_in: boolean;
  saved_at?: number;
  connect?: AvitoConnectStatus;
};

export type AvitoPhoneResult = {
  ok: boolean;
  phone?: string;
  error?: string;
  code?: string;
};

export type SpfaBalance = {
  success?: boolean;
  balance: number;
  error?: string;
};

export type PriceBatchStatus = {
  success?: boolean;
  status?: string;
  task_id?: string;
  results?: unknown[];
  price_per_ad?: string;
  total_cost?: string;
  billed?: boolean;
  error?: string;
  message?: string;
};

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
  ts?: number;
};
