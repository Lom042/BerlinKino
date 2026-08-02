const CACHE_NAME = "kino-berlin-v3";
// Nothing is precached at install time anymore — everything gets cached
// opportunistically as it's actually fetched (see networkFirst below).
// This avoids ever locking in a stale copy of index.html from install time.

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Network-first, cache as a pure offline fallback — for EVERYTHING that
// can change (the page itself, the data feeds). This is the important
// part: it means anyone with a connection always gets the current
// version, no manual refresh ever required. Cache only kicks in if the
// network request genuinely fails (offline), so people can still open
// the app with no signal and see the last version they loaded.
function networkFirst(request) {
  return fetch(request)
    .then((res) => {
      const clone = res.clone();
      caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
      return res;
    })
    .catch(() => caches.match(request));
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  const isAppShell =
    event.request.mode === "navigate" ||
    url.pathname.endsWith("/index.html") ||
    url.pathname.endsWith("/");
  const isDataFeed = /\/data\/(index|\d{4}-\d{2}-\d{2})\.json$/.test(url.pathname);

  if (isAppShell || isDataFeed) {
    event.respondWith(networkFirst(event.request));
    return;
  }

  // Icons/manifest: these essentially never change, cache-first is fine
  // and saves a request.
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
