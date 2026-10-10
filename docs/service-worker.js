/* 오늘어디로 · 서비스 워커
   - 화면(index.html)과 일정 자료(data/*.json)는 "인터넷 먼저": 항상 최신을 보여주고, 안 되면 저장본
   - 아이콘·글꼴·지도 라이브러리는 "저장본 먼저": 두 번째부터 빨리 뜸
   - 행사 사진 등 바깥 이미지는 저장하지 않음(용량 절약) */
const VERSION = "v2-2026-10-10";
const SHELL = `shell-${VERSION}`, DATA = `data-${VERSION}`, LIB = `lib-${VERSION}`;
const SHELL_FILES = ["./", "./index.html", "./manifest.webmanifest", "./icon-192.png", "./icon-512.png", "./apple-touch-icon.png"];
const LIB_HOSTS = ["cdnjs.cloudflare.com", "cdn.jsdelivr.net", "fonts.googleapis.com", "fonts.gstatic.com"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => ![SHELL, DATA, LIB].includes(k)).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

async function networkFirst(req, cacheName, timeoutMs = 6000) {
  const cache = await caches.open(cacheName);
  try {
    const res = await Promise.race([fetch(req), new Promise((_, rej) => setTimeout(() => rej(new Error("timeout")), timeoutMs))]);
    if (res && res.ok) cache.put(req, res.clone());
    return res;
  } catch (err) {
    const hit = await cache.match(req, { ignoreSearch: true });
    if (hit) return hit;
    throw err;
  }
}
async function cacheFirst(req, cacheName) {
  const cache = await caches.open(cacheName);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res && (res.ok || res.type === "opaque")) cache.put(req, res.clone());
  return res;
}

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  const same = url.origin === self.location.origin;
  if (req.mode === "navigate") {                          // 화면 열기
    e.respondWith(networkFirst(req, SHELL).catch(() => caches.match("./index.html")));
    return;
  }
  if (same && /\/data\/[^/]+\.json$/.test(url.pathname)) { // 일정·장소 자료
    e.respondWith(networkFirst(req, DATA));
    return;
  }
  if (same && /\.png$|manifest\.webmanifest$/.test(url.pathname)) {
    e.respondWith(cacheFirst(req, SHELL));
    return;
  }
  if (LIB_HOSTS.includes(url.hostname)) {                 // 글꼴·지도 라이브러리
    e.respondWith(cacheFirst(req, LIB));
    return;
  }
  // 그 밖(행사 사진, 날씨, 지도 타일 등)은 브라우저 기본 동작
});
