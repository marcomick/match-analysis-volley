# src/identity.py
"""
Identità di gioco (richiesta dell'utente del 2026-11-11): quale situazione
che porta punti è il nostro punto di forza, su cui appoggiarci sempre, e
dentro quella situazione quali scelte ci fanno vincere più spesso lo scambio
(linea del palleggiatore, zona, attaccante, rotazione). Più le situazioni da
recuperare (peggiorate rispetto all'anno scorso) e da costruire (più lontane
dalle squadre in zona playoff), e le indicazioni per ogni giocatore.

Dati: gli attacchi di build_dataset/attach_teams (src/performance_model.py),
salvati con i risultati del modello. Misura principale: scambi vinti dopo
l'attacco (1 scambio = 1 punto), con punto ed errori + murate accanto.
L'identità si legge sulla stagione in corso (la squadra cambia da un anno
all'altro); la stagione prima resta come confronto.

Certezza: probabilità bayesiana che una cosa sia vera, dai conteggi
(Beta(1 + vinti, 1 + persi) per ogni gruppo, confronto per simulazione).
Tre livelli (CERTAINTY_LEVELS): certo ≥ 90% (e, dove si può controllare,
visto in almeno 3 partite su 4), probabile ≥ 80%, da verificare ≥ 65%.
"""
import numpy as np
import pandas as pd

from src.performance_model import (
    SITUATIONS,
    SITUATION_LABELS,
    ZONE_LABELS,
    excluded_players,
    playoff_teams,
)

SITUATION_WITH = {"R#+": "Con palla buona (ricezione # o +)", "R!": "Dopo una ricezione !",
                  "R-": "Su palla alta (ricezione -)", "FB": "Sulla free ball", "CA": "In contrattacco"}
LINES = {"dietro": ("P1", "P6", "P5"), "davanti": ("P2", "P3", "P4")}
LINE_LABELS = {"dietro": "palleggiatore dietro (P1, P6, P5)", "davanti": "palleggiatore davanti (P2, P3, P4)"}
LINE_NOTES = {"dietro": "opposto in prima linea: tre attaccanti davanti",
              "davanti": "opposto in seconda linea: due attaccanti davanti"}
SHORT_SITUATION = {"R#+": "palla buona (ricezione # o +)", "R!": "ricezione !", "R-": "palla alta (ricezione -)",
                   "FB": "free ball", "CA": "contrattacco"}
BACK_ZONES = (8, 9)
CERTAINTY_LEVELS = [(0.90, "certo"), (0.80, "probabile"), (0.65, "da verificare")]
CERTAINTY_DOTS = {"certo": "●●●", "probabile": "●●○", "da verificare": "●○○"}
MIN_CELL = 6           # attacchi minimi di un attaccante in una zona per una regola
MIN_LINE = 15          # attacchi minimi in una linea per confrontarla
MIN_MATCH = 3          # attacchi minimi di una linea in una partita per il controllo partita per partita
PRIOR_STRENGTH = 10    # attacchi "virtuali" verso la media della linea nelle stime per attaccante
CERTAIN_MIN_N = 15     # azioni minime per dire "certo"
MIN_GAP = 0.10         # scarto minimo (scambi vinti) per una regola "rende meno" o "rotazione più difficile"
PLAYER_RULE_SITUATIONS = ("R#+", "FB")
MIN_PROFILE_EFFECT = 1.5  # scambi su 100 azioni: sotto, una differenza del modello è troppo piccola per i ragazzi   # situazioni con regole per attaccante nelle schede dei giocatori
DRAWS = 20000

# come lavorare, per situazione (testo per la squadra: allenamento sintetico, poi globale)
WORK_HINTS = {
    "R#+": "Sintetico: ricezione e attacco con chiamata decisa prima (primo tempo e opposto), "
           "poi in esercitazioni globali punto doppio se il cambio palla arriva al primo attacco.",
    "R#+_davanti": "Sintetico: ricezione con palleggiatore in prima linea e tre opzioni sempre pronte "
                   "(primo tempo, posto 4, attacco da seconda linea); poi nel 6 contro 6 partendo da quelle rotazioni.",
    "R!": "Sintetico: ricezione staccata da rete, palleggiatore in movimento e alzata su posto 4 o opposto "
          "con chiamata semplice; obiettivo palla tenuta in gioco senza regalare il punto.",
    "R-": "Sintetico: palla alta sulla banda contro muro a due: colpi contro le mani, pallonetto, rigiocata. "
          "Obiettivo zero errori, il punto se arriva.",
    "FB": "Sintetico: free ball mandata dall'allenatore, transizione e attacco con combinazione decisa prima; "
          "poi nel gioco libero la free ball vale doppio.",
    "CA": "Sintetico: difesa e contrattacco su palla alta con chiamata dell'attaccante e copertura; "
          "poi nel 6 contro 6 punto doppio sul contrattacco vinto.",
}
PLAYER_HINTS = {
    "Attacco": "serie di attacchi nella situazione indicata, con obiettivo di punti e di errori",
    "Battuta: errori": "serie di battute con obiettivo di errore (per esempio al massimo 1 su 10) alla stessa velocità",
    "Battuta: pressione": "battute su bersaglio (zona e giocatore) per mettere più in difficoltà la ricezione",
    "Ricezione": "ricezione sulla battuta più difficile che incontriamo, con obiettivo # o +",
    "Ricezione in prima linea": "ricezione in prima linea e subito la rincorsa per attaccare",
    "Attacco nei finali di set": "esercitazioni a punteggio (dal 20 pari) con palla a lui",
}


