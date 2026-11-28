"""
Probabilità di vincere il set, scambio per scambio, per l'app live (richiesta dell'utente del 2026-11-28: un quadrato
con la percentuale, che tenga conto del punteggio, di come stiamo facendo cambio palla e break-point, degli obiettivi
del set e degli errori nostri e loro; "prova il modello sulle partite già giocate e vedi quanto ci prende").

Modello: catena di Markov sul punteggio. Dal punteggio e da chi batte, la probabilità di arrivare a 25 (15 al quinto)
con due punti di scarto, se ogni scambio lo vinciamo con probabilità pSO quando battono loro e pBP quando battiamo noi
(`markov`). pSO e pBP partono dal "solito" delle due squadre (il nostro cambio palla contro il loro break-point, nelle
ultime 10 partite di ciascuna, `matchup`) e si spostano con quello che succede oggi: set in corso pieno, set precedenti
della partita con peso W, il solito vale K scambi (`rates`; K = 60, W = 1 scelti sui dati). L'app fa lo stesso conto
in live/core.js (`setWinProb`) con i parametri scritti in live/riferimenti.json ("prob_set", `prob_set_params`).

Provati anche, sopra il Markov, con una correzione logistica stimata senza la partita in esame: obiettivi del set
(la probabilità di vittoria dagli obiettivi dell'app), errori nostri meno loro, ultimi 6 scambi, tutti più pesanti a
inizio set. Nessuno migliora la previsione (log-loss 0,4694 → 0,470-0,472): quello che dicono è già nel cambio palla
e nel break-point di oggi. Quindi non ci sono (decisione presa sui dati il 2026-11-28).

Valutazione (`python -m src.set_win_prob`): ogni scambio di ogni set completo dei .dvw, con il solito delle squadre
dalle sole partite precedenti (come avrebbe fatto l'app quel giorno) e la correzione stimata senza la partita in
esame (validazione per partita). Dati: data/analisi/set_prob_rallies.pkl (rifatti con --rebuild).
"""
import json
import math
import os
import pickle
import sys

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data", "analisi", "set_prob_rallies.pkl")
N_PRIOR = 10              # partite precedenti per il solito di una squadra (come riferimenti.json)
PRIOR_N = 600             # il solito di una squadra con pochi dati si avvicina alla media del campionato (scelto sui dati il 2026-11-28; prima 150)
ERRORS = {"err_bat", "err_battuta", "err_att_1", "err_att_s", "mur_1", "mur_s", "invasione", "altro"}
GOAL_KEYS = ["bat", "a1", "ac", "muri", "grat"]
FEATURES = ["markov", "obiettivi", "errori", "ultimi"]   # provati, non usati (vedi sopra)
K_BEST, W_BEST = 60, 1.5


# ---------------------------------------------------------------------------- Markov
def markov(a, b, serve_us, pso, pbp, target=25):
    """P(vinciamo il set) dal punteggio a-b (noi-loro), con chi batte."""
    pso, pbp = min(max(pso, 1e-3), 1 - 1e-3), min(max(pbp, 1e-3), 1 - 1e-3)
    # parità ai vantaggi: x = battiamo noi, y = battono loro
    #   x = pbp·(pbp + (1−pbp)·y) + (1−pbp)·pso·x ;  y = pso·(pbp + (1−pbp)·y) + (1−pso)·pso·x
    a11, a12, c1 = 1 - (1 - pbp) * pso, -pbp * (1 - pbp), pbp * pbp
    a21, a22, c2 = -(1 - pso) * pso, 1 - pso * (1 - pbp), pso * pbp
    det = a11 * a22 - a12 * a21
    tie = {True: (c1 * a22 - a12 * c2) / det, False: (a11 * c2 - a21 * c1) / det}
    memo = {}

    def f(x, y, s):
        if x >= target and x - y >= 2:
            return 1.0
        if y >= target and y - x >= 2:
            return 0.0
        if x >= target - 1 and y >= target - 1:
            d = x - y
            if d == 0:
                return tie[s]
            if d == 1:      # vantaggio nostro: si batte noi (abbiamo vinto l'ultimo)
                return pbp + (1 - pbp) * tie[False]
            return pso * tie[True]                    # vantaggio loro, battono loro
        k = (x, y, s)
        if k not in memo:
            p = pbp if s else pso
            memo[k] = p * f(x + 1, y, True) + (1 - p) * f(x, y + 1, False)
        return memo[k]
    sys.setrecursionlimit(10000)
    return f(a, b, serve_us)


def logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def sig(z):
    return 1 / (1 + math.exp(-z))


