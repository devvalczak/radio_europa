self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

// Buttons under a notification ("Zrobione", "Drzemka 10 min"). Android and desktop show them, iOS (PWA) mostly does not;
// without buttons a tap simply opens the page. The server only sends routes under /api/m/ (module HTTP routes).
const MAX_ACTIONS = (typeof Notification !== "undefined" && Notification.maxActions) || 2;
const okAction = (a) => a && typeof a.id === "string" && typeof a.title === "string" && typeof a.url === "string" && a.url.startsWith("/api/m/");

self.addEventListener("push", (event) => {
  let data = { title: "Jarvis", body: "", url: "/", actions: [] };
  try {
    data = { ...data, ...event.data.json() };
  } catch {
    /* keep defaults */
  }
  const actions = (Array.isArray(data.actions) ? data.actions : []).filter(okAction).slice(0, MAX_ACTIONS);
  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: "/icon.svg",
      actions: actions.map((a) => ({ action: a.id, title: a.title })),
      data: { url: data.url, actions },
    }),
  );
});

const openUrl = (url) =>
  self.clients.matchAll({ type: "window" }).then((list) => {
    for (const c of list) {
      if ("focus" in c) {
        c.navigate(url);
        return c.focus();
      }
    }
    return self.clients.openWindow(url);
  });

// A button tap runs the action in the background: same-origin request with the login cookie and the X-Jarvis header.
// The routes are idempotent, so a repeated tap is harmless. When it fails (logged out, offline) open the page instead.
const runAction = async (action, fallbackUrl) => {
  try {
    const res = await fetch(action.url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-Jarvis": "1", "Content-Type": "application/json" },
      body: JSON.stringify(action.body || {}),
    });
    if (!res.ok) throw new Error(String(res.status));
    const clients = await self.clients.matchAll({ type: "window" });
    clients.forEach((c) => c.postMessage({ type: "jarvis:changed" }));
  } catch {
    await openUrl(fallbackUrl);
  }
};

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const url = data.url || "/";
  const action = event.action && (data.actions || []).find((a) => a.id === event.action && okAction(a));
  event.waitUntil(action ? runAction(action, url) : openUrl(url));
});