def certainty(p, agreement=None, n=None):
    """Livello di certezza di una probabilità (o None sotto la soglia più
    bassa). `agreement` = quota di partite in cui si vede la stessa cosa
    (sotto 3 su 4 al massimo "probabile", sotto la metà "da verificare");
    `n` = azioni su cui si basa (sotto CERTAIN_MIN_N al massimo "probabile",
    sotto MIN_CELL + 2 "da verificare")."""
    level = next((lv for threshold, lv in CERTAINTY_LEVELS if p >= threshold), None)
    if level is None:
        return None
    cap = 0
    if agreement is not None:
        cap = max(cap, 2 if agreement < 0.5 else (1 if agreement < 0.75 else 0))
    if n is not None:
        cap = max(cap, 2 if n < MIN_CELL + 2 else (1 if n < CERTAIN_MIN_N else 0))
    order = [lv for _, lv in CERTAINTY_LEVELS]
    return order[max(order.index(level), cap)]


def _p_greater(k1, n1, k2, n2, seed=0):
    """P(tasso del gruppo 1 > tasso del gruppo 2), Beta(1 + k, 1 + n - k)."""
    rng = np.random.default_rng(seed)
    a = rng.beta(1 + k1, 1 + n1 - k1, DRAWS)
    b = rng.beta(1 + k2, 1 + n2 - k2, DRAWS)
    return float((a > b).mean())


def _stats(d, sets=None):
    n = len(d)
    out = {"n": n, "vinti": int(d["vinto"].sum()) if n else 0,
           "vinti_pct": float(d["vinto"].mean()) if n else np.nan,
           "punto_pct": float((d["esito"] == "#").mean()) if n else np.nan,
           "errmur_pct": float(d["esito"].isin(["=", "/"]).mean()) if n else np.nan}
    if sets:
        out["per_set"] = n / sets
    return out


def _name(key):
    return str(key).rsplit("|", 1)[-1]


def _player_zone(name, zone):
    return f"{name} ({ZONE_LABELS.get(zone, zone)})"


def _cell_phrase(name, zone):
    """'il primo tempo di Carrer', 'Chimenton in posto 4'."""
    if zone == 3:
        return f"il primo tempo di {name}"
    if zone in BACK_ZONES:
        return f"{name} da seconda linea"
    return f"{name} in {ZONE_LABELS[zone]}"


def _won(k, n):
    return f"{k} scambi vinti su {n}"


def _num(x, dec):
    """Numero con segno e virgola decimale."""
    x = 0.0 if round(x, dec) == 0 else x
    return f"{x:+.{dec}f}".replace(".", ",")


def _pct(x):
    return f"{100 * x:.0f}%"


# ---------------------------------------------------------------------------
# Analisi
# ---------------------------------------------------------------------------
def prepare(attacks):
    a = attacks.copy()
    a["linea"] = np.where(a["rotazione"].isin(LINES["dietro"]), "dietro",
                          np.where(a["rotazione"].isin(LINES["davanti"]), "davanti", None))
    a["attaccante"] = a["giocatore"].map(_name)
    return a


def situation_table(a, sets_played, cur_keys, prev_keys, po_keys):
    """Una riga per situazione: noi quest'anno, noi l'anno scorso, squadre
    playoff (scambi vinti, punto, errori + murate, attacchi per set),
    regolarità partita per partita, probabilità e punti per set di scarto."""
    sets = {name: len(sets_played[sets_played["team"].isin(keys)]) for name, keys in
            (("cur", cur_keys), ("prev", prev_keys), ("po", po_keys))}
    rows = []
    for sit in SITUATIONS:
        s = a[a["situazione"] == sit]
        cur, prev, po = (s[s["team"].isin(k)] for k in (cur_keys, prev_keys, po_keys))
        c, p, q = _stats(cur, sets["cur"]), _stats(prev, sets["prev"]), _stats(po, sets["po"])
        per_match = cur.groupby("partita")["vinto"].agg(["sum", "size"])
        per_match = per_match[per_match["size"] >= 5]
        above = (per_match["sum"] / per_match["size"] >= q["vinti_pct"]).sum() if len(per_match) else 0
        rows.append({
            "situazione": sit, "etichetta": SITUATION_LABELS[sit], "breve": SHORT_SITUATION[sit],
            **{f"noi_{k}": v for k, v in c.items()}, **{f"prima_{k}": v for k, v in p.items()},
            **{f"playoff_{k}": v for k, v in q.items()},
            "partite": len(per_match), "partite_sopra_playoff": int(above),
            "partite_vinti": [f"{int(x['sum'])}/{int(x['size'])}" for _, x in per_match.iterrows()],
            "regolarita_sd": float((per_match["sum"] / per_match["size"]).std()) if len(per_match) > 1 else np.nan,
            "p_come_playoff": _p_greater(c["vinti"], c["n"], q["vinti"], q["n"]) if c["n"] else np.nan,
            "p_meglio_anno_prima": _p_greater(c["vinti"], c["n"], p["vinti"], p["n"]) if c["n"] and p["n"] else np.nan,
            "scarto_playoff_punti_set": ((c["vinti_pct"] - q["vinti_pct"]) * c.get("per_set", 0)) if c["n"] else np.nan,
        })
    return pd.DataFrame(rows)