# ---------------------------------------------------------------------------- solito delle squadre
def team_counts(r, side):
    other = "a" if side == "*" else "*"
    recv, serv, won = r["server"] == other, r["server"] == side, r["winner"] == side
    return {"ric": int(recv.sum()), "so": int((recv & won).sum()), "bat": int(serv.sum()), "bp": int((serv & won).sum())}


def matchup(us, them, league):
    """pSO e pBP nostri attesi: il nostro cambio palla contro il loro break-point (e viceversa), in scala logit
    rispetto alla media del campionato; ogni solito avvicinato alla media con PRIOR_N scambi."""
    def rate(c, k, n, p0):
        return (c.get(k, 0) + PRIOR_N * p0) / (c.get(n, 0) + PRIOR_N)
    so0, bp0 = league["so"] / league["ric"], league["bp"] / league["bat"]
    pso = sig(logit(rate(us, "so", "ric", so0)) - logit(rate(them, "bp", "bat", bp0)) + logit(bp0))
    pbp = sig(logit(rate(us, "bp", "bat", bp0)) - logit(rate(them, "so", "ric", so0)) + logit(so0))
    return pso, pbp


# ---------------------------------------------------------------------------- forza dai risultati federali
# (richiesta dell'utente del 2026-11-28: per le squadre diverse dal Decimo, i risultati della sola stagione in corso,
# anche delle partite non scoutizzate). Bradley-Terry sui punti dei set: logit(quota di punti di A contro B) = sA − sB,
# con una penalità (FED_LAM) che tiene le forze vicine a 0 finché le partite sono poche.
FED_LAM = 25.0


def _dkey(d):
    dd, mm, yy = (int(x) for x in str(d).split("/"))
    return (yy, mm, dd)


def fed_results(girone):
    """[(data, casa, ospite, punti casa, punti ospite)] delle partite giocate del girone (sito federale)."""
    from src.performance_model import load_calendar
    out = []
    for m in load_calendar(girone):
        if m.get("played") and m.get("pt_a"):
            out.append((m["date"], m["team1"]["title"].strip(), m["team2"]["title"].strip(),
                        sum(int(x) for x in m["pt_a"]), sum(int(x) for x in m["pt_b"])))
    return out


def fed_strength(results, before=None, lam=FED_LAM):
    """{squadra federale: forza} dai risultati con data < before (tutti se None)."""
    R = [r for r in results if before is None or _dkey(r[0]) < _dkey(before)]
    teams = sorted({t for r in R for t in r[1:3]})
    if not R:
        return {}
    ix = {t: i for i, t in enumerate(teams)}
    b = np.zeros(len(teams))
    for _ in range(30):
        g, H = -lam * b, -lam * np.eye(len(teams))
        for _, h, a, ph, pa in R:
            n, p = ph + pa, sig(b[ix[h]] - b[ix[a]])
            e = ph - n * p
            w = n * p * (1 - p)
            g[ix[h]] += e; g[ix[a]] -= e
            H[ix[h], ix[h]] -= w; H[ix[a], ix[a]] -= w; H[ix[h], ix[a]] += w; H[ix[a], ix[h]] += w
        b -= np.linalg.solve(H, g)
    return {t: float(b[ix[t]]) for t in teams}


def fed_title(name, titles, overrides):
    """Nome federale di una squadra dal nome del .dvw (come src.performance_model.team_seasons)."""
    if name in overrides:
        return overrides[name].strip()
    tokens = [t for t in name.split() if len(t) >= 3]
    hits = [t for t in titles if tokens and all(f" {tok}" in f" {t.lower()}" for tok in tokens)]
    return hits[0] if len(hits) == 1 else None


def rates(cur, prev, p0, K, W):
    """pSO, pBP di oggi: set in corso, set precedenti (peso W), solito (K scambi)."""
    pso = (cur["so"] + W * prev["so"] + K * p0[0]) / (cur["ric"] + W * prev["ric"] + K)
    pbp = (cur["bp"] + W * prev["bp"] + K * p0[1]) / (cur["bat"] + W * prev["bat"] + K)
    return pso, pbp


