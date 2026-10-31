# src/set_analysis.py
"""
Analisi per giocatore e per set richieste dall'utente il 2026-10-30:

1. Tabellino del report per giocatore (player_kpi_table): una tabella per
   giocatore con i fondamentali in riga (ricezione, battuta, attacco side-out,
   attacco break-point con "di cui free ball", muro, palla alta, palla
   veloce, alzata) e in colonna tot, punti, errori, murate, p-e/m,
   efficienza. Negli errori d'attacco solo gli attacchi sbagliati (=), mai
   le murate (il murato è un merito dell'avversario, negli errori interessa
   quanto "ci siamo incartati da soli"); le murate stanno nella loro colonna
   e, per l'attacco, nel saldo: **p-e/m** = punti - errori - murate
   (indicazione dell'utente, 2026-10-30, rivista lo stesso giorno: prima il
   p-e le escludeva). Per gli altri fondamentali p-e/m = punti - errori.
   Nel totale del giocatore il saldo è la somma delle righe (murate comprese).
2. Palla alta e palla veloce (BALL_HIGH / BALL_FAST, dal tipo di palla del
   .dvw: alta = H; veloce = super, quick, "7", fast; mezza e altro fuori da
   entrambe): p-e/m dei giocatori partita per partita (ball_type_pe_by_match).
3. I set di una partita rispetto alla nostra media (compare_sets_to_baseline):
   per ogni set e KPI (SET_PROFILE_KPIS) il valore, la media e la
   distribuzione nei set delle partite di riferimento, e un segnale quando il
   set è fuori dal 90% centrale di quei set (p empirico a due code <= 0.10).
   I conteggi dei set di riferimento sono riportati a un set da 25 punti; per
   un quinto set la media e la distribuzione si riportano a 15 punti
   (proporzione semplice 15/25, indicazione dell'utente).
4. Medie personali (personal_baseline, kpi_deviations): per ogni giocatore e
   cella del suo tabellino (punti, errori, murate, p-e/m, efficienza) media e
   deviazione standard nelle partite precedenti, per colorare la cella in
   base allo scostamento dalla sua media.
5. L'ultima partita (o alcuni suoi set) rispetto alle precedenti
   (FORM_KPIS, form_values): per giocatore e squadra i KPI di una partita,
   con i conteggi per set così che una parte dei set sia confrontabile con le
   partite intere.
"""
import numpy as np
import pandas as pd

from src.efficiency import SRV_NEG, SRV_POS, separate_attack_types

BALL_HIGH = ("H",)
BALL_FAST = ("T", "U", "Q", "N", "F")
ATT_NEG = ("=", "/")  # negativi dell'efficienza d'attacco: errori e murati


def _attack_counts(att):
    """tot, pt, err (solo '='), mur ('/'), pe = p-e/m = pt - err - mur, eff =
    (pt - err - mur) / tot."""
    n = len(att)
    pt = int((att["Voto"] == "#").sum())
    err = int((att["Voto"] == "=").sum())
    mur = int((att["Voto"] == "/").sum())
    return {"tot": n, "pt": pt, "err": err, "mur": mur, "pe": pt - err - mur,
            "eff": 100 * (pt - err - mur) / n if n else None}


# Righe della tabella per giocatore: (chiave, etichetta).
PLAYER_KPI_ROWS = [
    ("ricezione", "Ricezione"), ("battuta", "Battuta"), ("so", "Attacco SO"),
    ("bp", "Attacco BP"), ("fb", "di cui free ball"), ("muro", "Muro"),
    ("alta", "Palla alta"), ("veloce", "Palla veloce"), ("alzata", "Alzata"), ("totale", "Totale"),
]