def pick_pillars(t):
    """Punto di forza (la situazione con più volume tra quelle al livello
    playoff), da recuperare (peggiorate rispetto all'anno scorso), da
    costruire (più lontana dalle playoff in punti per set)."""
    ok = t[t["noi_n"] >= 30]
    near = ok[ok["noi_vinti_pct"] >= ok["playoff_vinti_pct"] - 0.03]
    if len(near):
        strength = near.sort_values("noi_per_set", ascending=False).iloc[0]["situazione"]
    elif len(ok):
        strength = ok.sort_values("p_come_playoff", ascending=False).iloc[0]["situazione"]
    else:
        strength = None
    recover = t[(t["noi_n"] >= 15) & (t["p_meglio_anno_prima"] <= 0.2)
                & (t["prima_vinti_pct"] - t["noi_vinti_pct"] >= 0.08)]["situazione"].tolist()
    build = t[(t["situazione"] != strength) & ~t["situazione"].isin(recover) & (t["noi_n"] >= 30)
              & (t["p_come_playoff"] <= 0.2)].sort_values("scarto_playoff_punti_set")
    return {"forza": strength, "recuperare": recover, "costruire": build["situazione"].tolist()[:1]}


def situation_detail(a, sit, cur_keys, po_keys):
    """Dentro una situazione: linee del palleggiatore, zone, attaccanti,
    rotazioni (stagione in corso), con il confronto con le playoff."""
    s = a[(a["situazione"] == sit) & a["linea"].notna()]
    cur, po = s[s["team"].isin(cur_keys)], s[s["team"].isin(po_keys)]
    out = {"situazione": sit, "n": len(cur), "linee": {}, "regole": []}
    for line in LINES:
        d, q = cur[cur["linea"] == line], po[po["linea"] == line]
        zones = []
        for z in sorted(set(d["zona"]) | set(q["zona"])):
            dz, qz = d[d["zona"] == z], q[q["zona"] == z]
            zones.append({"zona": z, "etichetta": ZONE_LABELS.get(z, z), **_stats(dz),
                          "quota": len(dz) / max(1, len(d)), "playoff_quota": len(qz) / max(1, len(q)),
                          "playoff_vinti_pct": qz["vinto"].mean() if len(qz) else np.nan,
                          "playoff_punto_pct": (qz["esito"] == "#").mean() if len(qz) else np.nan})
        base = d["vinto"].mean() if len(d) else np.nan
        cells = []
        for (att, z), dc in d.groupby(["attaccante", "zona"]):
            rest = d.drop(dc.index)
            k, n = int(dc["vinto"].sum()), len(dc)
            cells.append({"attaccante": att, "zona": z, **_stats(dc),
                          "stima": (k + PRIOR_STRENGTH * base) / (n + PRIOR_STRENGTH),
                          "p_meglio": _p_greater(k, n, int(rest["vinto"].sum()), len(rest), seed=n)
                          if len(rest) else np.nan})
        out["linee"][line] = {**_stats(d), **{f"playoff_{k}": v for k, v in _stats(q).items()},
                              "zone": pd.DataFrame(zones), "attaccanti": pd.DataFrame(cells),
                              "seconda_linea": int(d["zona"].isin(BACK_ZONES).sum()),
                              "playoff_seconda_linea": float(q["zona"].isin(BACK_ZONES).mean()) if len(q) else np.nan,
                              "righe": d}
    rots = []
    for rot, dr in cur.groupby("rotazione"):
        rest = cur.drop(dr.index)
        rots.append({"rotazione": rot, "linea": "dietro" if rot in LINES["dietro"] else "davanti", **_stats(dr),
                     "resto_pct": rest["vinto"].mean() if len(rest) else np.nan,
                     "p_meglio": _p_greater(int(dr["vinto"].sum()), len(dr), int(rest["vinto"].sum()), len(rest))
                     if len(rest) else np.nan})
    out["rotazioni"] = pd.DataFrame(rots)
    # linee a confronto, anche partita per partita
    L = out["linee"]
    if L["dietro"]["n"] >= MIN_LINE and L["davanti"]["n"] >= MIN_LINE:
        p = _p_greater(L["dietro"]["vinti"], L["dietro"]["n"], L["davanti"]["vinti"], L["davanti"]["n"])
        better = "dietro" if p >= 0.5 else "davanti"
        by_match = cur.groupby(["partita", "linea"])["vinto"].agg(["sum", "size"]).unstack("linea")
        rows, agree = [], []
        for m, x in by_match.iterrows():
            vals = {ln: (x.get(("sum", ln)), x.get(("size", ln))) for ln in LINES}
            rows.append({"partita": m, **{ln: (f"{int(v[0])}/{int(v[1])}" if pd.notna(v[1]) else "")
                                          for ln, v in vals.items()}})
            if all(pd.notna(v[1]) and v[1] >= MIN_MATCH for v in vals.values()):
                rates = {ln: v[0] / v[1] for ln, v in vals.items()}
                agree.append(rates[better] >= rates["davanti" if better == "dietro" else "dietro"])
        out["confronto_linee"] = {"p_dietro_meglio": p, "migliore": better,
                                  "partite": pd.DataFrame(rows), "concordi": int(sum(agree)), "partite_n": len(agree)}
    out["regole"] = _rules(out)
    return out


