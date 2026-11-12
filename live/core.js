// live/core.js — logica dell'app "Scambi live" (nessun accesso alla pagina: si prova anche con node).
// Una partita è: rosa a referto, per ogni set formazione di partenza e libero, e la lista degli eventi
// (scambi, cambi, timeout...). Tutto il resto (punteggio, battuta, chi è in campo, rotazione) si ricava
// rigiocando gli eventi: così "annulla" toglie l'ultimo evento e niente può andare fuori sincrono.
(function (root) {
  'use strict';

  // ---------- azioni (stesse chiavi di src/live_reference.py, salvo err_battuta) ----------
  // actor = squadra che fa l'azione; pt = l'azione dà il punto a chi la fa.
  // Murata (mur_*): chi la fa è chi attacca (perde il punto); si segna anche dal "Muro punto" dell'altra squadra.
  const ACTIONS = {
    att_pt_1: { pt: true, label: 'attacco punto al 1° attacco', cause: 'primo' },
    att_pt_s: { pt: true, label: 'attacco punto successivo', cause: 'succ' },
    err_att_1: { pt: false, label: 'errore al 1° attacco' },
    err_att_s: { pt: false, label: 'errore in attacco successivo' },
    mur_1: { pt: false, label: 'murata al 1° attacco' },
    mur_s: { pt: false, label: 'murata in attacco successivo' },
    ace: { pt: true, label: 'ace', cause: 'ace' },
    err_battuta: { pt: false, label: 'errore in battuta' },
    invasione: { pt: false, label: 'invasione' },
    altro: { pt: false, label: 'altro errore' },
  };
  const BUTTONS = {
    b_att_pt_1: { action: 'att_pt_1' }, b_att_pt_s: { action: 'att_pt_s' },
    b_muro_1: { action: 'mur_1', byOther: true }, b_muro_s: { action: 'mur_s', byOther: true },
    b_ace: { action: 'ace' },
    b_err_att_1: { action: 'err_att_1' }, b_err_att_s: { action: 'err_att_s' },
    b_mur_1: { action: 'mur_1' }, b_mur_s: { action: 'mur_s' },
    b_err_bat: { action: 'err_battuta' }, b_inv: { action: 'invasione' }, b_altro: { action: 'altro' },
  };
  const MAX_SUBS = 8, MAX_TO = 2;
  const other = t => (t === 'noi' ? 'loro' : 'noi');
  const nextZone = z => (z === 1 ? 6 : z - 1);           // la rotazione dopo un cambio palla (P1 -> P6 -> P5 ...)
  const rotate = six => six.slice(1).concat(six.slice(0, 1)); // chi era in 2 va in 1

  // ---------- rosa ----------
  const player = (m, n) => (m.roster || []).find(p => p.n === n) || { n, c: String(n), r: '?' };
  const nameOf = (m, n) => player(m, n).c;
  const roleOf = (m, n) => player(m, n).r;

  // ---------- rigioco di un set ----------
  // Ritorna lo stato del set e gli scambi arricchiti (chi batte, chi era in campo, rotazione...).
  // rot = zona del palleggiatore in campo; lr = zona della posizione del palleggiatore di partenza:
  // sono diverse durante il doppio cambio (il nuovo palleggiatore è nella posizione dell'opposto).
  function replay(m, n) {
    const set = m.sets[n - 1], evs = m.events.filter(e => e.set === n);
    const start = [1, 2, 3, 4, 5, 6].map(z => (set.lineup ? set.lineup[z] : null) || null);
    let six = start.slice(), server = set.firstServer, libero = set.libero || null;
    const setterSlot0 = start.findIndex(x => x && roleOf(m, x) === 'P');  // indice della posizione (0 = zona 1)
    let shift = 0;                                  // cambi palla vinti: quante volte si è ruotato
    const pts = { noi: 0, loro: 0 }, rallies = [], subs = [], timeouts = [], oppSubs = [], libs = [];
    let closedBy = null, p4Entries = 0;
    const zoneOfSlot = slot => (slot < 0 ? null : ((slot - shift) % 6 + 6) % 6 + 1);
    const setterZone = () => {
      const ps = six.map((x, i) => (x && roleOf(m, x) === 'P' ? i + 1 : null)).filter(Boolean);
      if (ps.length === 1) return ps[0];
      if (ps.length > 1) { const z = zoneOfSlot(setterSlot0); return ps.includes(z) ? z : ps[0]; }
      return null;
    };
    for (const e of evs) {
      if (e.t === 'r') {
        const rot = setterZone(), lr = zoneOfSlot(setterSlot0);
        const r = { set: n, idx: rallies.length, actor: e.actor, action: e.action, winner: e.winner, server, ora: e.ora || '',
          six: six.slice(), rot, lr, dc: !!(rot && lr && rot !== lr), libero,
          pre: { noi: pts.noi, loro: pts.loro } };
        pts[e.winner]++;
        r.noi = pts.noi; r.loro = pts.loro; r.fase = e.winner === server ? 'BP' : 'SO';
        rallies.push(r);
        if (e.winner === 'noi' && server === 'loro') {
          six = rotate(six); shift++;
          if (zoneOfSlot(setterSlot0) === 4) p4Entries++;
        }
        server = e.winner;
      } else if (e.t === 'sub') {
        const i = six.indexOf(e.out);
        if (i >= 0) six[i] = e.in;
        subs.push({ out: e.out, in: e.in, at: rallies.length, noi: pts.noi, loro: pts.loro });
      } else if (e.t === 'lib') { libero = e.n; libs.push({ n: e.n, at: rallies.length }); }
      else if (e.t === 'to') timeouts.push({ team: e.team, at: rallies.length, noi: pts.noi, loro: pts.loro });
      else if (e.t === 'osub') oppSubs.push({ at: rallies.length, noi: pts.noi, loro: pts.loro });
      else if (e.t === 'srv') server = other(server);
      else if (e.t === 'six') six = e.six.slice();
      else if (e.t === 'close') closedBy = e.team;
    }
    const target = n === 5 ? 15 : 25;
    const auto = Math.max(pts.noi, pts.loro) >= target && Math.abs(pts.noi - pts.loro) >= 2 ? (pts.noi > pts.loro ? 'noi' : 'loro') : null;
    const rot = setterZone(), lr = zoneOfSlot(setterSlot0);
    return { n, pts, server, six, rot, lr, dc: !!(rot && lr && rot !== lr), libero, start, setterSlot0, shift,
      winner: closedBy || auto, closedBy, rallies, subs, timeouts, oppSubs, libs, p4Entries, target };
  }
  const allRallies = m => m.sets.flatMap((_, i) => replay(m, i + 1).rallies);
  function setsWon(m) {
    const w = { noi: 0, loro: 0 };
    m.sets.forEach((_, i) => { const s = replay(m, i + 1); if (s.winner) w[s.winner]++; });
    return w;
  }
  const owner = r => r.actor;   // la voce dello scambio è di chi fa l'azione (la murata è di chi attacca)

  // ---------- regole dei cambi (classiche) ----------
  // titolare: mai uscito / uscito (rientra solo al posto di chi l'ha sostituito) / rientrato (non esce più);
  // riserva: mai entrata / entrata (esce solo per il titolare che ha sostituito) / uscita (non rientra).
  function subStatus(st, n) {
    const starter = st.start.includes(n);
    const outs = st.subs.filter(s => s.out === n), ins = st.subs.filter(s => s.in === n);
    if (starter) return !outs.length ? { k: 'free' } : !ins.length ? { k: 'out', pair: outs[0].in } : { k: 'back' };
    return !ins.length ? { k: 'fresh' } : !outs.length ? { k: 'sub', pair: ins[0].out } : { k: 'done' };
  }
  function subLegal(m, st, inN, outN) {
    if (st.subs.length >= MAX_SUBS) return `Cambi finiti (${MAX_SUBS})`;
    if (roleOf(m, inN) === 'L') return 'Il libero entra solo al posto del libero';
    const si = subStatus(st, inN), so = subStatus(st, outN);
    if (si.k === 'done') return `${nameOf(m, inN)} è già uscito: non rientra`;
    if (so.k === 'back') return `${nameOf(m, outN)} è già rientrato: non esce più`;
    if (so.k === 'sub' && so.pair !== inN) return `${nameOf(m, outN)} esce solo per ${nameOf(m, so.pair)}`;
    if (si.k === 'out' && si.pair !== outN) return `${nameOf(m, inN)} rientra solo per ${nameOf(m, si.pair)}`;
    return null;
  }

  // ---------- prima linea ----------
  // scritta come si attacca: posto 4 (martello), centro, posto 2 (opposto o palleggiatore)
  function frontLine(m, six) {
    const f = [six[3], six[2], six[1]].filter(Boolean).map(n => player(m, n));
    if (f.length < 3) return '';
    const pick = (rs, i) => f.find(p => rs.includes(p.r)) || f[i];
    const p4 = pick(['M'], 0), ce = f.find(p => p !== p4 && p.r === 'C') || f.find(p => p !== p4 && p.r !== 'O' && p.r !== 'P') || f[1];
    const p2 = f.find(p => p !== p4 && p !== ce) || f[2];
    return `${p4.c} – ${ce.c} – ${p2.c}`;
  }

  // ---------- conteggi ----------
  // i cinque saldi degli obiettivi per una squadra, sugli scambi dati
  function goalValues(rs, t) {
    const o = other(t), c = f => rs.filter(f).length, A = (r, ...k) => k.includes(r.action);
    return {
      bat: c(r => r.actor === t && A(r, 'ace')) - c(r => r.actor === t && A(r, 'err_battuta')),
      a1: c(r => r.actor === t && A(r, 'att_pt_1')) - c(r => r.actor === t && A(r, 'err_att_1', 'mur_1')),
      ac: c(r => r.actor === t && A(r, 'att_pt_s')) - c(r => r.actor === t && A(r, 'err_att_s', 'mur_s')),
      muri: c(r => r.actor === o && A(r, 'mur_1', 'mur_s')),
      grat: c(r => r.actor === t && A(r, 'invasione', 'altro')),
    };
  }
  // i tre momenti dello scambio per la squadra t, in ricezione e in battuta (stesse chiavi di riferimenti.json)
  function stageCounts(rs, t) {
    const o = other(t), c = f => rs.filter(f).length, A = (r, ...k) => k.includes(r.action);
    const recv = r => r.server === o, serv = r => r.server === t, won = r => r.winner === t;
    const srvEnd = r => A(r, 'ace', 'err_battuta'), first = r => A(r, 'att_pt_1', 'err_att_1', 'mur_1');
    return {
      ric: c(recv), so: c(r => recv(r) && won(r)), bat: c(serv), bp: c(r => serv(r) && won(r)),
      so_ace_sub: c(r => recv(r) && r.actor === o && A(r, 'ace')),
      ric_gioco: c(r => recv(r) && !srvEnd(r)),
      pt1: c(r => recv(r) && r.actor === t && A(r, 'att_pt_1')),
      err1: c(r => recv(r) && r.actor === t && A(r, 'err_att_1')), mur1: c(r => recv(r) && r.actor === t && A(r, 'mur_1')),
      so3_n: c(r => recv(r) && !srvEnd(r) && !first(r)), so3_w: c(r => recv(r) && !srvEnd(r) && !first(r) && won(r)),
      ace: c(r => serv(r) && r.actor === t && A(r, 'ace')), err_bat: c(r => serv(r) && r.actor === t && A(r, 'err_battuta')),
      bat_gioco: c(r => serv(r) && !srvEnd(r)),
      o_pt1: c(r => serv(r) && r.actor === o && A(r, 'att_pt_1')), o_err1: c(r => serv(r) && r.actor === o && A(r, 'err_att_1')),
      o_mur1: c(r => serv(r) && r.actor === o && A(r, 'mur_1')),
      bp3_n: c(r => serv(r) && !srvEnd(r) && !first(r)), bp3_w: c(r => serv(r) && !srvEnd(r) && !first(r) && won(r)),
    };
  }
  // i sei momenti come (positivi, negativi, totale): valore = (pos - neg) / n, più alto è meglio
  const STAGES = [
    { k: 'ric', fase: 'SO', label: 'Ricezione', f: c => ({ pos: 0, neg: c.so_ace_sub, n: c.ric }),
      txt: c => `${c.so_ace_sub} ace subiti su ${c.ric}` },
    { k: 'att1', fase: 'SO', label: '1° attacco', f: c => ({ pos: c.pt1, neg: c.err1 + c.mur1, n: c.ric_gioco }),
      txt: c => `${c.pt1} punti, ${c.err1 + c.mur1} err/murate su ${c.ric_gioco}` },
    { k: 'so3', fase: 'SO', label: 'Scambio lungo', f: c => ({ pos: c.so3_w, neg: c.so3_n - c.so3_w, n: c.so3_n }),
      txt: c => `vinti ${c.so3_w} su ${c.so3_n}` },
    { k: 'bat', fase: 'BP', label: 'Battuta', f: c => ({ pos: c.ace, neg: c.err_bat, n: c.bat }),
      txt: c => `${c.ace} ace, ${c.err_bat} errori su ${c.bat}` },
    { k: 'mdif', fase: 'BP', label: 'Muro e difesa sul loro 1° attacco', f: c => ({ pos: c.o_err1 + c.o_mur1, neg: c.o_pt1, n: c.bat_gioco }),
      txt: c => `${c.o_pt1} punti subiti, ${c.o_err1 + c.o_mur1} loro err/murate su ${c.bat_gioco}` },
    { k: 'bp3', fase: 'BP', label: 'Scambio lungo', f: c => ({ pos: c.bp3_w, neg: c.bp3_n - c.bp3_w, n: c.bp3_n }),
      txt: c => `vinti ${c.bp3_w} su ${c.bp3_n}` },
  ];

  // ---------- statistica ----------
  const Phi = z => 0.5 * (1 + erf(z / Math.SQRT2));
  function erf(x) {   // Abramowitz-Stegun 7.1.26
    const s = Math.sign(x); x = Math.abs(x);
    const t = 1 / (1 + 0.3275911 * x);
    const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496735) * t + 0.254829592) * t * Math.exp(-x * x);
    return s * y;
  }
  // media e varianza di (pos - neg)/n avvicinata al riferimento con k osservazioni "finte"
  function shrunk(x, ref, k = 10) {
    const pp = ref && ref.n ? ref.pos / ref.n : 0, pn = ref && ref.n ? ref.neg / ref.n : 0;
    const N = x.n + k, P = (x.pos + k * pp) / N, Q = (x.neg + k * pn) / N, mean = P - Q;
    return { mean, var: Math.max(1e-6, (P + Q - mean * mean) / N), n: x.n };
  }
  const pGreater = (a, b) => Phi((a.mean - b.mean) / Math.sqrt(a.var + b.var));   // P(a > b)
  const pAbove = (a, v) => Phi((a.mean - v) / Math.sqrt(a.var));
  // affidabilità: "affidabile al NN%" dal 65% in su, altrimenti nessuna differenza
  const relText = p => (p >= 0.65 ? `affidabile al ${Math.round(100 * p)}%` : 'nessuna differenza');
  const relClass = p => (p >= 0.9 ? 'high' : p >= 0.75 ? 'mid' : 'low');

  // confronto di due quote x1/n1 e x2/n2 (scambi vinti): P(seconda > prima), avvicinate a p0
  function compareRates(x1, n1, x2, n2, p0, k = 10) {
    const a = shrunk({ pos: x1, neg: 0, n: n1 }, { pos: p0, neg: 0, n: 1 }, k);
    const b = shrunk({ pos: x2, neg: 0, n: n2 }, { pos: p0, neg: 0, n: 1 }, k);
    return pGreater(b, a);
  }

  // doppia indicazione di un momento: rispetto al nostro solito e rispetto a loro (oggi)
  const SYM = { up: '▲', eq: '●', dn: '▼' };
  function verdictOf(vsUsual, vsThem) {
    if (vsUsual === 'dn') return vsThem === 'up' ? ['amb', 'Sottotono, ma basta'] : ['bad', 'Sottotono'];
    if (vsUsual === 'eq') return vsThem === 'up' ? ['good', 'Ci fa vincere'] : vsThem === 'dn' ? ['amb', 'Ok, ma non basta'] : ['neu', 'Alla pari'];
    return vsThem === 'up' ? ['good', 'Ci fa vincere'] : vsThem === 'dn' ? ['amb', 'Meglio, ma non basta'] : ['good', 'Sopra il solito'];
  }
  const totToStage = (st, tot) => (tot ? st.f(Object.assign({ so_ace_sub: 0, so3_n: 0, so3_w: 0, bat_gioco: 0, o_pt1: 0, o_err1: 0, o_mur1: 0, bp3_n: 0, bp3_w: 0 }, tot)) : null);
  function stageReport(rs, refNoi, refLoro) {
    const cn = stageCounts(rs, 'noi'), cl = stageCounts(rs, 'loro');
    return STAGES.map(st => {
      const xn = st.f(cn), xl = st.f(cl), un = totToStage(st, refNoi), ul = totToStage(st, refLoro) || un;
      const sn = shrunk(xn, un), sl = shrunk(xl, ul);
      const usualV = un && un.n ? (un.pos - un.neg) / un.n : null;
      const pU = usualV === null ? 0.5 : pAbove(sn, usualV), pT = pGreater(sn, sl);
      const vsUsual = xn.n < 4 ? 'eq' : pU >= 0.8 ? 'up' : pU <= 0.2 ? 'dn' : 'eq';
      const vsThem = xn.n < 4 || xl.n < 4 ? 'eq' : pT >= 0.8 ? 'up' : pT <= 0.2 ? 'dn' : 'eq';
      const [cls, label] = verdictOf(vsUsual, vsThem);
      return { st, cn, cl, xn, xl, usualV, pU, pT, vsUsual, vsThem, cls, label,
        today: xn.n ? (xn.pos - xn.neg) / xn.n : null, them: xl.n ? (xl.pos - xl.neg) / xl.n : null };
    });
  }

  // ---------- obiettivi del set ----------
  const GOALS = [
    // chiave, nome, spiegazione, verso (+1 = almeno, -1 = al massimo)
    { k: 'bat', name: 'Battuta', how: 'ace − errori', dir: 1 },
    { k: 'a1', name: '1° attacco', how: 'punti − errori/murate', dir: 1 },
    { k: 'ac', name: 'Contrattacco', how: 'punti − errori/murate', dir: 1 },
    { k: 'muri', name: 'Muri punto', how: '', dir: 1 },
    { k: 'grat', name: 'Errori gratuiti', how: 'invasioni, alzate, doppie, posizione', dir: -1 },
  ];
  const reached = (g, v, t) => (g.dir > 0 ? v >= t : v <= t);
  // probabilità di raggiungere l'obiettivo a fine set (approssimazione normale sul ritmo per scambio)
  function pReach(g, v, t, remaining, ritmo) {
    if (remaining <= 0 || !ritmo) return reached(g, v, t) ? 1 : 0;
    const mu = v + ritmo.m * remaining, sd = Math.sqrt(Math.max(0.05, ritmo.v * remaining));
    return g.dir > 0 ? 1 - Phi((t - 0.5 - mu) / sd) : Phi((t + 0.5 - mu) / sd);
  }
  // stato degli obiettivi del set n: valori, tacca "dove dovremmo essere", probabilità di vincere, su cosa concentrarsi
  function goalState(m, REF, n) {
    const st = replay(m, n), model = REF && REF.modello;
    if (!model) return null;
    const rs = st.rallies, expected = n === 5 ? model.scambi_set * 15 / 25 : model.scambi_set;
    const done = !!st.winner, progress = done ? 1 : Math.min(1, rs.length / expected), remaining = done ? 0 : Math.max(0, expected - rs.length);
    const val = { noi: goalValues(rs, 'noi'), loro: goalValues(rs, 'loro') };
    const items = [];
    GOALS.forEach(g => ['noi', 'loro'].forEach(t => {
      const thr = model.soglie[g.k], v = val[t][g.k];
      const p = pReach(g, v, thr, remaining, t === 'noi' ? model.ritmo_noi[g.k] : model.ritmo_tutti[g.k]);
      items.push({ g, t, v, thr, p, key: (t === 'loro' ? 'o_' : '') + g.k, pace: g.dir > 0 ? thr * progress : null });
    }));
    // probabilità di vincere il set con gli obiettivi pesati per la probabilità di raggiungerli
    const coef = model.coef, z0 = items.reduce((z, it) => z + coef[it.key] * it.p, coef.b0);
    const sig = z => 1 / (1 + Math.exp(-z));
    items.forEach(it => {
      const zr = z0 - coef[it.key] * it.p;
      it.swing = Math.abs(sig(zr + coef[it.key]) - sig(zr));   // quanto pesa raggiungerlo o no, adesso
      it.vale = Math.abs(model.vale[it.key]);
      it.focus = it.swing * 4 * it.p * (1 - it.p);            // conta di più se è ancora in bilico
    });
    const focus = done ? null : items.filter(it => it.p > 0.02 && it.p < 0.98).sort((a, b) => b.focus - a.focus)[0] || null;
    return { st, items, progress, remaining, focus, pWin: sig(z0), done };
  }
  // colore dal nostro punto di vista: per i saldi contro la tacca del momento (a fine set contro l'obiettivo),
  // per gli errori gratuiti contro il massimo; per loro al contrario
  function goalClass(it, done) {
    const ref = it.g.dir > 0 && !done ? Math.round(it.pace) : it.thr;
    const at = it.v === ref, good = it.g.dir > 0 ? it.v > ref : it.v < ref;
    if (it.t === 'noi') return at ? 'amb' : good ? 'ok' : 'bad';
    return at ? 'amb' : good ? 'bad' : 'ok';
  }

  // ---------- quando entreremo in P4: simulazione del resto del set ----------
  function whenP4(st, pSO, pBP, N = 6000) {
    const target = st.target, entries = [[], [], []];
    for (let it = 0; it < N; it++) {
      let a = st.pts.noi, b = st.pts.loro, server = st.server, lr = st.lr, k = 0;
      while (!((a >= target || b >= target) && Math.abs(a - b) >= 2) && k < 3 && a + b < 90) {
        const win = Math.random() < (server === 'noi' ? pBP : pSO);
        if (win) { a++; if (server === 'loro') { lr = nextZone(lr); if (lr === 4) entries[k++].push([a, b]); } server = 'noi'; }
        else { b++; server = 'loro'; }
      }
    }
    const q = (v, p) => { const x = v.slice().sort((m, n) => m - n); return x[Math.min(x.length - 1, Math.floor(p * x.length))]; };
    return entries.map(e => !e.length ? { reach: 0 } : {
      reach: e.length / N, med: [q(e.map(x => x[0]), 0.5), q(e.map(x => x[1]), 0.5)],
      lo: [q(e.map(x => x[0]), 0.1), q(e.map(x => x[1]), 0.1)], hi: [q(e.map(x => x[0]), 0.9), q(e.map(x => x[1]), 0.9)],
      fin: e.filter(x => Math.max(x[0], x[1]) >= (target === 15 ? 12 : 20)).length / N,
    });
  }
  // cambio palla e break-point di oggi avvicinati al solito (20 scambi "finti")
  function todayRates(m, REF) {
    const rs = allRallies(m), c = stageCounts(rs, 'noi'), u = REF && REF.decimo ? REF.decimo.totali : null;
    const pSO0 = u ? u.so / u.ric : 0.58, pBP0 = u ? u.bp / u.bat : 0.38, K = 20;
    return { pSO: (c.so + K * pSO0) / (c.ric + K), pBP: (c.bp + K * pBP0) / (c.bat + K) };
  }

  // ---------- battitori e suggerimenti di cambio ----------
  function serverStats(m) {   // oggi, per numero di maglia
    const out = {};
    allRallies(m).filter(r => r.server === 'noi' && r.six[0]).forEach(r => {
      const b = out[r.six[0]] = out[r.six[0]] || { battute: 0, ace: 0, errori: 0 };
      b.battute++;
      if (r.actor === 'noi' && r.action === 'ace') b.ace++;
      if (r.actor === 'noi' && r.action === 'err_battuta') b.errori++;
    });
    return out;
  }
  const usualServe = (REF, name) => (REF && REF.decimo && REF.decimo.battitori ? REF.decimo.battitori[name] : null);
  // chi può entrare al posto di n (regole dei cambi, giocatori a referto) per battere sicuro o mettere pressione
  function serveSuggestions(m, REF, st, n) {
    const onCourt = new Set(st.six.concat([st.libero]));
    const cands = (m.roster || []).filter(p => !onCourt.has(p.n) && p.r !== 'L' && !subLegal(m, st, p.n, n))
      .map(p => ({ p, u: usualServe(REF, p.c) })).filter(x => x.u && x.u.battute >= 10);
    if (!cands.length) return {};
    const safe = cands.slice().sort((a, b) => a.u.errori / a.u.battute - b.u.errori / b.u.battute)[0];
    const press = cands.slice().sort((a, b) => b.u.ace / b.u.battute - a.u.ace / a.u.battute)[0];
    return { safe: safe.p, press: press.p };
  }

  // ---------- chiamate ----------
  // Ognuna ha una chiave: nasce quando la condizione diventa vera e non si ripete per la stessa chiave.
  function rotUsual(REF, z) { const r = REF && REF.decimo && REF.decimo.totali.rot ? REF.decimo.totali.rot[z] : null; return r; }
  function lineSplit(rs) {   // scambi vinti col palleggiatore davanti (P4-P3-P2) e dietro (P1-P6-P5), senza doppio cambio
    const f = rs.filter(r => r.rot && !r.dc && [2, 3, 4].includes(r.rot)), b = rs.filter(r => r.rot && !r.dc && [1, 5, 6].includes(r.rot));
    const w = x => x.filter(r => r.winner === 'noi').length;
    return { fw: w(f), fn: f.length, bw: w(b), bn: b.length };
  }
  function usualLine(REF) {
    const rot = REF && REF.decimo ? REF.decimo.totali.rot : null;
    if (!rot) return null;
    const s = zs => zs.reduce((a, z) => { const r = rot[z] || {}; a.w += (r.so || 0) + (r.bp || 0); a.n += (r.ric || 0) + (r.bat || 0); return a; }, { w: 0, n: 0 });
    const f = s(['2', '3', '4']), b = s(['1', '5', '6']);
    return { front: f.n ? f.w / f.n : null, back: b.n ? b.w / b.n : null };
  }
  const pc = x => Math.round(100 * x) + '%';
  const xy = (w, n) => `${w} su ${n} · ${n ? pc(w / n) : '–'}`;

  function computeCalls(m, REF) {
    const out = [], n = m.sets.length, st = replay(m, n), rs = st.rallies, all = allRallies(m);
    if (!rs.length) return out;
    const last = rs[rs.length - 1], score = `${last.noi}-${last.loro}`;
    const opp = m.opponent;

    // break avversario da 3 (si aggiorna 0-4, 0-5...) e rimonta: rischio
    let k = 0; while (k < rs.length && rs[rs.length - 1 - k].winner === last.winner) k++;
    if (last.winner === 'loro' && k >= 3) {
      const from = rs[rs.length - 1 - k] ? rs[rs.length - 1 - k] : { noi: 0, loro: 0 };
      const causes = rs.slice(rs.length - k).map(r => ACTIONS[r.action].label);
      out.push({ key: `run-${n}-${rs.length - k}`, upd: true, cls: 'bad', k: `BREAK DI ${k}`,
        t: `0-${k} da ${from.noi}-${from.loro}${st.rot ? `, siamo in P${st.rot}` : ''}: alza il rischio`,
        d: [...new Set(causes)].join(', ') });
    }
    let minD = 0, lowAt = -1;
    rs.forEach((r, i) => { if (r.noi - r.loro < minD) { minD = r.noi - r.loro; lowAt = i; } });
    if (minD <= -3 && (last.noi - last.loro) - minD >= 3 && last.winner === 'noi') {
      const low = rs[lowAt];
      out.push({ key: `rec-${n}-${lowAt}`, cls: 'good', k: 'STIAMO RECUPERANDO', t: `Da ${low.noi}-${low.loro} a ${score}: abbassa il rischio`,
        d: 'Battuta in campo, palla in gioco: non farli rifiatare.' });
    }

    // i momenti dello scambio sulla partita: quando la doppia indicazione cambia
    const refNoi = REF && REF.decimo ? REF.decimo.totali : null, refLoro = REF && m.refKey && REF.squadre[m.refKey] ? REF.squadre[m.refKey].totali : null;
    stageReport(all, refNoi, refLoro).forEach(x => {
      if (x.cls === 'neu' || x.xn.n < 8) return;
      const lines = [['Noi oggi', x.st.txt(x.cn)]];
      if (x.usualV !== null && refNoi) lines.push(['Di solito', usualTxt(x, refNoi)]);
      lines.push(['Loro oggi', x.st.txt(x.cl)]);
      const p = x.vsUsual !== 'eq' ? (x.vsUsual === 'up' ? x.pU : 1 - x.pU) : (x.vsThem === 'up' ? x.pT : 1 - x.pT);
      out.push({ key: `stage-${x.st.k}-${x.cls}-${x.label}`, cls: x.cls, k: x.label.toUpperCase(),
        t: `${x.st.fase === 'SO' ? 'In ricezione' : 'In battuta'} · ${x.st.label}`, cmp: lines, rel: p });
    });

    // in arrivo: al prossimo cambio palla (se riceviamo) chi batte e quale prima linea riceverà
    if (st.server === 'loro' && !st.winner && st.lr) {
      const nextSix = rotate(st.six), nextRot = st.rot ? nextZone(st.rot) : null, srv = nextSix[0];
      const today = serverStats(m)[srv], u = usualServe(REF, nameOf(m, srv));
      if (today && today.errori >= 2 && u && u.battute >= 10) {
        const p = compareRates(u.errori, u.battute, today.errori, today.battute, u.errori / u.battute, 20);
        if (p >= 0.8) {
          const sg = serveSuggestions(m, REF, st, srv);
          out.push({ key: `srv-${n}-${srv}`, cls: 'info', k: `IN ARRIVO${nextRot ? ' · P' + nextRot : ''}`,
            t: `Batte ${nameOf(m, srv)}: ${today.errori} errori su ${today.battute}`,
            cmp: [['Di solito', `${u.errori} su ${u.battute} · ${pc(u.errori / u.battute)}`]], rel: p, sug: sg });
        }
      }
      if (nextRot) {
        const rr = all.filter(r => r.rot === nextRot && r.server === 'loro'), w = rr.filter(r => r.winner === 'noi').length, ru = rotUsual(REF, nextRot);
        if (rr.length >= 3 && ru && ru.ric) {
          const p = compareRates(w, rr.length, ru.so, ru.ric, ru.so / ru.ric, 10);
          if (p >= 0.8) out.push({ key: `rot-${n}-${nextRot}`, cls: 'info', k: `IN ARRIVO · P${nextRot}`,
            t: `Riceviamo con ${frontLine(m, nextSix)}: oggi ${w} su ${rr.length}`, cmp: [['Di solito in P' + nextRot, pc(ru.so / ru.ric)]], rel: p });
        }
      }
      // 2° giro di P4 in arrivo: oggi col palleggiatore davanti contro dietro
      if (!st.dc && nextZone(st.lr) === 4 && st.p4Entries === 1) {
        const s = lineSplit(all), ul = usualLine(REF);
        if (s.fn >= 4 && s.bn >= 4) {
          const p0 = (s.fw + s.bw) / (s.fn + s.bn), p = compareRates(s.fw, s.fn, s.bw, s.bn, p0);
          const diff = p >= 0.65 ? 'meno' : p <= 0.35 ? 'di più' : 'come';
          out.push({ key: `p4-${n}`, cls: 'info', k: '2° GIRO DI P4 IN ARRIVO',
            t: `Oggi col palleggiatore davanti vinciamo ${diff === 'come' ? 'come dietro' : diff}`,
            cmp: [['Davanti', xy(s.fw, s.fn)], ['Dietro', xy(s.bw, s.bn)]].concat(ul && ul.front ? [['Di solito', `${pc(ul.front)} e ${pc(ul.back)}`]] : []),
            rel: diff === 'di più' ? 1 - p : p });
        }
      }
      // chiusura del doppio cambio in arrivo: le rotazioni col doppio cambio contro le stesse del 1° giro senza
      if (st.dc && st.lr === 2) {
        const withDc = rs.filter(r => r.dc), without = rs.filter(r => !r.dc && [2, 3, 4].includes(r.lr));
        const w1 = withDc.filter(r => r.winner === 'noi').length, w0 = without.filter(r => r.winner === 'noi').length;
        if (withDc.length >= 3 && without.length >= 3) {
          const p = compareRates(w0, without.length, w1, withDc.length, (w0 + w1) / (withDc.length + without.length));
          out.push({ key: `dcend-${n}-${st.subs.length}`, cls: 'info', k: 'CHIUSURA DOPPIO CAMBIO IN ARRIVO',
            t: `Col doppio cambio abbiamo vinto ${p >= 0.65 ? 'di più' : p <= 0.35 ? 'di meno' : 'come senza'}`,
            cmp: [['P4-P3-P2 col doppio cambio', xy(w1, withDc.length)], ['P4-P3-P2 senza', xy(w0, without.length)]], rel: p <= 0.35 ? 1 - p : p });
        }
      }
    }
    // doppio cambio appena chiuso: le due coppie palleggiatore-opposto col palleggiatore dietro
    if (rs.length >= 2 && rs[rs.length - 2].dc && !last.dc) {
      const slots = r => { const z = r.rot; return z ? [r.six[z - 1], r.six[(z + 2) % 6]] : []; };   // palleggiatore e chi è di fronte a lui
      const dcR = all.filter(r => r.dc), normR = all.filter(r => !r.dc && [1, 5, 6].includes(r.rot));
      const pairB = slots(rs[rs.length - 2]).map(x => nameOf(m, x)), pairA = slots(last).map(x => nameOf(m, x));
      const wB = dcR.filter(r => r.winner === 'noi').length, wA = normR.filter(r => r.winner === 'noi').length;
      if (dcR.length >= 3 && normR.length >= 3) {
        const p = compareRates(wA, normR.length, wB, dcR.length, (wA + wB) / (dcR.length + normR.length));
        const t = p >= 0.65 ? `Con ${pairB.join(' e ')} meglio, col palleggiatore dietro` : p <= 0.35 ? `Con ${pairA.join(' e ')} meglio, col palleggiatore dietro` : `Col palleggiatore dietro le due coppie vanno uguali`;
        out.push({ key: `pairs-${n}-${st.subs.length}`, cls: 'info', k: 'DOPPIO CAMBIO', t,
          cmp: [[pairB.join(' – '), xy(wB, dcR.length)], [pairA.join(' – '), xy(wA, normR.length)]], rel: p <= 0.35 ? 1 - p : p });
      }
    }
    if (st.subs.length >= MAX_SUBS - 2 && st.subs.length < MAX_SUBS) out.push({ key: `subs-${n}-${st.subs.length}`, cls: 'info', k: 'CAMBI', t: `Ne restano ${MAX_SUBS - st.subs.length}` });
    return out;
  }
  function usualTxt(x, u) {
    const v = x.st.f(Object.assign({ so_ace_sub: 0, so3_n: 0, so3_w: 0, bat_gioco: 0, o_pt1: 0, o_err1: 0, o_mur1: 0, bp3_n: 0, bp3_w: 0 }, u));
    if (!v.n) return '–';
    if (x.st.k === 'ric') return `${(100 * v.neg / v.n).toFixed(0)}% di ace subiti`;
    if (x.st.k === 'so3' || x.st.k === 'bp3') return `vinti il ${(100 * v.pos / v.n).toFixed(0)}%`;
    return `saldo ${(100 * (v.pos - v.neg) / v.n).toFixed(0)}% (${(100 * v.pos / v.n).toFixed(0)}% punti, ${(100 * v.neg / v.n).toFixed(0)}% errori)`;
  }
  // aggiorna le chiamate della partita dopo uno scambio: nuove in coda, quelle "upd" si aggiornano e si riaprono
  function updateCalls(m, REF) {
    m.calls = m.calls || [];
    const n = m.sets.length, st = replay(m, n), at = st.rallies.length;
    computeCalls(m, REF).forEach(c => {
      const old = m.calls.find(x => x.key === c.key);
      if (old) { if (c.upd) Object.assign(old, c, { open: true, at, set: n }); return; }
      // lo stesso momento dello scambio non prima di 8 scambi dall'ultima chiamata; la nuova chiude la vecchia
      if (c.key.startsWith('stage-')) {
        const same = m.calls.filter(x => x.key.startsWith('stage-' + c.key.split('-')[1] + '-'));
        if (same.some(x => x.set === n && at - x.at < 10)) return;
        same.forEach(x => { x.open = false; });
      }
      m.calls.push(Object.assign({ id: Date.now() + Math.random(), open: true, at, set: n, score: `${st.pts.noi}-${st.pts.loro}` }, c));
    });
  }

  // chiamate di una partita già giocata (importata dallo scout): si rigioca scambio per scambio
  function rebuildCalls(m, REF) {
    const copy = Object.assign({}, m, { events: [], calls: [], sets: [] });
    m.events.forEach(e => {
      copy.sets = m.sets.slice(0, e.set);
      copy.events.push(e);
      if (e.t === 'r') updateCalls(copy, REF);
    });
    m.calls = copy.calls.map(c => Object.assign(c, { open: false }));
    m.callsBuilt = true;
  }

  // ---------- da dove arrivano i punti ----------
  const CAUSES = [['primo', '1° attacco'], ['succ', 'Attacco successivo'], ['muro', 'Muro'], ['ace', 'Ace'], ['err', 'Errori loro']];
  function pointCauses(rs, t) {
    const c = { primo: 0, succ: 0, muro: 0, ace: 0, err: 0 };
    rs.filter(r => r.winner === t).forEach(r => {
      if (r.actor === t) c[ACTIONS[r.action].cause] = (c[ACTIONS[r.action].cause] || 0) + 1;
      else if (r.action === 'mur_1' || r.action === 'mur_s') c.muro++;
      else c.err++;
    });
    return c;
  }

  // ---------- partita nuova / set nuovo ----------
  function nextSetStart(m) {   // formazione del set dopo: quella di partenza del set appena finito
    const prev = m.sets[m.sets.length - 1];
    return { firstServer: 'noi', lineup: Object.assign({}, prev.lineup), libero: prev.libero };
  }

  const Core = { ACTIONS, BUTTONS, MAX_SUBS, MAX_TO, other, nextZone, rotate, player, nameOf, roleOf, replay, allRallies, setsWon,
    owner, subStatus, subLegal, frontLine, goalValues, stageCounts, STAGES, stageReport, SYM, verdictOf, GOALS, goalState,
    goalClass, reached, whenP4, todayRates, serverStats, usualServe, serveSuggestions, computeCalls, updateCalls, rebuildCalls, relText,
    relClass, compareRates, lineSplit, usualLine, pointCauses, CAUSES, nextSetStart, usualTxt, pc, xy };
  if (typeof module !== 'undefined' && module.exports) module.exports = Core; else root.Core = Core;
})(this);