def player_kpi_table(df):
    """
    {giocatore ('<numero> <cognome>' o 'Squadra'): DataFrame} con una riga per
    fondamentale giocato (PLAYER_KPI_ROWS; sempre 'Totale') e colonne tot, pt,
    err, mur (murate, solo attacchi e totale), pe (p-e/m), eff (None dove
    non ha senso: punti e p-e/m della ricezione, efficienza di muro e alzata). `df`: azioni di una squadra (formato di
    src.dvw.side_actions); side-out e break-point divisi sulla sequenza di
    tutta la squadra, poi assegnati al giocatore.
    """
    so, fb, rest = separate_attack_types(df)
    so_att = so[so["Tipo"] == "attacco"]
    fb_att = fb[fb["Tipo"] == "attacco"]
    bp_att = pd.concat([fb, rest]).query("Tipo == 'attacco'")
    actions = df[df["Numero"].notna()]
    entities = [("Squadra", actions.index)] + [
        (f"{int(n)} {actions.loc[actions['Numero'] == n, 'Cognome'].iloc[0]}", actions.index[actions["Numero"] == n])
        for n in sorted(actions["Numero"].unique())
    ]
    ball = df["Tipo palla"] if "Tipo palla" in df.columns else pd.Series(None, index=df.index)
    out = {}
    for name, idx in entities:
        mine = actions.loc[idx]
        rows = {}
        ric = mine[mine["Tipo"] == "ricezione"]
        if len(ric):
            pos, neg = ric["Voto"].isin(["#", "+"]).sum(), ric["Voto"].isin(["/", "="]).sum()
            rows["ricezione"] = {"tot": len(ric), "pt": None, "err": int((ric["Voto"] == "=").sum()), "pe": None,
                                 "eff": 100 * (pos - neg) / len(ric)}
        bat = mine[mine["Tipo"] == "battuta"]
        if len(bat):
            pt, err = int((bat["Voto"] == "#").sum()), int(bat["Voto"].isin(SRV_NEG).sum())
            rows["battuta"] = {"tot": len(bat), "pt": pt, "err": err, "pe": pt - err,
                               "eff": 100 * (bat["Voto"].isin(SRV_POS).sum() - err) / len(bat)}
        att = mine[mine["Tipo"] == "attacco"]
        for key, part in (("so", so_att), ("bp", bp_att), ("fb", fb_att),
                          ("alta", att[ball.reindex(att.index).isin(BALL_HIGH)]),
                          ("veloce", att[ball.reindex(att.index).isin(BALL_FAST)])):
            part = part[part.index.isin(idx)]
            if len(part):
                rows[key] = _attack_counts(part)
        muro = mine[mine["Tipo"] == "muro"]
        if len(muro):
            pt, err = int((muro["Voto"] == "#").sum()), int((muro["Voto"] == "/").sum())
            rows["muro"] = {"tot": len(muro), "pt": pt, "err": err, "pe": pt - err, "eff": None}
        alz_err = int(((mine["Tipo"] == "alzata") & (mine["Voto"] == "=")).sum())
        if alz_err:
            rows["alzata"] = {"tot": None, "pt": None, "err": alz_err, "pe": -alz_err, "eff": None}
        # totale: punti, errori e murate di attacco (side-out + break-point),
        # battuta, muro e alzata; la ricezione non dà punti e i suoi errori
        # (ace subiti) sono già i punti della battuta avversaria
        parts = [rows.get(k) for k in ("battuta", "so", "bp", "muro", "alzata")]
        pt = sum(r["pt"] or 0 for r in parts if r)
        err = sum(r["err"] or 0 for r in parts if r)
        mur = sum(r.get("mur") or 0 for r in parts if r)
        rows["totale"] = {"tot": None, "pt": pt, "err": err, "mur": mur, "pe": pt - err - mur, "eff": None}
        table = pd.DataFrame([{"kpi": k, "label": label, **rows[k]} for k, label in PLAYER_KPI_ROWS if k in rows])
        out[name] = table
    return out


