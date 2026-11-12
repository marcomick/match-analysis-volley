# src/performance_model.py
"""
Modello bayesiano di prestazione della Serie D (richiesta dell'utente del
2026-11-06): cosa allenare di più e quanto rischiare nelle varie situazioni.

Unità di misura: la probabilità di vincere lo scambio. Ogni esito di una
battuta o di un attacco vale la quota di scambi che la squadra vince dopo
quell'esito (campionato intero); un giocatore o una squadra valgono la media
dei loro esiti pesati per quei valori. 1 scambio = 1 punto.

Dati: tutti i .dvw di campionato nelle cartelle delle stagioni di
config/performance_model.json (anche le partite tra altre squadre), dalla
data di inizio del campionato (le amichevoli di settembre restano fuori), un
solo file per partita (stessa data e stesse squadre: il più grande), esclusi
i file con una squadra senza nome. Il tipo di palla non serve: la situazione
lo fissa (veloce da R#, R+ e R!, alta da R-, come `_infer_ball_type`). La
difesa entra solo come free ball (D!).

Squadre: una per stagione (chiave 'stagione|NOME FEDERALE'), con effetti =
forza nei risultati del girone (sito federale) x coefficiente + società
(comune alle stagioni, `stesso_gruppo` in config: gruppi con qualche
giocatore in comune, indicazione dell'utente del 2026-11-07) + scostamento
della stagione. Giocatori = società + cognome.

Tre modelli gerarchici (NumPyro, NUTS):

1. Attacco: esito (#, +, !, -, =, /) come categorica con logit =
   situazione x zona + squadra che attacca x situazione + giocatore +
   squadra che difende + finale di set (comune + per giocatore, con una
   prior stretta: "nei finali nessuno rende diversamente dal solito" finché i
   dati non dicono altro). Situazioni: side-out da R#/R+, da R!, da R-, free
   ball, altro contrattacco.
2. Battuta: errore (logistica: squadra + battitore) e, se in gioco, voto della
   ricezione (=, /, -, !, +, #: squadra e battitore che servono, squadra e
   giocatore che ricevono, finale di set).
3. Transizioni, per coppia di squadre: chi vince lo scambio dopo un attacco
   rimasto in gioco (squadra che attacca e squadra che difende) e dopo una
   ricezione con un certo voto (squadra che batte; squadra che riceve x
   gruppo di voti). Da qui le indicazioni contro un avversario: battere forte
   o sicuro, forzare o no l'attacco, budget di errori per vincere i set
   (`opponent_strategies`, set simulati punto per punto con `p_set`).

Le squadre e i giocatori con pochi dati restano vicini alla media (del
campionato o della zona) finché i dati non dicono altro. I valori degli
esiti sono stimati a parte con una Beta per ogni esito (dati abbondanti).

`fit_and_save()` stima i tre modelli e salva i riassunti in
data/performance_model/risultati.pkl (fuori dal repo), letti dalla dashboard
(tab "Modello di prestazione"). Da terminale: python -m src.performance_model
"""
import glob
import json
import os
import pickle
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import numpyro

numpyro.set_host_device_count(2)  # catene in parallelo: va fatto prima di importare jax
import jax  # noqa: E402
import jax.numpy as jnp  # noqa: E402
import numpyro.distributions as dist  # noqa: E402
from numpyro.diagnostics import summary as mcmc_summary  # noqa: E402
from numpyro.infer import MCMC, NUTS  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from config.paths import build_base_path  # noqa: E402
from src.dvw import normalize_team_name, parse_dvw, read_dvw_header, role_codes  # noqa: E402
from src.match_report import side_actions  # noqa: E402
from src.setter_distribution import attack_rows  # noqa: E402

CONFIG_PATH = REPO / "config" / "performance_model.json"
RESULTS_PATH = REPO / "data" / "performance_model" / "risultati.pkl"

# esiti dell'attacco; il riferimento del logit è "-" (attacco difeso facile)
ATTACK_OUTCOMES = ["#", "+", "!", "-", "=", "/"]
ATTACK_OUTCOME_LABELS = {"#": "Punto", "+": "In gioco, difesa in difficoltà (+)", "!": "Coperto (!)",
                         "-": "Difeso facile (-)", "=": "Errore", "/": "Murato"}
ATTACK_REF = ATTACK_OUTCOMES.index("-")
# situazioni dell'attacco (la situazione fissa anche il tipo di palla)
SITUATIONS = ["R#+", "R!", "R-", "FB", "CA"]
SITUATION_LABELS = {
    "R#+": "Side-out da ricezione # o + (palla veloce)",
    "R!": "Side-out da ricezione ! (palla veloce)",
    "R-": "Side-out da ricezione - (palla alta)",
    "FB": "Free ball (D!)",
    "CA": "Contrattacco",
}
ZONES = [4, 3, 2, 8, 9]
ZONE_LABELS = {4: "posto 4", 3: "centro", 2: "posto 2", 8: "seconda linea (pipe)", 9: "seconda linea da 1"}

# esiti della battuta in gioco = voto della ricezione avversaria; riferimento "#"
SERVE_GRADES = ["=", "/", "-", "!", "+", "#"]
SERVE_GRADE_LABELS = {"=": "Ace", "/": "Ricezione / (palla rimandata di qua)", "-": "Ricezione -",
                      "!": "Ricezione !", "+": "Ricezione +", "#": "Ricezione #"}
SERVE_REF = SERVE_GRADES.index("#")

INTERVAL = (10, 90)  # intervallo di credibilità all'80%


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def _date_key(d):
    try:
        g, m, a = (int(x) for x in str(d).split("/"))
        return (a, m, g)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Dati
# ---------------------------------------------------------------------------
def match_catalog(config=None):
    """Le partite di campionato da usare: [{path, stagione, data, squadre}],
    più l'elenco dei file scartati con il motivo."""
    config = config or load_config()
    entries, skipped = [], []
    for season in config["stagioni"]:
        start = _date_key(config["inizio_campionato"][season])
        for path in sorted(glob.glob(str(build_base_path(season) / "**" / "*.dvw"), recursive=True)):
            name = os.path.basename(path)
            try:
                h = read_dvw_header(path)
            except Exception as e:  # file illeggibile: segnalato
                skipped.append((name, f"illeggibile: {e}"))
                continue
            teams = [n for n in h["teams"].values() if n]
            date = _date_key(h["data"])
            if len(teams) < 2:
                skipped.append((name, "una squadra senza nome"))
            elif date is None or date < start:
                skipped.append((name, "prima dell'inizio del campionato (amichevole)"))
            else:
                entries.append({"path": path, "file": name, "stagione": season, "data": h["data"],
                                "squadre": tuple(sorted(normalize_team_name(n) for n in teams)),
                                "size": os.path.getsize(path)})
    # stessa partita in più file (anche in stagioni diverse): il più grande
    keep = {}
    for e in entries:
        key = (e["data"], e["squadre"])
        if key not in keep or e["size"] > keep[key]["size"]:
            keep[key] = e
    kept = {id(e) for e in keep.values()}
    for e in entries:
        if id(e) not in kept:
            skipped.append((e["file"], f"doppione di {keep[(e['data'], e['squadre'])]['file']}"))
    return sorted(keep.values(), key=lambda e: _date_key(e["data"])), skipped


def _is_final(points_a, points_b, set_number):
    """Finale di set: una squadra a 20 (10 al quinto) e scarto al più 3,
    punteggio prima dell'azione."""
    threshold = 10 if int(set_number) % 100 == 5 else 20
    return max(points_a, points_b) >= threshold and abs(points_a - points_b) <= 3


def _player_key(team, cognome):
    """Giocatore = squadra + cognome (gli apostrofi tipografici dello scout
    unificati: D’Arienzo e D'Arienzo sono la stessa persona)."""
    name = str(cognome).strip().replace("\u2019", "'")
    return f"{team}|{name}"


def _attack_situation(row):
    if row["free_ball"]:
        return "FB"
    if row["fase"] == "Side-out":
        return {"#": "R#+", "+": "R#+", "!": "R!", "-": "R-"}.get(row["ricezione"])
    return "CA"


def _receiver_line(row, prefix):
    """'davanti' se chi riceve è in P2-P4, 'dietro' in P1/P5/P6, 'libero' se non
    è tra i sei della rotazione (il libero prende il posto di un centrale)."""
    try:
        num = int(row["Numero"])
    except (TypeError, ValueError):
        return None
    pos = {int(row[f"{prefix} P{k}"]): k for k in range(1, 7) if pd.notna(row.get(f"{prefix} P{k}"))}
    if num not in pos:
        return "libero"
    return "davanti" if pos[num] in (2, 3, 4) else "dietro"


def build_dataset(config=None, progress=None):
    """
    (attacchi, battute, partite, scartati, rose). Attacchi: uno per riga (esclusi
    palleggiatori e seconde intenzioni, come attack_rows), con situazione,
    zona, esito, squadra, avversario, giocatore, finale, scambio vinto.
    Battute: una per riga con esito ('err' o voto della ricezione), battitore,
    ricevitore, squadre, finale, scambio vinto. `partite`: set giocati da ogni
    squadra (per i volumi per set). `rose`: numero e sigla di ruolo di ogni
    giocatore in ogni partita (role_codes).
    """
    catalog, skipped = match_catalog(config)
    attacks, serves, sets_played, rosters = [], [], [], []
    for i, m in enumerate(catalog):
        if progress:
            progress(i, len(catalog), m["file"])
        P = parse_dvw(m["path"])
        winner = P["rallies"].set_index("Rally")["vincitore"].to_dict()
        actions = {s: side_actions(P, s) for s in ("*", "a")}
        names = {s: normalize_team_name(P["teams"][s]) for s in ("*", "a")}
        for side, other in (("*", "a"), ("a", "*")):
            team, opp = names[side], names[other]
            match_id = f"{m['data']} {names['*']} - {names['a']}"
            for s_ in actions[side]["Numero Set"].dropna().unique():
                sets_played.append({"partita": match_id, "stagione": m["stagione"], "team": team, "set": int(s_)})
            for cognome, (numero, code) in role_codes(P["players"], side).items():
                rosters.append({"stagione": m["stagione"], "team": team, "data": _date_key(m["data"]),
                                "cognome": cognome, "numero": numero, "sigla": code})

            ar = attack_rows(P, side, match_id)
            if len(ar):
                ar = ar[ar["voto"].isin(ATTACK_OUTCOMES) & ar["zona"].isin(ZONES)].copy()
                ar["situazione"] = ar.apply(_attack_situation, axis=1)
                ar = ar[ar["situazione"].notna()]
                for r in ar.itertuples():
                    attacks.append({
                        "partita": match_id, "stagione": m["stagione"], "set": int(r.set), "rally": r.rally,
                        "team": team, "opp": opp, "giocatore": _player_key(team, str(r.attaccante).split(" ", 1)[-1]),
                        "situazione": r.situazione, "zona": int(r.zona), "esito": r.voto,
                        "finale": _is_final(r.punti_noi, r.punti_loro, r.set), "vinto": winner.get(r.rally) == side,
                        "numero": str(r.attaccante).split(" ", 1)[0], "rotazione": r.rotazione,
                        "palleggiatore": str(r.palleggiatore).replace("\u2019", "'"), "ricezione": r.ricezione,
                    })

            d, od = actions[side], actions[other]
            own_pts, opp_pts = ("Punti Locali", "Punti Ospiti") if side == "*" else ("Punti Ospiti", "Punti Locali")
            receptions = od[od["Tipo"] == "ricezione"].drop_duplicates("Rally").set_index("Rally")
            rot = "Casa" if other == "*" else "Ospiti"
            for b in d[d["Tipo"] == "battuta"].drop_duplicates("Rally").to_dict("records"):
                rally = b["Rally"]
                receiver, line = None, None
                if b["Voto"] == "=":
                    outcome = "err"
                elif rally in receptions.index and receptions.loc[rally, "Voto"] in SERVE_GRADES:
                    outcome = receptions.loc[rally, "Voto"]
                    receiver = _player_key(opp, receptions.loc[rally, "Cognome"])
                    line = _receiver_line(receptions.loc[rally], rot)
                elif b["Voto"] == "#":
                    outcome = "="  # ace senza ricezione rilevata
                else:
                    continue
                serves.append({
                    "partita": match_id, "stagione": m["stagione"], "set": int(b["Numero Set"]), "rally": rally,
                    "team": team, "opp": opp, "giocatore": _player_key(team, b["Cognome"]),
                    "ricevitore": receiver, "linea_ricevitore": line, "esito": outcome,
                    "finale": _is_final(int(b[own_pts]), int(b[opp_pts]), b["Numero Set"]),
                    "vinto": winner.get(rally) == side,
                })
    return (pd.DataFrame(attacks), pd.DataFrame(serves), pd.DataFrame(sets_played).drop_duplicates(), skipped,
            pd.DataFrame(rosters))


