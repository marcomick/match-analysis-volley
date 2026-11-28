// Tiene in cache l'app per farla funzionare senza internet.
// Cambiare VERSION a ogni modifica dei file, così l'iPad scarica la nuova versione.
// riferimenti.json (valori "di solito") e partite.json (partite già giocate, dai .dvw), scritti da
// src/live_reference.py, si prendono dalla rete quando c'è, così basta ricaricarli su Netlify;
// senza rete si usa l'ultima copia salvata.
const VERSION = 'scambi-live-v29';
const FILES = ['./', 'index.html', 'core.js', 'manifest.json', 'icon-180.png', 'icon-512.png', 'riferimenti.json', 'partite.json'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const data = ['riferimenti.json', 'partite.json'].find(f => e.request.url.includes(f));
  if (data) {
    // si salva solo una risposta buona: una pagina di errore (es. password non valida) non sostituisce la copia salvata
    e.respondWith(fetch(e.request).then(res => {
      if (!res.ok) return caches.match(data).then(hit => hit || res);
      const copy = res.clone();
      caches.open(VERSION).then(c => c.put(data, copy));
      return res;
    }).catch(() => caches.match(data)));
    return;
  }
  e.respondWith(caches.match(e.request, { ignoreSearch: true }).then(hit => hit || fetch(e.request)));
});
