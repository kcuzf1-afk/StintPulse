// Same-origin app shell only. API, video, tokens and telemetry are never cached.
const CACHE = 'stintpulse-shell-v1'
self.addEventListener('install', (e) => {
  e.waitUntil(
    (async () => {
      const c = await caches.open(CACHE)
      await c.addAll(['/', '/icon.svg', '/manifest.webmanifest'])
      const r = await c.match('/')
      const html = await r.text()
      const assets = [...html.matchAll(/(?:src|href)="(\/assets\/[^\"]+)"/g)].map((m) => m[1])
      await c.addAll(assets)
    })(),
  )
  self.skipWaiting()
})
self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))),
  )
  self.clients.claim()
})
self.addEventListener('fetch', (e) => {
  const u = new URL(e.request.url)
  if (
    u.origin !== self.location.origin ||
    e.request.method !== 'GET' ||
    u.pathname.startsWith('/api') ||
    u.pathname === '/ws'
  )
    return
  if (
    u.pathname.startsWith('/assets/') ||
    ['/', '/icon.svg', '/manifest.webmanifest'].includes(u.pathname)
  ) {
    e.respondWith(
      fetch(e.request)
        .then((r) => {
          if (r.ok) {
            const copy = r.clone()
            caches.open(CACHE).then((c) => c.put(e.request, copy))
          }
          return r
        })
        .catch(() => caches.match(e.request)),
    )
  }
})
