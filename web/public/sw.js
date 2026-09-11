/* Service Worker — фоновые push-уведомления */

self.addEventListener("push", (event) => {
  let payload = { title: "Сигнал", body: "Новое объявление", url: "/" };
  if (event.data) {
    try {
      payload = { ...payload, ...event.data.json() };
    } catch {
      /* empty */
    }
  }

  event.waitUntil(
    self.registration.showNotification(payload.title || "Сигнал", {
      body: payload.body || "Новое объявление",
      tag: "parser-new-ads",
      renotify: true,
      icon: "/icons/icon-192.png",
      badge: "/icons/icon-192.png",
      data: { url: payload.url || "/" },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = event.notification.data?.url || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ("focus" in client) {
          if (client.url.includes(self.location.origin)) {
            client.postMessage({ type: "feed-pull" });
            return client.focus();
          }
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(target);
      }
      return undefined;
    }),
  );
});

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});
