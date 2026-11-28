# src/live_reference.py
"""
Valori "di solito" per l'app Scambi live (live/index.html): il rendimento
passato del Decimo e degli avversari, calcolato dai .dvw con le stesse voci
che l'app segna in partita, e scritto in live/riferimenti.json (l'app lo tiene
in cache e lo usa offline).

Ogni scambio di un .dvw viene ridotto a quello che si segna in diretta: chi
batte, chi vince e UNA azione che lo chiude, con chi la fa:
  att_pt_1 / att_pt_s   attacco punto, 1° attacco dopo ricezione / successivo
  err_att_1 / err_att_s errore d'attacco (=)
  mur_1 / mur_s         attacco murato (= muro punto dell'altra squadra);
                        chi "fa" l'azione è chi attacca
  ace / err_bat         battuta (chi fa = chi batte)
  invasione             errore di muro (/)
  altro                 alzata sbagliata e tutto il resto
1° attacco = l'attacco della squadra che riceve subito dopo la sua ricezione,
con al più una sua alzata in mezzo (stessa definizione di side_out_rallies in
src/match_report.py). Chi batte non ha mai un 1° attacco.

Uso: python -m src.live_reference   (rigenerare prima di ogni partita, poi
ricaricare la cartella live su Netlify).
"""
import glob
import json
import os
from datetime import date

import pandas as pd

from config.paths import build_base_path
from src.dvw import decimo_side, normalize_team_name, parse_dvw, read_dvw_header

N_MATCHES = 10
OUT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "live", "riferimenti.json")
SEASONS = ["2025-2026", "2026-2027"]


def _first_attack_index(a, receiver):
    """Indice (in `a`) del 1° attacco dopo ricezione della squadra che riceve, o None."""
    rec = a[(a["squadra"] == receiver) & (a["Tipo"] == "ricezione")]
    if rec.empty or rec.iloc[0]["Voto"] in ("/", "="):
        return None
    k = rec.index[0] + 1
    while k < len(a) and a.at[k, "squadra"] == receiver and a.at[k, "Tipo"] == "alzata":
        k += 1
    if k < len(a) and a.at[k, "squadra"] == receiver and a.at[k, "Tipo"] == "attacco":
        return k
    return None


def _decisive(a, winner, first_idx):
    """(azione, squadra che la fa) dello scambio, cercando a ritroso la prima
    azione che spiega il punto: vincente di chi vince o errore di chi perde."""
    loser = "a" if winner == "*" else "*"
    for k in range(len(a) - 1, -1, -1):
        t, v, s = a.at[k, "Tipo"], a.at[k, "Voto"], a.at[k, "squadra"]
        if t == "battuta" and v == "#" and s == winner:
            return "ace", winner
        if t == "battuta" and v == "=" and s == loser:
            return "err_bat", loser
        if t == "ricezione" and v == "=" and s == loser:
            return "ace", winner
        if t == "attacco" and s == winner and v == "#":
            return ("att_pt_1" if k == first_idx else "att_pt_s"), winner
        if t == "attacco" and s == loser and v == "=":
            return ("err_att_1" if k == first_idx else "err_att_s"), loser
        if t == "attacco" and s == loser and v == "/":
            return ("mur_1" if k == first_idx else "mur_s"), loser
        if t == "muro" and s == winner and v == "#":
            prev = a[(a.index < k) & (a["Tipo"] == "attacco") & (a["squadra"] == loser)]
            j = prev.index[-1] if len(prev) else None
            return ("mur_1" if j is not None and j == first_idx else "mur_s"), loser
        if t == "rigore" and s == winner and v == "#":
            # "rigore" (muro punto sulla loro ricezione o difesa /): non è un muro punto; per l'app è
            # un punto regalato da chi ha rimandato la palla di qua
            return "altro", loser
        if t == "muro" and s == loser and v == "/":
            return "invasione", loser
        if t == "alzata" and s == loser and v == "=":
            return "altro", loser
    return "altro", loser