def _rule(text, p, agreement=None, kind="regola", n=None, **extra):
    level = certainty(p, agreement, n)
    return {"testo": text, "p": p, "certezza": level, "tipo": kind, **extra} if level else None


def _rules(detail):
    sit = detail["situazione"]
    L, rules = detail["linee"], []
    cmp_ = detail.get("confronto_linee")
    if cmp_:
        better = cmp_["migliore"]
        worse = "davanti" if better == "dietro" else "dietro"
        p = cmp_["p_dietro_meglio"] if better == "dietro" else 1 - cmp_["p_dietro_meglio"]
        agreement = cmp_["concordi"] / cmp_["partite_n"] if cmp_["partite_n"] else None
        b, w = L[better], L[worse]
        rules.append(_rule(
            f"{SITUATION_WITH[sit]} rendiamo di più con il {LINE_LABELS[better]}: "
            f"{_won(b['vinti'], b['n'])} ({b['vinti_pct']:.0%}), contro {_won(w['vinti'], w['n'])} "
            f"({w['vinti_pct']:.0%}) con il {LINE_LABELS[worse]}"
            + (f"; succede in {cmp_['concordi']} partite su {cmp_['partite_n']}." if cmp_["partite_n"] else "."),
            p, agreement, kind="linee", n=min(b["n"], w["n"]), linea=better))
    for line, d in L.items():
        cells = d["attaccanti"]
        if cells.empty:
            continue
        cand = cells[cells["n"] >= MIN_CELL].sort_values("stima", ascending=False)
        for rank, (_, c) in enumerate(cand.head(2).iterrows()):
            short = (f"{'prima' if rank == 0 else 'seconda'} scelta: {_won(c['vinti'], c['n'])}, punto "
                     f"{_pct(c['punto_pct'])}, errori o murate {_pct(c['errmur_pct'])}")
            r = _rule(f"Con il {LINE_LABELS[line]}, {'prima' if rank == 0 else 'seconda'} scelta "
                      f"{_cell_phrase(c['attaccante'], c['zona'])}: {_won(c['vinti'], c['n'])}, punto "
                      f"{_pct(c['punto_pct'])} e errori o murate {_pct(c['errmur_pct'])}.",
                      c["p_meglio"], kind="scelta", n=c["n"], linea=line, attaccante=c["attaccante"],
                      zona=c["zona"], rango=rank, breve=short)
            if r:
                rules.append(r)
        for _, c in cand.iterrows():
            rest_rate = (d["vinti"] - c["vinti"]) / max(1, d["n"] - c["n"])
            if c["p_meglio"] > 1 - CERTAINTY_LEVELS[1][0] or c["vinti_pct"] > rest_rate - MIN_GAP:
                continue
            use = "meglio come finta per fissare il loro centrale" if c["zona"] == 3 else "non come prima scelta"
            short = (f"rende meno degli altri attaccanti ({_won(c['vinti'], c['n'])}, contro il {_pct(rest_rate)} "
                     f"degli altri; errori o murate {_pct(c['errmur_pct'])})")
            rules.append(_rule(
                f"Con il {LINE_LABELS[line]}, {_cell_phrase(c['attaccante'], c['zona'])} {short}: {use}.",
                1 - c["p_meglio"], kind="evitare", n=c["n"], linea=line, attaccante=c["attaccante"], zona=c["zona"],
                breve=short))
        po_share = d.get("playoff_seconda_linea")
        if (line == "davanti" and d["n"] >= MIN_LINE and pd.notna(po_share) and po_share >= 0.08
                and d["seconda_linea"] / d["n"] < po_share / 2):
            rng = np.random.default_rng(1)
            p = float((rng.beta(1 + d["seconda_linea"], 1 + d["n"] - d["seconda_linea"], DRAWS) < po_share).mean())
            rules.append(_rule(
                f"Con il {LINE_LABELS[line]} ci manca la terza opzione da seconda linea: la usiamo "
                f"{d['seconda_linea']} volte su {d['n']}, le squadre playoff nel {_pct(po_share)} dei palloni.",
                p, kind="costruire", n=d["n"], linea=line))
    rots = detail["rotazioni"]
    if len(rots):
        for _, x in rots[rots["n"] >= 10].sort_values("p_meglio").head(1).iterrows():
            if x["vinti_pct"] <= x["resto_pct"] - MIN_GAP:
                rules.append(_rule(f"La rotazione più difficile è {x['rotazione']}: {_won(x['vinti'], x['n'])} "
                                   f"({_pct(x['vinti_pct'])}, nelle altre il {_pct(x['resto_pct'])}).",
                                   1 - x["p_meglio"], kind="rotazione", n=min(x["n"], CERTAIN_MIN_N - 1),
                                   rotazione=x["rotazione"]))
    return [r for r in rules if r]


