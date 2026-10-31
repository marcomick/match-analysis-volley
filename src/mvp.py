# src/mvp.py
"""
MVP della partita (criteri concordati con l'utente il 2026-10-31).

Ogni azione vale in punti quanto sposta la probabilità di vincere lo scambio
(tabella in config/mvp.json, calcolata su tutti i .dvw e arrotondata a 0,05):
muro punto +0,65, ace +0,60, attacco punto +0,40 (+0,50 da free ball),
ricezione # o + +0,15 (alla pari, spinta anche al libero), ...; invasione a
muro -1,00, errore d'attacco su palla alta -0,75 (solo dove il tipo di
alzata è rilevato), errore d'attacco -0,60, murato -0,50, ...; l'attacco
difeso (-) e la battuta facile (-) valgono 0, così chi attacca tanto non
viene penalizzato. Muro e difesa sono confrontati con "attacco avversario in
gioco": un muro o una difesa non segnati valgono 0.

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
from pathlib import Path

import pandas as pd

from src.dvw import roster_by_number
from src.efficiency import separate_attack_types
from src.set_analysis import BALL_FAST, BALL_HIGH, entity_key

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "mvp.json"
FUNDAMENTALS = ["battuta", "ricezione", "attacco", "muro", "difesa", "alzata"]
FUNDAMENTAL_LABELS = {"battuta": "Battuta", "ricezione": "Ricezione", "attacco": "Attacco", "muro": "Muro",
                      "difesa": "Difesa", "alzata": "Alzata"}
SETTER_SITUATION_GRADES = ["#", "+", "!"]  # ricezioni da cui conta l'attacco alzato (più la free ball)


def load_mvp_config(path=CONFIG_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _ball_type_recorded(attacks):
    """Il tipo di alzata è stato rilevato nella partita? (Nei .dvw senza
    rilevazione tutti gli attacchi risultano palla alta, tranne quelli dei
    centrali, quick d'ufficio.)"""
    return attacks["Tipo palla"].isin([b for b in BALL_FAST if b != "Q"]).any()


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


def match_mvp(source, config=None):
    """
    Punteggio MVP di ogni giocatore della squadra "noi" di `source`
    (src.match_report.load_report_source). DataFrame ordinato per punteggio,
    una riga per giocatore: giocatore ('<numero> <cognome>'), cognome, un
    contributo per fondamentale (FUNDAMENTALS), totale, set_giocati,
    set_partita, punteggio (totale corretto per i set giocati, solo se
    positivo), alzati_n/alzati_pem/alzati_attesi (palleggiatori: attacchi
    alzati che contano per la quota, loro p-e/m e p-e/m atteso) e i conteggi
    per voto (colonne '<fondamentale> <voto>') per descrivere la partita.
    """
    cfg = config or load_mvp_config()
    df = source["noi"]
    df = df[df["Numero"].notna() & df["Tipo"].isin(FUNDAMENTALS)].copy()
    df["valore"] = [cfg["azioni"].get(t, {}).get(v, 0.0) for t, v in zip(df["Tipo"], df["Voto"])]

    so, fb, _ = separate_attack_types(source["noi"], rec_vote=SETTER_SITUATION_GRADES)
    fb_points = fb[(fb["Tipo"] == "attacco") & (fb["Voto"] == "#")].index.intersection(df.index)
    df.loc[fb_points, "valore"] = cfg["attacco_punto_free_ball"]
    attacks = df[df["Tipo"] == "attacco"]
    if _ball_type_recorded(attacks):
        high_err = attacks[(attacks["Voto"] == "=") & attacks["Tipo palla"].isin(BALL_HIGH)].index
        df.loc[high_err, "valore"] = cfg["errore_attacco_palla_alta"]

    df["numero"] = df["Numero"].astype(int)
    contrib = df.pivot_table(index="numero", columns="Tipo", values="valore", aggfunc="sum", fill_value=0.0)
    contrib = contrib.reindex(columns=FUNDAMENTALS, fill_value=0.0)
    counts = df.assign(k=df["Tipo"] + " " + df["Voto"]).pivot_table(
        index="numero", columns="k", values="valore", aggfunc="size", fill_value=0)

    # quota del palleggiatore: p-e/m dei suoi attacchi alzati oltre la media
    setter_cfg = cfg["palleggiatore"]
    set_attacks = pd.concat([so, fb])
    set_attacks = set_attacks[(set_attacks["Tipo"] == "attacco") & set_attacks["Palleggiatore in campo"].notna()
                              & (set_attacks["Cognome"] != set_attacks["Palleggiatore in campo"])]
    if setter_cfg.get("solo_alzate_veloci"):
        set_attacks = set_attacks[set_attacks["Tipo palla"].isin(BALL_FAST)]
    pem = set_attacks["Voto"].map({"#": 1, "=": -1, "/": -1}).fillna(0)
    by_setter = pd.DataFrame({"n": pem.groupby(set_attacks["Palleggiatore in campo"]).size(),
                              "pem": pem.groupby(set_attacks["Palleggiatore in campo"]).sum()})

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
            row.update(alzati_n=int(s["n"]), alzati_pem=int(s["pem"]), alzati_attesi=expected)
            row["alzata"] += setter_cfg["quota"] * (s["pem"] - expected)
        if n in counts.index:
            row.update({k: int(v) for k, v in counts.loc[n].items()})
        row["totale"] = sum(row[f] for f in FUNDAMENTALS)
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
        pt, err, mur = c("#"), c("="), c("/")
        eff = 100 * (pt - err - mur) / tot if tot else 0
        return (f"{_n(pt, 'punto', 'punti')} su {_n(tot, 'attacco', 'attacchi')}, {_n(err, 'errore', 'errori')}, "
                f"{_n(mur, 'murata', 'murate')}, efficienza {eff:.0f}%")
    if fundamental == "battuta":
        return f"{c('#')} ace su {_n(tot, 'battuta', 'battute')}, {_n(c('='), 'errore', 'errori')}"
    if fundamental == "ricezione":
        eff = 100 * (c("#") + c("+") - c("/") - c("=")) / tot if tot else 0
        return (f"{_n(tot, 'ricezione', 'ricezioni')}, {_n(c('#'), 'perfetta', 'perfette')}, "
                f"{_n(c('='), 'ace subito', 'ace subiti')}, efficienza {eff:.0f}%")
    if fundamental == "muro":
        parts = [_n(c("#"), "muro punto", "muri punto")]
        if c("+") + c("!"):
            parts.append(_n(c("+") + c("!"), "muro che rallenta la palla", "muri che rallentano la palla"))
        if c("/"):
            parts.append(_n(c("/"), "invasione", "invasioni"))
        return ", ".join(parts)
    if fundamental == "difesa":
        return (f"{_n(c('#'), 'difesa che fa', 'difese che fanno')} contrattaccare, "
                f"{_n(c('='), 'che dà', 'che danno')} il punto")
    if fundamental == "alzata":
        n = row.get("alzati_n")
        parts = []
        if n is not None and not pd.isna(n):
            parts.append(f"attacchi alzati da R#, R+, R! e free ball: p-e/m {int(row['alzati_pem']):+d} su {int(n)} "
                         f"(in media {row['alzati_attesi']:+.0f})")
        if c("="):
            parts.append(_n(c("="), "errore di alzata", "errori di alzata"))
        return ", ".join(parts)
    return ""


def mvp_highlights(row, n=3):
    """I fondamentali che hanno pesato di più nel punteggio del giocatore:
    [(etichetta, contributo, testo)], prima i contributi positivi."""
    contrib = sorted(((f, row[f]) for f in FUNDAMENTALS if abs(row[f]) >= 0.05), key=lambda x: -x[1])
    positive = [x for x in contrib if x[1] > 0]
    chosen = (positive or contrib)[:n]
    return [(FUNDAMENTAL_LABELS[f], v, fundamental_text(row, f)) for f, v in chosen]


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
                "/": "Battuta che fa tornare la palla di qua (B/)", "=": "Errore in battuta (B=)"},
    "ricezione": {"#": "Ricezione perfetta (R#)", "+": "Ricezione buona, si può giocare il primo tempo (R+)",
                  "!": "Ricezione senza primo tempo (R!)", "-": "Ricezione che permette solo la palla alta (R-)",
                  "/": "Ricezione che va di là (R/)", "=": "Ace subito (R=)"},
    "attacco": {"#": "Attacco punto (A#)", "+": "Attacco che mette in difficoltà la difesa (A+)",
                "!": "Attacco che torna nel nostro campo, rigiochiamo (A!)",
                "-": "Attacco difeso dall'avversario (A-)", "/": "Attacco murato (A/)", "=": "Errore in attacco (A=)"},
    "muro": {"#": "Muro punto (M#)", "+": "Muro che ci fa rigiocare (M+)", "!": "Muro che fa rigiocare l'avversario (M!)",
             "-": "Muro toccato che lascia contrattaccare l'avversario (M-)", "/": "Invasione a muro (M/)",
             "=": "Mani-fuori subito (M=)"},
    "difesa": {"#": "Difesa che fa contrattaccare (D#)", "-": "Difesa che tiene la palla senza contrattacco (D-)",
               "=": "Difesa che dà il punto all'avversario (D=)"},
    "alzata": {"=": "Errore di alzata (E=)"},
}


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
    items.append((cfg["attacco_punto_free_ball"], "Attacco punto dopo una free ball (A# da free ball)", "Attacco"))
    items.append((cfg["errore_attacco_palla_alta"], "Errore in attacco su palla alta (A= su palla alta, "
                  "solo dove il tipo di alzata è rilevato)", "Attacco"))
    setter = cfg["palleggiatore"]
    by_fund = [(FUNDAMENTAL_LABELS[f], sorted([(v, d) for v, d, lab in items if lab == FUNDAMENTAL_LABELS[f]],
                                              key=lambda x: -x[0]))
               for f in FUNDAMENTALS]
    return {
        "per_fondamentale": [(lab, rows) for lab, rows in by_fund if rows],
        "punti": sorted([x for x in items if x[0] > 0], key=lambda x: -x[0]),
        "penalita": sorted([x for x in items if x[0] < 0], key=lambda x: x[0]),
        "zero": [d for v, d, _ in items if v == 0],
        "quota": setter["quota"], "media": setter["pem_medio_per_attacco"],
        "solo_veloci": setter.get("solo_alzate_veloci", False), "set_fittizi": cfg["set_fittizi"],
    }
