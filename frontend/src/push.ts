import { api } from "@jarvis/sdk";

const b64ToBytes = (b64: string) => {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
};

const bytesToB64 = (buf: ArrayBuffer | null) =>
  buf ? btoa(String.fromCharCode(...new Uint8Array(buf))) : "";

export const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window;

export const registerSW = () => (pushSupported() ? navigator.serviceWorker.register("/sw.js") : undefined);

export async function enablePush(): Promise<void> {
  if (!pushSupported()) throw new Error("Ta przeglądarka nie obsługuje push.");
  if ((await Notification.requestPermission()) !== "granted") throw new Error("Brak zgody na powiadomienia.");
  const { key } = await api<{ key: string }>("/push/key");
  if (!key) throw new Error("Serwer nie ma skonfigurowanych kluczy VAPID.");
  const reg = await navigator.serviceWorker.ready;
  const sub =
    (await reg.pushManager.getSubscription()) ??
    (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(key) }));
  await api("/push/subscribe", {
    method: "POST",
    body: {
      endpoint: sub.endpoint,
      p256dh: bytesToB64(sub.getKey("p256dh")),
      auth: bytesToB64(sub.getKey("auth")),
    },
  });
}
