// Protegge l'app "Scambi live" con utente e password (finestra del browser).
// Utente e password stanno nelle variabili d'ambiente del sito su Netlify: SITE_USER e SITE_PASSWORD.
// Chi li conosce può solo usare l'app; pubblicarla o cambiarla resta possibile solo dall'account Netlify.
const enc = new TextEncoder();
const digest = async s => new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(s)));

// confronto a tempo costante (sulle impronte, così le lunghezze sono sempre uguali)
async function same(a, b) {
  const [x, y] = await Promise.all([digest(a), digest(b)]);
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= x[i] ^ y[i];
  return diff === 0;
}

export default async (request, context) => {
  const user = Netlify.env.get("SITE_USER");
  const pass = Netlify.env.get("SITE_PASSWORD");
  // senza variabili non si entra (altrimenti varrebbe "undefined:undefined")
  if (!user || !pass) return new Response("Accesso non configurato", { status: 503 });

  const header = request.headers.get("authorization") || "";
  if (header.startsWith("Basic ")) {
    let given = "";
    try {   // decodifica in UTF-8: la password può avere lettere accentate
      given = new TextDecoder().decode(Uint8Array.from(atob(header.slice(6)), c => c.charCodeAt(0)));
    } catch (_) { /* intestazione malformata: si chiede di nuovo */ }
    if (await same(given, `${user}:${pass}`)) return context.next();
  }
  return new Response("Accesso riservato", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="Scambi live", charset="UTF-8"', "Cache-Control": "no-store" },
  });
};

// icone e manifest restano liberi: Safari li scarica senza password quando si aggiunge l'app alla schermata Home
// (altrimenti mette un'icona generica); contengono solo il logo e il nome dell'app
export const config = { path: "/*", excludedPath: ["/icon-180.png", "/icon-512.png", "/manifest.json"] };