def ball_type_pe_by_match(matches):
    """
    p-e/m e attacchi su palla alta e palla veloce per giocatore e partita:
    DataFrame [match_seq, giocatore, tipo ('Palla alta'/'Palla veloce'), n,
    pt, err, mur, pe, eff]. `matches`: partite di src.leg_comparison.
    load_all_matches; solo quelle con il tipo di palla (.dvw).
    """
    rows = []
    for m in matches:
        df = m["df"]
        if "Tipo palla" not in df.columns:
            continue
        att = df[(df["Tipo"] == "attacco") & df["Numero"].notna()]
        for tipo, codes in (("Palla alta", BALL_HIGH), ("Palla veloce", BALL_FAST)):
            part = att[att["Tipo palla"].isin(codes)]
            for player, sub in part.groupby("Giocatore"):
                rows.append({"match_seq": m["match_seq"], "giocatore": player, "tipo": tipo, **_attack_counts(sub)})
    return pd.DataFrame(rows, columns=["match_seq", "giocatore", "tipo", "tot", "pt", "err", "mur", "pe", "eff"])


# KPI per set confrontati con la media: (chiave, etichetta, percentuale?,
# verso: +1 = più alto è meglio, -1 = più basso è meglio).
SET_PROFILE_KPIS = [
    ("ric_eff", "Ricezione (eff.)", True, 1),
    ("so_eff", "Attacco side-out (eff.)", True, 1),
    ("so_pt", "Punti in attacco side-out", False, 1),
    ("bp_eff", "Attacco break-point (eff.)", True, 1),
    ("bp_pt", "Punti in attacco break-point", False, 1),
    ("bat_eff", "Battuta (eff.)", True, 1),
    ("ace", "Ace", False, 1),
    ("bat_err", "Errori in battuta", False, -1),
    ("muro_pt", "Muri punto", False, 1),
    ("att_err", "Errori in attacco", False, -1),
    ("err", "Errori totali", False, -1),
]
TIEBREAK_SCALE = 25 / 15  # conteggi di un quinto set di riferimento riportati a un set da 25
SIGNAL_P = 0.10
MIN_BASELINE_SETS = 8


def set_profile(df, to_25=True):
    """Valori dei SET_PROFILE_KPIS per ogni set di una partita (azioni di una
    squadra): DataFrame indicizzato per numero di set. Efficienze in %, None
    se il fondamentale non c'è nel set; con `to_25` i conteggi del quinto set
    riportati a un set da 25 punti (TIEBREAK_SCALE)."""
    so, fb, rest = separate_attack_types(df)
    bp = pd.concat([fb, rest])
    out = {}
    for s_ in sorted(df["Numero Set"].dropna().unique()):
        d = df[df["Numero Set"] == s_]
        so_a = so[(so["Numero Set"] == s_) & (so["Tipo"] == "attacco")]
        bp_a = bp[(bp["Numero Set"] == s_) & (bp["Tipo"] == "attacco")]
        ric = d[d["Tipo"] == "ricezione"]
        bat = d[d["Tipo"] == "battuta"]
        att = d[d["Tipo"] == "attacco"]

        def eff(part, pos, neg):
            return 100 * (part["Voto"].isin(pos).sum() - part["Voto"].isin(neg).sum()) / len(part) if len(part) else None

        scale = TIEBREAK_SCALE if to_25 and int(s_) % 100 == 5 else 1.0
        errors = (((d["Tipo"].isin(["battuta", "attacco", "alzata"])) & (d["Voto"] == "="))
                  | ((d["Tipo"] == "muro") & (d["Voto"] == "/")))
        out[int(s_)] = {
            "ric_eff": eff(ric, ["#", "+"], ["/", "="]),
            "so_eff": eff(so_a, ["#"], ATT_NEG), "so_pt": scale * (so_a["Voto"] == "#").sum(),
            "bp_eff": eff(bp_a, ["#"], ATT_NEG), "bp_pt": scale * (bp_a["Voto"] == "#").sum(),
            "bat_eff": eff(bat, SRV_POS, SRV_NEG), "ace": scale * (bat["Voto"] == "#").sum(),
            "bat_err": scale * (bat["Voto"] == "=").sum(),
            "muro_pt": scale * ((d["Tipo"] == "muro") & (d["Voto"] == "#")).sum(),
            "att_err": scale * (att["Voto"] == "=").sum(),
            "err": scale * errors.sum(),
        }
    return pd.DataFrame.from_dict(out, orient="index")


