// Minimal service worker for the EO Analyst mobile companion — no Workbox,
// no offline app data. Caches only the app shell (the HTML entry point +
// manifest + favicon) so a flaky connection or a quick re-open still loads
// the shell; everything else (hashed JS/CSS bundles, /api/*, /ws/*) is
// always fetched from the network untouched — this is a live-data triage
// tool, stale cached data would be actively misleading.

const CACHE_NAME = "eoa-shell-v1";
const SHELL_URLS = ["/", "/manifest.webmanifest", "/favicon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(SHELL_URLS))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  // Never intercept API calls or websocket upgrades — always live.
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/ws")) return;

  // Only the app shell itself is cache-worthy; let Vite's hashed build
  // assets (/assets/*) always come from the network (or the browser's own
  // HTTP cache, which already handles immutable hashed filenames well).
  if (!SHELL_URLS.includes(url.pathname)) return;

  event.respondWith(
    fetch(request)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
        return res;
      })
      .catch(() => caches.match(request)),
  );
});
