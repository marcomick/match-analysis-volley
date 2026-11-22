# src/mvp.py
"""
MVP della partita (criteri concordati con l'utente il 2026-10-31).

Ogni azione vale in punti quanto sposta la probabilità di vincere lo scambio
(tabella in config/mvp.json, calcolata su tutti i .dvw e arrotondata a 0,05):
muro punto +0,65, ace +0,60, ricezione # +0,15 e + +0,12, ...; invasione a
muro -1,00; l'attacco difeso (-) e la battuta facile (-) valgono 0, così chi
attacca tanto non viene penalizzato. Muro e difesa sono confrontati con
"attacco avversario in gioco": un muro o una difesa non segnati valgono 0.

Revisione del 2026-11-09 (pari chance tra i ruoli per chi gioca davvero bene,
verificata su tutte le partite .dvw):
- attacco per situazione (attacco_per_situazione): da palla buona (R#/R+,
  free ball) l'errore costa poco, su palla alta (tipo di palla rilevato o
  stimato, src.dvw._infer_ball_type) il punto vale ma l'errore pesa; il
  punto da 2ª linea vale attacco_seconda_linea in più;
- alzate: E# (meritevole), E+ (attaccante contro muro a 1), E- (penalità),
  più alzata_a_punto se l'attacco che segue va a punto;
- premio al miglior ricevitore della partita: miglior_ricevitore.premio meno
  i punti già fatti in attacco, battuta e muro (mai sotto zero), così il
  libero può vincere e il martello che riceve bene non scappa via.

Palleggiatore: quota x (p-e/m degli attacchi alzati da lui dopo R#, R+, R! o
free ball - p-e/m medio atteso su quegli attacchi), cioè solo la parte "più
del solito" (con la quota assoluta era sul podio 17 partite su 29). Gli
attacchi del palleggiatore stesso non contano. In config si può limitare la
quota alle alzate veloci (la palla alta non è detto che la alzi lui).
Colonna "alzata": per il palleggiatore quota meno errori di alzata, per gli
altri solo gli errori di alzata.

Chi gioca meno: un punteggio positivo vale per i set giocati, punteggio =
totale x (set della partita + set fittizi) / (set giocati + set fittizi); un
set conta come giocato se il giocatore era tra i sei in campo in almeno uno
scambio o ha fatto un'azione (libero). Con 1 set fittizio chi gioca gli
ultimi due set di cinque vince con metà del punteggio del titolare.
"""
import json
import math
from pathlib import Path

import pandas as pd

from src.dvw import roster_by_number
from src.efficiency import separate_attack_types
from src.set_analysis import BALL_FAST, BALL_HIGH, entity_key

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "mvp.json"
# Sotto questo numero di azioni niente percentuali nei testi (un'efficienza
# su pochi attacchi non dice niente).
MIN_EVENTS_FOR_PERCENT = 10
FUNDAMENTALS = ["battuta", "ricezione", "attacco", "muro", "difesa", "alzata"]
FUNDAMENTAL_LABELS = {"battuta": "Battuta", "ricezione": "Ricezione", "attacco": "Attacco", "muro": "Muro",
                      "difesa": "Difesa", "alzata": "Alzata"}
SETTER_SITUATION_GRADES = ["#", "+", "!"]  # ricezioni da cui conta l'attacco alzato (più la free ball)