def compare_sets_to_baseline(match_df, baseline_dfs):
    """
    Ogni set della partita (`match_df`) rispetto ai set delle partite di
    riferimento (`baseline_dfs`): DataFrame [set, kpi, label, valore, media,
    n_rif, z, percentile (% dei set di riferimento con valore minore o
    uguale), p (empirico a due code), segnale (p <= SIGNAL_P con almeno
    MIN_BASELINE_SETS set di riferimento), buono (+1 meglio della media, -1
    peggio, 0 nessun segnale)]. I conteggi del set sono quelli reali; i set
    di riferimento sono riportati a 25 punti e, per un quinto set della
    partita, la loro distribuzione (quindi la media) a 15 punti.
    """
    base = pd.concat([set_profile(d) for d in baseline_dfs], ignore_index=True) if baseline_dfs else pd.DataFrame()
    current = set_profile(match_df, to_25=False)
    rows = []
    for s_, values in current.iterrows():
        for key, label, is_pct, verso in SET_PROFILE_KPIS:
            v = values.get(key)
            ref = base[key].dropna().astype(float).to_numpy() if key in base else np.array([])
            if int(s_) % 100 == 5 and not is_pct:
                ref = ref / TIEBREAK_SCALE  # proporzione semplice: media di un set da 15 punti
            row = {"set": int(s_), "kpi": key, "label": label, "percentuale": is_pct, "valore": v,
                   "media": float(ref.mean()) if len(ref) else None, "n_rif": len(ref),
                   "z": None, "percentile": None, "p": None, "segnale": False, "buono": 0}
            if v is not None and not pd.isna(v) and len(ref):
                std = ref.std(ddof=1) if len(ref) > 1 else 0.0
                row["z"] = float((v - ref.mean()) / std) if std > 0 else 0.0
                row["percentile"] = 100 * float((ref <= v).mean())
                p = 2 * min(((ref >= v).sum() + 1) / (len(ref) + 1), ((ref <= v).sum() + 1) / (len(ref) + 1))
                row["p"] = min(1.0, float(p))
                row["segnale"] = row["p"] <= SIGNAL_P and len(ref) >= MIN_BASELINE_SETS
                if row["segnale"]:
                    row["buono"] = int(np.sign(v - ref.mean()) * verso)
            rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Medie personali e ultima partita contro le precedenti
# ---------------------------------------------------------------------------
def entity_key(name):
    """Chiave di un giocatore indipendente dalla grafia del cognome
    ('22 D’Arienzo' = "22 D'arienzo"), 'Squadra' resta tale."""
    return str(name).replace("’", "'").casefold().strip()


# Verso di ogni colonna del tabellino (+1 più alto è meglio, -1 più basso è
# meglio); 'tot' è un volume, non si colora.
CELL_DIRECTION = {"pt": 1, "err": -1, "mur": -1, "pe": 1, "eff": 1}
MIN_HISTORY = 3  # partite precedenti con quel dato per colorare la cella
STD_FLOOR = {"eff": 8.0}  # deviazione minima: evita colori forti su storie piatte
STD_FLOOR_COUNTS = 1.0


def _long(tables, match_id):
    rows = []
    for name, table in tables.items():
        for r in table.to_dict("records"):
            for col in CELL_DIRECTION:
                v = r.get(col)
                if v is not None and not pd.isna(v):
                    rows.append({"key": entity_key(name), "kpi": r["kpi"], "col": col, "value": float(v),
                                 "match": match_id})
    return rows


