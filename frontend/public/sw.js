/*
 * Caches the app shell so opening from the Home Screen is instant.
 *
 * Deliberately narrow: the shell is cached, everything under /api is always
 * fetched from the network and never cached. A stale temperature reading served
 * from a cache would be exactly the kind of confident lie this app is built to
 * avoid.
 */

const CACHE = 'hydrosnooze-shell-v1'
const SHELL = ['/', '/index.html', '/manifest.webmanifest']

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)))
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))),
  )
  self.clients.claim()
})

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url)
  if (event.request.method !== 'GET') return
  if (url.origin !== self.location.origin) return
  if (url.pathname.startsWith('/api')) return

  // Network first, falling back to the cached shell, so a deployed update is
  // picked up on the next launch rather than being pinned forever.
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const copy = response.clone()
        void caches.open(CACHE).then((cache) => cache.put(event.request, copy))
        return response
      })
      .catch(() => caches.match(event.request).then((hit) => hit ?? caches.match('/index.html'))),
  )
})
