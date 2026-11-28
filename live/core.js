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
  // chi attacca in prima linea: la prima linea senza il palleggiatore (posto 4, centro, posto 2)
  function attackLine(m, six) {
    const f = frontLine(m, six);
    return f ? f.split(' – ').filter(c => (((m.roster || []).find(p => p.c === c)) || {}).r !== 'P').join(' – ') : '';
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

  // ---------- frasi brevi: cosa ci sposta rispetto al solito e rispetto a loro ----------
  // Ogni momento ha le sue componenti (conteggi su un totale). Si cerca quella che si discosta di più
  // dal riferimento, in numero di palloni, e la si scrive con una frase corta e il numero.
  // dir: +1 = più è meglio per noi. noi/solito/loro: frasi quando noi ne abbiamo di più / di meno.
  const COMP = {
    ric: [{ n: c => c.ric, x: c => c.so_ace_sub, u: u => [u.so_ace_sub, u.ric], dir: -1, more: 'Subiamo più ace', less: 'Subiamo pochi ace', unit: 'ace subiti', tm: 'Subiamo più ace di loro', tl: 'Subiamo meno ace di loro' }],
    att1: [
      { n: c => c.ric_gioco, x: c => c.pt1, u: u => [u.pt1, u.ric_gioco], dir: 1, more: 'Finalizziamo tanto', less: 'Finalizziamo poco', unit: 'punti', tm: 'Finalizziamo più di loro', tl: 'Finalizziamo meno di loro' },
      { n: c => c.ric_gioco, x: c => c.err1, u: u => [u.err1, u.ric_gioco], dir: -1, more: 'Sbagliamo di più', less: 'Sbagliamo poco', unit: 'errori', tm: 'Sbagliamo più di loro', tl: 'Sbagliamo meno di loro' },
      { n: c => c.ric_gioco, x: c => c.mur1, u: u => [u.mur1, u.ric_gioco], dir: -1, more: 'Ci murano di più', less: 'Ci murano poco', unit: 'murate', tm: 'Ci murano più di quanto murano loro', tl: 'Ci murano meno di quanto murano loro' }],
    so3: [{ n: c => c.so3_n, x: c => c.so3_w, u: u => [u.so3_w, u.so3_n], dir: 1, more: 'Lo scambio lungo lo vinciamo', less: 'Lo scambio lungo lo perdiamo', unit: 'vinti', tm: 'Nello scambio lungo vinciamo più di loro', tl: 'Nello scambio lungo vinciamo meno di loro' }],
    bat: [
      { n: c => c.bat, x: c => c.ace, u: u => [u.ace, u.bat], dir: 1, more: 'Facciamo più ace', less: 'Facciamo pochi ace', unit: 'ace', tm: 'Più ace di loro', tl: 'Meno ace di loro' },
      { n: c => c.bat, x: c => c.err_bat, u: u => [u.err_bat, u.bat], dir: -1, more: 'Sbagliamo più battute', less: 'Sbagliamo poche battute', unit: 'errori', tm: 'Più errori in battuta di loro', tl: 'Meno errori in battuta di loro' }],
    mdif: [
      { n: c => c.bat_gioco, x: c => c.o_pt1, u: u => [u.o_pt1, u.bat_gioco], dir: -1, more: 'Il loro 1° attacco ci fa più punti', less: 'Il loro 1° attacco ci fa pochi punti', unit: 'punti subiti', tm: 'Sul 1° attacco loro segnano più di noi', tl: 'Sul 1° attacco loro segnano meno di noi', swap: true },
      { n: c => c.bat_gioco, x: c => c.o_mur1, u: u => [u.o_mur1, u.bat_gioco], dir: 1, more: 'Muriamo di più', less: 'Muriamo poco', unit: 'muri', tm: 'Sul loro 1° attacco muriamo più di loro', tl: 'Sul loro 1° attacco muriamo meno di loro' },
      { n: c => c.bat_gioco, x: c => c.o_err1, u: u => [u.o_err1, u.bat_gioco], dir: 1, more: 'Li facciamo sbagliare di più', less: 'Li facciamo sbagliare poco', unit: 'loro errori', tm: 'Sul 1° attacco loro sbagliano più di noi', tl: 'Sul 1° attacco loro sbagliano meno di noi', swap: true }],
    bp3: [{ n: c => c.bp3_n, x: c => c.bp3_w, u: u => [u.bp3_w, u.bp3_n], dir: 1, more: 'Lo scambio lungo lo vinciamo', less: 'Lo scambio lungo lo perdiamo', unit: 'vinti', tm: 'Nello scambio lungo vinciamo più di loro', tl: 'Nello scambio lungo vinciamo meno di loro' }],
  };
  const fmtN = x => (x >= 10 || Math.abs(x - Math.round(x)) < 0.05 ? String(Math.round(x)) : x.toFixed(1).replace('.', ','));
  // la componente più lontana dal riferimento (attesi = quota del riferimento sui nostri palloni)
  function biggest(k, c, refRate) {
    let best = null;
    COMP[k].forEach(cp => {
      const n = cp.n(c), x = cp.x(c), r = refRate(cp);
      if (!n || r === null) return;
      const exp = r * n, d = x - exp, z = Math.abs(d) / Math.sqrt(Math.max(0.5, exp * (1 - Math.min(r, 0.99))));
      if (Math.abs(d) >= 1.5 && z >= 1 && (!best || z > best.z)) best = { cp, n, x, exp, d, z };
    });
    return best;
  }
  function stagePhrases(k, cn, cl, refNoi) {
    const vsU = refNoi ? biggest(k, cn, cp => { const [a, b] = cp.u(refNoi); return b ? (a || 0) / b : null; }) : null;
    const vsT = biggest(k, cn, cp => { const nn = cp.n(cl); return nn >= 4 ? cp.x(cl) / nn : null; });
    const u = vsU ? `${vsU.d > 0 ? vsU.cp.more : vsU.cp.less}: ${vsU.x} ${vsU.cp.unit} (di solito ${fmtN(vsU.exp)})` : 'Come al solito';
    const t = vsT ? `${vsT.d > 0 ? vsT.cp.tm : vsT.cp.tl}: ${vsT.x} su ${vsT.n} (${vsT.cp.swap ? 'noi' : 'loro'} ${vsT.cp.x(cl)} su ${vsT.cp.n(cl)})` : 'Come loro';
    return { u, t, good: { u: vsU ? Math.sign(vsU.d) * vsU.cp.dir : 0, t: vsT ? Math.sign(vsT.d) * vsT.cp.dir : 0 } };
  }

  // dettaglio per le barre degli obiettivi (al tocco)
  function goalDetails(rs, t) {
    const o = other(t), c = f => rs.filter(f).length, A = (r, ...k) => k.includes(r.action), me = r => r.actor === t;
    const pl = (x, a, b) => `${x} ${x === 1 ? a : b}`;
    return {
      bat: `${pl(c(r => me(r) && A(r, 'ace')), 'ace', 'ace')}, ${pl(c(r => me(r) && A(r, 'err_battuta')), 'errore', 'errori')}`,
      a1: `${pl(c(r => me(r) && A(r, 'att_pt_1')), 'punto', 'punti')}, ${pl(c(r => me(r) && A(r, 'err_att_1')), 'errore', 'errori')}, ${pl(c(r => me(r) && A(r, 'mur_1')), 'murata', 'murate')}`,
      ac: `${pl(c(r => me(r) && A(r, 'att_pt_s')), 'punto', 'punti')}, ${pl(c(r => me(r) && A(r, 'err_att_s')), 'errore', 'errori')}, ${pl(c(r => me(r) && A(r, 'mur_s')), 'murata', 'murate')}`,
      muri: `${pl(c(r => r.actor === o && A(r, 'mur_1')), 'sul 1° attacco', 'sul 1° attacco')}, ${c(r => r.actor === o && A(r, 'mur_s'))} sugli altri`,
      grat: `${pl(c(r => me(r) && A(r, 'invasione')), 'invasione', 'invasioni')}, ${pl(c(r => me(r) && A(r, 'altro')), 'altro errore', 'altri errori')}`,
    };
  }

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
    return vsThem === 'up' ? ['good', 'Ci fa vincere'] : vsThem === 'dn' ? ['amb', 'Meglio, ma non basta'] : ['good', 'Meglio del solito'];
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

  // ---------- segnali negli ultimi scambi ----------
  // Per ogni fondamentale: gli scambi in cui poteva succedere (opp), quelli in cui è successo (ev) e la quota
  // abituale del Decimo (rate, dai riferimenti). Si guarda solo il set in corso e solo quando l'ultimo scambio
  // è proprio uno di quei casi: si cerca il tratto finale (dall'1° evento del tratto all'ultimo scambio, al più
  // SIG_WIN occasioni) più lontano dal solito; con almeno 3 casi e affidabilità >= SIG_REL è un segnale.
  const SIG_WIN = 15, SIG_REL = 0.9;
  // aumentare quando cambiano le regole delle chiamate: le partite importate le ricalcolano
  const CALLS_VERSION = 4;
  const A_ = (r, ...k) => k.includes(r.action);
  const SIGNALS = [
    { k: 'mur', title: 'CI MURANO', unit: ['murata', 'murate'], opp: r => true, ev: r => r.actor === 'noi' && A_(r, 'mur_1', 'mur_s'),
      rate: u => (u.mur1 + u.murs) / u.scambi, per: 'scambi' },
    { k: 'erratt', title: 'SBAGLIAMO IN ATTACCO', unit: ['errore in attacco', 'errori in attacco'], opp: r => true, ev: r => r.actor === 'noi' && A_(r, 'err_att_1', 'err_att_s'),
      rate: u => (u.err1 + u.errs) / u.scambi, per: 'scambi' },
    { k: 'acesub', title: 'SUBIAMO ACE', unit: ['ace subito', 'ace subiti'], opp: r => r.server === 'loro', ev: r => r.actor === 'loro' && A_(r, 'ace'),
      rate: u => u.so_ace_sub / u.ric, per: 'ricezioni' },
    { k: 'errbat', title: 'SBAGLIAMO IN BATTUTA', unit: ['errore in battuta', 'errori in battuta'], opp: r => r.server === 'noi', ev: r => r.actor === 'noi' && A_(r, 'err_battuta'),
      rate: u => u.err_bat / u.bat, per: 'battute' },
    { k: 'opt1', title: 'IL LORO 1° ATTACCO PASSA', unit: ['punto sul loro 1° attacco', 'punti sul loro 1° attacco'], opp: r => r.server === 'noi', ev: r => r.actor === 'loro' && A_(r, 'att_pt_1'),
      rate: u => u.o_pt1 / u.bat, per: 'battute' },
    { k: 'lungo', title: 'PERDIAMO LO SCAMBIO LUNGO', unit: ['scambio lungo perso', 'scambi lunghi persi'],
      opp: r => !A_(r, 'ace', 'err_battuta', 'att_pt_1', 'err_att_1', 'mur_1'), ev: r => r.winner === 'loro',
      rate: u => (u.so3_n - u.so3_w + u.bp3_n - u.bp3_w) / (u.so3_n + u.bp3_n), per: 'scambi lunghi' },
    { k: 'grat', title: 'ERRORI GRATUITI', unit: ['errore gratuito', 'errori gratuiti'], opp: r => true, ev: r => r.actor === 'noi' && A_(r, 'invasione', 'altro'),
      rate: u => u.gratuiti / u.scambi, per: 'scambi' },
  ];
  // P(X >= x) per X binomiale(n, p)
  function binomTail(x, n, p) {
    let term = Math.pow(1 - p, n), cdf = 0;
    for (let i = 0; i < x; i++) { cdf += term; term *= (n - i) / (i + 1) * p / (1 - p); }
    return Math.max(0, 1 - cdf);
  }
  // perché si perdono gli scambi del tratto (per capire cosa c'è dietro, anche in break-point)
  function lossBreakdown(rr) {
    const L = { err_battuta: ['errore in battuta', 'errori in battuta'], att_pt_1: ['punto sul loro 1° attacco', 'punti sul loro 1° attacco'],
      ace: ['ace subito', 'ace subiti'], err_att_1: ['errore al 1° attacco', 'errori al 1° attacco'], mur_1: ['murata al 1° attacco', 'murate al 1° attacco'],
      att_pt_s: ['punto sul loro contrattacco', 'punti sul loro contrattacco'], err_att_s: ['errore in contrattacco', 'errori in contrattacco'],
      mur_s: ['murata in contrattacco', 'murate in contrattacco'], invasione: ['invasione', 'invasioni'], altro: ['altro errore', 'altri errori'] };
    const c = {};
    rr.filter(r => r.winner === 'loro').forEach(r => { c[r.action] = (c[r.action] || 0) + 1; });
    return Object.entries(c).sort((a, b) => b[1] - a[1]).map(([a, x]) => `${x} ${(L[a] || ['altro', 'altro'])[x === 1 ? 0 : 1]}`).join(', ');
  }
  function signals(m, REF, rs) {
    const u = REF && REF.decimo ? REF.decimo.totali : null, last = rs[rs.length - 1], out = [];
    if (!u || !last) return out;
    SIGNALS.forEach(sg => {
      if (!sg.opp(last) || !sg.ev(last)) return;
      const p0 = sg.rate(u);
      if (!(p0 > 0)) return;
      const ops = rs.filter(sg.opp).slice(-SIG_WIN);
      let best = null;
      ops.forEach((r, j) => {   // il tratto parte da un evento e arriva all'ultimo scambio
        if (!sg.ev(r)) return;
        const tr = ops.slice(j), x = tr.filter(sg.ev).length, n = tr.length;
        if (x < 3) return;
        const pv = binomTail(x, n, p0);
        if (!best || pv < best.pv) best = { pv, x, n, tr };
      });
      if (!best) return;
      const nStarts = ops.filter(sg.ev).length;   // tratti provati: l'affidabilità ne tiene conto
      const rel = 1 - Math.min(1, best.pv * nStarts);
      if (rel < SIG_REL) return;
      const evs = best.tr.filter(sg.ev);
      const first = rs[best.tr[0].idx - 1] || { noi: 0, loro: 0 };
      const rots = [...new Set(evs.map(r => r.rot).filter(Boolean))].map(z => 'P' + z);
      const all = rs.slice(best.tr[0].idx);
      const det = [`Da ${first.noi}-${first.loro} a ${last.noi}-${last.loro}`, rots.length ? `in ${rots.join(', ')}` : ''];
      if (sg.k === 'errbat') det.push('battono ' + [...new Set(evs.map(r => nameOf(m, r.six[0])))].join(', '));
      if (sg.k === 'mur' || sg.k === 'erratt') {
        const a = evs.filter(r => A_(r, 'mur_1', 'err_att_1')).length, b = evs.length - a;
        det.push(!b ? 'tutte al 1° attacco' : !a ? 'tutte in contrattacco' : `${a} al 1° attacco, ${b} in contrattacco`);
      }
      const lost = all.filter(r => r.winner === 'loro').length;
      out.push({ key: `sig-${sg.k}-${last.set}-${best.tr[0].idx}`, sig: sg.k, upd: true, cls: 'bad', k: sg.title,
        t: sg.k === 'lungo' ? `Persi ${best.x} degli ultimi ${best.n} scambi lunghi` : `${best.x} ${sg.unit[1]} ${['battute', 'ricezioni'].includes(sg.per) ? 'nelle ultime' : 'negli ultimi'} ${best.n} ${sg.per}`,
        cmp: [['Di solito', `${fmtN(p0 * best.n)} su ${best.n}`]].concat(sg.k === 'lungo' ? [] : [['Scambi persi', `${lost} su ${all.length}`]]),
        rel, d: det.filter(Boolean).join(' · ') + '\nPersi per: ' + lossBreakdown(all) });
    });
    return out;
  }

  function computeCalls(m, REF) {
    const out = [], n = m.sets.length, st = replay(m, n), rs = st.rallies, all = allRallies(m);
    if (!rs.length || st.winner) return out;   // a set finito non si chiama più niente
    const last = rs[rs.length - 1];
    const opp = m.opponent;

    // segnali: un fondamentale che negli ultimi scambi va molto peggio del solito
    signals(m, REF, rs).forEach(c => out.push(c));

    // i momenti dello scambio sulla partita: quando la doppia indicazione cambia
    const refNoi = REF && REF.decimo ? REF.decimo.totali : null, refLoro = REF && m.refKey && REF.squadre[m.refKey] ? REF.squadre[m.refKey].totali : null;
    stageReport(all, refNoi, refLoro).forEach(x => {
      if (x.cls === 'neu' || x.xn.n < 8) return;
      const ph = stagePhrases(x.st.k, x.cn, x.cl, refNoi);
      const lines = [['Rispetto al solito', ph.u], ['Rispetto a loro', ph.t]];
      const p = x.vsUsual !== 'eq' ? (x.vsUsual === 'up' ? x.pU : 1 - x.pU) : (x.vsThem === 'up' ? x.pT : 1 - x.pT);
      out.push({ key: `stage-${x.st.k}-${x.cls}-${x.label}`, cls: x.cls, k: x.label.toUpperCase(),
        t: `${x.st.fase === 'SO' ? 'In cambio palla' : 'In break-point'} · ${x.st.label}`, cmp: lines, rel: p });
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
            t: `In attacco ${attackLine(m, nextSix)}: oggi ${w} su ${rr.length}`, cmp: [['Cambi palla di solito in P' + nextRot, pc(ru.so / ru.ric)]], rel: p });
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
      const old = m.calls.find(x => x.key === c.key)
        || (c.sig && m.calls.find(x => x.sig === c.sig && x.set === n && at - x.at <= SIG_WIN));
      if (old) { if (c.upd) Object.assign(old, c, { open: true, at, set: n, score: `${st.pts.noi}-${st.pts.loro}` }); return; }
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
    m.callsBuilt = CALLS_VERSION;
  }

  // ---------- da dove arrivano i punti ----------
  const CAUSES = [['primo', '1° attacco'], ['succ', 'Attacco successivo'], ['muro', 'Muro'], ['ace', 'Ace'], ['err', 'Errori avversario']];
  function pointCauses(rs, t) {
    const c = { primo: 0, succ: 0, muro: 0, ace: 0, err: 0, errComp: { attacco: 0, battuta: 0, invasione: 0, altro: 0 } };
    rs.filter(r => r.winner === t).forEach(r => {
      if (r.actor === t) c[ACTIONS[r.action].cause] = (c[ACTIONS[r.action].cause] || 0) + 1;
      else if (r.action === 'mur_1' || r.action === 'mur_s') c.muro++;
      else {
        c.err++;
        const k = r.action === 'err_battuta' ? 'battuta' : r.action === 'invasione' ? 'invasione' : r.action.startsWith('err_att') ? 'attacco' : 'altro';
        c.errComp[k]++;
      }
    });
    return c;
  }

  // ---------- 1° attacco dopo ricezione per linea del palleggiatore ----------
  // Obiettivo del periodo (indicazione dell'utente del 2026-11-14): efficienza = (punti − errori − murate
  // sul 1° attacco) / ricezioni in gioco (senza ace ed errori in battuta), senza il doppio cambio.
  // Obiettivi: 25% col palleggiatore davanti; dietro il livello playoff (28%) + 5, arrotondato a 35%.
  const ATT1_LINES = { davanti: [4, 3, 2], dietro: [1, 6, 5] };
  const ATT1_GOALS = { davanti: 25, dietro: 35 };
  function att1Counts(rs) {
    const rot = {};
    rs.forEach(r => {
      if (r.server !== 'loro' || !r.rot || r.dc || r.action === 'ace' || r.action === 'err_battuta') return;
      const a = rot[r.rot] = rot[r.rot] || { pt: 0, e: 0, n: 0 };
      a.n++;
      if (r.actor === 'noi' && r.action === 'att_pt_1') a.pt++;
      if (r.actor === 'noi' && (r.action === 'err_att_1' || r.action === 'mur_1')) a.e++;
    });
    const sum = zs => zs.reduce((s, z) => { const a = rot[z]; if (a) { s.pt += a.pt; s.e += a.e; s.n += a.n; } return s; }, { pt: 0, e: 0, n: 0 });
    return { rot, davanti: sum(ATT1_LINES.davanti), dietro: sum(ATT1_LINES.dietro) };
  }
  const att1Eff = a => (a && a.n ? 100 * (a.pt - a.e) / a.n : null);
  // verde dall'obiettivo in su, giallo tra il solito e l'obiettivo, rosso sotto il solito (o sotto l'obiettivo se il solito è già sopra)
  function att1Class(v, usual, goal) {
    if (v === null) return '';
    if (v >= goal) return 'ok';
    return usual !== null && usual < goal && v >= usual ? 'amb' : 'bad';
  }

  // ---------- probabilità di vincere il set (stesso conto di src/set_win_prob.py) ----------
  // Catena di Markov sul punteggio: ogni scambio lo vinciamo con pSO quando battono loro e pBP quando battiamo noi.
  // pSO e pBP: il nostro solito contro il loro (scala logit sulla media del campionato), poi quello che succede oggi
  // (set in corso pieno, set prima con peso W, il solito vale K scambi). Obiettivi, errori e ultimi scambi provati
  // in più non miglioravano la previsione (sono già dentro il cambio palla e il break-point di oggi).
  function markovSet(a, b, serveUs, pso, pbp, target) {
    pso = Math.min(Math.max(pso, 1e-3), 1 - 1e-3); pbp = Math.min(Math.max(pbp, 1e-3), 1 - 1e-3);
    const a11 = 1 - (1 - pbp) * pso, a12 = -pbp * (1 - pbp), c1 = pbp * pbp;
    const a21 = -(1 - pso) * pso, a22 = 1 - pso * (1 - pbp), c2 = pso * pbp, det = a11 * a22 - a12 * a21;
    const tieUs = (c1 * a22 - a12 * c2) / det, tieThem = (a11 * c2 - a21 * c1) / det;
    const memo = new Map();
    const f = (x, y, s) => {
      if (x >= target && x - y >= 2) return 1;
      if (y >= target && y - x >= 2) return 0;
      if (x >= target - 1 && y >= target - 1) {
        const d = x - y;
        if (d === 0) return s ? tieUs : tieThem;
        if (d === 1) return pbp + (1 - pbp) * tieThem;
        return pso * tieUs;
      }
      const k = x * 4096 + y * 2 + (s ? 1 : 0);
      if (!memo.has(k)) { const p = s ? pbp : pso; memo.set(k, p * f(x + 1, y, true) + (1 - p) * f(x, y + 1, false)); }
      return memo.get(k);
    };
    return f(a, b, serveUs);
  }
  const lgt = p => { p = Math.min(Math.max(p, 1e-4), 1 - 1e-4); return Math.log(p / (1 - p)); };
  const sgm = z => 1 / (1 + Math.exp(-z));
  function setWinProb(m, REF, n) {
    const P = REF && REF.prob_set; if (!P) return null;
    const st = replay(m, n);
    if (st.winner) return { p: st.winner === 'noi' ? 1 : 0, done: true, st };
    const L = P.lega, so0 = L.so / L.ric, bp0 = L.bp / L.bat, N = P.prior_n;
    const rate = (c, k, d, p0) => ((c && c[k] || 0) + N * p0) / ((c && c[d] || 0) + N);
    const us = REF.decimo && REF.decimo.totali, them = m.refKey && REF.squadre[m.refKey] ? REF.squadre[m.refKey].totali : null;
    const p0so = sgm(lgt(rate(us, 'so', 'ric', so0)) - lgt(rate(them, 'bp', 'bat', bp0)) + lgt(bp0));
    const p0bp = sgm(lgt(rate(us, 'bp', 'bat', bp0)) - lgt(rate(them, 'so', 'ric', so0)) + lgt(so0));
    const prev = { ric: 0, so: 0, bat: 0, bp: 0 };
    for (let i = 1; i < n; i++) { const c = stageCounts(replay(m, i).rallies, 'noi'); ['ric', 'so', 'bat', 'bp'].forEach(k => { prev[k] += c[k]; }); }
    const cur = stageCounts(st.rallies, 'noi');
    const pso = (cur.so + P.W * prev.so + P.K * p0so) / (cur.ric + P.W * prev.ric + P.K);
    const pbp = (cur.bp + P.W * prev.bp + P.K * p0bp) / (cur.bat + P.W * prev.bat + P.K);
    return { p: markovSet(st.pts.noi, st.pts.loro, st.server === 'noi', pso, pbp, st.target), pso, pbp, done: false, st };
  }

  // ---------- decisioni: cambi e KPI (2026-11-28, solo sul Decimo) ----------
  // Markov in cui cambio palla e break-point dipendono da k = cambi palla vinti da adesso (le rotazioni che verranno):
  // oltre kmax valgono quelli di kmax.
  function markovK(a, b, serveUs, psoK, pbpK, target, kmax) {
    const memo = new Map();
    const f = (x, y, s, k) => {
      if (x >= target && x - y >= 2) return 1;
      if (y >= target && y - x >= 2) return 0;
      if (x >= target - 1 && y >= target - 1) return markovSet(x, y, s, psoK(kmax), pbpK(kmax), target);
      const kk = Math.min(k, kmax), key = ((x * 64 + y) * 2 + (s ? 1 : 0)) * 32 + kk;
      if (memo.has(key)) return memo.get(key);
      const v = s ? pbpK(kk) * f(x + 1, y, true, kk) + (1 - pbpK(kk)) * f(x, y + 1, false, kk)
        : psoK(kk) * f(x + 1, y, true, kk + 1) + (1 - psoK(kk)) * f(x, y + 1, false, kk);
      memo.set(key, v); return v;
    };
    return f(a, b, serveUs, 0);
  }
  // estrazioni da una Beta (Marsaglia-Tsang), con un seme fisso: lo stesso stato dà sempre la stessa risposta
  let seed = 1;
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const gauss = () => { let u = 0; while (!u) u = rnd(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * rnd()); };
  function gammaR(k) {
    if (k < 1) return gammaR(k + 1) * Math.pow(rnd(), 1 / k);
    const d = k - 1 / 3, c = 1 / Math.sqrt(9 * d);
    for (;;) {
      let x, v; do { x = gauss(); v = 1 + c * x; } while (v <= 0);
      v = v * v * v; const u = rnd();
      if (u < 1 - 0.0331 * x * x * x * x || Math.log(u) < 0.5 * x * x + d * (1 - v + Math.log(v))) return d * v;
    }
  }
  const betaR = (w, n) => { const x = gammaR(w + 1); return x / (x + gammaR(n - w + 1)); };   // Beta(1,1) a priori
  const dLogit = (a, b) => lgt(betaR(a.w, a.n)) - lgt(betaR(b.w, b.n));
  // una proposta si mostra solo se la probabilità del set sale con affidabilità >= DEC_REL e di almeno DEC_MIN in media
  const DEC_NSIM = 120, DEC_REL = 0.9, DEC_MIN = 0.02;
  function simGain(wp, kmax, draw) {
    seed = 7; const st = wp.st, gains = [];
    for (let i = 0; i < DEC_NSIM; i++) {
      const d = draw();
      const p = markovK(st.pts.noi, st.pts.loro, st.server === 'noi', k => sgm(lgt(wp.pso) + (d.so[k] || 0)),
        k => sgm(lgt(wp.pbp) + (d.bp[k] || 0)), st.target, kmax);
      gains.push(p - wp.p);
    }
    const mean = gains.reduce((x, y) => x + y, 0) / gains.length;
    return { gain: mean, rel: gains.filter(g => g > 0).length / gains.length };
  }
  // doppio cambio: quando la prossima rotazione mette il palleggiatore di partenza in P4 (si propone con il servizio
  // agli altri); cambio palla e break-point in P4-P3-P2 con e senza doppio cambio, storia del Decimo più oggi
  function dcProposal(m, REF, n, wp) {
    const st = wp && wp.st, H = REF && REF.decimo && REF.decimo.cambi;
    if (!st || !H || st.server !== 'loro' || st.dc || !st.lr || nextZone(st.lr) !== 4 || st.subs.length > MAX_SUBS - 2) return null;
    const onC = new Set(st.six.concat([st.libero])), setter = st.six.find(x => x && roleOf(m, x) === 'P'), opp = st.six.find(x => x && roleOf(m, x) === 'O');
    if (!setter || !opp) return null;
    const bench = (m.roster || []).filter(p => !onC.has(p.n));
    const newS = bench.find(p => p.r === 'P' && !subLegal(m, st, p.n, opp)), newO = bench.find(p => p.r === 'O' && !subLegal(m, st, p.n, setter));
    if (!newS || !newO) return null;
    const c = { si: Object.assign({}, H.doppio_cambio.si), no: Object.assign({}, H.doppio_cambio.no) };
    allRallies(m).filter(r => r.lr && [2, 3, 4].includes(r.lr)).forEach(r => {
      const x = c[r.dc ? 'si' : 'no'], w = r.winner === 'noi' ? 1 : 0;
      if (r.server === 'loro') { x.ric++; x.so += w; } else { x.bat++; x.bp += w; }
    });
    if (c.si.ric < 20 || c.si.bat < 20) return null;
    const g = simGain(wp, 4, () => {
      const so = dLogit({ w: c.si.so, n: c.si.ric }, { w: c.no.so, n: c.no.ric }), bp = dLogit({ w: c.si.bp, n: c.si.bat }, { w: c.no.bp, n: c.no.bat });
      return { so: { 1: so, 2: so, 3: so }, bp: { 1: bp, 2: bp, 3: bp } };
    });
    return Object.assign(g, { kind: 'dc', inS: newS, outS: opp, inO: newO, outO: setter, show: g.rel >= DEC_REL && g.gain >= DEC_MIN });
  }
  // cambio in battuta per il prossimo battitore (si batte dopo il prossimo cambio palla): break-point vinti sulle
  // battute di chi entra e di chi uscirebbe, storia del Decimo più oggi; solo chi può entrare per lui
  function serveProposal(m, REF, n, wp) {
    const st = wp && wp.st, H = REF && REF.decimo && REF.decimo.cambi;
    if (!st || !H || st.server !== 'loro' || !st.six.every(Boolean)) return null;
    const srv = rotate(st.six)[0], cnt = {};
    const get = p => { const k = player(m, p).c, h = H.battitori[k] || { bat: 0, bp: 0 }; return cnt[p] = cnt[p] || { w: h.bp, n: h.bat }; };
    allRallies(m).filter(r => r.server === 'noi' && r.six[0]).forEach(r => { const x = get(r.six[0]); x.n++; if (r.winner === 'noi') x.w++; });
    if (['P', 'L'].includes(roleOf(m, srv))) return null;          // il palleggiatore non si toglie per la battuta
    const s = get(srv); if (s.n < 20) return null;
    const onC = new Set(st.six.concat([st.libero]));
    const cands = (m.roster || []).filter(p => !onC.has(p.n) && p.r !== 'L' && !subLegal(m, st, p.n, srv)).filter(p => get(p.n).n >= 20);
    let best = null;
    for (const p of cands) {
      const c = get(p.n), g = simGain(wp, 2, () => ({ so: {}, bp: { 1: dLogit(c, s) } }));
      if (!best || g.gain > best.gain) best = Object.assign(g, { kind: 'srv', inN: p.n, outN: srv, show: g.rel >= DEC_REL && g.gain >= DEC_MIN });
    }
    return best;
  }

  // "cosa serve per vincere il set" (indicazioni dell'utente del 2026-11-28): cambio palla e break-point divisi nei
  // loro pezzi, ognuno di oggi (tutta la partita) avvicinato al nostro solito con KPI_K occasioni. Si porta un pezzo a
  // un altro valore per il resto del set e si rifà la Markov (lo scarto si aggiunge a pSO e pBP del modello, così la
  // base è la percentuale del quadrato). Sotto GOAL_P si cerca il KPI che ci porta a GOAL_P con l'obiettivo più vicino
  // al nostro solito: "fatica" z = quanto l'obiettivo va oltre il nostro solito, in deviazioni standard tra le squadre
  // del girone (z < 0 = ancora sotto il solito: facile); obiettivo = max(oggi, solito + z·sd), mai oltre la squadra
  // migliore del girone. Se nessun KPI basta da solo, la coppia (poi la terna) con la fatica minore (stessa z per tutti);
  // se nemmeno tre bastano, i tre che al livello della migliore ci fanno salire di più, con la probabilità raggiunta.
  // Due casi (indicazione dell'utente): sotto il 50% (LOSING_P) si cerca cosa serve per arrivare a GOAL_P; altrimenti si
  // propone di riportare al nostro solito un KPI in cui oggi siamo sotto, se questo alza la probabilità di almeno
  // MORE_GAIN ("vinciamo di più"), scegliendo quello che la alza di più.
  const KPI_K = 20, GOAL_P = 0.6, LOSING_P = 0.5, MORE_GAIN = 0.05, KPI_MIN_N = 100, SD_MIN = 0.02;
  function parts(c) {
    const eL = c.so_err_loro != null ? c.so_err_loro : c.ric - c.ric_gioco - c.so_ace_sub;
    return { ric: c.ric, aceS: c.so_ace_sub, errL: eL, g1: c.ric_gioco, pt1: c.pt1, bad1: c.err1 + c.mur1, so3n: c.so3_n, so3w: c.so3_w,
      bat: c.bat, ace: c.ace, errB: c.err_bat, gb: c.bat_gioco, opt: c.o_pt1, obad: c.o_err1 + c.o_mur1, bp3n: c.bp3_n, bp3w: c.bp3_w };
  }
  function partRates(t, u) {
    const r = (x, n, ux, un) => (x + KPI_K * (un ? ux / un : 0)) / (n + KPI_K);
    return { aceS: r(t.aceS, t.ric, u.aceS, u.ric), errL: r(t.errL, t.ric, u.errL, u.ric), pt1: r(t.pt1, t.g1, u.pt1, u.g1),
      bad1: r(t.bad1, t.g1, u.bad1, u.g1), wso: r(t.so3w, t.so3n, u.so3w, u.so3n),
      ace: r(t.ace, t.bat, u.ace, u.bat), errB: r(t.errB, t.bat, u.errB, u.bat), opt: r(t.opt, t.gb, u.opt, u.gb),
      obad: r(t.obad, t.gb, u.obad, u.gb), wbp: r(t.bp3w, t.bp3n, u.bp3w, u.bp3n) };
  }
  const pSOparts = v => v.errL + Math.max(0, 1 - v.aceS - v.errL) * (v.pt1 + Math.max(0, 1 - v.pt1 - v.bad1) * v.wso);
  const pBPparts = v => v.ace + Math.max(0, 1 - v.ace - v.errB) * (v.obad + Math.max(0, 1 - v.opt - v.obad) * v.wbp);
  const KPIS = [
    // val: valore di oggi e del solito dai conteggi; set: il pezzo portato a x; up: più alto è meglio
    { k: 'a1', lab: '1° attacco', name: '1° attacco dopo ricezione', short: 'il 1° attacco', up: true, eff: true,
      val: t => ({ pos: t.pt1, neg: t.bad1, n: t.g1 }), cur: v => v.pt1 - v.bad1,
      set: (v, x) => { const d = (x - (v.pt1 - v.bad1)) / 2; v.pt1 = Math.max(0, v.pt1 + d); v.bad1 = Math.max(0, v.bad1 - d); } },
    { k: 'lunghi', lab: 'Scambi lunghi vinti', name: 'scambi lunghi vinti', short: 'gli scambi lunghi vinti', up: true, val: t => ({ pos: t.so3w + t.bp3w, neg: 0, n: t.so3n + t.bp3n }),
      cur: v => (v.wso + v.wbp) / 2, set: (v, x) => { const d = x - (v.wso + v.wbp) / 2; v.wso = Math.min(1, Math.max(0, v.wso + d)); v.wbp = Math.min(1, Math.max(0, v.wbp + d)); } },
    { k: 'opt', lab: 'Loro punti al 1° attacco (in battuta)', name: 'loro punti al 1° attacco quando battiamo', short: 'i loro punti al 1° attacco (quando battiamo)', up: false, val: t => ({ pos: t.opt, neg: 0, n: t.gb }), cur: v => v.opt, set: (v, x) => { v.opt = x; } },
    { k: 'aceS', lab: 'Ace subiti', name: 'ace subiti', short: 'gli ace subiti', up: false, val: t => ({ pos: t.aceS, neg: 0, n: t.ric }), cur: v => v.aceS, set: (v, x) => { v.aceS = x; } },
    { k: 'errB', lab: 'Errori in battuta', name: 'errori in battuta', short: 'gli errori in battuta', up: false, val: t => ({ pos: t.errB, neg: 0, n: t.bat }), cur: v => v.errB, set: (v, x) => { v.errB = x; } },
    { k: 'ace', lab: 'Ace', name: 'ace', short: 'gli ace', up: true, val: t => ({ pos: t.ace, neg: 0, n: t.bat }), cur: v => v.ace, set: (v, x) => { v.ace = x; } },
  ];
  // opts: mode ('win' = cosa serve per arrivare a GOAL_P, 'more' = un KPI sotto il solito che ci fa vincere di più;
  // di default dal punteggio), keep (KPI già proposti da tenere: un suggerimento resta finché non si raggiunge),
  // exclude (KPI già raggiunti in questo set: si propongono solo se non c'è altro)
  function kpiWhatIf(m, REF, n, wp, opts) {
    opts = opts || {};
    const keep = opts.keep || [], excl = new Set(opts.exclude || []);
    const st = wp && wp.st, U = REF && REF.decimo && REF.decimo.totali;
    if (!st || wp.done || !U) return null;
    const t = parts(stageCounts(allRallies(m), 'noi')), u = parts(U), v0 = partRates(t, u);
    const base = { so: pSOparts(v0), bp: pBPparts(v0) };
    const prob = v => markovSet(st.pts.noi, st.pts.loro, st.server === 'noi', wp.pso + pSOparts(v) - base.so, wp.pbp + pBPparts(v) - base.bp, st.target);
    const teams = Object.values(REF.squadre || {}).map(x => parts(x.totali)).concat([u]);
    const ks = [];
    for (const K of KPIS) {
      const vals = teams.map(K.val).filter(x => x.n >= KPI_MIN_N).map(x => (x.pos - x.neg) / x.n);
      if (vals.length < 3) continue;
      const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
      const sd = Math.max(SD_MIN, Math.sqrt(vals.reduce((a, b) => a + (b - mean) ** 2, 0) / (vals.length - 1)));
      const uv = K.val(u), usual = (uv.pos - uv.neg) / uv.n, dir = K.up ? 1 : -1;
      const best = K.up ? Math.max(...vals) : Math.min(...vals), zMax = dir * (best - usual) / sd;
      const from = K.cur(v0), tv = K.val(t);
      if (dir * (best - from) <= 0) continue;                  // oggi siamo già al livello della migliore
      ks.push({ K, from, usual, sd, dir, zMax, best, tv, today: tv.n ? (tv.pos - tv.neg) / tv.n : null });
    }
    const mode = opts.mode || (wp.p >= LOSING_P ? 'more' : 'win');
    const kept = keep.map(k => ks.find(x => x.K.k === k)).filter(Boolean);
    const item = (x, target) => ({ k: x.K.k, lab: x.K.lab, name: x.K.name, short: x.K.short, up: x.K.up, today: x.today, nToday: x.tv.n,
      posToday: x.tv.pos, negToday: x.tv.neg, from: x.from, usual: x.usual, best: x.best, target });
    if (mode === 'more') {
      // stiamo vincendo: un KPI sotto il nostro solito che, riportato al solito, ci fa vincere di più
      const atUsual = x => { const v = Object.assign({}, v0); x.K.set(v, x.usual); return prob(v); };
      if (kept.length && kept[0].dir * (kept[0].usual - kept[0].from) > 0)
        return { mode: 'more', p: wp.p, goal: atUsual(kept[0]), items: [item(kept[0], kept[0].usual)] };
      let more = null;
      for (const x of ks) {
        if (excl.has(x.K.k) || x.dir * (x.usual - x.from) <= 0 || x.tv.n < 5) continue;
        const v = Object.assign({}, v0); x.K.set(v, x.usual);
        const pNew = prob(v);
        if (pNew - wp.p >= MORE_GAIN && (!more || pNew > more.goal)) more = { mode: 'more', p: wp.p, goal: pNew, items: [item(x, x.usual)] };
      }
      return more;
    }
    // obiettivo di un KPI a fatica z: mai peggio di oggi
    const tgt = (x, z) => { const v = x.usual + x.dir * z * x.sd; return x.dir > 0 ? Math.max(x.from, v) : Math.min(x.from, v); };
    const at = (list, z) => { const v = Object.assign({}, v0); list.forEach(x => x.K.set(v, tgt(x, z))); return prob(v); };
    const solve = list => {
      const hi0 = Math.min(...list.map(x => x.zMax));
      if (at(list, hi0) < GOAL_P) return null;
      let lo = -4, hi = hi0;
      if (at(list, lo) >= GOAL_P) return lo;
      for (let i = 0; i < 16; i++) { const mid = (lo + hi) / 2; if (at(list, mid) >= GOAL_P) hi = mid; else lo = mid; }
      return hi;
    };
    const plan = list => { const z = solve(list); return z == null ? null : { z, items: list.map(x => ({
      k: x.K.k, lab: x.K.lab, name: x.K.name, short: x.K.short, up: x.K.up, today: x.today, nToday: x.tv.n, posToday: x.tv.pos, negToday: x.tv.neg,
      from: x.from, usual: x.usual, best: x.best, target: tgt(x, z) })) }; };
    // uno, poi due, poi tre KPI insieme (stessa fatica z): il gruppo più piccolo che basta, con la z minore
    // i KPI già proposti restano sempre nel gruppo; gli altri si aggiungono solo se servono
    const search = pool => {
      const combos = size => { const out = [], rec = (st, acc) => { if (acc.length === size) return out.push(kept.concat(acc));
        for (let i = st; i < pool.length; i++) rec(i + 1, acc.concat([pool[i]])); }; rec(0, []); return out; };
      for (let size = Math.max(0, 1 - kept.length); size <= 3 - kept.length; size++) {
        let bestPlan = null;
        for (const c of combos(size)) { const p = plan(c); if (p && (!bestPlan || p.z < bestPlan.z)) bestPlan = p; }
        if (bestPlan) return Object.assign(bestPlan, { mode: 'win', p: wp.p, goal: GOAL_P });
      }
      return null;
    };
    const all = ks.filter(x => !kept.includes(x)), fresh = all.filter(x => !excl.has(x.K.k));
    const found = search(fresh);
    if (found) return found;
    const others = fresh.length ? fresh : all;                     // i raggiunti da poco solo se non c'è altro
    const combos = size => { const out = [], rec = (st, acc) => { if (acc.length === size) return out.push(kept.concat(acc));
      for (let i = st; i < others.length; i++) rec(i + 1, acc.concat([others[i]])); }; rec(0, []); return out; };
    // nemmeno tre bastano con la stessa fatica: i tre che, portati verso il livello della migliore del girone, ci fanno
    // salire di più; ognuno fa la stessa quota f di strada da oggi alla migliore, la minima che porta al 60%, o tutta
    // (f = 1) con la probabilità a cui si arriva ("per recuperare dobbiamo agire qui, qui e qui")
    const atF = (c, f) => { const v = Object.assign({}, v0); c.forEach(x => x.K.set(v, x.from + (x.best - x.from) * f)); return prob(v); };
    let rec = null;
    for (const c of combos(Math.max(0, Math.min(3, kept.length + others.length) - kept.length))) {
      const p = atF(c, 1);
      if (!rec || p > rec.pMax) rec = { c, pMax: p };
    }
    if (!rec || rec.pMax <= wp.p) return null;
    let f = 1;
    if (rec.pMax >= GOAL_P) { let lo = 0, hi = 1; for (let i = 0; i < 16; i++) { const mid = (lo + hi) / 2; if (atF(rec.c, mid) >= GOAL_P) hi = mid; else lo = mid; } f = hi; }
    return { mode: 'win', p: wp.p, goal: Math.min(GOAL_P, rec.pMax), short: rec.pMax < GOAL_P, z: null,
      items: rec.c.map(x => item(x, x.from + (x.best - x.from) * f)) };
  }

  // il suggerimento del set, scambio per scambio (indicazione dell'utente del 2026-11-28: non cambiarlo a ogni punto).
  // Un KPI proposto resta finché, dagli scambi in cui è stato proposto, non arriva al suo obiettivo (almeno SUG_MIN
  // occasioni); allora "ok, ora agite su…" (per OK_SHOW scambi) e si passa a un altro; il KPI raggiunto non si
  // ripropone per COOLDOWN scambi. Se da solo non basta più, gli se ne affianca un altro. "Cosa serve per
  // vincere" resta finché non arriviamo al 60%, "vinciamo di più" finché non scendiamo sotto il 50%.
  const SUG_MIN = 4, OK_SHOW = 3, COOLDOWN = 6, trackCache = new Map();
  function truncate(m, n, k) {
    const mm = Object.assign({}, m); let c = 0; mm.events = [];
    for (const e of m.events) { if (e.set > n) break; if (e.set === n && e.t === 'r' && c++ >= k) break; mm.events.push(e); }
    mm.sets = m.sets.slice(0, n); return mm;
  }
  const kpiValue = (K, rs) => { const v = K.val(parts(stageCounts(rs, 'noi'))); return { pos: v.pos, neg: v.neg, n: v.n, x: v.n ? (v.pos - v.neg) / v.n : null }; };
  function kpiPlan(m, REF, n) {
    const rs = replay(m, n).rallies, sigs = [''];
    rs.forEach((r, i) => sigs.push(sigs[i] + r.winner[0] + r.action + ','));
    const key = k => `${m.id}|${n}|${JSON.stringify(m.events.filter(e => e.set < n).length)}|${sigs[k]}`;
    let state = { keep: [], excl: [], mode: null, plan: null, reached: null }, k0 = 0;
    for (let k = rs.length; k >= 0; k--) { const c = trackCache.get(key(k)); if (c) { state = c; k0 = k + 1; break; } }
    if (trackCache.size > 3000) trackCache.clear();
    for (let k = k0; k <= rs.length; k++) {
      const mm = truncate(m, n, k), wp = setWinProb(mm, REF, n), done = rs.slice(0, k);
      const s = { keep: state.keep.slice(), excl: state.excl.filter(x => k - x.at < COOLDOWN), mode: state.mode, plan: null,
        reached: state.reached && k - state.reached.at < OK_SHOW ? state.reached : null };
      if (wp && !wp.done) {
        s.keep = s.keep.filter(x => {
          const K = KPIS.find(q => q.k === x.k), v = kpiValue(K, done.slice(x.start));
          const ok = v.n >= SUG_MIN && (K.up ? v.x >= x.target : v.x <= x.target);
          if (ok) { s.excl.push({ k: x.k, at: k }); s.reached = { k: x.k, lab: K.lab, short: K.short, up: K.up, pos: v.pos, neg: v.neg, n: v.n, x: v.x, since: x.score, at: k, target: x.target }; }
          return !ok;
        });
        let want = wp.p >= LOSING_P ? 'more' : 'win';
        if (s.mode === 'win' && wp.p < GOAL_P) want = 'win';
        if (s.mode === 'win' && wp.p >= GOAL_P) s.keep = [];                      // obiettivo di probabilità raggiunto
        const plan = kpiWhatIf(mm, REF, n, wp, { mode: want, keep: s.keep.map(x => x.k), exclude: s.excl.map(x => x.k) });
        if (plan && plan.items.length) {
          const score = `${wp.st.pts.noi}-${wp.st.pts.loro}`;
          s.keep = plan.items.map(it => { const old = s.keep.find(x => x.k === it.k);
            return { k: it.k, target: it.target, start: old ? old.start : k, score: old ? old.score : score }; });
          plan.items.forEach(it => { const x = s.keep.find(q => q.k === it.k), v = kpiValue(KPIS.find(q => q.k === it.k), done.slice(x.start));
            it.since = { score: x.score, pos: v.pos, neg: v.neg, n: v.n }; });
          s.mode = want; s.plan = plan;
        } else { s.keep = []; s.mode = null; }
      }
      trackCache.set(key(k), s); state = s;
    }
    return state;
  }

  // ---------- partita nuova / set nuovo ----------
  function nextSetStart(m) {   // formazione del set dopo: quella di partenza del set appena finito
    const prev = m.sets[m.sets.length - 1];
    return { firstServer: 'noi', lineup: Object.assign({}, prev.lineup), libero: prev.libero };
  }

  const Core = { ACTIONS, BUTTONS, MAX_SUBS, MAX_TO, other, nextZone, rotate, player, nameOf, roleOf, replay, allRallies, setsWon,
    owner, subStatus, subLegal, frontLine, attackLine, goalValues, stageCounts, STAGES, stageReport, SYM, verdictOf, GOALS, goalState,
    goalClass, reached, whenP4, todayRates, serverStats, usualServe, serveSuggestions, computeCalls, updateCalls, rebuildCalls, CALLS_VERSION, stagePhrases, goalDetails, relText,
    relClass, compareRates, lineSplit, usualLine, pointCauses, CAUSES, nextSetStart, usualTxt, pc, xy,
    ATT1_LINES, ATT1_GOALS, att1Counts, att1Eff, att1Class, markovSet, setWinProb, markovK, dcProposal, serveProposal, kpiWhatIf, KPIS, kpiPlan };
  if (typeof module !== 'undefined' && module.exports) module.exports = Core; else root.Core = Core;
})(this);
