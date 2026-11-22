// Tiene in cache l'app dello scout per farla partire anche senza internet (il video da file è sull'iPad;
// YouTube invece vuole la rete). VERSION la riscrive il workflow .github/workflows/scout-pages.yml dal contenuto dei file,
// così l'iPad scarica la nuova versione; qui resta un valore di comodo per la prova in locale.
// referti.json (partite del girone, scritto da `python -m src.referti`) si prende dalla rete quando c'è;
// senza rete si usa l'ultima copia salvata. Lo scout in corso non sta qui ma in IndexedDB.
const VERSION = 'scout-v1';
const FILES = ['./', 'index.html', 'manifest.json', 'icon-180.png', 'icon-512.png', 'referti.json'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(VERSION)
    // cache:'reload': mai la copia della cache HTTP del browser (GitHub Pages la tiene 10 minuti), altrimenti dopo
    // aggiornamenti ravvicinati la "nuova" versione può contenere la pagina vecchia
    .then(c => Promise.all(FILES.map(f => fetch(new Request(f, {cache:'reload'}))
      .then(r => r.ok ? c.put(f, r) : null).catch(() => {}))))      // referti.json può mancare
    .then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;      // YouTube e simili: rete
  if (/\.(mp4|mov|m4v|webm)$/i.test(url.pathname) || e.request.headers.has('range')) return;   // video: mai in cache
  if (url.pathname.endsWith('referti.json')) {
    e.respondWith(fetch(e.request, {cache:'no-store'}).then(res => {
      if (!res.ok) return caches.match('referti.json').then(hit => hit || res);
      const copy = res.clone();
      caches.open(VERSION).then(c => c.put('referti.json', copy));
      return res;
    }).catch(() => caches.match('referti.json')));
    return;
  }
  // la pagina dell'app: dalla rete quando c'è (sempre l'ultima versione), dalla cache senza rete o se la rete tarda
  // più di 4 secondi; il resto (icone, manifest) dalla cache
  if (e.request.mode === 'navigate' || /\/(index\.html)?$/.test(url.pathname)) {
    const net = fetch(e.request, {cache:'no-cache'}).then(res => {
      if (res.ok) { const copy = res.clone(); caches.open(VERSION).then(c => c.put('index.html', copy)); }
      return res;
    });
    const cached = () => caches.match('index.html').then(hit => hit || caches.match('./'));
    e.respondWith(Promise.race([net, new Promise(r => setTimeout(r, 4000))])
      .then(res => (res && res.ok) ? res : cached().then(hit => hit || res || net))
      .catch(() => cached()));
    return;
  }
  e.respondWith(caches.match(e.request, { ignoreSearch: true }).then(hit => hit || fetch(e.request)));
});