def personal_baseline(history_dfs):
    """Media, deviazione standard e numero di partite per (giocatore, riga
    del tabellino, colonna), dalle partite precedenti (`history_dfs`: azioni
    di una squadra per partita). DataFrame indicizzato per (key, kpi, col)."""
    rows = []
    for i, df in enumerate(history_dfs):
        rows += _long(player_kpi_table(df), i)
    if not rows:
        return pd.DataFrame(columns=["mean", "std", "n"])
    long = pd.DataFrame(rows)
    return long.groupby(["key", "kpi", "col"])["value"].agg(mean="mean", std="std", n="size")


def kpi_deviations(name, table, baseline):
    """{(indice riga, colonna): z} per la tabella di un giocatore: scostamento
    dalla sua media in deviazioni standard, nel verso giusto (positivo =
    meglio del solito); solo con almeno MIN_HISTORY partite precedenti."""
    out = {}
    key = entity_key(name)
    for i, r in enumerate(table.to_dict("records")):
        for col, verso in CELL_DIRECTION.items():
            v = r.get(col)
            if v is None or pd.isna(v) or (key, r["kpi"], col) not in baseline.index:
                continue
            b = baseline.loc[(key, r["kpi"], col)]
            if b["n"] < MIN_HISTORY:
                continue
            std = max(b["std"] if not pd.isna(b["std"]) else 0.0, STD_FLOOR.get(col, STD_FLOOR_COUNTS))
            out[(i, col)] = verso * (v - b["mean"]) / std
    return out


# KPI della vista "ultima partita contro le precedenti": (chiave, etichetta,
# riga del tabellino, colonna, per set?, verso, percentuale?). I conteggi sono
# per set giocato dalla squadra, così una parte dei set è confrontabile con
# partite intere; le efficienze contano solo con almeno MIN_FORM_VOLUME azioni.
FORM_KPIS = [
    ("ric_eff", "Ricezione (eff.)", "ricezione", "eff", False, 1, True),
    ("bat_eff", "Battuta (eff.)", "battuta", "eff", False, 1, True),
    ("ace", "Ace per set", "battuta", "pt", True, 1, False),
    ("bat_err", "Errori in battuta per set", "battuta", "err", True, -1, False),
    ("so_eff", "Attacco SO (eff.)", "so", "eff", False, 1, True),
    ("bp_eff", "Attacco BP (eff.)", "bp", "eff", False, 1, True),
    ("fb_eff", "Attacco da free ball (eff.)", "fb", "eff", False, 1, True),
    ("alta_eff", "Palla alta (eff.)", "alta", "eff", False, 1, True),
    ("veloce_eff", "Palla veloce (eff.)", "veloce", "eff", False, 1, True),
    ("muro_pt", "Muri punto per set", "muro", "pt", True, 1, False),
    ("pt", "Punti per set", "totale", "pt", True, 1, False),
    ("err", "Errori per set", "totale", "err", True, -1, False),
    ("pe", "p-e/m per set", "totale", "pe", True, 1, False),
]
MIN_FORM_VOLUME = 3


def form_values(df, sets=None):
    """{giocatore o 'Squadra' (chiave entity_key): {kpi FORM_KPIS: valore}} di
    una partita, sui set `sets` (None = tutti); nomi per la visualizzazione
    in '_nomi'."""
    if sets is not None:
        df = df[df["Numero Set"].isin(sets)]
    n_sets = df["Numero Set"].nunique()
    if not n_sets:
        return {}
    out, names = {}, {}
    for name, table in player_kpi_table(df).items():
        rows = {r["kpi"]: r for r in table.to_dict("records")}
        values = {}
        for key, _, row, col, per_set, _, is_pct in FORM_KPIS:
            r = rows.get(row)
            if r is None or r.get(col) is None or pd.isna(r.get(col)):
                continue
            if is_pct and (r.get("tot") or 0) < MIN_FORM_VOLUME:
                continue
            values[key] = float(r[col]) / n_sets if per_set else float(r[col])
        out[entity_key(name)] = values
        names[entity_key(name)] = name
    out["_nomi"] = names
    return out