def load_mvp_config(path=CONFIG_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _sets_played(source):
    """{numero: set giocati} e numero di set della partita."""
    actions = source["parsed"]["actions"]
    prefix = "Casa" if source["side"] == "*" else "Ospiti"
    played = {}
    for z in range(1, 7):
        for st, n in actions[["Numero Set", f"{prefix} P{z}"]].dropna().drop_duplicates().itertuples(index=False):
            played.setdefault(int(n), set()).add(st)
    noi = source["noi"]
    for st, n in noi[["Numero Set", "Numero"]].dropna().drop_duplicates().itertuples(index=False):
        played.setdefault(int(n), set()).add(st)
    return {n: len(s) for n, s in played.items()}, actions["Numero Set"].dropna().nunique()


def _scored_actions(source, cfg):
    """Le azioni dei nostri giocatori con il loro valore MVP ('valore') e la
    voce della tabella dei punti ('voce'); anche gli attacchi side-out e
    free ball (separate_attack_types) per la quota del palleggiatore."""
    noi = source["noi"]
    df = noi[noi["Numero"].notna() & noi["Tipo"].isin(FUNDAMENTALS + ["rigore"])].copy()
    # "rigore" (muro punto sulla loro ricezione o difesa rimandata di qua, src.dvw._mark_rigori):
    # nel punteggio vale come un attacco punto e sta nella colonna Attacco (indicazione dell'utente, 2026-11-09)
    rigori = df.index[df["Tipo"] == "rigore"]
    rig_pt = df.index[(df["Tipo"] == "rigore") & (df["Voto"] == "#")]
    rig_err = df.index[(df["Tipo"] == "rigore") & df["Voto"].isin(["=", "/"])]
    df.loc[rigori, "Tipo"] = "attacco"
    # rigore sbagliato (fuori, in rete o invasione) come un attacco sbagliato; difeso = attacco in gioco (dal 2026-11-22)
    df.loc[rig_err, "Voto"] = "="
    df["valore"] = [cfg["azioni"].get(t, {}).get(v, 0.0) for t, v in zip(df["Tipo"], df["Voto"])]
    df["voce"] = [ACTION_DESCRIPTIONS.get(t, {}).get(v) or f"{FUNDAMENTAL_LABELS[t]} con voto {v} ({GRADE_CODES[t]}{v})"
                  for t, v in zip(df["Tipo"], df["Voto"])]
    df.loc[rig_pt, "voce"] = RIGORE_LABEL
    df.loc[rig_err, "voce"] = RIGORE_ERR_LABEL
    df.loc[rigori.difference(rig_pt).difference(rig_err), "voce"] = RIGORE_PLAY_LABEL
    so, fb, _ = separate_attack_types(noi, rec_vote=SETTER_SITUATION_GRADES)
    # attacco per situazione: palla alta (tipo di palla rilevato o stimato in lettura,
    # src.dvw._infer_ball_type) prima di tutto, poi free ball, poi side-out da R# o R+
    so_good, _, _ = separate_attack_types(noi, rec_vote=["#", "+"])
    attacks = df[(df["Tipo"] == "attacco") & df["Voto"].isin(["#", "=", "/"])].index.difference(rigori)
    for i in attacks:
        sit = ("palla alta" if df.at[i, "Tipo palla"] in BALL_HIGH else "free ball" if i in fb.index
               else "R#/R+" if i in so_good.index else None)
        if sit and sit in cfg.get("attacco_per_situazione", {}):
            v = df.at[i, "Voto"]
            df.at[i, "valore"] = cfg["attacco_per_situazione"][sit][v]
            df.at[i, "voce"] = SITUATION_LABELS[(sit, v)]
    # attacco punto da 2ª linea (in P1, P5 o P6 al momento dell'attacco): vale di più
    prefix = "Casa" if source["side"] == "*" else "Ospiti"
    back = [i for i in attacks if df.at[i, "Voto"] == "#"
            and df.at[i, "Numero"] in {df.at[i, f"{prefix} P{z}"] for z in (1, 5, 6)}]
    df.loc[back, "valore"] += cfg.get("attacco_seconda_linea", 0.0)
    df.loc[back, "voce"] = [f"{v.replace(' (A#)', '')}, da 2ª linea (A#)" for v in df.loc[back, "voce"]]
    # alzata # o + seguita da un attacco punto nello stesso scambio: vale di più
    bonus = cfg.get("alzata_a_punto", 0.0)
    for i in df.index[(df["Tipo"] == "alzata") & df["Voto"].isin(["#", "+"])]:
        j = noi.index.get_loc(i) + 1
        if j < len(noi) and noi.iloc[j]["Tipo"] == "attacco" and noi.iloc[j]["Voto"] == "#" \
                and noi.iloc[j]["Rally"] == df.at[i, "Rally"]:
            df.at[i, "valore"] += bonus
            df.at[i, "voce"] = df.at[i, "voce"].replace(")", " a punto)")
    # errore in battuta secondo il tipo di battuta (salto spin / salto float), dove è rilevato
    for code, value in cfg.get("errore_battuta_per_tipo", {}).items():
        err = df[(df["Tipo"] == "battuta") & (df["Voto"] == "=") & (df["Tipo palla"] == code)].index
        df.loc[err, "valore"] = value
        df.loc[err, "voce"] = SERVE_ERROR_LABELS.get(code, f"Errore in battuta (B=, tipo {code})")
    df["numero"] = df["Numero"].astype(int)
    return df, so, fb


def match_mvp(source, config=None):
    """
    Punteggio MVP di ogni giocatore della squadra "noi" di `source`
    (src.match_report.load_report_source). DataFrame ordinato per punteggio,
    una riga per giocatore: giocatore ('<numero> <cognome>'), cognome, un
    contributo per fondamentale (FUNDAMENTALS), totale, set_giocati,
    set_partita, punteggio (totale corretto per i set giocati, solo se
    positivo), alzati_n/alzati_pem/alzati_attesi (palleggiatori: attacchi
    alzati che contano per la quota, loro p-e/m e p-e/m atteso) e i conteggi
    per voto (colonne '<fondamentale> <voto>', più 'attacco rigore') per descrivere la partita.
    """
    cfg = config or load_mvp_config()
    df, so, fb = _scored_actions(source, cfg)
    best_rec = _best_receiver(df, source, cfg)
    contrib = df.pivot_table(index="numero", columns="Tipo", values="valore", aggfunc="sum", fill_value=0.0)
    contrib = contrib.reindex(columns=FUNDAMENTALS, fill_value=0.0)
    counts = df.assign(k=df["Tipo"] + " " + df["Voto"]).pivot_table(
        index="numero", columns="k", values="valore", aggfunc="size", fill_value=0)
    # i rigori sono tra gli attacchi punto ('attacco #'), contati anche a parte per il testo
    counts["attacco rigore"] = df[df["voce"] == RIGORE_LABEL].groupby("numero").size().reindex(
        counts.index, fill_value=0)

    # quota del palleggiatore: p-e/m dei suoi attacchi alzati oltre la media
    setter_cfg = cfg["palleggiatore"]
    set_attacks = pd.concat([so, fb])
    set_attacks = set_attacks[(set_attacks["Tipo"] == "attacco") & set_attacks["Palleggiatore in campo"].notna()
                              & (set_attacks["Cognome"] != set_attacks["Palleggiatore in campo"])]
    if setter_cfg.get("solo_alzate_veloci"):
        set_attacks = set_attacks[set_attacks["Tipo palla"].isin(BALL_FAST)]
    pem = set_attacks["Voto"].map({"#": 1, "=": -1, "/": -1}).fillna(0)
    by_setter = pd.DataFrame({"n": pem.groupby(set_attacks["Palleggiatore in campo"]).size(),
                              "pem": pem.groupby(set_attacks["Palleggiatore in campo"]).sum(),
                              "pt": (set_attacks["Voto"] == "#").groupby(set_attacks["Palleggiatore in campo"]).sum()})

    names = {int(n): c for n, c in df.groupby("numero")["Cognome"].first().items()}
    roster = {n: p.cognome for (sq, n), p in roster_by_number(source["parsed"]["players"]).items() if sq == source["side"]}
    played, n_sets = _sets_played(source)
    rows = []
    for n in sorted(contrib.index):  # un palleggiatore in campo ha sempre qualche azione (battuta)
        cognome = names.get(n) or roster.get(n, str(n))
        row = {"giocatore": f"{n} {cognome}", "cognome": cognome}
        row.update({f: float(contrib.loc[n, f]) if n in contrib.index else 0.0 for f in FUNDAMENTALS})
        if cognome in by_setter.index:
            s = by_setter.loc[cognome]
            expected = setter_cfg["pem_medio_per_attacco"] * s["n"]
            row.update(alzati_n=int(s["n"]), alzati_pem=int(s["pem"]), alzati_pt=int(s["pt"]), alzati_attesi=expected)
            row["alzata"] += setter_cfg["quota"] * (s["pem"] - expected)
        if n in counts.index:
            row.update({k: int(v) for k, v in counts.loc[n].items()})
        # premio al miglior ricevitore: il valore meno i punti già fatti in attacco, battuta e muro
        row["miglior_ricevitore"] = best_rec is not None and n == best_rec["numero"]
        row["premio"] = 0.0
        if row["miglior_ricevitore"]:
            p = cfg["miglior_ricevitore"]["premio"]
            row["premio"] = min(p, max(0.0, p - (row["attacco"] + row["battuta"] + row["muro"])))
            row.update(ric_n=best_rec["n"], ric_eff=best_rec["eff"])
        row["totale"] = sum(row[f] for f in FUNDAMENTALS) + row["premio"]
        row["set_giocati"] = played.get(n, n_sets)
        row["set_partita"] = n_sets
        k = cfg["set_fittizi"]
        row["punteggio"] = (row["totale"] * (n_sets + k) / (row["set_giocati"] + k) if row["totale"] > 0
                            else row["totale"])
        rows.append(row)
    out = pd.DataFrame(rows)
    for c in out.columns:
        if " " in c and c.split(" ")[0] in FUNDAMENTALS:
            out[c] = out[c].fillna(0).astype(int)
    return out.sort_values("punteggio", ascending=False, ignore_index=True)


def _best_receiver(df, source, cfg):
    """Il miglior ricevitore della partita: migliore efficienza di ricezione
    ((# + +) - (/ + =)) / ricezioni, con almeno min_ricezioni_per_set x set
    della partita; a parità chi ne ha fatte di più. {numero, n, eff} o None."""
    rc = cfg.get("miglior_ricevitore")
    if not rc:
        return None
    _, n_sets = _sets_played(source)
    rec = df[df["Tipo"] == "ricezione"].groupby("numero")["Voto"].agg(
        n="size", eff=lambda v: 100 * (v.isin(["#", "+"]).sum() - v.isin(["/", "="]).sum()) / len(v))
    rec = rec[rec["n"] >= math.ceil(rc["min_ricezioni_per_set"] * n_sets)]
    if rec.empty:
        return None
    best = rec.sort_values(["eff", "n"], ascending=False).iloc[0]
    return {"numero": int(best.name), "n": int(best["n"]), "eff": float(best["eff"])}


def match_awards(source, mvp=None, config=None):
    """
    I riconoscimenti della partita per il report: miglior ricevitore (quello
    del premio MVP), miglior attaccante (efficienza d'attacco, almeno
    min_attacchi_per_set x set; a parità più attacchi; senza i "rigori") e
    miglior muro (più muri punto, poi meno invasioni). Solo il ricevitore ha
    un premio in punti MVP. {"ricevitore"|"attaccante"|"muro": dict o None}.
    """
    cfg = config or load_mvp_config()
    mvp = match_mvp(source, cfg) if mvp is None else mvp
    names = dict(zip(mvp["giocatore"].str.split(" ").str[0].astype(int), mvp["giocatore"]))
    noi = source["noi"][source["noi"]["Numero"].notna()]
    _, n_sets = _sets_played(source)
    out = {"ricevitore": None, "attaccante": None, "muro": None}
    r = mvp[mvp.get("miglior_ricevitore", False) == True] if "miglior_ricevitore" in mvp else mvp.iloc[0:0]
    if len(r):
        r = r.iloc[0]
        out["ricevitore"] = {"giocatore": r["giocatore"], "n": int(r["ric_n"]), "eff": r["ric_eff"], "premio": r["premio"]}
    att = noi[noi["Tipo"] == "attacco"].groupby("Numero")["Voto"].agg(
        n="size", pt=lambda v: (v == "#").sum(), eff=lambda v: 100 * ((v == "#").sum() - v.isin(["=", "/"]).sum()) / len(v))
    att = att[att["n"] >= math.ceil(cfg.get("miglior_attaccante", {}).get("min_attacchi_per_set", 4.0) * n_sets)]
    if len(att):
        b = att.sort_values(["eff", "n"], ascending=False).iloc[0]
        out["attaccante"] = {"giocatore": names.get(int(b.name), str(int(b.name))), "n": int(b["n"]), "pt": int(b["pt"]),
                             "eff": float(b["eff"])}
    blk = noi[noi["Tipo"] == "muro"].groupby("Numero")["Voto"].agg(pt=lambda v: (v == "#").sum(), inv=lambda v: (v == "/").sum())
    blk = blk[blk["pt"] > 0]
    if len(blk):
        b = blk.assign(neg=-blk["inv"]).sort_values(["pt", "neg"], ascending=False).iloc[0]
        out["muro"] = {"giocatore": names.get(int(b.name), str(int(b.name))), "pt": int(b["pt"]), "inv": int(b["inv"])}
    return out


def _count(row, key):
    v = row.get(key, 0)
    return 0 if pd.isna(v) else int(v)


def _n(k, one, many):
    return f"{k} {one if k == 1 else many}"


def fundamental_text(row, fundamental):
    """Una riga di KPI del fondamentale per descrivere la partita del
    giocatore (`row`: una riga di match_mvp)."""
    c = lambda v: _count(row, f"{fundamental} {v}")
    tot = sum(c(v) for v in ("#", "+", "!", "-", "/", "="))
    if fundamental == "attacco":
        rig = c("rigore")  # i "rigori" contano come attacchi punto ma non sono attacchi: a parte
        pt, err, mur, tot = c("#") - rig, c("="), c("/"), tot - rig
        text = (f"{_n(pt, 'punto', 'punti')} su {_n(tot, 'attacco', 'attacchi')}, {_n(err, 'errore', 'errori')}, "
                f"{_n(mur, 'murata', 'murate')}")
        if tot >= MIN_EVENTS_FOR_PERCENT:
            text += f", efficienza {100 * (pt - err - mur) / tot:.0f}%"
        rig_text = _n(rig, '"rigore"', '"rigori"')
        if rig and not tot:
            return rig_text
        return text + (f", più {rig_text}" if rig else "")
    if fundamental == "battuta":
        return f"{c('#')} ace su {_n(tot, 'battuta', 'battute')}, {_n(c('='), 'errore', 'errori')}"
    if fundamental == "ricezione":
        # solo i conteggi, mai l'efficienza (indicazione dell'utente: "eff 100% su una ricezione è ridicolo")
        parts = [(c("#"), "perfetta", "perfette"), (c("+"), "positiva", "positive"), (c("="), "ace subito", "ace subiti")]
        detail = ", ".join(_n(k, one, many) for k, one, many in parts if k)
        if tot == 1:
            return f"1 ricezione {detail.split(' ', 1)[1]}" if detail else "1 ricezione"
        return f"{_n(tot, 'ricezione', 'ricezioni')}" + (f": {detail}" if detail else "")
    if fundamental == "muro":
        parts = [_n(c("#"), "muro punto", "muri punto")]
        if c("/"):
            parts.append(_n(c("/"), "invasione", "invasioni"))
        return ", ".join(parts)
    if fundamental == "difesa":
        good = c("#") + c("+")
        return (f"{_n(good, 'difesa che ci fa', 'difese che ci fanno')} contrattaccare"
                + (f", {_n(c('='), 'che dà', 'che danno')} il punto" if c("=") else ""))
    if fundamental == "premio":
        return (f"miglior efficienza in ricezione della squadra ({row['ric_eff']:.0f}% su {int(row['ric_n'])}): "
                "premio meno i punti fatti in attacco, battuta e muro")
    if fundamental == "alzata":
        n = row.get("alzati_n")
        parts = []
        if n is not None and not pd.isna(n):
            parts.append(f"attacchi alzati da R#, R+, R! e free ball: p-e/m {int(row['alzati_pem']):+d} su {int(n)} "
                         f"(in media {row['alzati_attesi']:+.0f})")
        good, bad = c("#") + c("+"), c("-")
        if good:
            parts.append(_n(good, "alzata meritevole", "alzate meritevoli"))
        if bad:
            parts.append(_n(bad, "alzata imprecisa", "alzate imprecise"))
        if c("="):
            parts.append(_n(c("="), "errore di alzata", "errori di alzata"))
        return ", ".join(parts)
    return ""


def mvp_highlights(row, n=3, min_contribution=0.2):
    """I fondamentali che hanno pesato di più nel punteggio del giocatore:
    [(etichetta, contributo, testo)], prima i contributi positivi; quelli
    sotto `min_contribution` solo se non c'è altro da dire."""
    # muro senza muri punto e battuta senza ace non si raccontano (i tocchi a
    # muro e le battute difficili da soli non dicono niente a chi legge)
    contrib = sorted(((f, row[f]) for f in FUNDAMENTALS + ["premio"] if abs(row.get(f, 0) or 0) >= 0.05
                      and not (f == "muro" and _count(row, "muro #") == 0)
                      and not (f == "battuta" and _count(row, "battuta #") == 0)), key=lambda x: -x[1])
    positive = [x for x in contrib if x[1] > 0]
    chosen = (positive or contrib)[:n]
    relevant = [x for x in chosen if abs(x[1]) >= min_contribution]
    chosen = relevant or chosen[:1]
    # gli ace ci sono sempre, anche uno solo (indicazione dell'utente)
    if _count(row, "battuta #") and all(f != "battuta" for f, _ in chosen):
        chosen = chosen[: n - 1] + [("battuta", row["battuta"])]
    return [(FUNDAMENTAL_LABELS.get(f, "Miglior ricevitore"), v, fundamental_text(row, f)) for f, v in chosen]


def _ranking_name(giocatore):
    """'18 Carrer' -> 'Carrer 18', come nelle altre classifiche."""
    numero, _, cognome = str(giocatore).partition(" ")
    return f"{cognome} {numero}" if cognome else numero


def season_mvp(per_match, players=None):
    """
    Classifica MVP cumulativa. `per_match`: [(match_seq, DataFrame di
    match_mvp)]; `players`: cognomi da tenere (giocatori registrati), None =
    tutti. Giocatori abbinati tra partite per cognome. DataFrame: Giocatore
    ('<cognome> <numero>', ultimo numero visto), Partite, Punti MVP (somma dei punteggi), Media,
    MVP (partite vinte), ordinato per Punti MVP.
    """
    keep = None if players is None else {entity_key(p) for p in players}
    rows = []
    for seq, df in sorted(per_match, key=lambda x: x[0]):
        if df.empty:
            continue
        winner = entity_key(df.iloc[0]["cognome"])
        for r in df.itertuples(index=False):
            key = entity_key(r.cognome)
            if keep is not None and key not in keep:
                continue
            rows.append({"key": key, "giocatore": r.giocatore, "seq": seq, "punteggio": r.punteggio,
                         "mvp": key == winner})
    if not rows:
        return pd.DataFrame(columns=["Pos", "Giocatore", "Partite", "Punti MVP", "Media", "MVP"])
    long = pd.DataFrame(rows)
    g = long.sort_values("seq").groupby("key")
    table = pd.DataFrame({
        "Giocatore": g["giocatore"].last().map(_ranking_name), "Partite": g.size(), "Punti MVP": g["punteggio"].sum(),
        "Media": g["punteggio"].mean(), "MVP": g["mvp"].sum().astype(int),
    }).sort_values(["Punti MVP", "MVP"], ascending=False, ignore_index=True)
    table.insert(0, "Pos", range(1, len(table) + 1))
    return table


def mvp_by_match_long(per_match, players=None):
    """Punteggi partita per partita in formato lungo (match_seq, cognome,
    giocatore, punteggio, cumulato), per i grafici lungo la stagione."""
    keep = None if players is None else {entity_key(p) for p in players}
    rows = [{"match_seq": seq, "key": entity_key(r.cognome), "giocatore": r.giocatore, "punteggio": r.punteggio,
             "totale": r.totale, "set_giocati": r.set_giocati, "set_partita": r.set_partita}
            for seq, df in per_match for r in df.itertuples(index=False)
            if keep is None or entity_key(r.cognome) in keep]
    long = pd.DataFrame(rows, columns=["match_seq", "key", "giocatore", "punteggio", "totale", "set_giocati",
                                       "set_partita"])
    if long.empty:
        return long
    long = long.sort_values("match_seq")
    long["nome"] = long.groupby("key")["giocatore"].transform("last")
    long["cumulato"] = long.groupby("key")["punteggio"].cumsum()
    return long


# Descrizione a parole di ogni voto, per spiegare il punteggio (appendice del
# report, dashboard). Le voci con valore 0 finiscono tra "non contano".
ACTION_DESCRIPTIONS = {
    "battuta": {"#": "Ace (B#)", "+": "Battuta che mette in difficoltà la ricezione (B+)",
                "!": "Battuta che toglie il primo tempo (B!)", "-": "Battuta facile da ricevere (B-)",
                "/": "Battuta che fa tornare la palla di qua (B/)", "=": "Errore in battuta, tipo non rilevato (B=)"},
    "ricezione": {"#": "Ricezione perfetta (R#)", "+": "Ricezione buona, si può giocare il primo tempo (R+)",
                  "!": "Ricezione senza primo tempo (R!)", "-": "Ricezione che permette solo la palla alta (R-)",
                  "/": "Ricezione che va di là (R/)", "=": "Ace subito (R=)"},
    "attacco": {"#": "Attacco punto in altre situazioni: dopo R!, contrattacco su palla veloce (A#)",
                "+": "Attacco che mette in difficoltà la difesa (A+)",
                "!": "Attacco che torna nel nostro campo, rigiochiamo (A!)",
                "-": "Attacco difeso dall'avversario (A-)", "/": "Attacco murato in altre situazioni (A/)",
                "=": "Errore in attacco in altre situazioni (A=)"},
    "muro": {"#": "Muro punto (M#)", "+": "Muro che ci fa rigiocare (M+)", "!": "Muro che fa rigiocare l'avversario (M!)",
             "-": "Muro toccato che lascia contrattaccare l'avversario (M-)", "/": "Invasione a muro (M/)",
             "=": "Mani-fuori subito (M=)"},
    "difesa": {"#": "Difesa che fa la differenza, per esempio un tuffo (D#)",
               "+": "Difesa nella giusta posizione (D+)",
               "-": "Difesa che tiene la palla senza contrattacco (D-)",
               "=": "Difesa che dà il punto all'avversario (D=)"},
    "alzata": {"#": "Alzata meritevole (E#)", "+": "Alzata che lascia l'attaccante contro muro a 1 (E+)",
               "-": "Alzata imprecisa, mette in difficoltà l'attaccante (E-)", "=": "Errore di alzata (E=)"},
}


GRADE_CODES = {"battuta": "B", "ricezione": "R", "attacco": "A", "muro": "M", "difesa": "D", "alzata": "E"}
SERVE_ERROR_LABELS = {"Q": "Errore in battuta salto spin (B= spin)", "M": "Errore in battuta salto float (B= float)"}
# attacco per situazione (config attacco_per_situazione)
SITUATION_LABELS = {
    (sit, v): f"{what} {where} (A{v})"
    for sit, where in (("R#/R+", "da R# o R+"), ("free ball", "da free ball"), ("palla alta", "su palla alta"))
    for v, what in (("#", "Attacco punto"), ("=", "Errore in attacco"), ("/", "Attacco murato"))
}
BACK_ROW_LABEL = "Attacco punto da 2ª linea: in più"
SET_POINT_LABEL = "Alzata # o + che porta a punto: in più"
PREMIO_LABEL = "Miglior ricevitore della partita: premio meno i punti fatti in attacco, battuta e muro"
RIGORE_LABEL = '"Rigore": punto sulla loro ricezione o difesa rimandata di qua (M# dopo R/ o D/), come un attacco punto'
RIGORE_ERR_LABEL = '"Rigore" sbagliato: fuori, in rete o invasione (M=, M/ dopo R/ o D/), come un attacco sbagliato'
RIGORE_PLAY_LABEL = '"Rigore" difeso dagli altri (M- dopo R/ o D/), come un attacco rimasto in gioco'


def player_mvp_detail(source, giocatore, config=None):
    """
    Da dove arriva il punteggio MVP di un giocatore ('<numero> <cognome>',
    come in match_mvp): {"azioni": DataFrame [fondamentale, azione, volte,
    valore, totale] per voce della tabella dei punti, dal fondamentale più
    importante; "riga": la sua riga di match_mvp (quota del palleggiatore,
    totale, set giocati, punteggio); "config"}.
    """
    cfg = config or load_mvp_config()
    df, _, _ = _scored_actions(source, cfg)
    numero = int(str(giocatore).split(" ", 1)[0])
    mine = df[df["numero"] == numero]
    table = (mine.groupby(["Tipo", "voce", "valore"]).size().reset_index(name="volte")
             .assign(totale=lambda d: d["valore"] * d["volte"]))
    table["ordine"] = table["Tipo"].map({f: i for i, f in enumerate(FUNDAMENTALS)})
    table = table.sort_values(["ordine", "valore"], ascending=[True, False])
    table = table.rename(columns={"Tipo": "fondamentale", "voce": "azione"})
    table["fondamentale"] = table["fondamentale"].map(FUNDAMENTAL_LABELS)
    rows = match_mvp(source, cfg)
    row = rows[rows["giocatore"] == giocatore].iloc[0].to_dict()
    if row.get("premio"):
        table = pd.concat([table, pd.DataFrame([{"fondamentale": "Premio", "azione": PREMIO_LABEL, "volte": 1,
                                                 "valore": row["premio"], "totale": row["premio"]}])])
    return {"azioni": table[["fondamentale", "azione", "volte", "valore", "totale"]].reset_index(drop=True),
            "riga": row, "config": cfg}


def scoring_rules(config=None):
    """
    Le regole del punteggio a parole, dai valori di config/mvp.json:
    {"per_fondamentale": [(fondamentale, [(valore, descrizione)])], dal valore
    più alto al più basso, "punti": [(valore, descrizione, fondamentale)], "penalita": [...]
    (ordinati dal valore più grande in assoluto), "zero": [descrizioni],
    "quota", "media", "solo_veloci", "set_fittizi"}.
    """
    cfg = config or load_mvp_config()
    items = []
    for fund, grades in cfg["azioni"].items():
        for grade, value in grades.items():
            desc = ACTION_DESCRIPTIONS.get(fund, {}).get(grade)
            if desc:
                items.append((value, desc, FUNDAMENTAL_LABELS[fund]))
    for sit, grades in cfg.get("attacco_per_situazione", {}).items():
        for v, value in grades.items():
            items.append((value, SITUATION_LABELS[(sit, v)], "Attacco"))
    if cfg.get("attacco_seconda_linea"):
        items.append((cfg["attacco_seconda_linea"], BACK_ROW_LABEL, "Attacco"))
    items.append((cfg["azioni"]["attacco"]["#"], RIGORE_LABEL, "Attacco"))
    items.append((cfg["azioni"]["attacco"]["="], RIGORE_ERR_LABEL, "Attacco"))
    if cfg.get("alzata_a_punto"):
        items.append((cfg["alzata_a_punto"], SET_POINT_LABEL, "Alzata"))
    for code, value in cfg.get("errore_battuta_per_tipo", {}).items():
        items.append((value, SERVE_ERROR_LABELS.get(code, f"Errore in battuta (B=, tipo {code})"), "Battuta"))
    setter = cfg["palleggiatore"]
    by_fund = [(FUNDAMENTAL_LABELS[f], sorted([(v, d) for v, d, lab in items if lab == FUNDAMENTAL_LABELS[f]],
                                              key=lambda x: (-x[0], "non rilevato" in x[1])))
               for f in FUNDAMENTALS]
    return {
        "per_fondamentale": [(lab, rows) for lab, rows in by_fund if rows],
        "punti": sorted([x for x in items if x[0] > 0], key=lambda x: -x[0]),
        "penalita": sorted([x for x in items if x[0] < 0], key=lambda x: x[0]),
        "zero": [d for v, d, _ in items if v == 0],
        "quota": setter["quota"], "media": setter["pem_medio_per_attacco"],
        "solo_veloci": setter.get("solo_alzate_veloci", False), "set_fittizi": cfg["set_fittizi"],
        "situazioni": {sit: grades for sit, grades in cfg.get("attacco_per_situazione", {}).items()},
        "attacco_altro": {v: cfg["azioni"]["attacco"][v] for v in ("#", "=", "/")},
        "seconda_linea": cfg.get("attacco_seconda_linea", 0.0),
        "premio": cfg.get("miglior_ricevitore", {}).get("premio"),
        "min_ricezioni_per_set": cfg.get("miglior_ricevitore", {}).get("min_ricezioni_per_set"),
    }
