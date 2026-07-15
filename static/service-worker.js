const CACHE_NAME = 'cards-public-v1';
const PAGE_CACHE_NAME = 'cards-public-pages-v1';
const MAX_PUBLIC_PAGES = 40;
const CORE_ASSETS = [
  '/static/offline.html',
  '/static/app.css',
  '/static/app.js',
  '/static/vendor/bootstrap/bootstrap.min.css',
  '/static/vendor/bootstrap/bootstrap.bundle.min.js'
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(CORE_ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(
    keys.filter((key) => key.startsWith('cards-public-') && ![CACHE_NAME, PAGE_CACHE_NAME].includes(key)).map((key) => caches.delete(key))
  )));
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET' || request.mode !== 'navigate') { return; }
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) { return; }
  event.respondWith(fetch(request).then((response) => {
    if (response.ok && response.headers.get('X-Cards-Public') === '1') {
      const copy = response.clone();
      caches.open(PAGE_CACHE_NAME).then(async (cache) => {
        await cache.put(request, copy);
        const keys = await cache.keys();
        await Promise.all(keys.slice(0, Math.max(0, keys.length - MAX_PUBLIC_PAGES)).map((key) => cache.delete(key)));
      });
    }
    return response;
  }).catch(async () => {
    const cached = await caches.match(request);
    return cached || caches.match('/static/offline.html');
  }));
});