# ---------------------------------------------------------------------------
# Squadre: una per stagione, legata alla società della stagione prima e
# con la forza misurata sui risultati del girone (sito federale)
# ---------------------------------------------------------------------------
CALENDAR_CACHE = REPO / "data" / "performance_model"


def load_calendar(girone):
    """Calendario del girone dal sito federale; copia locale se il sito non
    risponde (data/performance_model/calendario_<girone>.json)."""
    from src.fipav import fetch_calendar
    cache = CALENDAR_CACHE / f"calendario_{girone}.json"
    try:
        matches = fetch_calendar(girone)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(matches))
    except Exception:
        if not cache.exists():
            raise
        matches = json.loads(cache.read_text())
    return matches


def standings(matches):
    """Classifica del girone dai risultati (3 punti per 3-0/3-1, 2 e 1 per
    il 3-2), con punti fatti/subiti nei set. DataFrame per squadra."""
    rows = {}
    for m in matches:
        if not m.get("played") or not m.get("pt_a"):
            continue
        home, away = m["team1"]["title"].strip(), m["team2"]["title"].strip()
        sh, sa = int(m["team1-setwin"]), int(m["team2-setwin"])
        ph, pa = sum(int(x) for x in m["pt_a"]), sum(int(x) for x in m["pt_b"])
        for t, s1, s2, p1, p2 in ((home, sh, sa, ph, pa), (away, sa, sh, pa, ph)):
            r = rows.setdefault(t, {"squadra": t, "giocate": 0, "punti": 0, "vinte": 0, "set_vinti": 0,
                                    "set_persi": 0, "punti_fatti": 0, "punti_subiti": 0})
            r["giocate"] += 1; r["set_vinti"] += s1; r["set_persi"] += s2
            r["punti_fatti"] += p1; r["punti_subiti"] += p2
            if s1 > s2:
                r["vinte"] += 1; r["punti"] += 3 if s2 <= 1 else 2
            else:
                r["punti"] += 1 if s1 == 2 else 0
    df = pd.DataFrame(rows.values())
    df = df.sort_values(["punti", "vinte", "set_vinti"], ascending=False).reset_index(drop=True)
    df["posizione"] = np.arange(1, len(df) + 1)
    return df


def display_name(fipav_title):
    """'TVL ANGUILLARA' → 'TVL Anguillara' (sigle senza vocali e con i punti in
    maiuscolo, il resto con l'iniziale maiuscola)."""
    def word(w):
        acronym = w.isalpha() and not any(c in "AEIOU" for c in w)
        return w if acronym or "." in w or any(c.isdigit() for c in w) else w.capitalize()
    return " ".join(word(w) for w in str(fipav_title).split())


def team_seasons(config, dvw_names_by_season):
    """
    Tabella delle squadre-stagione: chiave 'stagione|NOME FEDERALE', nome per
    la dashboard, gruppo (società: stessa chiave della stagione in cui
    compare per la prima volta, vedi `stesso_gruppo`), forza dai risultati
    (z: logit della quota di punti fatti nei set, standardizzato su tutte le
    squadre-stagione), classifica. Più {(stagione, nome nel .dvw): chiave}
    e i nomi dei .dvw non abbinati al girone.
    """
    rows, name_map, unmatched = [], {}, []
    for season in config["stagioni"]:
        st_df = standings(load_calendar(config["gironi_fipav"][season]))
        titles = st_df["squadra"].tolist()
        for r in st_df.itertuples():
            rows.append({"chiave": f"{season}|{r.squadra}", "stagione": season, "fipav": r.squadra,
                         "nome": display_name(r.squadra), "posizione": r.posizione, "punti": r.punti,
                         "giocate": r.giocate, "punti_fatti": r.punti_fatti, "punti_subiti": r.punti_subiti})
        overrides = config.get("nomi_fipav", {}).get(season, {})
        for name in dvw_names_by_season.get(season, []):
            if name in overrides:
                title = overrides[name]
            else:
                tokens = [t for t in name.split() if len(t) >= 3]
                hits = [t for t in titles if tokens and all(f" {tok}" in f" {t.lower()}" for tok in tokens)]
                title = hits[0] if len(hits) == 1 else None
            if title is None:
                unmatched.append((season, name))
                title = name.upper()
                rows.append({"chiave": f"{season}|{title}", "stagione": season, "fipav": title,
                             "nome": display_name(title), "posizione": None, "punti": None, "giocate": 0,
                             "punti_fatti": 0, "punti_subiti": 0})
            name_map[(season, name)] = f"{season}|{title}"
    ts = pd.DataFrame(rows).drop_duplicates("chiave").reset_index(drop=True)
    share = (ts["punti_fatti"] + 250) / (ts["punti_fatti"] + ts["punti_subiti"] + 500)  # prior: 500 punti alla pari
    logit = np.log(share / (1 - share))
    ts["z"] = (logit - logit.mean()) / logit.std()
    # società: si risale la catena di `stesso_gruppo` fino alla prima stagione
    links = {}
    for season, mapping in config.get("stesso_gruppo", {}).items():
        prev = config["stagioni"][config["stagioni"].index(season) - 1]
        for title, prev_title in mapping.items():
            links[f"{season}|{title}"] = f"{prev}|{prev_title}"

    def root(key):
        while key in links:
            key = links[key]
        return key
    ts["gruppo"] = ts["chiave"].map(root)
    ts.attrs["nomi_dvw"] = {f"{s}|{n}": k for (s, n), k in name_map.items()}
    return ts, name_map, unmatched


def attach_teams(attacks, serves, sets_played, config, rosters=None):
    """Squadre dei .dvw → squadre-stagione; giocatori = società + cognome
    (chi resta nella stessa società da una stagione all'altra porta con sé i
    suoi dati)."""
    names = {}
    for df in (attacks, serves, sets_played):
        for season, part in df.groupby("stagione"):
            cols = [c for c in ("team", "opp") if c in part]
            names.setdefault(season, set()).update(*(set(part[c]) for c in cols))
    ts, name_map, unmatched = team_seasons(config, {k: sorted(v) for k, v in names.items()})
    group = ts.set_index("chiave")["gruppo"].to_dict()

    def remap(df, player_cols):
        df = df.copy()
        for c in ("team", "opp"):
            if c in df:
                df[c] = [name_map[(s, n)] for s, n in zip(df["stagione"], df[c])]
        for c, team_col in player_cols:
            df[c] = [None if p is None or (isinstance(p, float) and np.isnan(p))
                     else f"{group[t]}|{str(p).split('|', 1)[1]}" for p, t in zip(df[c], df[team_col])]
        return df
    attacks = remap(attacks, [("giocatore", "team")])
    serves = remap(serves, [("giocatore", "team"), ("ricevitore", "opp")])
    sets_played = remap(sets_played, [])
    if rosters is not None and len(rosters):
        rosters = rosters[[(s, n) in name_map for s, n in zip(rosters["stagione"], rosters["team"])]]
        rosters = remap(rosters, [])
    return attacks, serves, sets_played, ts, unmatched, rosters


# ---------------------------------------------------------------------------
# Modelli
# ---------------------------------------------------------------------------
def _codes(values, categories=None):
    cat = pd.Categorical(values, categories=categories)
    return np.asarray(cat.codes), list(cat.categories)


def _insert_ref(eta_free, ref):
    """Logit con la categoria di riferimento a 0 (identificabilità)."""
    zeros = jnp.zeros(eta_free.shape[:-1] + (1,))
    return jnp.concatenate([eta_free[..., :ref], zeros, eta_free[..., ref:]], axis=-1)


def _scaled(name, shape, scale):
    """Effetti casuali non centrati: raw ~ N(0, 1) per una deviazione
    standard comune (HalfNormal(scale)) per ogni categoria (ultima dimensione)."""
    sd = numpyro.sample(f"sd_{name}", dist.HalfNormal(scale).expand([shape[-1]]).to_event(1))
    raw = numpyro.sample(f"{name}_raw", dist.Normal(0, 1).expand(list(shape)).to_event(len(shape)))
    return numpyro.deterministic(name, raw * sd)


def _team_effect(name, teams, shape_tail, scale=0.5):
    """Effetto di squadra-stagione = forza dai risultati (z) x coefficiente
    + società (comune alle stagioni) + scostamento della stagione. Quanto
    pesa la stagione prima lo decidono i dati (rapporto tra le due deviazioni
    standard). `teams` = dict con z (array), gruppo (indici), n_gruppi."""
    shape_tail = tuple(shape_tail)
    beta = numpyro.sample(f"beta_{name}", dist.Normal(0, 0.5).expand(list(shape_tail)).to_event(len(shape_tail)))
    club = _scaled(f"{name}_societa", (teams["n_gruppi"],) + shape_tail, scale)
    season = _scaled(f"{name}_stagione", (len(teams["z"]),) + shape_tail, scale)
    z = teams["z"].reshape((-1,) + (1,) * len(shape_tail))
    return numpyro.deterministic(name, beta * z + club[teams["gruppo"]] + season)


# palla buona (side-out da R#/R+ e da R!, free ball) e palla difficile (side-out da R-, contrattacco): ogni
# attaccante ha un effetto suo più uno scostamento sulla palla difficile, così un punto di forza o un punto
# debole in una delle due situazioni si vede (richiesta dell'utente del 2026-11-11)
HARD_SITUATIONS = ("R-", "CA")
SIT_GROUP_LABELS = {0: "palla buona (R#/R+, R!, free ball)", 1: "palla difficile (R-, contrattacco)"}


def attack_model(teams, cell, sit, team, opp, player, final, n_cells, n_sit, n_players, hard, y=None):
    k = len(ATTACK_OUTCOMES) - 1
    mu = numpyro.sample("mu", dist.Normal(0, 1.5).expand([n_cells, k]).to_event(2))
    team_att = _team_effect("team_att", teams, (n_sit, k))
    team_def = _team_effect("team_def", teams, (k,))
    pl = _scaled("player", (n_players, k), 0.5)
    g_final = numpyro.sample("g_final", dist.Normal(0, 0.5).expand([k]).to_event(1))
    clutch = _scaled("clutch", (n_players, k), 0.2)
    pl_hard = _scaled("player_hard", (n_players, k), 0.3)
    eta = (mu[cell] + team_att[team, sit] + team_def[opp] + pl[player] + hard[:, None] * pl_hard[player]
           + final[:, None] * (g_final + clutch[player]))
    with numpyro.plate("attacchi", len(cell)):
        numpyro.sample("y", dist.Categorical(logits=_insert_ref(eta, ATTACK_REF)), obs=y)


def serve_model(teams, team, opp, server, receiver, final, front, inplay_idx, n_servers, n_receivers,
                err=None, grade=None):
    k = len(SERVE_GRADES) - 1
    a = numpyro.sample("a_err", dist.Normal(-2, 1))
    t_err = _team_effect("team_err", teams, (1,))[:, 0]
    s_err = _scaled("server_err", (n_servers, 1), 0.5)[:, 0]
    with numpyro.plate("battute", len(team)):
        numpyro.sample("err", dist.Bernoulli(logits=a + t_err[team] + s_err[server]), obs=err)
    b = numpyro.sample("b", dist.Normal(0, 1.5).expand([k]).to_event(1))
    t_srv = _team_effect("team_srv", teams, (k,))
    s_srv = _scaled("server_srv", (n_servers, k), 0.5)
    t_rec = _team_effect("team_rec", teams, (k,))
    r_rec_raw = _scaled("receiver_rec_raw", (n_receivers, k), 0.5)
    r_rec = jnp.concatenate([jnp.zeros((1, k)), r_rec_raw], axis=0)  # 0 = ricevitore ignoto
    g_final = numpyro.sample("g_final_srv", dist.Normal(0, 0.5).expand([k]).to_event(1))
    # ricevitore in prima linea (P2-P4): effetto comune più uno scostamento per ricevitore
    g_front = numpyro.sample("g_front", dist.Normal(0, 0.5).expand([k]).to_event(1))
    r_front_raw = _scaled("receiver_front_raw", (n_receivers, k), 0.3)
    r_front = jnp.concatenate([jnp.zeros((1, k)), r_front_raw], axis=0)
    i = inplay_idx
    eta = (b + t_srv[team[i]] + s_srv[server[i]] + t_rec[opp[i]] + r_rec[receiver[i]] + final[i][:, None] * g_final
           + front[i][:, None] * (g_front + r_front[receiver[i]]))
    with numpyro.plate("in_gioco", len(i)):
        numpyro.sample("grade", dist.Categorical(logits=_insert_ref(eta, SERVE_REF)), obs=grade)


