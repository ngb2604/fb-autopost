/* Service worker: cache "vỏ" ứng dụng để mở nhanh trên iPhone.
 * Dữ liệu (ảnh, DB) luôn lấy từ mạng — không cache để tránh dữ liệu cũ.
 */

const CACHE = "autopost-shell-v6";
const SHELL = [
  "./",
  "./index.html",
  "./style.css",
  "./app.js",
  "./config.js",
  "./manifest.json?v=2",
  "./icons/icon-180.png?v=2",
  "./icons/icon-192.png?v=2",
  "./icons/icon-512.png?v=2",
  "./icons/icon-maskable-512.png?v=2",
  "./icons/favicon.ico?v=2",
  "./icons/favicon-32.png?v=2",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  // Không cache API Supabase hay CDN
  if (url.origin !== self.location.origin) return;
  if (url.pathname.includes("/storage/v1/") || url.pathname.includes("/rest/v1/")) return;

  event.respondWith(
    fetch(req)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((cache) => cache.put(req, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(req).then((hit) => hit || caches.match("./index.html")))
  );
});