def _rotation(row, side):
    prefix = "Casa" if side == "*" else "Ospiti"
    setter = row[f"Palleggiatore {'casa' if side == '*' else 'ospite'}"]
    if pd.isna(setter):
        return None
    for z in range(1, 7):
        if row[f"{prefix} P{z}"] == setter:
            return z
    return None


def live_rallies(parsed):
    """Uno per riga gli scambi del .dvw come li segnerebbe l'app live:
    set, server, winner, action, actor ('*'/'a') e rotazione di ciascuna squadra."""
    actions = parsed["actions"]
    actions = actions[~actions["Tipo"].isin(["cambio configurazione"])]
    start = parsed["actions"].groupby("Rally").first()
    rows = []
    for r in parsed["rallies"].to_dict("records"):
        a = actions[(actions["Rally"] == r["Rally"]) & (actions["Tipo"] != "generico")].reset_index(drop=True)
        serve = a[a["Tipo"] == "battuta"]
        if serve.empty:
            continue  # scambio senza battuta rilevata: non si sa chi batteva
        server = serve.iloc[0]["squadra"]
        receiver = "a" if server == "*" else "*"
        action, actor = _decisive(a, r["vincitore"], _first_attack_index(a, receiver))
        st = start.loc[r["Rally"]]
        sv = serve.iloc[0]   # i sei in campo al momento della battuta (dopo eventuali cambi)
        six = {s: [None if pd.isna(sv[f"{p} P{z}"]) else int(sv[f"{p} P{z}"]) for z in range(1, 7)]
               for s, p in (("*", "Casa"), ("a", "Ospiti"))}
        rows.append({"Rally": r["Rally"], "set": r["Numero Set"], "server": server, "winner": r["vincitore"],
                     "action": action, "actor": actor,
                     "rot_*": _rotation(st, "*"), "rot_a": _rotation(st, "a"),
                     "six_*": six["*"], "six_a": six["a"]})
    return pd.DataFrame(rows)


def team_counts(rallies, side, with_rotations=False):
    """Conteggi della squadra `side` sugli scambi (stesse definizioni dell'app):
    da questi l'app ricava side-out, break-point, 1° attacco stimato, ecc."""
    other = "a" if side == "*" else "*"
    mine, theirs = rallies["actor"] == side, rallies["actor"] == other
    recv, serv = rallies["server"] == other, rallies["server"] == side
    won = rallies["winner"] == side
    act = rallies["action"]
    lost_serve = recv & theirs & act.isin(["ace", "err_bat"])  # ricezione mai giocata
    c = lambda m: int(m.sum())
    out = {
        "set": int(rallies.groupby(["match", "set"]).ngroups) if "match" in rallies else int(rallies["set"].nunique()),
        "scambi": len(rallies),
        "ric": c(recv), "so": c(recv & won), "bat": c(serv), "bp": c(serv & won),
        "ric_gioco": c(recv & ~lost_serve),
        "pt1": c(mine & (act == "att_pt_1")), "err1": c(mine & (act == "err_att_1")), "mur1": c(mine & (act == "mur_1")),
        "pts": c(mine & (act == "att_pt_s")), "errs": c(mine & (act == "err_att_s")), "murs": c(mine & (act == "mur_s")),
        "ace": c(mine & (act == "ace")), "err_bat": c(mine & (act == "err_bat")),
        "muri": c(theirs & act.isin(["mur_1", "mur_s"])),
        "gratuiti": c(mine & act.isin(["invasione", "altro"])),
        "punti": c(won), "subiti": c(~won),
    }
    # i tre momenti dello scambio, in ricezione (cambio palla) e in battuta (break-point):
    # 1) la battuta, 2) il primo attacco di chi riceve, 3) tutto il resto ("scambio lungo")
    first = act.isin(["att_pt_1", "err_att_1", "mur_1"])
    srv_end = act.isin(["ace", "err_bat"])
    out.update({
        "so_ace_sub": c(recv & theirs & (act == "ace")), "so_err_loro": c(recv & theirs & (act == "err_bat")),
        "so3_n": c(recv & ~srv_end & ~first), "so3_w": c(recv & ~srv_end & ~first & won),
        "bat_gioco": c(serv & ~srv_end),
        "o_pt1": c(serv & theirs & (act == "att_pt_1")), "o_err1": c(serv & theirs & (act == "err_att_1")),
        "o_mur1": c(serv & theirs & (act == "mur_1")),
        "bp3_n": c(serv & ~srv_end & ~first), "bp3_w": c(serv & ~srv_end & ~first & won),
    })
    if with_rotations:
        rot = {}
        for z in range(1, 7):
            rr = rallies[(rallies[f"rot_{side}"] == z) & recv]
            rs = rallies[(rallies[f"rot_{side}"] == z) & serv]
            if rr.empty:
                continue
            rw = rr["winner"] == side
            rl = (rr["actor"] == other) & rr["action"].isin(["ace", "err_bat"])
            rm = rr["actor"] == side
            rot[str(z)] = {"ric": len(rr), "so": int(rw.sum()), "ric_gioco": int((~rl).sum()),
                           "pt1": int((rm & (rr["action"] == "att_pt_1")).sum()),
                           "err1": int((rm & (rr["action"] == "err_att_1")).sum()),
                           "mur1": int((rm & (rr["action"] == "mur_1")).sum()),
                           "bat": len(rs), "bp": int((rs["winner"] == side).sum())}
        out["rot"] = rot
    return out


