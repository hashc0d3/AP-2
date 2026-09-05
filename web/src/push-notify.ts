import type { Ad } from "./types";

export function canUsePush(): boolean {
  return "Notification" in window;
}

export async function requestPushPermission(): Promise<boolean> {
  if (!canUsePush()) return false;
  if (Notification.permission === "granted") return true;
  if (Notification.permission === "denied") return false;
  const result = await Notification.requestPermission();
  return result === "granted";
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
