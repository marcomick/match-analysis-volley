// Tiene in cache l'app per farla funzionare senza internet.
// Cambiare VERSION a ogni modifica dei file, così l'iPad scarica la nuova versione.
// riferimenti.json (valori "di solito", src/live_reference.py) si prende dalla rete quando
// c'è, così basta ricaricarlo su Netlify; senza rete si usa l'ultima copia salvata.
const VERSION = 'scambi-live-v6';
const FILES = ['./', 'index.html', 'manifest.json', 'icon-180.png', 'icon-512.png', 'riferimenti.json'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  if (e.request.url.includes('riferimenti.json')) {
    e.respondWith(fetch(e.request).then(res => {
      const copy = res.clone();
      caches.open(VERSION).then(c => c.put('riferimenti.json', copy));
      return res;
    }).catch(() => caches.match('riferimenti.json')));
    return;
  }
  e.respondWith(caches.match(e.request, { ignoreSearch: true }).then(hit => hit || fetch(e.request)));
});
