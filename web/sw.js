/* Météo : permet l'installation comme application.
   Toujours la version du serveur quand internet marche ; sans internet, la dernière copie de la page. */
const CACHE = 'meteo-perso-1';
const FIXES = ['/style.css', '/theme.js', '/manifest.json', '/icone-192.png'];
self.addEventListener('install', e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(FIXES)).catch(() => {})); self.skipWaiting(); });
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(k => Promise.all(k.filter(n => n !== CACHE).map(n => caches.delete(n)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.origin !== location.origin || u.pathname.startsWith('/api/') || u.pathname === '/entrer') return;
  e.respondWith(fetch(e.request).then(r => {
    if (r.ok && !r.redirected) { const copie = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copie)); }
    return r;
  }).catch(() => caches.match(e.request, { ignoreSearch: true }).then(r => r || Response.error())));
});
