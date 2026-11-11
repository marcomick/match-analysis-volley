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
        if t == "rigore" and s == winner:
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
        rows.append({"set": r["Numero Set"], "server": server, "winner": r["vincitore"],
                     "action": action, "actor": actor,
                     "rot_*": _rotation(st, "*"), "rot_a": _rotation(st, "a")})
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
    if with_rotations:
        rot = {}
        for z in range(1, 7):
            rr = rallies[(rallies[f"rot_{side}"] == z) & recv]
            if rr.empty:
                continue
            rw = rr["winner"] == side
            rl = (rr["actor"] == other) & rr["action"].isin(["ace", "err_bat"])
            rm = rr["actor"] == side
            rot[str(z)] = {"ric": len(rr), "so": int(rw.sum()), "ric_gioco": int((~rl).sum()),
                           "pt1": int((rm & (rr["action"] == "att_pt_1")).sum()),
                           "err1": int((rm & (rr["action"] == "err_att_1")).sum()),
                           "mur1": int((rm & (rr["action"] == "mur_1")).sum())}
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
                               actor=df["actor"].map(swap)).rename(columns={"rot_*": "rot_a", "rot_a": "rot_*"})
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
    return {"generato": date.today().isoformat(), "n_partite": n_matches, "decimo": dec, "squadre": squadre}


def main():
    ref = build_reference()
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(ref, f, ensure_ascii=False, indent=1)
    t = ref["decimo"]["totali"]
    print(f"Scritto {OUT_PATH}")
    print(f"Decimo, {len(ref['decimo']['partite'])} partite: side-out {t['so']}/{t['ric']} "
          f"({100 * t['so'] / t['ric']:.0f}%), break-point {t['bp']}/{t['bat']} ({100 * t['bp'] / t['bat']:.0f}%), "
          f"1° attacco {100 * (t['pt1'] - t['err1'] - t['mur1']) / t['ric_gioco']:.0f}%")
    print(f"Avversari: {len(ref['squadre'])} squadre")


if __name__ == "__main__":
    main()
