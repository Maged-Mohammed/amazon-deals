// Service worker: الصفحة نفسها من الكاش (سريعة وبتفتح أوفلاين)، والداتا من النت الأول
const CACHE = "loata-v3";
const SHELL = ["./", "style.css", "app.js", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png"];

self.addEventListener("install", (e) => {
  // واحدة واحدة ومن غير ما نفشل كله: addAll بيقع كله لو ملف واحد فيه مشكلة ("Entry already exists")
  e.waitUntil(caches.open(CACHE)
    .then((c) => Promise.all(SHELL.map((u) => c.add(u).catch(() => {}))))
    .then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return; // صور أمازون من غير كاش
  if (url.pathname.endsWith(".json")) {
    // اللقطات: من النت، ولو مفيش نت آخر نسخة متخزنة
    e.respondWith(fetch(e.request).then((res) => {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(e.request, copy));
      return res;
    }).catch(() => caches.match(e.request)));
    return;
  }
  // الصفحة: من الكاش وبنحدّثها في الخلفية
  e.respondWith(caches.match(e.request).then((hit) => {
    const net = fetch(e.request).then((res) => {
      if (res.ok) {
        const copy = res.clone(); // لازم ننسخ قبل ما الصفحة تقرا الـ body
        caches.open(CACHE).then((c) => c.put(e.request, copy));
      }
      return res;
    }).catch(() => hit);
    return hit || net;
  }));
});