# esiti dell'attacco "in gioco" e gruppi di voti di ricezione per i modelli di transizione
INPLAY = ["+", "!", "-"]
REC_GROUPS = {"#": 0, "+": 0, "!": 1, "-": 2, "/": 3}
REC_GROUP_LABELS = ["# o +", "!", "-", "/"]
W_GRADES = ["#", "+", "!", "-", "/"]


def transition_model(teams, c_cell, c_att, c_def, w_grade, w_grp, w_srv, w_rec, n_sit, c_y=None, w_y=None):
    """
    Due parti che dipendono dalla coppia di squadre:
    - continuazione: dopo un attacco rimasto in gioco (+, !, -), chi attacca
      vince lo scambio? logit = situazione x esito + squadra che attacca
      (muro e difesa sul contrattacco avversario) + squadra che difende (il suo
      contrattacco);
    - side-out per voto di ricezione: dopo una battuta in gioco con quel voto,
      chi batte vince lo scambio? logit = voto + squadra che batte (il suo
      break-point) + squadra che riceve x gruppo di voti (# o +, !, -, /): una
      squadra che fa side-out anche su ricezione scarsa ha un effetto basso
      sul gruppo "-".
    """
    c0 = numpyro.sample("c0", dist.Normal(0, 1).expand([n_sit * len(INPLAY)]).to_event(1))
    cont_att = _team_effect("cont_att", teams, (1,), 0.3)[:, 0]
    cont_def = _team_effect("cont_def", teams, (1,), 0.3)[:, 0]
    with numpyro.plate("in_gioco_attacco", len(c_cell)):
        numpyro.sample("c_y", dist.Bernoulli(logits=c0[c_cell] + cont_att[c_att] + cont_def[c_def]), obs=c_y)
    w0 = numpyro.sample("w0", dist.Normal(0, 1).expand([len(W_GRADES)]).to_event(1))
    bp_srv = _team_effect("bp_srv", teams, (1,), 0.3)[:, 0]
    rec_side = _team_effect("rec_side", teams, (len(REC_GROUP_LABELS),), 0.3)
    with numpyro.plate("battute_in_gioco", len(w_grade)):
        numpyro.sample("w_y", dist.Bernoulli(logits=w0[w_grade] + bp_srv[w_srv] + rec_side[w_rec, w_grp]), obs=w_y)


def _run(model, kwargs, mcmc_cfg, keep):
    mcmc = MCMC(NUTS(model, target_accept_prob=0.85), num_warmup=mcmc_cfg["warmup"],
                num_samples=mcmc_cfg["campioni"], num_chains=mcmc_cfg["catene"],
                chain_method="parallel", progress_bar=False)
    mcmc.run(jax.random.PRNGKey(mcmc_cfg["seed"]), **kwargs)
    by_chain = mcmc.get_samples(group_by_chain=True)
    diag = mcmc_summary({k: v for k, v in by_chain.items() if k in keep}, group_by_chain=True)
    rhat = max(float(np.nanmax(d["r_hat"])) for d in diag.values())
    ess = min(float(np.nanmin(d["n_eff"])) for d in diag.values())
    divergences = int(np.asarray(mcmc.get_extra_fields()["diverging"]).sum())
    samples = {k: np.asarray(v).reshape((-1,) + v.shape[2:]) for k, v in by_chain.items() if k in keep}
    return samples, {"r_hat_max": rhat, "ess_min": ess, "divergenze": divergences}


def _teams_arg(ts):
    gruppo, _ = _codes(ts["gruppo"])
    return {"z": jnp.asarray(ts["z"].to_numpy(float)), "gruppo": jnp.asarray(gruppo),
            "n_gruppi": int(gruppo.max()) + 1}


def fit_attack_model(attacks, mcmc_cfg, ts, players):
    teams = ts["chiave"].tolist()
    sit, _ = _codes(attacks["situazione"], SITUATIONS)
    zone, _ = _codes(attacks["zona"], ZONES)
    team, _ = _codes(attacks["team"], teams)
    opp, _ = _codes(attacks["opp"], teams)
    player, _ = _codes(attacks["giocatore"], players)
    y, _ = _codes(attacks["esito"], ATTACK_OUTCOMES)
    kwargs = dict(teams=_teams_arg(ts), cell=jnp.asarray(sit * len(ZONES) + zone), sit=jnp.asarray(sit),
                  team=jnp.asarray(team), opp=jnp.asarray(opp), player=jnp.asarray(player),
                  final=jnp.asarray(attacks["finale"].to_numpy(float)), n_cells=len(SITUATIONS) * len(ZONES),
                  n_sit=len(SITUATIONS), n_players=len(players),
                  hard=jnp.asarray(attacks["situazione"].isin(HARD_SITUATIONS).to_numpy(float)), y=jnp.asarray(y))
    keep = {"mu", "team_att", "team_def", "player", "g_final", "clutch", "player_hard"}
    return _run(attack_model, kwargs, mcmc_cfg, keep)


def fit_serve_model(serves, mcmc_cfg, ts, servers, receivers):
    teams = ts["chiave"].tolist()
    team, _ = _codes(serves["team"], teams)
    opp, _ = _codes(serves["opp"], teams)
    server, _ = _codes(serves["giocatore"], servers)
    rec_codes, _ = _codes(serves["ricevitore"], receivers)
    receiver = np.where(rec_codes < 0, 0, rec_codes + 1)
    is_err = (serves["esito"] == "err").to_numpy()
    inplay = np.flatnonzero(~is_err)
    grade, _ = _codes(serves["esito"].iloc[inplay], SERVE_GRADES)
    kwargs = dict(teams=_teams_arg(ts), team=jnp.asarray(team), opp=jnp.asarray(opp), server=jnp.asarray(server),
                  receiver=jnp.asarray(receiver), final=jnp.asarray(serves["finale"].to_numpy(float)),
                  front=jnp.asarray((serves["linea_ricevitore"] == "davanti").to_numpy(float)),
                  inplay_idx=jnp.asarray(inplay), n_servers=len(servers), n_receivers=len(receivers),
                  err=jnp.asarray(is_err.astype(int)), grade=jnp.asarray(grade))
    keep = {"a_err", "team_err", "server_err", "b", "team_srv", "server_srv", "team_rec", "receiver_rec_raw",
            "g_final_srv", "g_front", "receiver_front_raw"}
    return _run(serve_model, kwargs, mcmc_cfg, keep)


def fit_transition_model(attacks, serves, mcmc_cfg, ts):
    teams = ts["chiave"].tolist()
    a = attacks[attacks["esito"].isin(INPLAY)]
    sit, _ = _codes(a["situazione"], SITUATIONS)
    out, _ = _codes(a["esito"], INPLAY)
    c_att, _ = _codes(a["team"], teams)
    c_def, _ = _codes(a["opp"], teams)
    w = serves[serves["esito"].isin(W_GRADES)]
    w_grade, _ = _codes(w["esito"], W_GRADES)
    w_srv, _ = _codes(w["team"], teams)
    w_rec, _ = _codes(w["opp"], teams)
    kwargs = dict(teams=_teams_arg(ts), c_cell=jnp.asarray(sit * len(INPLAY) + out), c_att=jnp.asarray(c_att),
                  c_def=jnp.asarray(c_def), w_grade=jnp.asarray(w_grade),
                  w_grp=jnp.asarray(w["esito"].map(REC_GROUPS).to_numpy()), w_srv=jnp.asarray(w_srv),
                  w_rec=jnp.asarray(w_rec), n_sit=len(SITUATIONS),
                  c_y=jnp.asarray(a["vinto"].to_numpy(int)), w_y=jnp.asarray(w["vinto"].to_numpy(int)))
    keep = {"c0", "cont_att", "cont_def", "w0", "bp_srv", "rec_side", "sd_rec_side_stagione",
            "sd_rec_side_societa", "beta_rec_side"}
    return _run(transition_model, kwargs, mcmc_cfg, keep)


# ---------------------------------------------------------------------------
# Riassunti
# ---------------------------------------------------------------------------
def _softmax_ref(eta_free, ref):
    zeros = np.zeros(eta_free.shape[:-1] + (1,))
    eta = np.concatenate([eta_free[..., :ref], zeros, eta_free[..., ref:]], axis=-1)
    eta = eta - eta.max(axis=-1, keepdims=True)
    e = np.exp(eta)
    return e / e.sum(axis=-1, keepdims=True)


def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


def _stats(draws, prefix=""):
    lo, hi = np.percentile(draws, INTERVAL)
    return {f"{prefix}media": float(np.mean(draws)), f"{prefix}basso": float(lo), f"{prefix}alto": float(hi)}


def outcome_values(df, group_col, outcomes, n_draws, seed):
    """Valore di ogni esito = probabilità che la squadra vinca lo scambio dopo
    quell'esito, per gruppo (Beta(1 + vinti, 1 + persi)): draws
    {gruppo: array (n_draws, esiti)} e tabella con frequenze e intervalli."""
    rng = np.random.default_rng(seed)
    draws, rows = {}, []
    for g, part in (df.groupby(group_col) if group_col else [(None, df)]):
        arr = np.zeros((n_draws, len(outcomes)))
        for j, o in enumerate(outcomes):
            x = part[part["esito"] == o]["vinto"]
            arr[:, j] = rng.beta(1 + x.sum(), 1 + len(x) - x.sum(), n_draws)
            rows.append({"gruppo": g, "esito": o, "n": len(x), "quota": len(x) / len(part), **_stats(arr[:, j])})
        draws[g] = arr
    return draws, pd.DataFrame(rows)


def p_set(so_us, so_opp, target=25):
    """Probabilità di vincere un set a `target` (scarto 2) con side-out
    costanti delle due squadre, metà dei set iniziati in battuta."""
    p_srv, p_rec = 1 - so_opp, so_us  # scambio vinto quando battiamo / quando riceviamo
    # ai vantaggi (pari, da target-1 in poi): A = pari e battiamo noi, B = pari e battono loro
    m = np.array([[1 - (1 - p_srv) * p_rec, -p_srv * (1 - p_srv)],
                  [-(1 - p_rec) * p_rec, 1 - p_rec * (1 - p_srv)]])
    A, B = np.linalg.solve(m, np.array([p_srv * p_srv, p_rec * p_srv]))
    memo = {}

    def f(a, b, serve_us):
        if a >= target and a - b >= 2:
            return 1.0
        if b >= target and b - a >= 2:
            return 0.0
        if a == b and a >= target - 1:
            return A if serve_us else B
        key = (a, b, serve_us)
        if key not in memo:
            p = p_srv if serve_us else p_rec
            memo[key] = p * f(a + 1, b, True) + (1 - p) * f(a, b + 1, False)
        return memo[key]
    return 0.5 * (f(0, 0, True) + f(0, 0, False))


def required_side_out(so_opp, prob):
    """Side-out che serve per vincere il set con probabilità `prob`."""
    lo, hi = 0.2, 0.95
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if p_set(mid, so_opp) < prob else (lo, mid)
    return (lo + hi) / 2


SET_TARGETS = [0.5, 0.6, 0.7]
ERROR_LEVELS = [0.05, 0.10, 0.15, 0.20, 0.25]


