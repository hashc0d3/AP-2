import type { Ad } from "./types";

export type PushBlockReason = "unsupported" | "insecure" | "denied" | "default";

export function canUsePush(): boolean {
  return typeof window !== "undefined"
    && window.isSecureContext
    && "Notification" in window;
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
  switch (reason) {
    case "unsupported":
      return "Браузер не поддерживает уведомления";
    case "insecure":
      return "Уведомления работают только по HTTPS (или на localhost)";
    case "denied":
      return "Уведомления заблокированы для этого сайта. Откройте настройки сайта в браузере → Уведомления → Разрешить, затем обновите страницу";
    case "default":
      return "Разрешите уведомления во всплывающем окне браузера";
    default:
      return "Не удалось включить уведомления";
  }
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
