import type { Ad } from "./types";

export type PushBlockReason = "unsupported" | "insecure" | "denied" | "default";

function isMobileDevice(): boolean {
  return /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
}

function isYandexBrowser(): boolean {
  return /YaBrowser/i.test(navigator.userAgent);
}

export function canUsePush(): boolean {
  return typeof window !== "undefined"
    && window.isSecureContext
    && "Notification" in window;
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
  if (Notification.permission === "denied") return "denied";
  if (Notification.permission === "default") return "default";
  return null;
}

export function pushEnableHint(reason: PushBlockReason | null = pushBlockReason()): string {
  const mobile = isMobileDevice();
  const yandex = isYandexBrowser();

  switch (reason) {
    case "unsupported":
      return "Браузер не поддерживает уведомления";
    case "insecure":
      if (mobile && yandex) {
        return "На Android в Яндекс.Браузере нужен HTTPS. Откройте сайт как https://ваш-домен (не http://IP:8765) — иначе включить пуши нельзя.";
      }
      return "Уведомления работают только по HTTPS (или на localhost)";
    case "denied":
      if (mobile && yandex) {
        return "Яндекс.Браузер → ⋮ → Настройки → Настройки сайта → этот сайт → Уведомления → Разрешить. Затем обновите страницу.";
      }
      return "Уведомления заблокированы. В настройках сайта в браузере выберите «Разрешить» и обновите страницу";
    case "default":
      if (mobile) {
        return "Нажмите «Разрешить» ниже — должно появиться системное окно браузера";
      }
      return "Разрешите уведомления во всплывающем окне браузера";
    default:
      return "Не удалось включить уведомления";
  }
}

/** Короткий статус для блока в меню пользователя. */
export function pushStatusLine(): string {
  if (!hasNotificationApi()) return "Браузер не поддерживает уведомления";
  if (!window.isSecureContext) {
    const proto = location.protocol;
    return proto === "http:"
      ? `Сейчас ${location.host} по HTTP — нужен HTTPS`
      : "Нужно защищённое соединение (HTTPS)";
  }
  if (Notification.permission === "granted") {
    return isMobileDevice()
      ? "Включено. Уведомления приходят, пока вкладка открыта"
      : "Включено";
  }
  if (Notification.permission === "denied") {
    return "Заблокировано в настройках браузера";
  }
  return "Нажмите переключатель или «Разрешить»";
}

export async function requestPushPermission(): Promise<boolean> {
  if (!canUsePush()) return false;
  if (Notification.permission === "granted") return true;
  if (Notification.permission === "denied") return false;

  try {
    const result = await Notification.requestPermission();
    if (result === "granted") return true;

    // Яндекс/Chrome: иногда настройки сайта «разрешено», а API ещё default — уточняем через Permissions API.
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

export function notifyNewAds(ads: Ad[]): void {
  if (!canUsePush() || Notification.permission !== "granted" || !ads.length) return;

  const first = ads[0];
  const title = first.title?.trim() || "Новое объявление";
  const body = ads.length === 1
    ? title
    : `${ads.length} новых объявлений · ${title}`;

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