def _date_key(d):
    try:
        dd, mm, yy = (int(x) for x in str(d).split("/"))
        return (yy, mm, dd)
    except ValueError:
        return (0, 0, 0)


def _catalog():
    """Tutti i .dvw delle stagioni, senza duplicati (stessa data e squadre: il
    file più grande) e senza file con una squadra senza nome."""
    files = []
    for season in SEASONS:
        try:
            base = build_base_path(season)
        except Exception:
            continue
        for path in glob.glob(str(base / "**" / "*.dvw"), recursive=True):
            try:
                h = read_dvw_header(path)
            except Exception:
                continue
            names = [n for n in h["teams"].values() if n]
            if h["data"] and len(names) == 2:
                files.append({"path": path, "data": h["data"], "teams": h["teams"],
                              "key": (h["data"], tuple(sorted(normalize_team_name(n) for n in names)))})
    best = {}
    for f in files:
        if f["key"] not in best or os.path.getsize(f["path"]) > os.path.getsize(best[f["key"]]["path"]):
            best[f["key"]] = f
    return sorted(best.values(), key=lambda f: _date_key(f["data"]))


# ---------------------------------------------------------------------------
# Per l'app nuova (2026-11-12): rosa, battitori, obiettivi del set, momenti caldi, partite già giocate
# ---------------------------------------------------------------------------
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAST_PATH = os.path.join(REPO, "live", "partite.json")
ROLE_CODES = {1: "L", 2: "M", 3: "O", 4: "C", 5: "P"}
# obiettivi del set (livello medio dei set vinti, arrotondato): saldi "almeno", errori gratuiti "al massimo"
GOALS = {"bat": -1, "a1": 4, "ac": 4, "muri": 3, "grat": 1}
GOAL_KEYS = ["bat", "a1", "ac", "muri", "grat"]
APP_ACTION = {"err_bat": "err_battuta"}   # stessa azione, nome diverso nell'app


def _season_of(d):
    dd, mm, yy = _date_key(d)[2], _date_key(d)[1], _date_key(d)[0]
    return f"{yy}-{yy + 1}" if mm >= 8 else f"{yy - 1}-{yy}"


def pretty_name(cognome):
    """'D’ELIA ' / 'D’Elia' -> "D'Elia"."""
    c = str(cognome).strip().replace("’", "'").replace("`", "'")
    return " ".join("'".join(x[:1].upper() + x[1:].lower() for x in w.split("'")) for w in c.split())