# ---------------------------------------------------------------------------- obiettivi (come live/core.js goalState)
def goal_values(rs, t):
    o = "a" if t == "*" else "*"
    c = lambda f: sum(1 for r in rs if f(r))
    A = lambda r, *k: r["action"] in k
    return {"bat": c(lambda r: r["actor"] == t and A(r, "ace")) - c(lambda r: r["actor"] == t and A(r, "err_bat", "err_battuta")),
            "a1": c(lambda r: r["actor"] == t and A(r, "att_pt_1")) - c(lambda r: r["actor"] == t and A(r, "err_att_1", "mur_1")),
            "ac": c(lambda r: r["actor"] == t and A(r, "att_pt_s")) - c(lambda r: r["actor"] == t and A(r, "err_att_s", "mur_s")),
            "muri": c(lambda r: r["actor"] == o and A(r, "mur_1", "mur_s")),
            "grat": c(lambda r: r["actor"] == t and A(r, "invasione", "altro"))}


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def goal_logit(rs, model, fifth):
    """logit della probabilità di vincere il set dagli obiettivi (nostri e loro), come pWin di goalState."""
    expected = model["scambi_set"] * (15 / 25 if fifth else 1)
    remaining = max(0.0, expected - len(rs))
    z = model["coef"]["b0"]
    for t, pre, ritmo in (("*", "", model["ritmo_tutti"]), ("a", "o_", model["ritmo_tutti"])):
        v = goal_values(rs, t)
        for k in GOAL_KEYS:
            thr, up = model["soglie"][k], k != "grat"
            if remaining <= 0:
                p = float(v[k] >= thr if up else v[k] <= thr)
            else:
                mu, sd = v[k] + ritmo[k]["m"] * remaining, math.sqrt(max(0.05, ritmo[k]["v"] * remaining))
                p = 1 - _phi((thr - 0.5 - mu) / sd) if up else _phi((thr + 0.5 - mu) / sd)
            z += model["coef"][pre + k] * p
    return z


# ---------------------------------------------------------------------------- stati dei set
def _unify(names):
    keys = sorted(set(names), key=len, reverse=True)
    return {k: next(o for o in keys if o == k or o.startswith(k + " ")) for k in keys}


def set_outcome(g, fifth):
    a, b = int((g["winner"] == "*").sum()), int((g["winner"] == "a").sum())
    T = 15 if fifth else 25
    if max(a, b) >= T and abs(a - b) >= 2:
        return a > b
    return None                                   # set non concluso nello scout


def build_states(matches, model, K, W, fed=None, fed_b=0.0, from_date=None):
    """Una riga per scambio (stato prima dello scambio) di ogni set completo, dal punto di vista di '*'.
    fed = (risultati federali, stagione dal, nomi forzati): la differenza di forza (sNoi − sLoro) dai risultati
    precedenti alla partita sposta di fed_b·diff il logit di pSO e pBP attesi; from_date: righe solo da quella data."""
    uni = _unify([n for m in matches for n in m["teams"].values()])
    order = sorted(range(len(matches)), key=lambda i: tuple(int(x) for x in reversed(matches[i]["data"].split("/"))))
    hist, league, rows = {}, {"ric": 0, "so": 0, "bat": 0, "bp": 0}, []
    for mi in order:
        m = matches[mi]
        r = m["rallies"]
        tk = {s: uni[n] for s, n in m["teams"].items()}

        def prior(team):
            c = {"ric": 0, "so": 0, "bat": 0, "bp": 0}
            for x in hist.get(team, [])[-N_PRIOR:]:
                for k in c:
                    c[k] += x[k]
            return c
        lg = league if league["ric"] > 500 else {"ric": 1000, "so": 590, "bat": 1000, "bp": 410}
        p0 = matchup(prior(tk["*"]), prior(tk["a"]), lg)
        dF = 0.0
        if fed is not None and _dkey(m["data"]) >= _dkey(fed[1]):
            st = fed_strength(fed[0], before=m["data"])
            titles = sorted({t for r in fed[0] for t in r[1:3]})
            ts = [fed_title(m["teams"][x], titles, fed[2]) for x in ("*", "a")]
            dF = (st.get(ts[0], 0.0) if ts[0] else 0.0) - (st.get(ts[1], 0.0) if ts[1] else 0.0)
            p0 = (sig(logit(p0[0]) + fed_b * dF), sig(logit(p0[1]) + fed_b * dF))
        emit = from_date is None or _dkey(m["data"]) >= _dkey(from_date)
        prev = {"ric": 0, "so": 0, "bat": 0, "bp": 0}
        for s, g in r.groupby("set", sort=True):
            g = g.sort_values("Rally")
            fifth = int(s) == 5
            y = set_outcome(g, fifth)
            recs = g.to_dict("records")
            if y is not None and emit:
                cur = {"ric": 0, "so": 0, "bat": 0, "bp": 0}
                a = b = 0
                for k, x in enumerate(recs):
                    serve_us = x["server"] == "*"
                    pso, pbp = rates(cur, prev, p0, K, W)
                    pm = markov(a, b, serve_us, pso, pbp, 15 if fifth else 25)
                    done = recs[:k]
                    err = sum(1 for d in done if d["action"] in ERRORS and d["actor"] == "*") - \
                        sum(1 for d in done if d["action"] in ERRORS and d["actor"] == "a")
                    last = recs[max(0, k - 6):k]
                    exp = model["scambi_set"] * (15 / 25 if fifth else 1)
                    rem = max(0.0, 1 - k / exp)
                    rows.append({"match": mi, "file": os.path.basename(m["path"]), "data": m["data"], "set": int(s),
                                 "k": k, "a": a, "b": b, "serve_us": serve_us, "y": int(y),
                                 "dF": dF, "decimo": "*" if "decimo" in tk["*"] else ("a" if "decimo" in tk["a"] else ""),
                                 "pso": pso, "pbp": pbp, "p_markov": pm,
                                 "markov": logit(pm),
                                 "obiettivi": goal_logit(done, model, fifth) * rem,
                                 "errori": err * rem,
                                 "ultimi": (sum(1 for d in last if d["winner"] == "*") - len(last) / 2) * rem,
                                 "p_solo_punteggio": markov(a, b, serve_us, lg["so"] / lg["ric"], lg["bp"] / lg["bat"], 15 if fifth else 25)})
                    w = x["winner"] == "*"
                    if serve_us:
                        cur["bat"] += 1; cur["bp"] += w
                    else:
                        cur["ric"] += 1; cur["so"] += w
                    a, b = a + w, b + (not w)
            for k2, v in team_counts(g, "*").items():
                prev[k2] += v
        for side in ("*", "a"):
            hist.setdefault(tk[side], []).append(team_counts(r, side))
        for side in ("*", "a"):                  # media del campionato su tutte e due le squadre (non solo chi gioca in casa)
            for k2, v in team_counts(r, side).items():
                league[k2] += v
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------- stima e valutazione
def fit(X, y, l2=1.0):
    """Logistica senza intercetta (Newton con un po' di penalità)."""
    b = np.zeros(X.shape[1])
    for _ in range(50):
        p = 1 / (1 + np.exp(-X @ b))
        H = X.T @ (X * (p * (1 - p))[:, None]) + l2 * np.eye(X.shape[1])
        b -= np.linalg.solve(H, X.T @ (p - y) + l2 * b)
    return b