def _profile_text(x):
    """Frase semplice per i giocatori da una voce dei punti di forza e deboli
    del modello (src/performance_model.player_profiles)."""
    n = max(1, round(abs(x["diff_media"])))
    more = x["diff_media"] > 0
    scambi = f"{n} {'scambio' if n == 1 else 'scambi'}"
    area = x["area"]
    if area == "Attacco":
        ball = x["dettaglio"].split(" (")[0]
        return (f"Attacco su {ball}: ogni 100 attacchi vinci {scambi} {'in più' if more else 'in meno'} "
                "del compagno medio nella stessa situazione")
    if area == "Attacco nei finali di set":
        return f"Finali di set: ogni 100 attacchi vinci {scambi} {'in più' if more else 'in meno'} che nel resto del set"
    if area == "Battuta: errori":
        return (f"Battuta: sbagli {'meno' if more else 'più'} dei compagni, ogni 100 battute "
                f"{scambi} {'in più' if more else 'in meno'}")
    if area == "Battuta: pressione":
        return (f"Battuta: le tue battute in gioco mettono {'più' if more else 'meno'} in difficoltà la ricezione, "
                f"ogni 100 battute {scambi} {'in più' if more else 'in meno'}")
    if area == "Ricezione":
        return (f"Ricezione: dopo le tue ricezioni facciamo {'più' if more else 'meno'} cambi palla, "
                f"{n} ogni 100 ricezioni")
    if area == "Ricezione in prima linea":
        return (f"Ricezione in prima linea: rendi {'meglio' if more else 'peggio'} del solito quando sei davanti, "
                f"{n} cambi palla ogni 100 ricezioni")
    return f"{area}: {_num(x['diff_media'], 1)} {x['unita']}"


def player_items(details, profiles, players, cur_season_players):
    """Per ogni giocatore: su cosa ci appoggiamo e su cosa lavora. Dalle
    regole delle situazioni (prima o seconda scelta, da evitare) e dai punti
    di forza e deboli del modello di prestazione (confronto con il compagno
    medio)."""
    out = {p: {"appoggio": [], "lavoro": []} for p in players}
    for sit, d in details.items():
        if sit not in PLAYER_RULE_SITUATIONS:
            continue
        for r in d["regole"]:
            att = r.get("attaccante")
            if att not in out or r["tipo"] not in ("scelta", "evitare"):
                continue
            where = (f"{SHORT_SITUATION[sit][0].upper()}{SHORT_SITUATION[sit][1:]}, {LINE_LABELS[r['linea']]}, "
                     f"{ZONE_LABELS.get(r['zona'], r['zona'])}")
            item = {"area": "Attacco", "testo": f"{where}: {r['breve']}", "certezza": r["certezza"], "p": r["p"]}
            if r["tipo"] == "scelta":
                out[att]["appoggio"].append(item)
            else:
                out[att]["lavoro"].append({**item, "come": PLAYER_HINTS["Attacco"]})
    if profiles is not None and len(profiles):
        for _, x in profiles.iterrows():
            if (x["giocatore"] not in out or x["giudizio"] is None or pd.isna(x["giudizio"])
                    or abs(x["diff_media"]) < MIN_PROFILE_EFFECT):
                continue
            p = x["p_meglio"] if x["giudizio"] == "forza" else 1 - x["p_meglio"]
            level = certainty(p)
            if not level:
                continue
            text = _profile_text(x)
            item = {"area": x["area"], "testo": text, "certezza": level, "p": p, "modello": True}
            if x["giudizio"] == "forza":
                out[x["giocatore"]]["appoggio"].append(item)
            else:
                out[x["giocatore"]]["lavoro"].append({**item, "come": PLAYER_HINTS.get(x["area"], "")})
    order = {"certo": 0, "probabile": 1, "da verificare": 2}
    for p in out:
        for k in out[p]:
            out[p][k].sort(key=lambda i: (order[i["certezza"]], -i["p"]))
    return {p: v for p, v in out.items() if v["appoggio"] or v["lavoro"] or p in cur_season_players}


def identity_analysis(data, profiles=None, config=None):
    """
    `data` = results["dati"] del modello di prestazione (attacchi, set
    giocati, squadre-stagione, config). Ritorna situazioni, pilastri
    (forza, da recuperare, da costruire), dettaglio e regole per ogni
    situazione, indicazioni per giocatore, partite considerate.
    """
    config = config or data["config"]
    a, ts, sets_played = prepare(data["attacchi"]), data["squadre"], data["set"]
    current, previous = config["stagioni"][-1], (config["stagioni"][-2] if len(config["stagioni"]) > 1 else None)
    cur_keys = [f"{current}|{config['squadra']}"]
    prev_keys = [f"{previous}|{config['squadra']}"] if previous else []
    ti = {t: i for i, t in enumerate(ts["chiave"])}
    po_keys = playoff_teams(ts, sets_played, config, ti)
    table = situation_table(a, sets_played, cur_keys, prev_keys, po_keys)
    pillars = pick_pillars(table)
    details = {sit: situation_detail(a, sit, cur_keys, po_keys) for sit in SITUATIONS}
    out_of_team = excluded_players(config)
    cur = a[a["team"].isin(cur_keys)]
    cur_players = set(cur["attaccante"])
    serves = data.get("battute")
    if serves is not None:
        cur_players |= set(serves.loc[serves["team"].isin(cur_keys), "giocatore"].map(_name))
        cur_players |= set(serves.loc[serves["opp"].isin(cur_keys), "ricevitore"].dropna().map(_name))
    prof_players = set(profiles["giocatore"]) if profiles is not None and len(profiles) else set()
    players = sorted((cur_players | prof_players) - out_of_team)
    matches = sorted(cur["partita"].unique())
    names = ts.set_index("chiave")["nome"]
    return {
        "stagione": current, "stagione_prima": previous, "partite": matches, "n_partite": len(matches),
        "squadre_playoff": [f"{names[k]} ({k.split('|')[0]})" for k in po_keys],
        "situazioni": table, "pilastri": pillars, "dettagli": details,
        "giocatori": player_items(details, profiles, players, cur_players - out_of_team),
    }