def _excluded(season):
    """Cognomi (minuscoli) fuori rosa: non_in_rosa della stagione e IGNORED del Decimo."""
    out = set()
    cfg = os.path.join(REPO, "config", "performance_model.json")
    if os.path.exists(cfg):
        with open(cfg, encoding="utf-8") as f:
            out |= {c.lower() for c in json.load(f).get("non_in_rosa", {}).get(season, [])}
    ids = os.path.join(REPO, "config", "player_identities.csv")
    if os.path.exists(ids):
        df = pd.read_csv(ids)
        out |= set(df.loc[(df["player_id"] == "IGNORED") & df["team"].str.lower().str.contains("decimo"), "cognome"].str.lower())
    return out


def _played(p, rr, side):
    """Numeri del lato `side` che hanno giocato: in campo in almeno uno scambio o libero in un set."""
    nums = {n for six in rr[f"six_{side}"] for n in six if n}
    pl = p["players"][p["players"]["squadra"] == side]
    for row in pl.itertuples():
        if row.ruolo == 1 and any(z == "*" for z in (row.zone_partenza or [])):
            nums.add(row.numero)
    return nums


def _team_players(p, side):
    from src.dvw import roster_by_number
    return {num: x for (sq, num), x in roster_by_number(p["players"]).items() if sq == side}


def fixed_roles():
    """Ruoli del Decimo fissati dall'utente (config/ruoli.csv, cognome -> P/M/C/O/L): valgono più del
    ruolo scritto nel .dvw, che cambia da una partita all'altra (chi entra solo in battuta, chi gioca
    qualche volta in un altro ruolo). Chiave: cognome minuscolo."""
    path = os.path.join(REPO, "config", "ruoli.csv")
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path)
    return {pretty_name(c).lower(): r.strip().upper() for c, r in zip(df["cognome"], df["ruolo"])}


def build_roster(files, rallies_of):
    """Rosa del Decimo nella stagione in corso. Numero dell'ultima partita in cui compare, ruolo più
    frequente nelle partite in cui ha giocato (nel .dvw il ruolo può cambiare da una partita all'altra);
    'referto' = ha giocato in una delle ultime due partite (nell'app è già spuntato). Chi è nella rosa
    dell'ultima partita senza aver mai giocato compare non spuntato, con il ruolo da scegliere ('?')."""
    from collections import Counter
    season = _season_of(files[-1]["data"]) if files else None
    excluded = _excluded(season) if season else set()
    roster, roles = {}, {}
    for i, f in enumerate(files):
        p, rr = rallies_of(f)
        side = decimo_side(p)
        played = _played(p, rr, side)
        last = i == len(files) - 1
        for num, x in _team_players(p, side).items():
            c = pretty_name(x.cognome)
            if c.lower() in excluded or (num not in played and not last):
                continue
            role = ROLE_CODES.get(int(x.ruolo)) if x.ruolo is not None and not pd.isna(x.ruolo) else None
            if role and num in played:
                roles.setdefault(c.lower(), Counter())[role] += 1
            prev = roster.get(c.lower(), {"referto": False})
            roster[c.lower()] = {"n": int(num), "c": c,
                                 "referto": prev["referto"] or (i >= len(files) - 2 and num in played)}
    fixed = fixed_roles()
    for k, x in roster.items():
        x["r"] = fixed.get(k) or (roles[k].most_common(1)[0][0] if k in roles else "?")
    return sorted(roster.values(), key=lambda x: x["n"])


def server_stats(files, rallies_of):
    """Battute, ace ed errori di ogni battitore del Decimo (chi batte = chi è in zona 1)."""
    out = {}
    for f in files:
        p, rr = rallies_of(f)
        side = decimo_side(p)
        names = {num: pretty_name(x.cognome) for num, x in _team_players(p, side).items()}
        for r in rr[rr["server"] == side].to_dict("records"):
            n = r[f"six_{side}"][0]
            if not n:
                continue
            b = out.setdefault(names.get(n, str(n)), {"battute": 0, "ace": 0, "errori": 0})
            b["battute"] += 1
            if r["actor"] == side and r["action"] == "ace":
                b["ace"] += 1
            if r["actor"] == side and r["action"] == "err_bat":
                b["errori"] += 1
    return out