def summarize(attacks, serves, sets_played, ts, att_s, srv_s, tr_s, players, servers, receivers, config):
    teams = ts["chiave"].tolist()
    ti = {t: i for i, t in enumerate(teams)}
    pi = {p: i for i, p in enumerate(players)}
    current = config["stagioni"][-1]
    us = f"{current}|{config['squadra']}"
    if us not in ti:
        raise ValueError(f"Squadra {us} non trovata")
    u = ti[us]
    fam_us = ts.set_index("chiave").loc[us, "gruppo"]
    D = att_s["mu"].shape[0]
    seed = config["mcmc"]["seed"]
    scouted = sets_played.groupby("team")["partita"].nunique()
    comparable = [t for t in teams if scouted.get(t, 0) >= config["min_partite_squadra_confronto"]]
    comp_idx = np.array([ti[t] for t in comparable])
    ours_fam = ts.loc[ts["gruppo"] == fam_us, "chiave"].tolist()   # il Decimo in tutte le stagioni
    our_sets = len(sets_played[sets_played["team"].isin(ours_fam)])

    # --- valori degli esiti (campionato) ------------------------------------
    v_att, att_values = outcome_values(attacks, "situazione", ATTACK_OUTCOMES, D, seed)
    v_srv_d, srv_values = outcome_values(serves[serves["esito"] != "err"], None, SERVE_GRADES, D, seed + 1)
    w = v_srv_d[None]
    mu = att_s["mu"].reshape(D, len(SITUATIONS), len(ZONES), -1)

    def att_ev(s, eta_free):
        probs = _softmax_ref(eta_free, ATTACK_REF)
        v = v_att[SITUATIONS[s]].reshape((D,) + (1,) * (probs.ndim - 2) + (-1,))
        return (probs * v).sum(-1)

    a_err = srv_s["a_err"]
    t_err, s_err = srv_s["team_err"][..., 0], srv_s["server_err"][..., 0]
    b, t_srv, s_srv, t_rec = srv_s["b"], srv_s["team_srv"], srv_s["server_srv"], srv_s["team_rec"]
    r_rec = srv_s["receiver_rec_raw"]
    league_rec = 1 - (_softmax_ref(b, SERVE_REF) * w[0]).sum(-1)

    # --- battuta: battitori (ultimo anno di ciascuno) -----------------------
    out_of_team = excluded_players(config)
    srv_rows = []
    si = {p: i for i, p in enumerate(servers)}
    last_team = serves.sort_values("stagione").groupby("giocatore")["team"].last()
    counts = serves.groupby("giocatore").size()
    names = ts.set_index("chiave")["nome"]
    for p in servers:
        if counts.get(p, 0) < config["min_battute_giocatore"]:
            continue
        team = last_team[p]
        err = _sigmoid(a_err + t_err[:, ti[team]] + s_err[:, si[p]])
        q = _softmax_ref(b + t_srv[:, ti[team]] + s_srv[:, si[p]], SERVE_REF)
        inplay = (q * w[0]).sum(-1)
        part = serves[serves["giocatore"] == p]
        srv_rows.append({"giocatore": p.rsplit("|", 1)[1], "squadra": names[team],
                         "noi": p.startswith(fam_us + "|") and p.rsplit("|", 1)[1] not in out_of_team,
                         "n": len(part), "errori_oss": (part["esito"] == "err").mean(),
                         "ace_oss": (part["esito"] == "=").mean(), **_stats(err, "err_"),
                         **_stats(inplay, "in_gioco_"), **_stats((1 - err) * inplay, "totale_")})
    servers_df = pd.DataFrame(srv_rows)

    # --- attacco: nostri attaccanti per situazione e zona -------------------
    team_att, team_def, pl, pl_hard = att_s["team_att"], att_s["team_def"], att_s["player"], att_s["player_hard"]
    g_final, clutch = att_s["g_final"], att_s["clutch"]
    ours = attacks[attacks["team"].isin(ours_fam)]
    att_rows = []
    for s, sit in enumerate(SITUATIONS):
        part_s = ours[ours["situazione"] == sit]
        cand = part_s.groupby(["giocatore", "zona"]).size()
        cand = cand[cand >= config["min_attacchi_giocatore"]]
        cand = cand[[p.rsplit("|", 1)[1] not in out_of_team for p, _ in cand.index]]
        evs = {}
        for (p, z), n in cand.items():
            zi = ZONES.index(z)
            eta = mu[:, s, zi] + team_att[:, u, s] + pl[:, pi[p]] + (pl_hard[:, pi[p]] if sit in HARD_SITUATIONS else 0)
            eta_f = eta + g_final + clutch[:, pi[p]]
            ev, ev_f = att_ev(s, eta), att_ev(s, eta_f)
            probs = _softmax_ref(eta, ATTACK_REF).mean(0)
            pz = part_s[(part_s["giocatore"] == p) & (part_s["zona"] == z)]
            fin = pz[pz["finale"]]
            evs[(p, z)] = (ev, ev_f)
            att_rows.append({
                "situazione": sit, "giocatore": p.rsplit("|", 1)[1], "chiave": p, "zona": z, "n": int(n),
                "quota": n / len(part_s), "n_finale": len(fin),
                "quota_finale": len(fin) / max(1, part_s["finale"].sum()),
                **_stats(ev, "ev_"), **_stats(ev_f, "ev_fin_"),
                "ev_rif_zona": float(att_ev(s, mu[:, s, zi]).mean()),
                "p_meglio_in_finale": float((ev_f > ev).mean()),
                "punto": probs[0], "errore": probs[ATTACK_OUTCOMES.index("=")],
                "murato": probs[ATTACK_OUTCOMES.index("/")],
                "vinti_oss": pz["vinto"].mean(),
                # l'attaccante medio del Decimo nella stessa zona e situazione
                "punto_rif": float(_softmax_ref(mu[:, s, zi] + team_att[:, u, s], ATTACK_REF).mean(0)[0]),
                "errmur_rif": float(_softmax_ref(mu[:, s, zi] + team_att[:, u, s], ATTACK_REF)
                                    .mean(0)[[ATTACK_OUTCOMES.index("="), ATTACK_OUTCOMES.index("/")]].sum()),
            })
        if evs:
            keys = list(evs)
            stack = np.stack([evs[k][0] for k in keys], axis=1)
            stack_f = np.stack([evs[k][1] for k in keys], axis=1)
            best = np.bincount(stack.argmax(1), minlength=len(keys)) / D
            best_f = np.bincount(stack_f.argmax(1), minlength=len(keys)) / D
            for row in att_rows:
                if row["situazione"] == sit:
                    j = keys.index((row["chiave"], row["zona"]))
                    row["p_migliore"], row["p_migliore_finale"] = float(best[j]), float(best_f[j])
    attackers_df = pd.DataFrame(att_rows)

    # --- cosa allenare: il Decimo di quest'anno contro le squadre più brave in
    # ogni area (primo quarto delle squadre-stagione con almeno 3 scout) -------
    serves_ours = serves[serves["team"].isin(ours_fam)]
    serves_vs = serves[(serves["opp"].isin(ours_fam)) & (serves["esito"] != "err")]
    per_set = {"battute": len(serves_ours) / our_sets, "ricezioni": len(serves_vs) / our_sets}
    err_t = _sigmoid(a_err[:, None] + t_err)
    inplay_t = (_softmax_ref(b[:, None] + t_srv, SERVE_REF) * w[:, None]).sum(-1)
    rec_t = 1 - (_softmax_ref(b[:, None] + t_rec, SERVE_REF) * w[:, None]).sum(-1)
    areas = []

    def add(area, fondamentale, gain, ours_val, top_val, league_val, unit, volume, nota):
        areas.append({"area": area, "fondamentale": fondamentale, **_stats(gain, "punti_set_"),
                      "p_guadagno": float((gain > 0).mean()), "noi": float(np.mean(ours_val)),
                      "quarto_migliore": float(np.mean(top_val)), "media_campionato": float(np.mean(league_val)),
                      "unita": unit, "volume_set": volume, "nota": nota})

    err_q = np.percentile(err_t[:, comp_idx], 25, axis=1)
    add("Battuta: meno errori", "Battuta", (err_t[:, u] - err_q) * inplay_t[:, u] * per_set["battute"],
        err_t[:, u], err_q, _sigmoid(a_err), "% errori", per_set["battute"],
        "errori in battuta come le squadre più brave, a parità di efficacia delle battute in gioco")
    inq = np.percentile(inplay_t[:, comp_idx], 75, axis=1)
    add("Battuta: più pressione", "Battuta", (inq - inplay_t[:, u]) * (1 - err_t[:, u]) * per_set["battute"],
        inplay_t[:, u], inq, (_softmax_ref(b, SERVE_REF) * w[0]).sum(-1), "% break sulle battute in gioco",
        per_set["battute"], "battute in gioco efficaci come le squadre più brave, a parità di errori")
    rq = np.percentile(rec_t[:, comp_idx], 75, axis=1)
    add("Ricezione", "Ricezione", (rq - rec_t[:, u]) * per_set["ricezioni"], rec_t[:, u], rq, league_rec,
        "% side-out sulle battute in gioco", per_set["ricezioni"],
        "ricezione come le squadre più brave (contro un battitore medio)")
    for s, sit in enumerate(SITUATIONS):
        part = ours[ours["situazione"] == sit]
        mix = np.array([(part["zona"] == z).mean() for z in ZONES])
        ev_t = sum(mix[zi] * att_ev(s, mu[:, s, zi][:, None] + team_att[:, :, s]) for zi in range(len(ZONES)))
        q75 = np.percentile(ev_t[:, comp_idx], 75, axis=1)
        league = sum(mix[zi] * att_ev(s, mu[:, s, zi]) for zi in range(len(ZONES)))
        vol = len(part) / our_sets
        add(f"Attacco: {SITUATION_LABELS[sit].split(' (')[0].lower()}", "Attacco", (q75 - ev_t[:, u]) * vol,
            ev_t[:, u], q75, league, "% scambi vinti", vol,
            "attacco come le squadre più brave in questa situazione, con la nostra distribuzione tra le zone")
    for label, long_label, sits in DEFENCE_GROUPS:
        gain_def, ours_def, top_def, league_def = np.zeros(D), np.zeros(D), np.zeros(D), np.zeros(D)
        opp_total = 0
        for sit in sits:
            s = SITUATIONS.index(sit)
            part = attacks[(attacks["opp"].isin(ours_fam)) & (attacks["situazione"] == sit)]
            if part.empty:
                continue
            mix = np.array([(part["zona"] == z).mean() for z in ZONES])
            ev_t = sum(mix[zi] * att_ev(s, mu[:, s, zi][:, None] + team_def) for zi in range(len(ZONES)))
            q25 = np.percentile(ev_t[:, comp_idx], 25, axis=1)
            vol = len(part) / our_sets
            gain_def += (ev_t[:, u] - q25) * vol
            ours_def += ev_t[:, u] * vol; top_def += q25 * vol
            league_def += sum(mix[zi] * att_ev(s, mu[:, s, zi]) for zi in range(len(ZONES))) * vol
            opp_total += vol
        add(f"Muro e difesa: {label}", "Muro e difesa", gain_def, ours_def / opp_total, top_def / opp_total,
            league_def / opp_total, "% scambi vinti dagli avversari", opp_total,
            f"muro, difesa e copertura {long_label} come le squadre più brave")
    areas_df = pd.DataFrame(areas).sort_values("punti_set_media", ascending=False)

    # --- ricezione e punti di forza / deboli dei giocatori --------------------
    playoff = playoff_teams(ts, sets_played, config, ti)
    reception = reception_analysis(serves, sets_played, srv_s, tr_s, ti, u, playoff, ours_fam, fam_us, config)
    profiles = player_profiles(attacks, serves, att_s, srv_s, v_srv_d[None], v_att, reception, players, servers, u,
                               ours_fam, fam_us, config)

    # --- contro un avversario -----------------------------------------------
    opponents = opponent_strategies(attacks, serves, ts, att_s, srv_s, tr_s, us, ours_fam, attackers_df, config)
    examples = practical_examples(serves, srv_values, srv_s, servers, ti, u, fam_us, opponents, sets_played,
                                  ours_fam, config, ts, attacks=attacks, att_s=att_s, att_values=att_values)

    return {
        "squadra": us, "aree": areas_df, "attaccanti": attackers_df, "battitori": servers_df, "esempi": examples,
        "ricezione": reception, "profili": profiles, "valori_attacco": att_values, "valori_battuta": srv_values,
        "set_giocati": our_sets, "squadre_confronto": [names[t] for t in comparable],
        "avversari": opponents, "squadre": ts,
    }


PLAYOFF_POSITIONS = 4   # zona playoff: dalla 1ª alla 4ª in classifica


def excluded_players(config):
    """Cognomi del Decimo esclusi dalle indicazioni per giocatore (restano nel
    modello e nei riferimenti): chi non fa più parte della squadra nella
    stagione in corso (`non_in_rosa` in config, indicazione dell'utente del
    2026-11-11) e chi è IGNORED in config/player_identities.csv."""
    out = set(config.get("non_in_rosa", {}).get(config["stagioni"][-1], []))
    ids = REPO / "config" / "player_identities.csv"
    if ids.exists():
        df = pd.read_csv(ids)
        out |= set(df.loc[(df["player_id"] == "IGNORED") & df["team"].str.lower().str.contains("decimo"), "cognome"])
    return {str(c).strip().replace("\u2019", "'") for c in out}


# muro e difesa divisi per la situazione dell'attacco avversario ("Cosa allenare")
DEFENCE_GROUPS = [("loro primo attacco (R#/R+, R!)", "sul loro primo attacco da ricezione buona", ["R#+", "R!"]),
                  ("loro palla alta (R-)", "sul loro attacco da palla alta dopo ricezione -", ["R-"]),
                  ("loro contrattacco e free ball", "sul loro contrattacco e sulla free ball", ["CA", "FB"])]
STRONG = 0.8          # probabilità minima per parlare di punto di forza (o, sotto 1 - STRONG, di punto debole)
TENDENCY_MIN = 2.0    # scarto minimo (su 100 ricezioni) per segnalare una tendenza su un voto