# ---------------------------------------------------------------------------
# Frasi comuni a dashboard e PDF
# ---------------------------------------------------------------------------
def pillar_texts(I):
    """Frasi (markdown con **grassetto**) per punto di forza, situazioni da
    recuperare e da costruire, con il come lavorarci."""
    t = I["situazioni"].set_index("situazione")
    out = {"forza": None, "recuperare": [], "costruire": []}
    sit = I["pilastri"]["forza"]
    if sit:
        x = t.loc[sit]
        text = (f"**{x['etichetta']}**: circa {x['noi_per_set']:.0f} attacchi a set. Vinciamo il "
                f"**{_pct(x['noi_vinti_pct'])}** degli scambi ({x['noi_vinti']} su {x['noi_n']}), le squadre in zona "
                f"playoff il {_pct(x['playoff_vinti_pct'])}")
        if pd.notna(x["prima_vinti_pct"]):
            text += f"; l'anno scorso noi il {_pct(x['prima_vinti_pct'])}"
        text += (f". Siamo sopra il loro livello in {x['partite_sopra_playoff']} partite su {x['partite']}." if x["partite"]
                 else ".")
        cmp_ = I["dettagli"][sit].get("confronto_linee")
        hint = WORK_HINTS[sit]
        weak_line = None
        if cmp_ and certainty(max(cmp_["p_dietro_meglio"], 1 - cmp_["p_dietro_meglio"])):
            weak_line = "davanti" if cmp_["migliore"] == "dietro" else "dietro"
            if weak_line == "davanti" and f"{sit}_davanti" in WORK_HINTS:
                hint = WORK_HINTS[f"{sit}_davanti"]
        out["forza"] = {"situazione": sit, "testo": text, "come": hint, "linea_debole": weak_line}
    for sit in I["pilastri"]["recuperare"]:
        x = t.loc[sit]
        pts = (x["prima_vinti_pct"] - x["noi_vinti_pct"]) * x["noi_per_set"]
        out["recuperare"].append({"situazione": sit, "come": WORK_HINTS[sit], "testo": (
            f"**{x['etichetta']}**: quest'anno vinciamo il **{_pct(x['noi_vinti_pct'])}** degli scambi "
            f"({x['noi_vinti']} su {x['noi_n']}), l'anno scorso il {_pct(x['prima_vinti_pct'])} e le squadre playoff "
            f"il {_pct(x['playoff_vinti_pct'])}. Tornare come l'anno scorso vale circa "
            f"{_num(pts, 1)[1:]} punti a set.")})
    for sit in I["pilastri"]["costruire"]:
        x = t.loc[sit]
        out["costruire"].append({"situazione": sit, "come": WORK_HINTS[sit], "testo": (
            f"**{x['etichetta']}**: circa {x['noi_per_set']:.0f} attacchi a set; vinciamo il "
            f"**{_pct(x['noi_vinti_pct'])}** degli scambi, le squadre playoff il {_pct(x['playoff_vinti_pct'])}. "
            f"È la situazione in cui perdiamo di più: circa {_num(-x['scarto_playoff_punti_set'], 1)[1:]} punti a set.")})
    return out


# ---------------------------------------------------------------------------
# Chi c'è in prima linea (richiesta dell'utente del 2026-11-11: la scelta di
# C1/C2 e M1/M2 decide quali attaccanti sono davanti insieme; con il
# palleggiatore davanti gli attaccanti in prima linea sono solo due)
# ---------------------------------------------------------------------------
ROLE_NAMES = {1: "L", 2: "M", 3: "O", 4: "C", 5: "P"}
MIN_LINEUP = 8          # ricezioni minime di una prima linea (o di una coppia) per una regola
PAIR_PRIOR = 10         # ricezioni "virtuali" verso la media della linea nelle stime delle coppie


def _clean(name):
    return str(name).replace("’", "'").strip()