def change_stats(files, rallies_of):
    """Per le proposte di cambio dell'app live (2026-11-28), su tutte le partite del Decimo: scambi in cambio palla e
    break-point con il palleggiatore di partenza in prima linea (P4-P3-P2 della sua posizione), con e senza doppio
    cambio (stessa definizione di replay in live/core.js: doppio cambio = il palleggiatore in campo non è nella
    posizione del palleggiatore di partenza); battute e break-point vinti di ogni battitore."""
    fixed = fixed_roles()
    dc = {"si": {"ric": 0, "so": 0, "bat": 0, "bp": 0}, "no": {"ric": 0, "so": 0, "bat": 0, "bp": 0}}
    srv = {}
    for f in files:
        p, rr = rallies_of(f)
        side = decimo_side(p)
        other = "a" if side == "*" else "*"
        pl = _team_players(p, side)
        names = {num: pretty_name(x.cognome) for num, x in pl.items()}
        role = {num: fixed.get(names[num].lower()) or ROLE_CODES.get(int(x.ruolo)) if x.ruolo is not None and not pd.isna(x.ruolo) else None
                for num, x in pl.items()}
        for _, g in rr.groupby("set", sort=True):
            g = g.sort_values("Rally")
            six0 = list(g.iloc[0][f"six_{side}"])
            slot0 = next((i for i, n in enumerate(six0) if n and role.get(n) == "P"), None)
            shift = 0
            for r in g.to_dict("records"):
                recv, won = r["server"] == other, r["winner"] == side
                if slot0 is not None and r[f"rot_{side}"] is not None and not pd.isna(r[f"rot_{side}"]):
                    lr = (slot0 - shift) % 6 + 1
                    if lr in (2, 3, 4):
                        c = dc["si" if int(r[f"rot_{side}"]) != lr else "no"]
                        if recv:
                            c["ric"] += 1
                            c["so"] += int(won)
                        else:
                            c["bat"] += 1
                            c["bp"] += int(won)
                if not recv and r[f"six_{side}"][0]:
                    b = srv.setdefault(names.get(r[f"six_{side}"][0], str(r[f"six_{side}"][0])), {"bat": 0, "bp": 0})
                    b["bat"] += 1
                    b["bp"] += int(won)
                if recv and won:
                    shift += 1
    return {"doppio_cambio": dc, "battitori": srv}


def set_table(catalog, rallies_of):
    """Una riga per squadra e set (quinti set e set con meno di 30 scambi esclusi): i cinque saldi
    degli obiettivi, quelli dell'avversario, vinto/perso, scambi giocati."""
    rows = []
    for f in catalog:
        try:
            p, r = rallies_of(f)
        except Exception:
            continue
        for s, g in r.groupby("set"):
            if s == 5 or len(g) < 30:
                continue
            vals = {}
            for side in ("*", "a"):
                other = "a" if side == "*" else "*"
                act, mine, theirs = g["action"], g["actor"] == side, g["actor"] == other
                vals[side] = {
                    "bat": int((mine & (act == "ace")).sum() - (mine & (act == "err_bat")).sum()),
                    "a1": int((mine & (act == "att_pt_1")).sum() - (mine & act.isin(["err_att_1", "mur_1"])).sum()),
                    "ac": int((mine & (act == "att_pt_s")).sum() - (mine & act.isin(["err_att_s", "mur_s"])).sum()),
                    "muri": int((theirs & act.isin(["mur_1", "mur_s"])).sum()),
                    "grat": int((mine & act.isin(["invasione", "altro"])).sum()),
                }
            for side in ("*", "a"):
                other = "a" if side == "*" else "*"
                rows.append({"team": normalize_team_name(p["teams"][side]), "file": f["path"], "set": s, "scambi": len(g),
                             "vinto": bool((g["winner"] == side).sum() > (g["winner"] == other).sum()),
                             **vals[side], **{"o_" + k: v for k, v in vals[other].items()}})
    return pd.DataFrame(rows)