def playoff_teams(ts, sets_played, config, ti):
    """Squadre-stagione tra le prime PLAYOFF_POSITIONS in classifica con
    almeno `min_partite_squadra_confronto` partite scoutate."""
    scouted = sets_played.groupby("team")["partita"].nunique()
    return [k for k in ts.loc[ts["posizione"].fillna(99) <= PLAYOFF_POSITIONS, "chiave"]
            if scouted.get(k, 0) >= config["min_partite_squadra_confronto"] and k in ti]


def _sets_by_player(attacks, serves, ours_fam):
    """Set giocati da ogni nostro giocatore = set con almeno un suo attacco,
    battuta o ricezione."""
    parts = [attacks.loc[attacks["team"].isin(ours_fam), ["partita", "set", "giocatore"]],
             serves.loc[serves["team"].isin(ours_fam), ["partita", "set", "giocatore"]],
             serves.loc[serves["opp"].isin(ours_fam) & serves["ricevitore"].notna(), ["partita", "set", "ricevitore"]]
             .rename(columns={"ricevitore": "giocatore"})]
    return pd.concat(parts).drop_duplicates().groupby("giocatore").size()


def reception_analysis(serves, sets_played, srv_s, tr_s, ti, u, playoff, ours_fam, fam_us, config):
    """
    Ricezione (richiesta dell'utente del 2026-11-11: conviene allenarla? come
    vanno i singoli ricevitori, chi fa troppe !?).

    Squadra: side-out sulle battute in gioco = somma sui voti di (quota del
    voto) x (side-out dopo quel voto). Lo scarto dalle squadre playoff si
    divide in due parti che sommate lo danno esattamente: ricezione = voti
    diversi, a parità di side-out dopo ogni voto (quello delle playoff);
    dopo la ricezione = side-out diverso a parità di voti (i nostri). Contro
    un battitore medio, con la quota di ricezioni in prima linea del
    campionato.

    Ricevitori del Decimo: voti su 100 ricezioni stimati dal modello (effetto
    del ricevitore, a parità di battitore) contro il ricevitore medio del
    Decimo con la stessa quota di ricezioni in prima linea; side-out che ne
    segue con il nostro side-out dopo ogni voto; in prima linea contro in
    seconda linea.
    """
    D = srv_s["b"].shape[0]
    b, t_rec, r_rec = srv_s["b"], srv_s["team_rec"], srv_s["receiver_rec_raw"]
    g_front, r_front = srv_s["g_front"], srv_s["receiver_front_raw"]
    w0, rec_side = tr_s["w0"], tr_s["rec_side"]
    G = SERVE_GRADES
    inplay = serves[serves["esito"] != "err"]
    known = inplay["linea_ricevitore"].notna()
    f_league = float((inplay.loc[known, "linea_ricevitore"] == "davanti").mean())

    def q_team(t, f):
        base = b + t_rec[:, t]
        return (1 - f) * _softmax_ref(base, SERVE_REF) + f * _softmax_ref(base + g_front, SERVE_REF)

    def so_team(t):
        out = np.zeros((D, len(G)))
        for j, g in enumerate(G):
            if g in W_GRADES:
                out[:, j] = 1 - _sigmoid(w0[:, W_GRADES.index(g)] + rec_side[:, t, REC_GROUPS[g]])
        return out          # ace subito ("=") = 0
    q_u, so_u = q_team(u, f_league), so_team(u)
    ref = playoff or [u]
    q_p = np.mean([q_team(ti[k], f_league) for k in ref], axis=0)
    so_p = np.mean([so_team(ti[k]) for k in ref], axis=0)
    ours_rec = inplay[inplay["opp"].isin(ours_fam)]
    our_sets = len(sets_played[sets_played["team"].isin(ours_fam)])
    per_set = len(ours_rec) / max(1, our_sets)
    so_mean_p = (q_p * so_p).sum(-1, keepdims=True)
    rec_part = ((q_u - q_p) * so_p).sum(-1) * per_set
    after_part = (q_u * (so_u - so_p)).sum(-1) * per_set
    team = {"ricezioni_per_set": per_set, "quota_prima_linea": f_league,
            "so_noi": _stats((q_u * so_u).sum(-1)), "so_playoff": _stats((q_p * so_p).sum(-1)),
            "totale": {**_stats(rec_part + after_part), "p_sotto": float((rec_part + after_part < 0).mean())},
            "ricezione": {**_stats(rec_part), "p_sotto": float((rec_part < 0).mean())},
            "dopo": {**_stats(after_part), "p_sotto": float((after_part < 0).mean())}}
    grades = pd.DataFrame([{
        "voto": g, "noi_100": float(100 * q_u[:, j].mean()), "playoff_100": float(100 * q_p[:, j].mean()),
        "so_noi": float(so_u[:, j].mean()), "so_playoff": float(so_p[:, j].mean()),
        # punti per set dovuti alla quota diversa di questo voto (le righe sommano la parte "ricezione")
        "punti_set": float(((q_u[:, j] - q_p[:, j]) * (so_p[:, j] - so_mean_p[:, 0])).mean() * per_set),
    } for j, g in enumerate(G)])

    # --- ricevitori del Decimo --------------------------------------------
    out_of_team = excluded_players(config)
    receivers_idx = {r: i for i, r in enumerate(sorted(serves["ricevitore"].dropna().unique()))}
    sets_rec = ours_rec[["ricevitore", "partita", "set"]].drop_duplicates().groupby("ricevitore").size()
    rows, tendencies = [], []
    for r, part in ours_rec[ours_rec["ricevitore"].notna()].groupby("ricevitore"):
        name = r.rsplit("|", 1)[1]
        if (not r.startswith(fam_us + "|") or len(part) < config["min_ricezioni_giocatore"]
                or name in out_of_team):
            continue
        i = receivers_idx[r]
        lines = part["linea_ricevitore"].value_counts()
        n_front, n_back = int(lines.get("davanti", 0)), int(lines.get("dietro", 0) + lines.get("libero", 0))
        f = n_front / max(1, n_front + n_back)
        base = b + t_rec[:, u]
        me_back = _softmax_ref(base + r_rec[:, i], SERVE_REF)
        me_front = _softmax_ref(base + r_rec[:, i] + g_front + r_front[:, i], SERVE_REF)
        avg_back, avg_front = _softmax_ref(base, SERVE_REF), _softmax_ref(base + g_front, SERVE_REF)
        me = (1 - f) * me_back + f * me_front
        avg = (1 - f) * avg_back + f * avg_front
        po = np.mean([(1 - f) * _softmax_ref(b + t_rec[:, ti[k]], SERVE_REF)
                      + f * _softmax_ref(b + t_rec[:, ti[k]] + g_front, SERVE_REF) for k in ref], axis=0)
        so_me, so_avg = (me * so_u).sum(-1), (avg * so_u).sum(-1)
        diff = so_me - so_avg
        rps = len(part) / max(1, sets_rec.get(r, 1))
        row = {"giocatore": name, "chiave": r, "n": len(part), "n_prima_linea": n_front, "n_seconda_linea": n_back,
               "ricezioni_per_set": rps, **_stats(100 * so_me, "so_"), "so_rif": float(100 * so_avg.mean()),
               **_stats(100 * diff, "diff_"), "p_meglio": float((diff > 0).mean()),
               **_stats(diff * rps, "punti_set_"),
               **{f"oss_{g}": int((part["esito"] == g).sum()) for g in G}}
        for j, g in enumerate(G):
            d = 100 * (me[:, j] - avg[:, j])
            row[f"me_{g}"], row[f"rif_{g}"], row[f"po_{g}"] = (float(100 * me[:, j].mean()),
                                                              float(100 * avg[:, j].mean()),
                                                              float(100 * po[:, j].mean()))
            row[f"p_piu_{g}"] = float((d > 0).mean())
            pm = float(d.mean())
            if abs(pm) >= TENDENCY_MIN and (row[f"p_piu_{g}"] >= STRONG or row[f"p_piu_{g}"] <= 1 - STRONG):
                tendencies.append({"giocatore": name, "voto": g, "me_100": row[f"me_{g}"],
                                   "rif_100": row[f"rif_{g}"], "scarto_100": pm, "p_piu": row[f"p_piu_{g}"],
                                   "buona": (pm > 0) == (g in ("#", "+"))})
        # prima linea: quanto cambia rispetto a come cambia per il ricevitore medio del Decimo
        if n_front >= 15 and n_back >= 15:
            d_front = ((me_front - me_back) * so_u).sum(-1) - ((avg_front - avg_back) * so_u).sum(-1)
            row.update({"so_davanti": float(100 * (me_front * so_u).sum(-1).mean()),
                        "so_dietro": float(100 * (me_back * so_u).sum(-1).mean()),
                        **_stats(100 * d_front, "davanti_diff_"), "p_davanti_meglio": float((d_front > 0).mean())})
        rows.append(row)
    receivers = pd.DataFrame(rows)
    if len(receivers):
        receivers = receivers.sort_values("diff_media", ascending=False)
    return {"squadra": team, "voti": grades, "ricevitori": receivers, "tendenze": pd.DataFrame(tendencies),
            "squadre_playoff": ref}