def build_lineups(config):
    """Una riga per ogni nostra ricezione delle partite della stagione in
    corso: rotazione (zona del nostro palleggiatore), linea, voto, cambio
    palla vinto, prima linea (attaccanti in P2-P4, senza il palleggiatore),
    centrale e martello davanti (se uno e uno), opposto davanti."""
    from src.dvw import decimo_side, parse_dvw, roster_by_number
    from src.match_report import side_actions
    from src.performance_model import match_catalog
    current = config["stagioni"][-1]
    rows = []
    for m in match_catalog(config)[0]:
        if m["stagione"] != current or not any("DECIMO" in t.upper() for t in m["squadre"]):
            continue
        P = parse_dvw(m["path"])
        side = decimo_side(P)
        prefix = "Casa" if side == "*" else "Ospiti"
        roster = {n: p for (sq, n), p in roster_by_number(P["players"]).items() if sq == side}
        winner = P["rallies"].set_index("Rally")["vincitore"].to_dict()
        d = side_actions(P, side)
        match_id = f"{m['data']} " + " - ".join(_clean(P["teams"][s]).title() for s in ("*", "a"))
        for _, x in d[d["Tipo"] == "ricezione"].drop_duplicates("Rally").iterrows():
            front = {}
            for k in (2, 3, 4):
                v = x.get(f"{prefix} P{k}")
                if pd.isna(v):
                    continue
                p = roster.get(int(v))
                role = ROLE_NAMES.get(int(p.ruolo)) if p is not None and pd.notna(p.ruolo) else None
                if role == "P":
                    continue
                front[_clean(p.cognome) if p is not None else str(int(v))] = role
            rot = str(x["Posizione Palleggiatore"] or "").upper()
            rows.append({
                "partita": match_id, "set": int(x["Numero Set"]), "rally": x["Rally"], "rotazione": rot,
                "linea": "dietro" if rot in LINES["dietro"] else ("davanti" if rot in LINES["davanti"] else None),
                "voto": x["Voto"], "vinto": winner.get(x["Rally"]) == side, "_front": front,
            })
    # ruolo di ogni giocatore = il più frequente nella stagione (la rosa di una partita può averne un altro)
    counts = {}
    for r in rows:
        for n, role in r["_front"].items():
            counts.setdefault(n, {}).setdefault(role, 0)
            counts[n][role] += 1
    main_role = {n: max(c, key=lambda k: (k is not None, c[k])) for n, c in counts.items()}
    for r in rows:
        front = {n: main_role.get(n) for n in r.pop("_front")}
        cs = [n for n, role in front.items() if role == "C"]
        ms = [n for n, role in front.items() if role == "M"]
        os_ = [n for n, role in front.items() if role == "O"]
        others = [n for n, role in front.items() if role not in ("C", "M", "O")]
        # ordine d'attacco: posto 4 (martello), centro, posto 2 (opposto o palleggiatore)
        order = ms[:1] + cs[:1] + os_[:1] + ms[1:] + cs[1:] + others
        r["prima_linea"] = " · ".join(order + ([] if os_ or r["linea"] == "dietro" else ["palleggiatore"]))
        r["centrale"] = cs[0] if len(cs) == 1 else None
        r["martello"] = ms[0] if len(ms) == 1 else None
    return pd.DataFrame(rows)


def _vs_rest(part, group):
    """Cambi palla di `part` contro il resto di `group` (probabilità che
    `part` faccia meglio)."""
    rest = group.drop(part.index)
    p = _p_greater(int(part["vinto"].sum()), len(part), int(rest["vinto"].sum()), len(rest), seed=len(part)) \
        if len(rest) else np.nan
    return {"n": len(part), "vinti": int(part["vinto"].sum()), "pct": float(part["vinto"].mean()),
            "buone_n": int(part["voto"].isin(["#", "+"]).sum()),
            "buone_vinti": int(part.loc[part["voto"].isin(["#", "+"]), "vinto"].sum()),
            "resto_pct": float(rest["vinto"].mean()) if len(rest) else np.nan, "p_meglio": p}


def front_line_analysis(rows):
    """Cambi palla per prima linea in ogni rotazione e per coppia centrale +
    martello (secondo la linea del palleggiatore); regole con la loro
    certezza."""
    if rows is None or rows.empty:
        return None
    rows = rows[rows["linea"].notna()].copy()
    by_rot = []
    for rot, g in rows.groupby("rotazione"):
        for line, part in g.groupby("prima_linea"):
            by_rot.append({"rotazione": rot, "linea": g["linea"].iloc[0], "prima_linea": line, **_vs_rest(part, g)})
    by_rot = pd.DataFrame(by_rot)
    pairs = []
    for line, g in rows[rows["centrale"].notna() & rows["martello"].notna()].groupby("linea"):
        for (c, m), part in g.groupby(["centrale", "martello"]):
            pairs.append({"linea": line, "centrale": c, "martello": m, **_vs_rest(part, g)})
    pairs = pd.DataFrame(pairs)
    # disposizione di ogni set: le coppie centrale + martello viste davanti insieme
    overall = float(rows["vinto"].mean())
    rules = []
    for _, x in by_rot[by_rot["n"] >= MIN_LINEUP].iterrows():
        gap = x["pct"] - x["resto_pct"]
        if abs(gap) < MIN_GAP:
            continue
        better = gap > 0
        r = _rule(f"In {x['rotazione']} ({LINE_LABELS[x['linea']]}) la prima linea **{x['prima_linea']}** fa "
                  f"{x['vinti']} cambi palla su {x['n']} ricezioni ({_pct(x['pct'])}), con le altre prime linee il "
                  f"{_pct(x['resto_pct'])}.", x["p_meglio"] if better else 1 - x["p_meglio"],
                  kind="scelta" if better else "evitare", n=x["n"], rotazione=x["rotazione"])
        if r:
            rules.append(r)
    for _, x in pairs[pairs["n"] >= MIN_LINEUP].iterrows() if len(pairs) else []:
        gap = x["pct"] - x["resto_pct"]
        if abs(gap) < MIN_GAP:
            continue
        better = gap > 0
        r = _rule(f"Con il {LINE_LABELS[x['linea']]}, **{x['centrale']}** (centrale) e **{x['martello']}** (martello) "
                  f"davanti insieme: {x['vinti']} cambi palla su {x['n']} ({_pct(x['pct'])}), le altre coppie il "
                  f"{_pct(x['resto_pct'])}.", x["p_meglio"] if better else 1 - x["p_meglio"],
                  kind="scelta" if better else "evitare", n=x["n"], linea=x["linea"])
        if r:
            rules.append(r)
    return {"righe": rows, "per_rotazione": by_rot, "coppie": pairs, "regole": rules, "media": overall,
            "medie_linea": rows.groupby("linea")["vinto"].mean().to_dict(),
            "ricezioni_per_rotazione": rows.groupby("rotazione").size().to_dict()}