def _goal_features(D):
    X = pd.DataFrame({k: (D[k] <= GOALS[k]) if k == "grat" else (D[k] >= GOALS[k]) for k in GOAL_KEYS})
    for k in GOAL_KEYS:
        X["o_" + k] = (D["o_" + k] <= GOALS[k]) if k == "grat" else (D["o_" + k] >= GOALS[k])
    return X.astype(float)


def _logit_fit(X, y, lam=1.0):
    import numpy as np
    A = np.c_[np.ones(len(X)), X.values]
    b = np.zeros(A.shape[1])
    pen = lam * np.diag([0.0] + [1.0] * (A.shape[1] - 1))
    with np.errstate(all="ignore"):
        for _ in range(50):
            pr = 1 / (1 + np.exp(-A @ b))
            H = A.T @ (A * (pr * (1 - pr))[:, None]) + pen
            b -= np.linalg.solve(H, A.T @ (pr - y) + pen @ b)
    return b


def goal_model(D):
    """Probabilità di vincere il set in base a quali obiettivi (nostri e loro) sono raggiunti:
    regressione logistica sui set di tutti i .dvw. 'vale' = di quanto sale in media la probabilità
    quando l'obiettivo è raggiunto. Ritmi per scambio (media e varianza) per proiettare il set."""
    import numpy as np
    X = _goal_features(D)
    y = D["vinto"].values.astype(float)
    b = _logit_fit(X, y)
    P = lambda M: 1 / (1 + np.exp(-(b[0] + M @ b[1:])))
    vale = {}
    for i, c in enumerate(X.columns):
        on, off = X.values.copy(), X.values.copy()
        on[:, i], off[:, i] = 1, 0
        vale[c] = round(float(P(on).mean() - P(off).mean()), 3)
    dec = D[D["team"].str.contains("decimo")]

    def rates(d):
        n = d["scambi"].mean()
        return {k: {"m": round(float(d[k].mean() / n), 4), "v": round(float(d[k].var() / n), 4)} for k in GOAL_KEYS}
    return {"soglie": GOALS, "coef": {"b0": round(float(b[0]), 3), **{c: round(float(v), 3) for c, v in zip(X.columns, b[1:])}},
            "vale": vale, "set": int(len(D)),
            "ritmo_noi": rates(dec), "ritmo_tutti": rates(D), "scambi_set": round(float(D["scambi"].mean()), 1)}


def hot_moments(catalog, rallies_of):
    """Rotazione nei momenti caldi (una squadra tra 18 e 20, scarto al più 2) rispetto a quella di
    partenza: quota per numero di cambi palla vinti mod 6 (0 = la stessa, 5 = quella prima)."""
    import numpy as np
    tot, nsets = np.zeros(6), 0
    for f in catalog:
        try:
            p, r = rallies_of(f)
        except Exception:
            continue
        for s, g in r.groupby("set"):
            if s == 5 or len(g) < 30:
                continue
            g = g.reset_index(drop=True)
            for side in ("*", "a"):
                other = "a" if side == "*" else "*"
                won, recv = g["winner"] == side, g["server"] == other
                a, b = won.cumsum().shift(fill_value=0), (~won).cumsum().shift(fill_value=0)
                so = (recv & won).cumsum().shift(fill_value=0)
                m = (a.between(18, 20) | b.between(18, 20)) & ((a - b).abs() <= 2)
                if m.any():
                    tot += np.bincount((so[m] % 6).astype(int), minlength=6) / m.sum()
                    nsets += 1
    return {"quote": [round(float(x / nsets), 3) for x in tot], "set": nsets}