def player_profiles(attacks, serves, att_s, srv_s, v_srv, v_att, reception, players, servers, u, ours_fam,
                    fam_us, config):
    """
    Punti di forza e punti deboli di ogni giocatore del Decimo (richiesta
    dell'utente del 2026-11-11: "un giocatore magari scopro che ha un certo
    punto di forza, o un punto debole, in una certa situazione, e mi comporto
    di conseguenza"). Ogni voce confronta il giocatore con il giocatore medio
    del Decimo nella stessa situazione (stessa zona per l'attacco, stessa
    quota di ricezioni in prima linea per la ricezione), in scambi vinti su
    100 azioni e in punti per set (con le sue azioni per set giocato).
    Punto di forza: probabilità di fare meglio ≥ STRONG; punto debole: ≤ 1 - STRONG.
    """
    D = att_s["mu"].shape[0]
    out_of_team = excluded_players(config)
    sets_pl = _sets_by_player(attacks, serves, ours_fam)
    items, detail = [], []

    def item(key, area, dettaglio, n, diff100, per_set_actions, unita):
        p = float((diff100 > 0).mean())
        items.append({"giocatore": key.rsplit("|", 1)[1], "area": area, "dettaglio": dettaglio, "n": int(n),
                      **_stats(diff100, "diff_"), "punti_set": float(diff100.mean() / 100 * per_set_actions),
                      "azioni_set": per_set_actions, "unita": unita, "p_meglio": p,
                      "giudizio": "forza" if p >= STRONG else ("debole" if p <= 1 - STRONG else None)})

    # --- attacco ------------------------------------------------------------
    mu = att_s["mu"].reshape(D, len(SITUATIONS), len(ZONES), -1)
    team_att, pl, pl_hard = att_s["team_att"], att_s["player"], att_s["player_hard"]
    g_final, clutch = att_s["g_final"], att_s["clutch"]
    pi = {p: i for i, p in enumerate(players)}

    def ev(sit, eta):
        return (_softmax_ref(eta, ATTACK_REF) * v_att[sit]).sum(-1)
    ours = attacks[attacks["team"].isin(ours_fam)]
    for p, part in ours.groupby("giocatore"):
        if (not p.startswith(fam_us + "|") or p.rsplit("|", 1)[1] in out_of_team
                or len(part) < config["min_attacchi_giocatore"]):
            continue
        n_sets = max(1, sets_pl.get(p, 1))
        groups = {0: [np.zeros(D), 0], 1: [np.zeros(D), 0]}
        fin = [np.zeros(D), 0]
        for (sit, z), cell in part.groupby(["situazione", "zona"]):
            s, zi = SITUATIONS.index(sit), ZONES.index(z)
            hard = sit in HARD_SITUATIONS
            ref = mu[:, s, zi] + team_att[:, u, s]
            me = ref + pl[:, pi[p]] + (pl_hard[:, pi[p]] if hard else 0)
            d = ev(sit, me) - ev(sit, ref)
            groups[int(hard)][0] += d * len(cell)
            groups[int(hard)][1] += len(cell)
            fin[0] += (ev(sit, me + g_final + clutch[:, pi[p]]) - ev(sit, me + g_final)) * len(cell)
            fin[1] += len(cell)
            detail.append({"giocatore": p.rsplit("|", 1)[1], "situazione": sit, "zona": z, "n": len(cell),
                           **_stats(100 * d, "diff_"), "p_meglio": float((d > 0).mean())})
        for g, (acc, n) in groups.items():
            if n >= config["min_attacchi_giocatore"]:
                item(p, "Attacco", SIT_GROUP_LABELS[g], n, 100 * acc / n, n / n_sets, "scambi vinti su 100 attacchi")
        n_fin = int(part["finale"].sum())
        if n_fin >= 5:
            item(p, "Attacco nei finali di set", "una squadra a 20 (10 al quinto), scarto al massimo 3", n_fin,
                 100 * fin[0] / fin[1], n_fin / n_sets, "scambi vinti su 100 attacchi nei finali")

    # --- battuta ------------------------------------------------------------
    a_err, t_err, s_err = srv_s["a_err"], srv_s["team_err"][..., 0], srv_s["server_err"][..., 0]
    b, t_srv, s_srv = srv_s["b"], srv_s["team_srv"], srv_s["server_srv"]
    si = {p: i for i, p in enumerate(servers)}
    err_ref = _sigmoid(a_err + t_err[:, u])
    press_ref = (_softmax_ref(b + t_srv[:, u], SERVE_REF) * v_srv).sum(-1)
    for p, part in serves[serves["team"].isin(ours_fam)].groupby("giocatore"):
        if (not p.startswith(fam_us + "|") or p.rsplit("|", 1)[1] in out_of_team
                or len(part) < config["min_battute_giocatore"]):
            continue
        n_sets = max(1, sets_pl.get(p, 1))
        err = _sigmoid(a_err + t_err[:, u] + s_err[:, si[p]])
        press = (_softmax_ref(b + t_srv[:, u] + s_srv[:, si[p]], SERVE_REF) * v_srv).sum(-1)
        per_set = len(part) / n_sets
        # scambi vinti per battuta = (1 - errore) x valore della battuta in gioco: le due parti sommano lo scarto
        item(p, "Battuta: errori", "quante battute sbaglia (meno errori = meglio)", len(part),
             -100 * (err - err_ref) * press_ref, per_set, "scambi vinti su 100 battute")
        item(p, "Battuta: pressione", "quanto mette in difficoltà la ricezione con le battute in gioco", len(part),
             100 * (1 - err) * (press - press_ref), per_set, "scambi vinti su 100 battute")

    # --- ricezione (dalla analisi dei ricevitori) ---------------------------
    rec = reception["ricevitori"]
    for _, x in rec.iterrows():
        items.append({"giocatore": x["giocatore"], "area": "Ricezione", "dettaglio": "side-out dopo le sue ricezioni",
                      "n": int(x["n"]), "diff_media": x["diff_media"], "diff_basso": x["diff_basso"],
                      "diff_alto": x["diff_alto"], "punti_set": x["punti_set_media"],
                      "azioni_set": x["ricezioni_per_set"], "unita": "side-out su 100 ricezioni",
                      "p_meglio": x["p_meglio"],
                      "giudizio": "forza" if x["p_meglio"] >= STRONG else ("debole" if x["p_meglio"] <= 1 - STRONG
                                                                            else None)})
        if pd.notna(x.get("p_davanti_meglio")):
            p = x["p_davanti_meglio"]
            items.append({"giocatore": x["giocatore"], "area": "Ricezione in prima linea",
                          "dettaglio": "quanto cambia quando riceve in prima linea, rispetto a come cambia per "
                                       "il ricevitore medio", "n": int(x["n_prima_linea"]),
                          "diff_media": x["davanti_diff_media"], "diff_basso": x["davanti_diff_basso"],
                          "diff_alto": x["davanti_diff_alto"],
                          "punti_set": x["davanti_diff_media"] / 100 * x["ricezioni_per_set"]
                          * x["n_prima_linea"] / x["n"],
                          "azioni_set": x["ricezioni_per_set"] * x["n_prima_linea"] / x["n"],
                          "unita": "side-out su 100 ricezioni in prima linea", "p_meglio": p,
                          "giudizio": "forza" if p >= STRONG else ("debole" if p <= 1 - STRONG else None)})
    return {"voci": pd.DataFrame(items), "attacco_dettaglio": pd.DataFrame(detail)}


def attack_licence(attacks, att_s, att_values, ti, u, playoff, ours_fam, our_sets):
    """
    Licenza di sbagliare in attacco, per situazione (indicazione dell'utente
    del 2026-11-11: stesso schema della battuta). Valore di un attacco per
    chi attacca: punto 1, errore o murata 0, pallone rimasto in gioco c
    (media dei valori di +, !, - nella situazione, pesata sulle frequenze del
    campionato). Riferimento: l'attaccante medio delle squadre playoff, per
    zona e, per la squadra, pesato sulla nostra distribuzione tra le zone.
    Ritorna {situazione: {c, attacchi_per_set, punto/errmur playoff e nostri
    per set, riferimento per zona}}.
    """
    D = att_s["mu"].shape[0]
    mu = att_s["mu"].reshape(D, len(SITUATIONS), len(ZONES), -1)
    team_att = att_s["team_att"]
    vals = att_values.set_index(["gruppo", "esito"])
    ours = attacks[attacks["team"].isin(ours_fam)]
    i_err, i_mur = ATTACK_OUTCOMES.index("="), ATTACK_OUTCOMES.index("/")
    out = {}
    for s, sit in enumerate(SITUATIONS):
        q = np.array([vals.loc[(sit, o), "quota"] for o in INPLAY])
        c = float((q * np.array([vals.loc[(sit, o), "media"] for o in INPLAY])).sum() / q.sum())
        part = ours[ours["situazione"] == sit]
        mix = np.array([(part["zona"] == z).mean() for z in ZONES]) if len(part) else np.zeros(len(ZONES))

        def probs(team_idx, zi):
            eta = mu[:, s, zi] + (team_att[:, team_idx, s] if team_idx is not None else 0)
            return _softmax_ref(eta, ATTACK_REF).mean(0)
        per_zone = {}
        for zi, z in enumerate(ZONES):
            pz = [probs(ti[k], zi) for k in playoff] or [probs(None, zi)]
            pm = np.mean(pz, axis=0)
            per_zone[z] = {"punto": float(pm[0]), "errmur": float(pm[i_err] + pm[i_mur])}
        us_p = sum(mix[zi] * probs(u, zi) for zi in range(len(ZONES))) if mix.sum() else probs(u, 0)
        po_k = sum(mix[zi] * per_zone[z]["punto"] for zi, z in enumerate(ZONES))
        po_e = sum(mix[zi] * per_zone[z]["errmur"] for zi, z in enumerate(ZONES))
        out[sit] = {"c": c, "attacchi_per_set": len(part) / max(1, our_sets),
                    "playoff_punto": float(po_k), "playoff_errmur": float(po_e),
                    "noi_punto": float(us_p[0]), "noi_errmur": float(us_p[i_err] + us_p[i_mur]),
                    "per_zona": per_zone}
    return out


def practical_examples(serves, srv_values, srv_s, servers, ti, u, fam_us, opponents, sets_played, ours_fam, config,
                       ts=None, attacks=None, att_s=None, att_values=None):
    """
    Numeri per gli esempi della dashboard.

    Battuta: tra le battute ricevute, gruppo "facile" (# o +) e "difficile"
    (!, -, /), ognuno con il suo valore (scambi vinti da chi batte, media del
    campionato pesata sulle frequenze). Su 100 battute, rispetto a un
    riferimento: scambi vinti in più = ace in più x (1 - facile) + ricezioni
    spostate da facile a difficile x (difficile - facile) - errori in più x
    facile (l'errore prende il posto di una battuta facile). Quindi 1 errore =
    facile / (1 - facile) ace, oppure facile / (difficile - facile)
    ricezioni spostate. Ogni battitore del Decimo è confrontato con il
    battitore medio del Decimo (effetto di squadra, senza quello personale).

    Attacco: un pallone rimasto in gioco vale c (scambi vinti dopo un nostro
    attacco difeso, contro una squadra media); 1 errore o murata in più va
    ripagato da c / (1 - c) punti in più.

    Set: quanto valgono g punti in più per set in set vinti contro una squadra
    media (g spalmato su tutti gli scambi del set).
    """
    v = srv_values.set_index("esito")
    good, bad = ["#", "+"], ["!", "-", "/"]
    w_good = float((v.loc[good, "quota"] * v.loc[good, "media"]).sum() / v.loc[good, "quota"].sum())
    w_bad = float((v.loc[bad, "quota"] * v.loc[bad, "media"]).sum() / v.loc[bad, "quota"].sum())
    err_cost, ace_gain, shift_gain = w_good, 1 - w_good, w_bad - w_good

    a_err, t_err, s_err = srv_s["a_err"], srv_s["team_err"][..., 0], srv_s["server_err"][..., 0]
    b, t_srv, s_srv = srv_s["b"], srv_s["team_srv"], srv_s["server_srv"]
    gi = {g: SERVE_GRADES.index(g) for g in SERVE_GRADES}

    def per100(err, q):
        inplay = 100 * (1 - err)[:, None] * q
        return {"errori": 100 * err, "ace": inplay[:, gi["="]], "difficili": inplay[:, [gi[g] for g in bad]].sum(-1),
                "facili": inplay[:, [gi[g] for g in good]].sum(-1)}
    ref = per100(_sigmoid(a_err + t_err[:, u]), _softmax_ref(b + t_srv[:, u], SERVE_REF))
    # livello playoff (indicazione dell'utente del 2026-11-11): il battitore medio delle squadre-stagione tra le
    # prime PLAYOFF_POSITIONS in classifica con almeno `min_partite_squadra_confronto` partite scoutate
    playoff = playoff_teams(ts, sets_played, config, ti) if ts is not None else []
    playoff_draws = [per100(_sigmoid(a_err + t_err[:, ti[k]]), _softmax_ref(b + t_srv[:, ti[k]], SERVE_REF))
                     for k in playoff]
    ref_playoff = ({k: np.mean([d[k] for d in playoff_draws], axis=0) for k in ref} if playoff_draws else ref)
    si = {p: i for i, p in enumerate(servers)}
    counts = serves.groupby("giocatore").size()
    rows = []
    out_of_team = excluded_players(config)
    for p in servers:
        if (not p.startswith(fam_us + "|") or counts.get(p, 0) < config["min_battute_giocatore"]
                or p.rsplit("|", 1)[1] in out_of_team):
            continue
        me = per100(_sigmoid(a_err + t_err[:, u] + s_err[:, si[p]]),
                    _softmax_ref(b + t_srv[:, u] + s_srv[:, si[p]], SERVE_REF))
        d = {k: float(np.mean(me[k] - ref[k])) for k in me}
        paid = d["ace"] * ace_gain + d["difficili"] * shift_gain            # in scambi su 100 battute
        part = serves[serves["giocatore"] == p]
        rows.append({"giocatore": p.rsplit("|", 1)[1], "battute": len(part),
                     "errori_oss": int((part["esito"] == "err").sum()), "ace_oss": int((part["esito"] == "=").sum()),
                     **{f"{k}_100": float(np.mean(me[k])) for k in me},
                     **{f"delta_{k}": val for k, val in d.items()},
                     "ripagati": paid / err_cost,                             # errori "pagati" su 100 battute
                     "bilancio": paid - d["errori"] * err_cost})              # scambi vinti in più su 100 battute
    licences = pd.DataFrame(rows)
    if len(licences):
        licences["quota_ripagata"] = np.where(licences["delta_errori"] > 0.5,
                                              licences["ripagati"] / licences["delta_errori"], np.nan)
        licences = licences.sort_values("bilancio", ascending=False)

    media = opponents[opponents["chiave"] == "media"].iloc[0]
    c = float(media["c_R#+"])
    our_sets = len(sets_played[sets_played["team"].isin(ours_fam)])
    rallies_per_set = (len(serves[serves["team"].isin(ours_fam)]) + len(serves[serves["opp"].isin(ours_fam)])) / our_sets
    so_us, so_opp = float(media["so_noi"]), float(media["so_loro"])
    base = p_set(so_us, so_opp)
    set_rows = []
    for g in (0.25, 0.5, 1.0, 2.0):
        dlt = g / rallies_per_set
        set_rows.append({"punti_per_set": g, "p_set_prima": base, "p_set_dopo": p_set(so_us + dlt, so_opp - dlt)})
    serves_per_set = len(serves[serves["team"].isin(ours_fam)]) / max(1, len(sets_played[sets_played["team"].isin(ours_fam)]))
    names = ts.set_index("chiave")["nome"] if ts is not None else {}
    return {
        "battuta": {"valore_facile": w_good, "valore_difficile": w_bad, "costo_errore": err_cost,
                    "ace_per_errore": err_cost / ace_gain, "ricezioni_per_errore": err_cost / shift_gain,
                    "riferimento": {k: float(np.mean(val)) for k, val in ref.items()},
                    "riferimento_playoff": {k: float(np.mean(val)) for k, val in ref_playoff.items()},
                    "squadre_playoff": [f"{names[k]} ({k.split('|')[0]})" for k in playoff],
                    "battute_per_set": serves_per_set, "battitori": licences},
        "attacco": {"valore_in_gioco": c, "punti_per_errore": c / (1 - c),
                    "licenza": (attack_licence(attacks, att_s, att_values, ti, u, playoff, ours_fam,
                                               max(1, len(sets_played[sets_played["team"].isin(ours_fam)])))
                                if attacks is not None else {})},
        "ricezione": {"so_facile": 1 - w_good, "so_difficile": 1 - w_bad, "guadagno": shift_gain},
        "set": {"scambi_per_set": rallies_per_set, "tabella": pd.DataFrame(set_rows)},
    }


