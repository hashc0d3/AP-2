import type { Ad } from "./types";

export type PushBlockReason = "unsupported" | "insecure" | "denied" | "default" | "server";

const WEB_PUSH_KEY = "parser1.webPushSubscribed";

function isMobileDevice(): boolean {
  return /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
}

function isIosDevice(): boolean {
  return /iPhone|iPad|iPod/i.test(navigator.userAgent);
}

function isYandexBrowser(): boolean {
  return /YaBrowser/i.test(navigator.userAgent);
}

export function canUsePush(): boolean {
  return typeof window !== "undefined"
    && window.isSecureContext
    && "Notification" in window
    && "serviceWorker" in navigator
    && "PushManager" in window;
}

export function hasNotificationApi(): boolean {
  return typeof window !== "undefined" && "Notification" in window;
}

export function pushPermission(): NotificationPermission | "unsupported" {
  if (!canUsePush()) return "unsupported";
  return Notification.permission;
}

export function pushBlockReason(): PushBlockReason | null {
  if (typeof window === "undefined" || !("Notification" in window)) return "unsupported";
  if (!window.isSecureContext) return "insecure";
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return "unsupported";
  if (Notification.permission === "denied") return "denied";
  if (Notification.permission === "default") return "default";
  return null;
}

export function pushEnableHint(reason: PushBlockReason | null = pushBlockReason()): string {
  const mobile = isMobileDevice();
  const yandex = isYandexBrowser();
  const ios = isIosDevice();

  switch (reason) {
    case "unsupported":
      return "Браузер не поддерживает фоновые push (нужны Safari 16.4+ / Chrome)";
    case "insecure":
      if (mobile && yandex) {
        return "Нужен HTTPS — откройте https://peterparser.ru";
      }
      return "Уведомления работают только по HTTPS";
    case "denied":
      if (mobile && yandex) {
        return "Яндекс.Браузер → Настройки сайта → Уведомления → Разрешить";
      }
      if (ios) {
        return "Настройки → Уведомления → Safari/Chrome → разрешите для сайта";
      }
      return "Уведомления заблокированы в настройках браузера";
    case "default":
      return mobile
        ? "Нажмите «Разрешить» — появится системный запрос"
        : "Разрешите уведомления во всплывающем окне";
    case "server":
      return "На сервере не настроены VAPID-ключи — см. tools/generate_vapid.py";
    default:
      return "Не удалось включить уведомления";
  }
}

/** Короткий статус для блока в меню пользователя. */
export function pushStatusLine(): string {
  if (!hasNotificationApi()) return "Браузер не поддерживает уведомления";
  if (!window.isSecureContext) {
    return location.protocol === "http:"
      ? `Сейчас ${location.host} по HTTP — нужен HTTPS`
      : "Нужно защищённое соединение (HTTPS)";
  }
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    return "Нужен Safari 16.4+ или Chrome с поддержкой Web Push";
  }
  if (Notification.permission === "granted") {
    if (isIosDevice()) {
      return "Включено. На iPhone добавьте сайт «На экран Домой» для фона";
    }
    return "Включено. Push приходят даже при свёрнутой вкладке";
  }
  if (Notification.permission === "denied") {
    return "Заблокировано в настройках браузера";
  }
  return "Нажмите переключатель или «Разрешить»";
}

function urlBase64ToUint8Array(base64: string): Uint8Array {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const normalized = (base64 + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(normalized);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out;
}

function keyBytesEqual(a: ArrayBuffer | null | undefined, b: Uint8Array): boolean {
  if (!a) return false;
  const left = new Uint8Array(a);
  if (left.length !== b.length) return false;
  for (let i = 0; i < left.length; i += 1) {
    if (left[i] !== b[i]) return false;
  }
  return true;
}

let swRegistration: ServiceWorkerRegistration | null = null;

async function ensureServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (!("serviceWorker" in navigator)) return null;
  if (swRegistration) return swRegistration;
  try {
    swRegistration = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
    await navigator.serviceWorker.ready;
    return swRegistration;
  } catch {
    return null;
  }
}

async function fetchVapidPublicKey(): Promise<string | null> {
  try {
    const res = await fetch("/api/push/vapid");
    if (!res.ok) return null;
    const data = await res.json() as { configured?: boolean; publicKey?: string };
    return data.configured && data.publicKey ? data.publicKey : null;
  } catch {
    return null;
  }
}

export async function initPushServiceWorker(): Promise<void> {
  await ensureServiceWorker();
}