def past_matches(rallies_of, roles=None):
    """Partite della stagione in corso in config/matches.csv (con .dvw) nel formato dell'app: rosa,
    formazione di partenza e libero per set, eventi (scambi, cambi, correzioni di battuta e formazione
    quando lo scout salta qualcosa). I timeout il .dvw non li ha."""
    matches = pd.read_csv(os.path.join(REPO, "config", "matches.csv"))
    season = SEASONS[-1]
    rows = matches[(matches["season"] == season) & (matches["active"] == 1) & matches["path"].str.endswith(".dvw")]
    base = build_base_path(season)
    out, fixes = [], {}
    for row in rows.itertuples():
        path = str(base / row.path)
        h = read_dvw_header(path)
        f = {"path": path, "data": h["data"], "teams": h["teams"]}
        p, rr = rallies_of(f)
        side = decimo_side(p)
        other = "a" if side == "*" else "*"
        team = lambda x: "noi" if x == side else "loro"
        players = _team_players(p, side)
        played = _played(p, rr, side)
        # ruolo della stagione (il più frequente), non quello della singola partita
        roster = [{"n": int(num), "c": pretty_name(x.cognome),
                   "r": (roles or {}).get(pretty_name(x.cognome).lower())
                   or (ROLE_CODES.get(int(x.ruolo)) if x.ruolo is not None and not pd.isna(x.ruolo) else "?")}
                  for num, x in sorted(players.items()) if num in played]
        sets, events, nfix = [], [], 0
        for s, g in rr.groupby("set", sort=True):
            g = g.sort_values("Rally")
            first = g.iloc[0]
            six = list(first[f"six_{side}"])
            lib = [x.numero for x in p["players"][p["players"]["squadra"] == side].itertuples()
                   if x.ruolo == 1 and len(x.zone_partenza or []) >= s and x.zone_partenza[s - 1] == "*"]
            sets.append({"firstServer": team(first["server"]), "lineup": {str(z): six[z - 1] for z in range(1, 7)},
                         "libero": int(lib[0]) if lib else None})
            server = first["server"]
            for r in g.to_dict("records"):
                real = list(r[f"six_{side}"])
                if r["server"] != server:
                    events.append({"t": "srv", "set": int(s)})
                    nfix += 1
                    server = r["server"]
                if real != six:
                    diff = [z for z in range(6) if real[z] != six[z]]
                    if all(real[z] not in six for z in diff):
                        for z in diff:
                            events.append({"t": "sub", "set": int(s), "out": six[z], "in": real[z]})
                    else:
                        events.append({"t": "six", "set": int(s), "six": real})
                        nfix += 1
                    six = real
                events.append({"t": "r", "set": int(s), "actor": team(r["actor"]), "winner": team(r["winner"]),
                               "action": APP_ACTION.get(r["action"], r["action"])})
                if r["winner"] == side and r["server"] == other:
                    six = six[1:] + six[:1]   # si ruota: chi era in 2 va in 1
                server = r["winner"]
        dd, mm, yy = h["data"].split("/")
        out.append({"id": "dvw-" + os.path.basename(path), "importata": True, "opponent": row.opponent,
                    "refKey": normalize_team_name(p["teams"][other]), "date": f"{yy}-{mm}-{dd}",
                    "roster": roster, "sets": sets, "events": events})
        fixes[row.opponent] = nfix
    return out, fixes


