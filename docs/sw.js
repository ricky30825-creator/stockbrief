const CACHE = "stockbrief-v11";
const SHELL = ["./", "index.html", "style.css", "app.js", "manifest.json"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// 데이터(JSON)와 페이지(index.html)는 네트워크 우선(실패 시 캐시), 나머지 셸은 캐시 우선.
// index.html이 캐시 우선이면 메타 태그 변경이 홈 화면 재설치에 반영되지 않는다.
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.mode === "navigate" || url.pathname.endsWith("/index.html")) {
    e.respondWith(
      fetch(e.request)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
          return res;
        })
        .catch(() => caches.match(e.request))
    );
    return;
  }
  if (url.pathname.includes("/data/")) {
    e.respondWith(
      fetch(e.request)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request.url.split("?")[0], copy));
          return res;
        })
        .catch(() => caches.match(e.request.url.split("?")[0]))
    );
  } else {
    e.respondWith(
      caches.match(e.request).then((hit) => hit || fetch(e.request))
    );
  }
});