def opponent_strategies(attacks, serves, ts, att_s, srv_s, tr_s, us, ours_fam, attackers_df, config):
    """
    Per ogni squadra della stagione in corso (e per l'avversario medio):
    battere forte o sicuro, forzare o no il primo attacco e il contrattacco,
    il loro attacco contro di noi, e il budget di errori per vincere i set.
    """
    teams = ts["chiave"].tolist()
    ti = {t: i for i, t in enumerate(teams)}
    u = ti[us]
    D = att_s["mu"].shape[0]
    current = config["stagioni"][-1]
    a_err, t_err = srv_s["a_err"], srv_s["team_err"][..., 0]
    b, t_srv, t_rec = srv_s["b"], srv_s["team_srv"], srv_s["team_rec"]
    c0 = tr_s["c0"].reshape(D, len(SITUATIONS), len(INPLAY))
    cont_att, cont_def = tr_s["cont_att"][..., 0], tr_s["cont_def"][..., 0]
    w0, bp_srv, rec_side = tr_s["w0"], tr_s["bp_srv"][..., 0], tr_s["rec_side"]
    mu = att_s["mu"].reshape(D, len(SITUATIONS), len(ZONES), -1)
    team_att, team_def = att_s["team_att"], att_s["team_def"]
    grp = np.array([REC_GROUPS[g] for g in W_GRADES])
    # ordine dei voti nel modello della battuta → indici in W_GRADES
    w_index = {g: W_GRADES.index(g) for g in W_GRADES}
    ours = attacks[attacks["team"].isin(ours_fam)]
    inplay_mix = {s: ours[(ours["situazione"] == s) & ours["esito"].isin(INPLAY)]["esito"]
                  .value_counts(normalize=True).reindex(INPLAY).fillna(0).to_numpy() for s in SITUATIONS}
    league_mix = attacks.groupby("situazione")["zona"].value_counts(normalize=True)
    scouted = attacks.groupby("team")["partita"].nunique()

    def serve_side(server, receiver):
        """(errore di chi batte, distribuzione dei voti, P(chi batte vince | voto))
        con effetti a 0 per l'avversario medio (indice None)."""
        te = t_err[:, server] if server is not None else 0
        ts_ = t_srv[:, server] if server is not None else 0
        tr = t_rec[:, receiver] if receiver is not None else 0
        err = _sigmoid(a_err + te)
        q = _softmax_ref(b + ts_ + tr, SERVE_REF)                       # ordine SERVE_GRADES
        bp = bp_srv[:, server][:, None] if server is not None else 0
        rs = rec_side[:, receiver][:, grp] if receiver is not None else 0
        W = _sigmoid(w0 + bp + rs)                                      # ordine W_GRADES
        win = np.zeros((D, len(SERVE_GRADES)))
        for j, g in enumerate(SERVE_GRADES):
            win[:, j] = 1.0 if g == "=" else W[:, w_index[g]]
        return err, q, win

    def cont(att_team, def_team, s):
        ca = cont_att[:, att_team] if att_team is not None else 0
        cd = cont_def[:, def_team] if def_team is not None else 0
        c = _sigmoid(c0[:, s] + np.asarray(ca).reshape(-1, 1) + np.asarray(cd).reshape(-1, 1))
        return (c * inplay_mix[SITUATIONS[s]]).sum(-1)

    def their_attack(x, s):
        mix = league_mix.get(SITUATIONS[s], pd.Series(dtype=float))
        probs = 0
        for zi, z in enumerate(ZONES):
            ta = team_att[:, x, s] if x is not None else 0
            probs = probs + mix.get(z, 0) * _softmax_ref(mu[:, s, zi] + ta + team_def[:, u], ATTACK_REF)
        return probs

    def analyse(x):
        out = {}
        # noi in battuta contro di loro
        err, q, win = serve_side(u, x)
        e_in = (q * win).sum(-1)
        gap = win[:, SERVE_GRADES.index("-")] - win[:, SERVE_GRADES.index("#")]
        out["batt_err"] = err
        out["batt_in_gioco"] = e_in
        out["batt_ricezioni_per_errore"] = e_in / np.maximum(gap, 1e-3)
        out["batt_gap"] = gap
        out["loro_ric_positiva"] = q[:, [SERVE_GRADES.index("#"), SERVE_GRADES.index("+")]].sum(-1)
        bp_us = (1 - err) * e_in
        # loro in battuta contro di noi
        err_x, q_x, win_x = serve_side(x, u)
        so_us = err_x + ((1 - err_x)[:, None] * q_x * (1 - win_x)).sum(-1)
        out["so_noi"], out["so_loro"] = so_us, 1 - bp_us
        pos = [SERVE_GRADES.index("#"), SERVE_GRADES.index("+")]
        out["q_pos_noi"] = q_x[:, pos].sum(-1) * (1 - err_x)
        out["w_pos_noi"] = (q_x[:, pos] * (1 - win_x[:, pos])).sum(-1) / q_x[:, pos].sum(-1)
        # attacco: quanto vale un pallone rimesso in gioco contro di loro
        for s, sit in enumerate(SITUATIONS):
            c = cont(u, x, s)
            out[f"c_{sit}"] = c
            out[f"soglia_{sit}"] = c / (1 - c)
        # il loro attacco contro di noi
        for s, sit in enumerate(SITUATIONS):
            p = their_attack(x, s)
            out[f"loro_{sit}_punto"] = p[..., 0]
            out[f"loro_{sit}_murato"] = p[..., ATTACK_OUTCOMES.index("/")]
            out[f"loro_{sit}_errore"] = p[..., ATTACK_OUTCOMES.index("=")]
        return out

    base = analyse(None)
    rows = []
    cur = ts[(ts["stagione"] == current) & (ts["chiave"] != us)]
    entries = [(None, "Avversario medio del campionato", None)] + [(r.chiave, r.nome, r) for r in cur.itertuples()]
    for key, name, info in entries:
        x = ti[key] if key else None
        res = analyse(x) if key else base
        row = {"chiave": key or "media", "nome": name}
        for k, v in res.items():
            # il rapporto ricezioni/errore ha code lunghe (divisione per uno scarto piccolo): mediana
            row[k] = float(np.median(v) if k == "batt_ricezioni_per_errore" else np.mean(v))
        # decisioni: confronto con l'avversario medio, probabilità sui campioni
        row["p_battere_forte"] = float((res["batt_ricezioni_per_errore"] < base["batt_ricezioni_per_errore"]).mean())
        for sit in SITUATIONS:
            row[f"p_forzare_{sit}"] = float((res[f"soglia_{sit}"] < base[f"soglia_{sit}"]).mean())
        row["p_set_ora"] = p_set(row["so_noi"], row["so_loro"])
        for t in SET_TARGETS:
            so_req = required_side_out(row["so_loro"], t)
            row[f"so_richiesto_{int(t * 100)}"] = so_req
            # tutto l'aumento chiesto al primo attacco da R#/R+, il resto com'è
            w_req = row["w_pos_noi"] + (so_req - row["so_noi"]) / max(row["q_pos_noi"], 1e-3)
            c = row["c_R#+"]
            for e in ERROR_LEVELS:
                row[f"punti_richiesti_{int(t * 100)}_{int(e * 100)}"] = (w_req - (1 - e) * c) / (1 - c)
        if info is not None:
            group = ts.set_index("chiave").loc[key, "gruppo"]
            prev = ts[(ts["gruppo"] == group) & (ts["stagione"] != current)]
            row.update({"posizione": info.posizione, "punti": info.punti, "giocate": info.giocate,
                        "punti_fatti": info.punti_fatti, "punti_subiti": info.punti_subiti, "forza_z": info.z,
                        "scout_stagione": int(scouted.get(key, 0)),
                        "scout_stagione_prima": int(sum(scouted.get(k, 0) for k in prev["chiave"])),
                        "stagione_prima": ", ".join(prev["nome"]) if len(prev) else ""})
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Scheda dell'avversario: i numeri diretti degli scout (quest'anno e, per i
# giocatori rimasti, l'anno prima) e le frasi che ne escono
# ---------------------------------------------------------------------------
CARD_MIN_PLAYER = 5      # azioni minime per una riga di giocatore
CARD_MIN_FACT = 8        # azioni minime perché un dato diventi una frase


SETTER_BACK = ("P1", "P6", "P5")   # palleggiatore in seconda linea: opposto in prima linea


def setter_line(rotation):
    """'dietro' (P1, P6, P5: opposto davanti) o 'davanti' (P2, P3, P4)."""
    return "dietro" if str(rotation).upper() in SETTER_BACK else "davanti"


def _attack_table(a, label):
    if a.empty:
        return pd.DataFrame()
    g = a.groupby("giocatore").agg(
        attacchi=("esito", "size"), punto=("esito", lambda x: (x == "#").mean()),
        errore_murato=("esito", lambda x: x.isin(["=", "/"]).mean()),
        zona=("zona", lambda x: int(x.mode()[0])), scambi_vinti=("vinto", "mean"))
    g["efficienza"] = g["punto"] - g["errore_murato"]
    g = g[g["attacchi"] >= CARD_MIN_PLAYER].sort_values("attacchi", ascending=False)
    g.index = g.index.map(label)
    return g.reset_index()


def _reception_table(r, label):
    r = r[r["ricevitore"].notna()]
    if r.empty:
        return pd.DataFrame()
    g = r.groupby("ricevitore").agg(
        ricezioni=("esito", "size"), positive=("esito", lambda x: x.isin(["#", "+"]).mean()),
        negative=("esito", lambda x: x.isin(["-", "/", "="]).mean()), side_out=("vinto", lambda x: 1 - x.mean()))
    g = g[g["ricezioni"] >= CARD_MIN_PLAYER].sort_values("ricezioni", ascending=False)
    g.index = g.index.map(label)
    return g.reset_index().rename(columns={"ricevitore": "giocatore"})


def _serve_table(s, label):
    if s.empty:
        return pd.DataFrame()
    g = s.groupby("giocatore").agg(
        battute=("esito", "size"), errori=("esito", lambda x: (x == "err").mean()),
        ace=("esito", lambda x: (x == "=").mean()), break_point=("vinto", "mean"))
    g = g[g["battute"] >= CARD_MIN_PLAYER].sort_values("battute", ascending=False)
    g.index = g.index.map(label)
    return g.reset_index()


def _pct_it(x):
    return f"{100 * x:.0f}%"


def _labeler(rosters, team_keys):
    """giocatore ('società|cognome') → 'Cognome (numero, sigla)', con numero e
    ruolo della partita più recente tra le squadre-stagione `team_keys` (in
    ordine di preferenza: prima quella di quest'anno)."""
    lookup = {}
    if rosters is not None and len(rosters):
        for t in reversed(team_keys):
            part = rosters[rosters["team"] == t].sort_values("data")
            for r in part.itertuples():
                lookup[r.cognome] = (r.numero, r.sigla)

    def label(key):
        cognome = str(key).rsplit("|", 1)[-1]
        if cognome not in lookup:
            return cognome
        numero, sigla = lookup[cognome]
        return f"{cognome} ({numero}, {sigla})" if sigla else f"{cognome} ({numero})"
    return label