# ordine di battuta fisso (indicazione dell'utente del 2026-11-11): con il palleggiatore in zona 1, M1 in 2, C2 in 3,
# O in 4, M2 in 5, C1 in 6. C1 è davanti in P3, P4, P5 e C2 in P6, P1, P2; M1 in P1, P2, P3 e M2 in P4, P5, P6;
# con il palleggiatore davanti: P2 = M1 + C2, P3 = M1 + C1, P4 = M2 + C1
SERVE_ORDER = ["P", "M1", "C2", "O", "M2", "C1"]
ROTATION_SEQUENCE = ["P1", "P6", "P5", "P4", "P3", "P2"]


def front_rotations(assign):
    """Prima linea di ogni rotazione in ordine d'attacco (posto 4, centro,
    posto 2) per un'assegnazione {M1, C2, O, M2, C1: nome}. Rotazione = zona
    del palleggiatore: il giocatore k posti dopo di lui nell'ordine di
    battuta è in zona (zona - 1 + k) % 6 + 1."""
    out = {}
    for z in range(1, 7):
        front = [SERVE_ORDER[k] for k in range(6) if (z - 1 + k) % 6 + 1 in (2, 3, 4)]
        m = next(sl for sl in front if sl.startswith("M"))
        c = next(sl for sl in front if sl.startswith("C"))
        right = "O" if "O" in front else "P"
        out[f"P{z}"] = [(m, assign.get(m, m)), (c, assign.get(c, c)),
                        (right, assign.get("O", "opposto") if right == "O" else "palleggiatore")]
    return out


def arrangement_estimates(F, centrals, hitters, draws=DRAWS):
    """
    Le quattro disposizioni di due centrali e due martelli con l'ordine di
    battuta fisso (SERVE_ORDER): chi è C1 e chi C2, chi M1 e chi M2. Per
    ogni rotazione la coppia centrale + martello davanti insieme e i cambi
    palla stimati dai dati di quella coppia nella stessa linea del
    palleggiatore (in qualunque rotazione, spinti verso la media della linea
    con PAIR_PRIOR ricezioni: una coppia mai vista vale la media). Cambi
    palla della disposizione = media sulle sei rotazioni pesata sulle
    ricezioni giocate in ognuna. Ritorna una riga per disposizione (stima,
    intervallo, probabilità di essere la migliore), il dettaglio per
    rotazione e i campioni.
    """
    rng = np.random.default_rng(3)
    pairs = F["coppie"].set_index(["linea", "centrale", "martello"]) if len(F["coppie"]) else None
    means = F["medie_linea"]
    weights = F["ricezioni_per_rotazione"]
    total_w = sum(weights.get(f"P{z}", 0) for z in range(1, 7)) or 6
    rows, detail, samples, cache = [], {}, [], {}
    for c1, c2 in (tuple(centrals), tuple(centrals)[::-1]):
        for m1, m2 in (tuple(hitters), tuple(hitters)[::-1]):
            assign = {"M1": m1, "C2": c2, "M2": m2, "C1": c1}
            fronts = front_rotations(assign)
            acc = np.zeros(draws)
            per_rot = []
            for rot in ROTATION_SEQUENCE:
                line = "dietro" if rot in LINES["dietro"] else "davanti"
                (ms, m), (cs, c), (rs, right) = fronts[rot]
                k = n = 0
                if pairs is not None and (line, c, m) in pairs.index:
                    k, n = int(pairs.loc[(line, c, m), "vinti"]), int(pairs.loc[(line, c, m), "n"])
                mean = means.get(line, 0.5)
                if (line, c, m) not in cache:
                    cache[(line, c, m)] = rng.beta(1 + k + PAIR_PRIOR * mean, 1 + n - k + PAIR_PRIOR * (1 - mean),
                                                   draws)
                acc += weights.get(rot, 1) / total_w * cache[(line, c, m)]
                per_rot.append({"rotazione": rot, "linea": line, "posto4": m, "posto4_ruolo": ms, "centro": c,
                                "centro_ruolo": cs, "posto2": right, "posto2_ruolo": rs,
                                "prima_linea": f"{m} · {c} · {right}", "centrale": c, "martello": m,
                                "osservate": n, "vinti": k, "stima": (k + PAIR_PRIOR * mean) / (n + PAIR_PRIOR)})
            label = f"M1 {m1} · C1 {c1}  (M2 {m2} · C2 {c2})"
            detail[label] = pd.DataFrame(per_rot)
            samples.append(acc)
            p4 = fronts["P4"]
            rows.append({"disposizione": label, "assegnazione": assign, "p4": f"{p4[0][1]} · {p4[1][1]}",
                         **_stats_draws(acc)})
    S = np.stack(samples, axis=1)
    out = pd.DataFrame(rows)
    out["p_migliore"] = np.bincount(S.argmax(1), minlength=S.shape[1]) / draws
    out = out.sort_values("media", ascending=False).reset_index(drop=True)
    return out, detail, S


def _stats_draws(x):
    lo, hi = np.percentile(x, [10, 90])
    return {"media": float(x.mean()), "basso": float(lo), "alto": float(hi)}