export async function requestPushPermission(): Promise<boolean> {
  if (!canUsePush()) return false;
  if (Notification.permission === "granted") return true;
  if (Notification.permission === "denied") return false;

  try {
    const result = await Notification.requestPermission();
    if (result === "granted") return true;

    if ("permissions" in navigator) {
      try {
        const status = await navigator.permissions.query({ name: "notifications" as PermissionName });
        return status.state === "granted";
      } catch {
        /* empty */
      }
    }
    return false;
  } catch {
    return false;
  }
}

export async function subscribeWebPush(options?: { test?: boolean }): Promise<boolean> {
  if (!canUsePush() || Notification.permission !== "granted") return false;

  const publicKey = await fetchVapidPublicKey();
  if (!publicKey) return false;

  const registration = await ensureServiceWorker();
  if (!registration) return false;

  const applicationServerKey = urlBase64ToUint8Array(publicKey);
  let subscription = await registration.pushManager.getSubscription();
  if (subscription) {
    const boundKey = subscription.options?.applicationServerKey;
    if (!keyBytesEqual(boundKey, applicationServerKey)) {
      try {
        await subscription.unsubscribe();
      } catch {
        /* empty */
      }
      subscription = null;
    }
  }
  if (!subscription) {
    subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: applicationServerKey as BufferSource,
    });
  }

  const subscribeUrl = options?.test ? "/api/push/subscribe?test=1" : "/api/push/subscribe";
  const res = await fetch(subscribeUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(subscription.toJSON()),
  });
  if (res.ok) {
    try {
      localStorage.setItem(WEB_PUSH_KEY, "on");
    } catch {
      /* empty */
    }
  }
  return res.ok;
}

export async function unsubscribeWebPush(): Promise<void> {
  const registration = await ensureServiceWorker();
  const subscription = registration ? await registration.pushManager.getSubscription() : null;
  if (subscription) {
    try {
      await fetch("/api/push/unsubscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ endpoint: subscription.endpoint }),
      });
    } catch {
      /* empty */
    }
    try {
      await subscription.unsubscribe();
    } catch {
      /* empty */
    }
  }
  try {
    localStorage.removeItem(WEB_PUSH_KEY);
  } catch {
    /* empty */
  }
}

export async function enableWebPush(): Promise<{ ok: boolean; reason?: PushBlockReason }> {
  if (!canUsePush()) {
    return { ok: false, reason: pushBlockReason() || "unsupported" };
  }

  const publicKey = await fetchVapidPublicKey();
  if (!publicKey) {
    return { ok: false, reason: "server" };
  }

  const granted = await requestPushPermission();
  if (!granted) {
    return { ok: false, reason: pushBlockReason() || "denied" };
  }

  const subscribed = await subscribeWebPush({ test: true });
  return subscribed ? { ok: true } : { ok: false, reason: "default" };
}

export async function disableWebPush(): Promise<void> {
  await unsubscribeWebPush();
}

export function isWebPushSubscribed(): boolean {
  try {
    return localStorage.getItem(WEB_PUSH_KEY) === "on";
  } catch {
    return false;
  }
}

export function showTestNotification(): boolean {
  if (!canUsePush() || Notification.permission !== "granted") return false;
  try {
    const notification = new Notification("Сигнал", {
      body: "Уведомления включены",
      tag: "parser-push-test",
      silent: false,
    });
    notification.onclick = () => {
      window.focus();
      notification.close();
    };
    return true;
  } catch {
    return false;
  }
}

function adNotifyLine(ad: Ad): string {
  const title = ad.title?.trim() || "Новое объявление";
  const price = ad.price?.trim();
  if (price && price !== "—") return `${title} · ${price}`;
  return title;
}

/** Fallback, когда Web Push не подписан (вкладка открыта). */
export function notifyNewAds(ads: Ad[]): void {
  if (!canUsePush() || Notification.permission !== "granted" || !ads.length) return;
  if (isWebPushSubscribed()) return;

  const line = adNotifyLine(ads[0]);
  const body = ads.length === 1
    ? line
    : `${ads.length} новых объявлений · ${line}`;

  try {
    const notification = new Notification("Сигнал", {
      body,
      tag: "parser-new-ads",
      silent: false,
    });
    notification.onclick = () => {
      window.focus();
      notification.close();
    };
  } catch {
    /* empty */
  }
}

export function isStandalonePwa(): boolean {
  return window.matchMedia("(display-mode: standalone)").matches
    || (navigator as Navigator & { standalone?: boolean }).standalone === true;
}

export function pwaInstallHint(): string | null {
  if (!isIosDevice() || isStandalonePwa()) return null;
  return "На iPhone: Поделиться → «На экран Домой» — тогда push придут при закрытой вкладке";
}