def cv_predict(S, feats, folds=10):
    groups = S["match"].unique()
    rng = np.random.RandomState(0)
    rng.shuffle(groups)
    fold = {g: i % folds for i, g in enumerate(groups)}
    f = S["match"].map(fold).values
    out = np.zeros(len(S))
    X, y = S[feats].values, S["y"].values
    for i in range(folds):
        tr, te = f != i, f == i
        b = fit(X[tr], y[tr])
        out[te] = 1 / (1 + np.exp(-X[te] @ b))
    return out


def scores(p, y):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return {"logloss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
            "brier": float(np.mean((p - y) ** 2)), "acc": float(np.mean((p > 0.5) == y))}


def load(rebuild=False):
    if rebuild or not os.path.exists(DATA):
        raise SystemExit("rifare i dati: vedi /tmp/build_rallies.py (catalogo di src.live_reference)")
    with open(DATA, "rb") as f:
        return pickle.load(f)


def prob_set_params(league):
    """Parametri per l'app live (riferimenti.json): K, W, PRIOR_N e i totali del campionato."""
    return {"K": K_BEST, "W": W_BEST, "prior_n": PRIOR_N, "lega": {k: int(league[k]) for k in ("ric", "so", "bat", "bp")}}


def main():
    """Valutazione sulle partite già giocate (--tune: rifà la scelta di K e W)."""
    import warnings
    warnings.filterwarnings("ignore")
    matches = load()
    model = json.load(open(os.path.join(REPO, "live", "riferimenti.json"), encoding="utf-8"))["modello"]
    K, W = K_BEST, W_BEST
    if "--tune" in sys.argv:
        best = None
        for k in (15, 30, 60, 100, 200):
            for w in (0.0, 0.5, 1.0, 1.5):
                sc = scores(*(lambda S: (S["p_markov"].values, S["y"].values))(build_states(matches, model, k, w)))
                print(f"K={k} W={w}: {sc}", flush=True)
                if best is None or sc["logloss"] < best[0]:
                    best = (sc["logloss"], k, w)
        K, W = best[1], best[2]
    S = build_states(matches, model, K, W)
    S.to_pickle(os.path.join(REPO, "data", "analisi", "set_prob_states.pkl"))
    y = S["y"].values
    print(f"K={K} W={W}, {S.groupby(['match', 'set']).ngroups} set, {len(S)} scambi")
    print("solo punteggio:", scores(S["p_solo_punteggio"].values, y))
    print("modello:       ", scores(S["p_markov"].values, y))
    for name, feats in [("+ obiettivi", ["markov", "obiettivi"]), ("+ errori", ["markov", "errori"]),
                        ("+ ultimi", ["markov", "ultimi"]), ("tutto", FEATURES)]:
        print(f"{name:15s}", scores(cv_predict(S, feats), y))


if __name__ == "__main__":
    main()