def scouting_cards(attacks, serves, sets_played, ts, config, rosters=None):
    """{chiave della squadra: scheda} per le squadre della stagione in corso
    (Decimo escluso). Scheda: partite scoutate, numeri di squadra, attacco per
    situazione, distribuzione per zona da R#/R+ e da R-, tabelle di
    attaccanti/ricevitori/battitori (quest'anno e, per i giocatori rimasti o,
    se la rosa di quest'anno non è nota, per tutti, la stagione prima) e
    frasi con i dati che spiccano (solo con almeno CARD_MIN_FACT azioni)."""
    current = config["stagioni"][-1]
    us = f"{current}|{config['squadra']}"
    league_so = 1 - serves["vinto"].mean()
    received = serves[serves["ricevitore"].notna()]
    league_pos = received["esito"].isin(["#", "+"]).mean()
    cards = {}
    for t in ts[(ts["stagione"] == current) & (ts["chiave"] != us)].itertuples():
        key, fam = t.chiave, t.gruppo
        a = attacks[attacks["team"] == key]
        s = serves[serves["team"] == key]
        r = serves[serves["opp"] == key]
        matches = sorted(sets_played.loc[sets_played["team"] == key, "partita"].unique())
        roster = set(a["giocatore"]) | set(s["giocatore"]) | set(r["ricevitore"].dropna())
        prev_keys = ts[(ts["gruppo"] == fam) & (ts["stagione"] != current)]["chiave"].tolist()
        a_p = attacks[attacks["team"].isin(prev_keys)]
        s_p = serves[serves["team"].isin(prev_keys)]
        r_p = serves[serves["opp"].isin(prev_keys)]
        if roster:  # rosa di quest'anno nota: solo i giocatori rimasti
            a_p = a_p[a_p["giocatore"].isin(roster)]
            s_p = s_p[s_p["giocatore"].isin(roster)]
            r_p = r_p[r_p["ricevitore"].isin(roster)]
        label = _labeler(rosters, [key] + prev_keys)
        label_prev = _labeler(rosters, prev_keys)
        card = {"nome": t.nome, "partite": matches, "n_scambi": int(len(s) + len(r)),
                "stagione_prima": ", ".join(ts.set_index("chiave").loc[prev_keys, "nome"]) if prev_keys else "",
                "rosa_nota": bool(roster), "frasi": []}
        if len(r):
            card["side_out"] = float(1 - r["vinto"].mean())
        if len(s):
            card["break_point"] = float(s["vinto"].mean())
            card["errori_battuta"] = float((s["esito"] == "err").mean())
        sit = a.groupby("situazione").agg(attacchi=("esito", "size"), punto=("esito", lambda x: (x == "#").mean()),
                                          errore_murato=("esito", lambda x: x.isin(["=", "/"]).mean()),
                                          scambi_vinti=("vinto", "mean"))
        card["attacco_situazioni"] = sit.reindex([x for x in SITUATIONS if x in sit.index]).reset_index()
        card["zone"] = {k: a[a["situazione"] == k]["zona"].value_counts().to_dict() for k in ("R#+", "R-", "FB")}
        card["attaccanti"] = _attack_table(a, label)
        card["ricevitori"], card["battitori"] = _reception_table(r, label), _serve_table(s, label)
        # i rimasti col numero e il ruolo di quest'anno (se la rosa è nota), gli altri con quelli di allora
        lab_p = label if roster else label_prev
        card["attaccanti_prima"] = _attack_table(a_p, lab_p)
        card["ricevitori_prima"] = _reception_table(r_p, lab_p)
        card["battitori_prima"] = _serve_table(s_p, lab_p)

        # distribuzione da R#/R+ divisa per linea del palleggiatore (righe nel
        # formato di attack_rows per zone_stats); anno prima solo se il
        # palleggiatore di quest'anno è lo stesso
        def court_rows(df, lab):
            df = df[df["situazione"] == "R#+"]
            return pd.DataFrame({"zona": df["zona"], "attaccante": df["giocatore"].map(lab), "voto": df["esito"],
                                 "linea": df["rotazione"].map(setter_line)})
        card["campetti"] = court_rows(a, label)
        setters_now = set()
        if rosters is not None and len(rosters):
            setters_now = set(rosters[(rosters["team"] == key) & (rosters["sigla"] == "P")]["cognome"])
        a_prev_all = attacks[attacks["team"].isin(prev_keys)]
        same_setter = a_prev_all[a_prev_all["palleggiatore"].isin(setters_now)]
        card["campetti_prima"] = court_rows(same_setter, label)
        card["palleggiatore_prima"] = ", ".join(sorted(setters_now & set(a_prev_all["palleggiatore"])))

        f = card["frasi"]
        if len(r) >= 20:
            word = "alto" if card["side_out"] > league_so + 0.05 else "basso" if card["side_out"] < league_so - 0.05 \
                else "nella media"
            f.append(f"Side-out {word}: {_pct_it(card['side_out'])} (media del campionato {_pct_it(league_so)}), "
                     f"break-point {_pct_it(card.get('break_point', 0))}.")
        for line, words in (("dietro", "palleggiatore in seconda linea (opposto davanti)"),
                            ("davanti", "palleggiatore in prima linea (opposto dietro)")):
            zp = card["campetti"][card["campetti"]["linea"] == line]
            if len(zp) < 6:
                continue
            vc = zp["zona"].value_counts(normalize=True)
            z, share = vc.idxmax(), vc.max()
            if share >= 0.45:
                who = zp[zp["zona"] == z]["attaccante"].value_counts()
                others = ", ".join(f"{ZONE_LABELS[k]} {_pct_it(v)}" for k, v in vc.items() if k != z)
                f.append(f"Da ricezione # o +, con il {words}, vanno a **{ZONE_LABELS[z]}** nel {_pct_it(share)} "
                         f"dei casi ({len(zp)} attacchi{'; ' + others if others else ''}; "
                         + ", ".join(f"{n_} {k_}" for k_, n_ in who.items()) + "): a muro partire da lì.")
        opp = a[a["giocatore"].map(label).str.contains(", O)", regex=False)]
        if len(opp) >= 10:
            back = (opp["rotazione"].map(setter_line) == "dietro").mean()
            if back >= 0.8:
                f.append(f"L'opposto **{label(opp['giocatore'].iloc[0])}** attacca quasi solo da prima linea: "
                         f"{_pct_it(back)} dei suoi {len(opp)} attacchi con il palleggiatore dietro. Con il "
                         "palleggiatore davanti il loro attacco ha un'arma in meno.")
        rm = a[a["situazione"] == "R-"]
        if len(rm) >= CARD_MIN_FACT:
            em = rm["esito"].isin(["=", "/"]).mean()
            if em >= 0.25:
                f.append(f"Da ricezione - vanno in difficoltà: {_pct_it((rm['esito'] == '#').mean())} di punti e "
                         f"{_pct_it(em)} tra errori e murate ({len(rm)} attacchi): togliergli la ricezione buona rende.")
        at = card["attaccanti"]
        if len(at) and at.iloc[0]["attacchi"] >= CARD_MIN_FACT:
            x = at.iloc[0]
            f.append(f"Attaccante più servito: **{x['giocatore']}**, {int(x['attacchi'])} "
                     f"attacchi, {_pct_it(x['punto'])} di punti e {_pct_it(x['errore_murato'])} tra errori e murate.")
            weak = at[(at["attacchi"] >= CARD_MIN_FACT) & (at["errore_murato"] >= 0.3)]
            for x in weak.itertuples():
                f.append(f"Attaccante in difficoltà: **{x.giocatore}**, {_pct_it(x.punto)} "
                         f"di punti e {_pct_it(x.errore_murato)} tra errori e murate su {int(x.attacchi)}.")
        rc = card["ricevitori"]
        rc_ok = rc[rc["ricezioni"] >= CARD_MIN_FACT] if len(rc) else rc
        if len(rc_ok) >= 2:
            x = rc_ok.sort_values("positive").iloc[0]
            what = ("bersaglio in battuta" if x["positive"] < league_pos
                    else f"comunque sopra la media del campionato ({_pct_it(league_pos)})")
            f.append(f"Ricevitore meno solido: **{x['giocatore']}**, {_pct_it(x['positive'])} di positive e "
                     f"{_pct_it(x['negative'])} tra negative e ace su {int(x['ricezioni'])}: {what}.")
            y = rc_ok.iloc[0]
            if y["giocatore"] != x["giocatore"]:
                f.append(f"Riceve di più **{y['giocatore']}** ({int(y['ricezioni'])} ricezioni, "
                         f"{_pct_it(y['positive'])} positive).")
        if len(s) >= 20:
            f.append(f"In battuta sbagliano il {_pct_it(card['errori_battuta'])} e fanno ace il "
                     f"{_pct_it((s['esito'] == '=').mean())}.")
        ap = card["attaccanti_prima"]
        if len(ap):
            ap30 = ap[ap["attacchi"] >= 30]
            if len(ap30):
                x = ap30.sort_values("efficienza", ascending=False).iloc[0]
                who = "Tra i rimasti, l'anno scorso" if roster else "L'anno scorso"
                f.append(f"{who} l'attaccante più efficace era **{x['giocatore']}**: "
                         f"{_pct_it(x['punto'])} di punti su {int(x['attacchi'])} attacchi.")
        cards[key] = card
    return cards


def fit_and_save(progress=None):
    """Dati → tre modelli → riassunti salvati in RESULTS_PATH. Ritorna i
    risultati (dict). `progress(testo)` riceve i passi."""
    config = load_config()
    say = progress or (lambda t: print(t, flush=True))
    t0 = time.time()
    say("Lettura delle partite e dei risultati del girone…")
    attacks, serves, sets_played, skipped, rosters = build_dataset(config)
    attacks, serves, sets_played, ts, unmatched, rosters = attach_teams(attacks, serves, sets_played, config, rosters)
    players = sorted(attacks["giocatore"].unique())
    servers = sorted(serves["giocatore"].unique())
    receivers = sorted(serves["ricevitore"].dropna().unique())
    say(f"Modello dell'attacco ({len(attacks)} attacchi)…")
    att_s, att_d = fit_attack_model(attacks, config["mcmc"], ts, players)
    say(f"Modello della battuta ({len(serves)} battute)…")
    srv_s, srv_d = fit_serve_model(serves, config["mcmc"], ts, servers, receivers)
    say("Modello di cosa succede dopo (palla in gioco, side-out per voto di ricezione)…")
    tr_s, tr_d = fit_transition_model(attacks, serves, config["mcmc"], ts)
    say("Riassunti…")
    results = summarize(attacks, serves, sets_played, ts, att_s, srv_s, tr_s, players, servers, receivers, config)
    results["schede"] = scouting_cards(attacks, serves, sets_played, ts, config, rosters)
    results["nomi_dvw"] = ts.attrs.get("nomi_dvw", {})   # 'stagione|nome nel .dvw' → squadra-stagione
    calendar = load_calendar(config["gironi_fipav"][config["stagioni"][-1]])
    results["calendario"] = [{"giornata": m["day"], "data": m["date"], "casa": m["team1"]["title"].strip(),
                              "ospite": m["team2"]["title"].strip(), "giocata": bool(m.get("played"))}
                             for m in calendar]
    results["dati"] = {"attacchi": attacks, "battute": serves, "set": sets_played, "squadre": ts, "config": config,
                       "data": datetime.now().strftime("%d/%m/%Y %H:%M")}
    from src.identity import build_lineups
    results["dati"]["prime_linee"] = build_lineups(config)
    results["meta"] = {
        "data_stima": datetime.now().strftime("%d/%m/%Y %H:%M"), "durata_s": round(time.time() - t0),
        "partite": sets_played.groupby("stagione")["partita"].nunique().to_dict(),
        "attacchi": len(attacks), "battute": len(serves), "squadre": len(ts),
        "scartati": skipped, "non_abbinate": unmatched,
        "diagnostica": {"attacco": att_d, "battuta": srv_d, "transizioni": tr_d},
        "config": config,
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_PATH, "wb") as f:
        pickle.dump(results, f)
    say(f"Fatto in {results['meta']['durata_s']} s.")
    return results


def refresh_data(progress=None):
    """Rilegge i .dvw e aggiorna solo i dati salvati con i risultati
    (results["dati"], usati dall'identità di gioco), senza ristimare il
    modello: una partita nuova entra subito nell'analisi. Ritorna i risultati."""
    config = load_config()
    say = progress or (lambda t: print(t, flush=True))
    results = load_results()
    if results is None:
        say("Modello mai stimato: stima completa.")
        return fit_and_save(progress)
    say("Lettura delle partite…")
    attacks, serves, sets_played, _, rosters = build_dataset(config)
    attacks, serves, sets_played, ts, _, rosters = attach_teams(attacks, serves, sets_played, config, rosters)
    results["dati"] = {"attacchi": attacks, "battute": serves, "set": sets_played, "squadre": ts, "config": config,
                       "data": datetime.now().strftime("%d/%m/%Y %H:%M")}
    from src.identity import build_lineups
    results["dati"]["prime_linee"] = build_lineups(config)
    with open(RESULTS_PATH, "wb") as f:
        pickle.dump(results, f)
    say("Dati aggiornati.")
    return results


def load_results():
    """Ultimi risultati salvati, o None."""
    if not RESULTS_PATH.exists():
        return None
    with open(RESULTS_PATH, "rb") as f:
        return pickle.load(f)


if __name__ == "__main__":
    fit_and_save()
