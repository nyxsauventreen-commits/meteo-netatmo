/* Météo : permet l'installation comme application, et affiche les notifications du site.
   Toujours la version du serveur quand internet marche ; sans internet, la dernière copie de la page. */
const CACHE = 'meteo-perso-1';
const SEEN = 'meteo-notifications'; // le numéro du dernier message affiché sur cet appareil
const FIXES = ['/style.css', '/theme.js', '/manifest.json', '/icone-192.png'];
self.addEventListener('install', e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(FIXES)).catch(() => {})); self.skipWaiting(); });
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(k => Promise.all(k.filter(n => n !== CACHE && n !== SEEN).map(n => caches.delete(n)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.origin !== location.origin || u.pathname.startsWith('/api/') || u.pathname === '/entrer') return;
  e.respondWith(fetch(e.request).then(r => {
    if (r.ok && !r.redirected) { const copie = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copie)); }
    return r;
  }).catch(() => caches.match(e.request, { ignoreSearch: true }).then(r => r || Response.error())));
});

/* Notifications : le serveur envoie un signal vide, la page demande ensuite le texte au site. */
const lastSeen = () => caches.open(SEEN).then(c => c.match('/vu')).then(r => (r ? r.json() : 0)).catch(() => 0);
const remember = id => caches.open(SEEN).then(c => c.put('/vu', new Response(JSON.stringify(id)))).catch(() => {});
self.addEventListener('push', e => {
  e.waitUntil(Promise.all([
    fetch('/api/push/pending', { credentials: 'include', cache: 'no-store' })
      .then(r => (r.ok ? r.json() : { messages: [] })).catch(() => ({ messages: [] })),
    lastSeen(),
  ]).then(([{ messages }, seen]) => {
    const fresh = messages.filter(m => m.id > seen);
    // Le navigateur exige une notification à chaque signal : à défaut, la plus récente revient sans bruit.
    const shown = fresh.length ? fresh : messages.slice(0, 1);
    const list = shown.length ? shown : [{ id: 0, title: 'Météo', body: 'Nouvelle notification : ouvrez le site.' }];
    return Promise.all(list.map(m => self.registration.showNotification(m.title, {
      body: m.body, tag: `meteo-${m.id}`, icon: '/icone-192.png', badge: '/icone-192.png',
    }))).then(() => remember(Math.max(seen, ...messages.map(m => m.id))));
  }));
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true })
    .then(list => (list.length ? list[0].focus() : self.clients.openWindow('/'))));
});