def build_reference(n_matches=N_MATCHES):
    catalog = _catalog()
    parsed_cache = {}

    def rallies_of(f):
        if f["path"] not in parsed_cache:
            p = parse_dvw(f["path"])
            parsed_cache[f["path"]] = (p, live_rallies(p))
        return parsed_cache[f["path"]]

    def summary(entries, side_of, rotations=False):
        parts, labels = [], []
        for i, f in enumerate(entries):
            p, rr = rallies_of(f)
            side = side_of(p)
            other = "a" if side == "*" else "*"
            parts.append(rr.assign(match=i, _side=side))
            labels.append(f"{f['data']} {p['teams'][side]} - {p['teams'][other]}")
        # ogni partita con la squadra sullo stesso lato "*": così i conteggi si sommano
        norm = []
        for df in parts:
            if df["_side"].iloc[0] == "a":
                swap = {"*": "a", "a": "*"}
                df = df.assign(server=df["server"].map(swap), winner=df["winner"].map(swap),
                               actor=df["actor"].map(swap)).rename(columns={"rot_*": "rot_a", "rot_a": "rot_*",
                                                                             "six_*": "six_a", "six_a": "six_*"})
            norm.append(df.drop(columns="_side"))
        allr = pd.concat(norm, ignore_index=True)
        return {"partite": labels, "totali": team_counts(allr, "*", with_rotations=rotations)}

    # Decimo: le ultime N partite su .dvw
    decimo_files = [f for f in catalog if any("decimo" in normalize_team_name(n) for n in f["teams"].values())]
    dec = summary(decimo_files[-n_matches:], decimo_side, rotations=True)

    # avversari: per ogni squadra le sue ultime N partite (anche contro altre squadre)
    teams = {}
    for f in catalog:
        for n in f["teams"].values():
            k = normalize_team_name(n)
            if "decimo" not in k:
                teams.setdefault(k, {"nome": n, "files": []})["files"].append(f)
    squadre = {}
    for k, t in sorted(teams.items()):
        def side_of(p, k=k):
            return next(s for s, n in p["teams"].items() if normalize_team_name(n) == k)
        try:
            squadre[k] = {"nome": t["nome"].strip(), **summary(t["files"][-n_matches:], side_of)}
        except Exception as e:  # una squadra illeggibile non blocca le altre
            print(f"  salto {t['nome']}: {e}")
    # per l'app nuova: rosa e battitori del Decimo, modello degli obiettivi, momenti caldi
    season_files = [f for f in decimo_files if _season_of(f["data"]) == SEASONS[-1]]
    dec["battitori"] = server_stats(decimo_files[-n_matches:], rallies_of)
    dec["cambi"] = change_stats(decimo_files, rallies_of)
    D = set_table(catalog, rallies_of)
    ref = {"generato": date.today().isoformat(), "n_partite": n_matches, "decimo": dec, "squadre": squadre,
           "rosa": build_roster(season_files or decimo_files[-2:], rallies_of),
           "modello": goal_model(D), "momenti_caldi": hot_moments(catalog, rallies_of)}
    # probabilità di vincere il set (src/set_win_prob.py): totali del campionato per il "solito" delle squadre
    from src.set_win_prob import prob_set_params
    league = {"ric": 0, "so": 0, "bat": 0, "bp": 0}
    for f in catalog:
        try:
            _, rr = rallies_of(f)
        except Exception:
            continue
        for side in ("*", "a"):                  # tutte e due le squadre: la sola squadra di casa ha il vantaggio del campo
            c = team_counts(rr, side)
            for k in league:
                league[k] += c[k]
    ref["prob_set"] = prob_set_params(league)
    ref["_partite"] = past_matches(rallies_of, {**{x["c"].lower(): x["r"] for x in ref["rosa"] if x["r"] != "?"}, **fixed_roles()})
    return ref


def main():
    ref = build_reference()
    past, fixes = ref.pop("_partite")
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(ref, f, ensure_ascii=False, indent=1)
    with open(PAST_PATH, "w", encoding="utf-8") as f:
        json.dump({"generato": ref["generato"], "partite": past}, f, ensure_ascii=False)
    print(f"Scritto {PAST_PATH}: " + ", ".join(f"{m['opponent']} ({sum(e['t'] == 'r' for e in m['events'])} scambi, "
                                                f"{sum(e['t'] == 'sub' for e in m['events'])} cambi, {fixes[m['opponent']]} correzioni)" for m in past))
    t = ref["decimo"]["totali"]
    print(f"Scritto {OUT_PATH}")
    print(f"Decimo, {len(ref['decimo']['partite'])} partite: side-out {t['so']}/{t['ric']} "
          f"({100 * t['so'] / t['ric']:.0f}%), break-point {t['bp']}/{t['bat']} ({100 * t['bp'] / t['bat']:.0f}%), "
          f"1° attacco {100 * (t['pt1'] - t['err1'] - t['mur1']) / t['ric_gioco']:.0f}%")
    print(f"Avversari: {len(ref['squadre'])} squadre")


if __name__ == "__main__":
    main()
