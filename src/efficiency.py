# src/efficiency.py
"""
Funzioni core per il calcolo dell'efficienza per fondamentale, estratte dalle celle
di setup di notebooks/tabellino.ipynb (fino alla sezione "altro" esclusa: il tabellino
"standard" e le celle successive restano intenzionalmente nel notebook, non refactorate).

Nota sulla dedup: il notebook definiva due volte (in celle diverse) calcola_efficienza,
find_errors e separate_attacks_counterattacks. find_errors era divergente tra le due copie
(una contava anche gli errori di muro, l'altra no): qui è unificata includendo sempre il
muro. Questa versione unificata è usata da export_tabellino_to_xlsx (export "[tabellino F]").
Il tabellino "standard" (create_player_summary_df, sezione esclusa dal refactor) definisce
ancora localmente nel notebook la propria find_errors "senza muro" — la divergenza tra i due
export resta quindi presente, non essendo questa sezione in scope.
"""
import numpy as np
import pandas as pd

# Voti positivi/negativi di default per la battuta.
SRV_POS = ["#", "+", "/"]
SRV_NEG = ["="]


def eff_scalar(res):
    """Estrae lo scalare percentuale da calcola_efficienza(total_efficiency=True)."""
    if isinstance(res, pd.DataFrame) and "Efficienza Totale" in res.columns:
        v = res["Efficienza Totale"].iloc[0]
    else:
        v = float(res)
    return (v * 100) if -1.0 <= v <= 1.0 else v


def eff_from_calcola(df_subset, tipo_val, pos, neg):
    """Wrapper pratico per ottenere direttamente la % numerica di efficienza."""
    return eff_scalar(calcola_efficienza(
        df=df_subset, tipo=tipo_val, pos=list(pos), neg=list(neg), total_efficiency=True
    ))


def calcola_efficienza(df, tipo, pos=('#', '+'), neg=('-', '='),
                        total_efficiency=False, min_val=0, set=all):
    """
    Calcola l'efficienza per un fondamentale (`tipo`), opzionalmente per singolo set.
    - total_efficiency=True: ritorna una riga con l'efficienza aggregata.
    - total_efficiency=False: ritorna una riga per giocatore + riga 'Totale'.
    """
    filtered_df = df[df['Tipo'] == tipo]
    if set != all:
        filtered_df = filtered_df[filtered_df['Numero Set'] == set]

    if total_efficiency:
        positive_rows = filtered_df[filtered_df['Voto'].isin(pos)].shape[0]
        negative_rows = filtered_df[filtered_df['Voto'].isin(neg)].shape[0]
        total_rows = filtered_df.shape[0]
        efficiency = (positive_rows - negative_rows) / total_rows if total_rows > 0 else 0
        return pd.DataFrame([{'Tipo': tipo, 'Efficienza Totale': efficiency, 'Effettuati': total_rows}])

    grouped = filtered_df.groupby('Giocatore')
    results = []
    for player, player_df in grouped:
        positive_rows = player_df[player_df['Voto'].isin(pos)].shape[0]
        negative_rows = player_df[player_df['Voto'].isin(neg)].shape[0]
        total_rows = player_df.shape[0]
        if total_rows >= min_val:
            efficiency = (positive_rows - negative_rows) / total_rows
            player_result = {'Giocatore': player, 'Eff': efficiency, 'Tot': total_rows}
            for voto in player_df['Voto'].unique():
                player_result[voto] = player_df[player_df['Voto'] == voto].shape[0]
            results.append(player_result)
    results_df = pd.DataFrame(results)

    total_positive = filtered_df[filtered_df['Voto'].isin(pos)].shape[0]
    total_negative = filtered_df[filtered_df['Voto'].isin(neg)].shape[0]
    total_actions = filtered_df.shape[0]
    overall_efficiency = (total_positive - total_negative) / total_actions if total_actions > 0 else 0
    total_row = {'Giocatore': 'Totale', 'Eff': overall_efficiency, 'Tot': total_actions}
    for voto in filtered_df['Voto'].unique():
        total_row[voto] = filtered_df[filtered_df['Voto'] == voto].shape[0]
    total_row_df = pd.DataFrame([total_row])
    results_df = pd.concat([results_df, total_row_df], ignore_index=True)
    return results_df


def error_mask(df, tipo_col='Tipo', voto_col='Voto'):
    """
    Righe che sono un errore, unica definizione per tutto il progetto: Voto
    '=' su battuta/attacco/alzata e Voto '/' sul muro (l'errore di muro non
    usa '='). L'errore di muro va SEMPRE contato tra gli errori (indicazione
    esplicita dell'utente, 2026-10-23): prima il conteggio per set e i radar
    lo escludevano, il tabellino no.
    """
    tipo = df[tipo_col].astype(str).str.strip().str.lower()
    voto = df[voto_col].astype(str).str.strip()
    return ((tipo.isin(['battuta', 'attacco', 'alzata'])) & (voto == '=')) | ((tipo == 'muro') & (voto == '/'))


def find_errors(df):
    """
    Conta gli errori per giocatore (vedi error_mask): battuta/attacco/alzata
    con Voto '=' e muro con Voto '/'.
    """
    error_types = ['battuta', 'attacco', 'muro', 'alzata']
    errors_df = df[error_mask(df)]
    error_counts = errors_df.groupby(['Giocatore', 'Tipo']).size().unstack(fill_value=0)
    for error_type in error_types:
        if error_type not in error_counts.columns:
            error_counts[error_type] = 0
    total_errors = error_counts[error_types].sum()
    total_row = pd.DataFrame(total_errors).T
    total_row.index = ['Totale']
    error_counts_with_total = pd.concat([error_counts[error_types], total_row])
    error_counts_with_total = error_counts_with_total.reset_index().rename(columns={'index': 'Giocatore'})
    return error_counts_with_total


def separate_attacks_counterattacks(df, rec_vote=("#", "+", "!", "-")):
    """
    Ritorna (attacchi dopo ricezione, contrattacchi): un attacco è "dopo ricezione"
    se l'azione precedente (o quella-1 quando c'è un'alzata di mezzo) è una ricezione
    con voto in `rec_vote`; tutti gli altri attacchi sono contrattacchi.

    Controllo di consistenza: la ricezione (e l'eventuale alzata) e l'attacco devono
    avere lo stesso 'Numero Set' e lo stesso punteggio ('Punti Locali'/'Punti Ospiti').
    Il dataframe qui in ingresso è già filtrato sui giocatori riconosciuti (o solo
    sulla nostra squadra, come nei file di match analysis): un'azione di un
    giocatore IGNORED, o un evento generico avversario senza attribuzione, non
    compare più come riga e la posizione i-1/i-2 può quindi "saltare" oltre la
    vera azione successiva alla ricezione, agganciando erroneamente un attacco di
    un rally diverso. Se set o punteggio non coincidono, qualcosa è successo in
    mezzo che qui non è visibile: l'attacco non può essere il primo dopo questa
    ricezione e ricade tra i contrattacchi. (Verificato sui dati reali stagione
    2025-2026: 5 falsi positivi su 1640 attacchi "dopo ricezione", tutti dovuti
    a giocatori IGNORED nel mezzo della sequenza.)
    """
    if "Tipo" not in df.columns or "Voto" not in df.columns:
        raise ValueError("Mancano colonne 'Tipo' o 'Voto' nel DataFrame.")
    d = df.copy()
    d["_tipo_lc"] = d["Tipo"].astype(str).str.lower()
    d["_voto"] = d["Voto"].astype(str).str.strip()

    score_cols = ("Numero Set", "Punti Locali", "Punti Ospiti")
    has_score_cols = all(c in d.columns for c in score_cols)

    def _consistent(rec_pos, att_pos):
        """Stesso set e stesso punteggio tra la riga ricezione e la riga attacco."""
        if not has_score_cols:
            return True
        rec_row, att_row = d.iloc[rec_pos], d.iloc[att_pos]
        return all(rec_row[c] == att_row[c] for c in score_cols)

    idx_after_rec, idx_counter = [], []
    for i in range(len(d)):
        if d.iloc[i]["_tipo_lc"] != "attacco":
            continue
        after_reception = False
        if (i - 1 >= 0 and d.iloc[i - 1]["_tipo_lc"] == "ricezione" and d.iloc[i - 1]["_voto"] in rec_vote
                and _consistent(i - 1, i)):
            after_reception = True
        if not after_reception and i - 2 >= 0:
            if (d.iloc[i - 2]["_tipo_lc"] == "ricezione" and d.iloc[i - 2]["_voto"] in rec_vote
                    and d.iloc[i - 1]["_tipo_lc"] == "alzata"
                    and _consistent(i - 2, i)):
                after_reception = True
        (idx_after_rec if after_reception else idx_counter).append(df.index[i])
    return df.loc[idx_after_rec], df.loc[idx_counter]


def separate_free_ball(df):
    """
    Ritorna gli attacchi che avvengono subito dopo una difesa con voto '!'
    (free ball: palla rimandata facile nel nostro campo — convenzione Tipo
    'difesa'/Voto '!'), o dopo un'alzata che segue una difesa '!'.

    Stesso controllo di consistenza di separate_attacks_counterattacks:
    difesa (ed eventuale alzata) e attacco devono avere lo stesso
    'Numero Set' e lo stesso punteggio ('Punti Locali'/'Punti Ospiti'),
    altrimenti un'azione IGNORED nel mezzo della sequenza potrebbe agganciare
    erroneamente un attacco di un rally diverso alla difesa (stesso bug già
    corretto in separate_attacks_counterattacks — fix applicato qui per
    parità, 2026-08-12).
    """
    if "Tipo" not in df.columns or "Voto" not in df.columns:
        raise ValueError("Mancano colonne 'Tipo' o 'Voto' nel DataFrame.")
    d = df.copy()
    d["_tipo_lc"] = d["Tipo"].astype(str).str.lower()
    d["_voto"] = d["Voto"].astype(str).str.strip()

    score_cols = ("Numero Set", "Punti Locali", "Punti Ospiti")
    has_score_cols = all(c in d.columns for c in score_cols)

    def _consistent(def_pos, att_pos):
        if not has_score_cols:
            return True
        def_row, att_row = d.iloc[def_pos], d.iloc[att_pos]
        return all(def_row[c] == att_row[c] for c in score_cols)

    idx_free_ball = []
    for i in range(1, len(d)):
        if d.iloc[i]["_tipo_lc"] != "attacco":
            continue
        after_free_ball = False
        if (d.iloc[i - 1]["_tipo_lc"] == "difesa" and d.iloc[i - 1]["_voto"] == "!"
                and _consistent(i - 1, i)):
            after_free_ball = True
        elif (i - 2 >= 0 and d.iloc[i - 2]["_tipo_lc"] == "difesa" and d.iloc[i - 2]["_voto"] == "!"
                and d.iloc[i - 1]["_tipo_lc"] == "alzata" and _consistent(i - 2, i)):
            after_free_ball = True
        if after_free_ball:
            idx_free_ball.append(d.index[i])
    return df.loc[idx_free_ball]


def separate_attack_types(df, rec_vote=("#", "+", "!", "-")):
    """
    Come separate_attacks_counterattacks, ma a tre vie: distingue anche gli
    attacchi immediatamente successivi a una free ball (difesa Voto '!',
    vedi separate_free_ball) dal contrattacco generico — sono situazioni
    favorevoli (palla facile) concettualmente diverse da una vera azione di
    rimessa in gioco confusa, e prima venivano contate insieme nel
    contrattacco.

    Stesso controllo di consistenza (Numero Set/punteggio) usato da
    separate_attacks_counterattacks e separate_free_ball. Ritorna
    (so_df, freeball_df, contrattacco_df): tre sottoinsiemi disgiunti ed
    esaustivi di tutti gli attacchi del dataframe — per costruzione,
    so_df + freeball_df + contrattacco_df ricopre esattamente tutti gli
    attacchi (verificato: Attacco SO Tot + Attacco FB Tot + Contrattacco Tot
    == Attacco Tot, sempre, stagione 2025-2026).

    Nota: non sostituisce separate_attacks_counterattacks/separate_free_ball
    (usate anche da export_tabellino_to_xlsx con la vecchia sovrapposizione
    a due vie, lasciata invariata) — è una funzione aggiuntiva per i casi
    (dashboard/pagella) che vogliono i tre gruppi non sovrapposti.
    """
    if "Tipo" not in df.columns or "Voto" not in df.columns:
        raise ValueError("Mancano colonne 'Tipo' o 'Voto' nel DataFrame.")
    d = df.copy()
    d["_tipo_lc"] = d["Tipo"].astype(str).str.lower()
    d["_voto"] = d["Voto"].astype(str).str.strip()

    score_cols = ("Numero Set", "Punti Locali", "Punti Ospiti")
    has_score_cols = all(c in d.columns for c in score_cols)

    def _consistent(pos_a, pos_b):
        if not has_score_cols:
            return True
        row_a, row_b = d.iloc[pos_a], d.iloc[pos_b]
        return all(row_a[c] == row_b[c] for c in score_cols)

    idx_so, idx_freeball, idx_contr = [], [], []
    for i in range(len(d)):
        if d.iloc[i]["_tipo_lc"] != "attacco":
            continue

        after_reception = False
        if (i - 1 >= 0 and d.iloc[i - 1]["_tipo_lc"] == "ricezione" and d.iloc[i - 1]["_voto"] in rec_vote
                and _consistent(i - 1, i)):
            after_reception = True
        elif (i - 2 >= 0 and d.iloc[i - 2]["_tipo_lc"] == "ricezione" and d.iloc[i - 2]["_voto"] in rec_vote
                and d.iloc[i - 1]["_tipo_lc"] == "alzata" and _consistent(i - 2, i)):
            after_reception = True

        after_free_ball = False
        if not after_reception:
            if (i - 1 >= 0 and d.iloc[i - 1]["_tipo_lc"] == "difesa" and d.iloc[i - 1]["_voto"] == "!"
                    and _consistent(i - 1, i)):
                after_free_ball = True
            elif (i - 2 >= 0 and d.iloc[i - 2]["_tipo_lc"] == "difesa" and d.iloc[i - 2]["_voto"] == "!"
                    and d.iloc[i - 1]["_tipo_lc"] == "alzata" and _consistent(i - 2, i)):
                after_free_ball = True

        if after_reception:
            idx_so.append(df.index[i])
        elif after_free_ball:
            idx_freeball.append(df.index[i])
        else:
            idx_contr.append(df.index[i])

    return df.loc[idx_so], df.loc[idx_freeball], df.loc[idx_contr]


def calcola_efficienza_free_ball(df):
    """Efficienza degli attacchi di Free Ball: (# - (=/+/)) / totale attacchi."""
    return eff_from_calcola(df, tipo_val='attacco', pos=['#'], neg=['=', '/'])


def compute_set_metrics(
    df,
    set_col="Numero Set",
    tipo_col="Tipo",
    voto_col="Voto",
    pos_srv=SRV_POS, neg_srv=SRV_NEG,
    pos_rec=("#", "+"), neg_rec=("/", "="),
    pos_att=("#",), neg_att=("/", "="),
    pos_blk=("#", "+"), neg_blk=("/", "="),
    rec_vote=("#", "+", "!", "-"),
):
    """
    Ritorna un DataFrame indicizzato per Numero Set con colonne:
    ['Battuta%', 'Ricezione%', 'Attacco SO%', 'Contrattacco%', 'Muro%', 'Errori'].
    """
    if set_col not in df.columns:
        raise ValueError(f"Colonna set '{set_col}' non trovata nel DataFrame.")

    out_rows = []
    for s in sorted(df[set_col].dropna().unique()):
        d = df[df[set_col] == s].copy()
        if d.empty:
            continue
        d[tipo_col] = d[tipo_col].astype(str).str.strip().str.lower()

        def _eff_or_nan(sub_df, tipo, pos, neg):
            sub = sub_df[sub_df[tipo_col] == tipo]
            if sub.empty:
                return float("nan")
            return eff_scalar(calcola_efficienza(sub, tipo=tipo, pos=list(pos), neg=list(neg), total_efficiency=True))

        battuta_pct = _eff_or_nan(d, "battuta", pos_srv, neg_srv)
        ricez_pct = _eff_or_nan(d, "ricezione", pos_rec, neg_rec)
        muro_pct = _eff_or_nan(d, "muro", pos_blk, neg_blk)

        so_d, ctr_d = separate_attacks_counterattacks(d, rec_vote=list(rec_vote)) if rec_vote is not None \
            else separate_attacks_counterattacks(d)
        so_d = so_d[so_d[tipo_col] == "attacco"]
        ctr_d = ctr_d[ctr_d[tipo_col] == "attacco"]

        att_so_pct = (eff_scalar(calcola_efficienza(so_d, tipo="attacco", pos=list(pos_att), neg=list(neg_att),
                                                      total_efficiency=True))
                      if not so_d.empty else float("nan"))
        att_ctr_pct = (eff_scalar(calcola_efficienza(ctr_d, tipo="attacco", pos=list(pos_att), neg=list(neg_att),
                                                       total_efficiency=True))
                       if not ctr_d.empty else float("nan"))

        errors = int(error_mask(d, tipo_col, voto_col).sum())

        out_rows.append({
            set_col: s,
            "Battuta%": battuta_pct,
            "Ricezione%": ricez_pct,
            "Attacco SO%": att_so_pct,
            "Contrattacco%": att_ctr_pct,
            "Muro%": muro_pct,
            "Errori": errors,
        })

    return pd.DataFrame(out_rows).set_index(set_col).sort_index()


# Sezioni del tabellino: (titolo, sottocolonne). Blocco sopra = side-out
# (ricezione e attacco dopo ricezione), blocco sotto = break point (battuta,
# contrattacco, free ball, muro) + errori. Insieme di voti ampio per gli
# attacchi, per coprire ogni evenienza.
TABELLINO_ATT_VOTES = ['-', '#', '+', '/', '=', '!']
TABELLINO_REC_VOTES = ['#', '+', '!', '-', '/', '=']
TABELLINO_TOP_SECTIONS = [
    ("Ricezione", ['Eff', 'Tot'] + TABELLINO_REC_VOTES),
    ("Attacco(R tot)", ['Eff', 'Tot'] + TABELLINO_ATT_VOTES),
    ("Attacco(R#+)", ['Eff', 'Tot'] + TABELLINO_ATT_VOTES),
    ("Attacco(R!)", ['Eff', 'Tot'] + TABELLINO_ATT_VOTES),
    ("Attacco(R-)", ['Eff', 'Tot'] + TABELLINO_ATT_VOTES),
]
TABELLINO_BOTTOM_SECTIONS = [
    ("Battuta", ['Eff', 'Tot'] + TABELLINO_ATT_VOTES),
    ("Contrattacco", ['Eff', 'Tot'] + TABELLINO_ATT_VOTES),
    ("Attacco(Free Ball)", ['Eff', 'Tot'] + TABELLINO_ATT_VOTES),
    ("Muro", ['Eff', 'Tot'] + ['#', '+', '!', '/', '=']),
    ("Errori", ['Bat', 'Att', 'Muro', 'Alz']),
]


SET_TABELLINO_TOTAL = "Totale"


def compute_set_tabellino(df, group_col="Numero Set"):
    """
    Tabellino della dashboard set per set (richiesta dell'utente, 2026-10-30:
    al posto delle colonne per voto, totale ed efficienza per set e sul
    totale), con le stesse sezioni e definizioni di compute_tabellino:
    ricezione, attacco dopo ricezione (tutte, # o +, !, -), contrattacco
    (free ball comprese), attacco da free ball: Tot ed Eff (%); battuta: Tot,
    Err, Ace (senza efficienza); muro: solo i muri punto (Pt); errori
    (error_mask) per fondamentale: Bat, Att, Alz, Muro e Tot. `group_col`: colonna che definisce le colonne del
    tabellino (il set; per più partite insieme, la partita).

    Ritorna (players, groups, sections): players = giocatori (colonna
    'Giocatore') in ordine alfabetico, groups = valori di group_col in
    ordine, sections = [(titolo, DataFrame)] con indice players + 'Totale' e
    colonne MultiIndex (gruppo o SET_TABELLINO_TOTAL, misura); NaN dove il
    giocatore non ha azioni di quel tipo.
    """
    all_rec_attacks, counterattacks = separate_attacks_counterattacks(df)
    att_pos, _ = separate_attacks_counterattacks(df, rec_vote=["#", "+"])
    att_excl, _ = separate_attacks_counterattacks(df, rec_vote=["!"])
    att_neg, _ = separate_attacks_counterattacks(df, rec_vote=["-"])
    free_ball = separate_free_ball(df)

    def only(frame, tipo):
        return frame[frame["Tipo"] == tipo]

    specs = [
        ("Ricezione", "eff", only(df, "ricezione"), ["#", "+"], ["=", "/"]),
        ("Attacco (R tot)", "eff", only(all_rec_attacks, "attacco"), ["#"], ["=", "/"]),
        ("Attacco (R#+)", "eff", only(att_pos, "attacco"), ["#"], ["=", "/"]),
        ("Attacco (R!)", "eff", only(att_excl, "attacco"), ["#"], ["=", "/"]),
        ("Attacco (R-)", "eff", only(att_neg, "attacco"), ["#"], ["=", "/"]),
        ("Contrattacco", "eff", only(counterattacks, "attacco"), ["#"], ["=", "/"]),
        ("Attacco (Free Ball)", "eff", only(free_ball, "attacco"), ["#"], ["=", "/"]),
        ("Battuta", "battuta", only(df, "battuta"), None, None),
        ("Muro", "muro", only(df, "muro"), None, None),
        ("Errori", "errori", df[error_mask(df)], None, None),
    ]
    players = sorted(p for p in df["Giocatore"].dropna().unique() if p != "Totale")
    groups = list(dict.fromkeys(df[group_col].dropna()))
    rows = players + ["Totale"]

    def measures(frame, kind, pos, neg):
        n = len(frame)
        if kind == "eff":
            if not n:
                return {"Tot": np.nan, "Eff": np.nan}
            return {"Tot": n, "Eff": 100 * (frame["Voto"].isin(pos).sum() - frame["Voto"].isin(neg).sum()) / n}
        if kind == "battuta":
            if not n:
                return {"Tot": np.nan, "Err": np.nan, "Ace": np.nan}
            return {"Tot": n, "Err": int((frame["Voto"] == "=").sum()), "Ace": int((frame["Voto"] == "#").sum())}
        if kind == "muro":
            return {"Pt": int((frame["Voto"] == "#").sum()) if n else np.nan}
        if not n:
            return {k: np.nan for k in ("Bat", "Att", "Alz", "Muro", "Tot")}
        return {"Bat": int((frame["Tipo"] == "battuta").sum()), "Att": int((frame["Tipo"] == "attacco").sum()),
                "Alz": int((frame["Tipo"] == "alzata").sum()), "Muro": int((frame["Tipo"] == "muro").sum()),
                "Tot": n}

    sections = []
    for title, kind, frame, pos, neg in specs:
        data = {}
        for g in groups + [SET_TABELLINO_TOTAL]:
            part = frame if g == SET_TABELLINO_TOTAL else frame[frame[group_col] == g]
            for player in rows:
                sub = part if player == "Totale" else part[part["Giocatore"] == player]
                for m, v in measures(sub, kind, pos, neg).items():
                    data.setdefault((g, m), {})[player] = v
        table = pd.DataFrame(data, index=rows)
        table.columns = pd.MultiIndex.from_tuples(table.columns)
        sections.append((title, table))
    return players, groups, sections


def export_set_tabellino_to_xlsx(sections, output, sheet_name="Tabellino"):
    """
    Excel del tabellino set per set (compute_set_tabellino), con la stessa
    struttura della dashboard (richiesta dell'utente, 2026-10-30: l'export
    allineato al tabellino nuovo, al posto delle colonne per voto di
    "[tabellino F]"): una sezione sotto l'altra, set (o partite) come gruppi
    di colonne separati da una linea spessa, totale in grassetto, Eff
    colorata con la scala rosso/bianco/verde dell'export storico. `output`:
    percorso o buffer (BytesIO).
    """
    import xlsxwriter

    wb = xlsxwriter.Workbook(output, {"in_memory": True})
    ws = wb.add_worksheet(sheet_name)
    title = wb.add_format({"bold": True, "font_size": 12})
    sep = {"left": 5, "left_color": "#5b6b82"}
    head = {"bold": True, "align": "center", "bg_color": "#eef1f5", "border": 1, "border_color": "#c8ccd2"}
    cell = {"align": "center", "border": 1, "border_color": "#d9dce1"}
    fmt_cache = {}

    def fmt(base, *, bold=False, left=False, pct=False):
        key = (id(base), bold, left, pct)
        if key not in fmt_cache:
            props = dict(base)
            if bold:
                props["bold"] = True
            if left:
                props.update(sep)
            if pct:
                props["num_format"] = '0"%"'
            fmt_cache[key] = wb.add_format(props)
        return fmt_cache[key]

    row = 0
    ws.set_column(0, 0, 22)
    for name, table in sections:
        ws.write(row, 0, name, title)
        row += 1
        cols = list(table.columns)
        starts = {i for i, c in enumerate(cols) if i and c[0] != cols[i - 1][0]}
        # intestazione: gruppo (set/partita/totale) su colonne unite, poi la misura
        i = 0
        while i < len(cols):
            j = i
            while j + 1 < len(cols) and cols[j + 1][0] == cols[i][0]:
                j += 1
            f = fmt(head, left=i in starts)
            if j > i:
                ws.merge_range(row, 1 + i, row, 1 + j, cols[i][0], f)
            else:
                ws.write(row, 1 + i, cols[i][0], f)
            i = j + 1
        ws.write(row + 1, 0, "", fmt(head))
        for i, c in enumerate(cols):
            ws.write(row + 1, 1 + i, c[1], fmt(head, left=i in starts))
        row += 2
        first = row
        for player, values in table.iterrows():
            total = player == "Totale"
            ws.write(row, 0, player, fmt({"bold": True, "border": 1, "border_color": "#d9dce1"}, bold=total))
            for i, c in enumerate(cols):
                v = values[c]
                f = fmt(cell, bold=total or c[0] == SET_TABELLINO_TOTAL, left=i in starts, pct=c[1] == "Eff")
                if pd.isna(v):
                    ws.write_blank(row, 1 + i, None, f)
                else:
                    ws.write_number(row, 1 + i, round(float(v)) if c[1] == "Eff" else int(v), f)
            row += 1
        for i, c in enumerate(cols):
            if c[1] == "Eff":
                ws.conditional_format(first, 1 + i, row - 1, 1 + i, {
                    "type": "3_color_scale", "min_type": "num", "min_value": -100, "min_color": "#FF0000",
                    "mid_type": "num", "mid_value": 0, "mid_color": "#FFFFFF",
                    "max_type": "num", "max_value": 100, "max_color": "#00B050"})
        row += 2
    ws.freeze_panes(0, 1)
    wb.close()


def compute_tabellino(df):
    """
    Calcoli del tabellino "[tabellino F]" (vedi export_tabellino_to_xlsx),
    separati dalla scrittura Excel per riusarli anche in dashboard.

    Ritorna (players, top_maps, bottom_maps): players = giocatori in ordine
    alfabetico (senza 'Totale'); top_maps/bottom_maps = una mappa per sezione
    di TABELLINO_TOP_SECTIONS/TABELLINO_BOTTOM_SECTIONS, nello stesso ordine,
    {giocatore o 'Totale': {sottocolonna: valore, '' se assente}}.

    `df` può contenere più partite concatenate (con indice univoco): la
    separazione side-out/contrattacco richiede ricezione e attacco nello stesso
    set con lo stesso punteggio, quindi non aggancia mai azioni di partite diverse.
    """
    # ---------- calcoli base (riuso funzioni esistenti) ----------
    rice_eff = calcola_efficienza(df, 'ricezione', pos=['#', '+'], neg=['=', '/'])

    # attacchi dopo ricezione (tutti), + contrattacchi
    all_rec_attacks, counterattacks = separate_attacks_counterattacks(df)
    att_rtot = calcola_efficienza(all_rec_attacks, 'attacco', pos=['#'], neg=['=', '/'])

    # att(R#+), att(R!), att(R-)
    att_rpos_att, _ = separate_attacks_counterattacks(df, rec_vote=["#", "+"])
    att_rpos = calcola_efficienza(att_rpos_att, 'attacco', pos=['#'], neg=['=', '/'])

    att_rexcl_att, _ = separate_attacks_counterattacks(df, rec_vote=["!"])
    att_rexcl = calcola_efficienza(att_rexcl_att, 'attacco', pos=['#'], neg=['=', '/'])

    att_rneg_att, _ = separate_attacks_counterattacks(df, rec_vote=["-"])
    att_rneg = calcola_efficienza(att_rneg_att, 'attacco', pos=['#'], neg=['=', '/'])

    # Free Ball
    free_ball_attacks = separate_free_ball(df)
    free_ball_eff = calcola_efficienza(free_ball_attacks, 'attacco', pos=['#'], neg=['=', '/'])

    # altri fondamentali
    battuta = calcola_efficienza(df, 'battuta', pos=SRV_POS, neg=SRV_NEG)
    muro = calcola_efficienza(df, 'muro', pos=['#', '+'], neg=['=', '/'])
    contr = calcola_efficienza(counterattacks, 'attacco', pos=['#'], neg=['=', '/'])
    errors = find_errors(df)

    # elenco giocatori (esclude "Totale")
    def _players_from(*frames):
        names = set()
        for fr in frames:
            if fr is not None and not fr.empty and 'Giocatore' in fr.columns:
                names |= set(fr['Giocatore'])
        return sorted([n for n in names if isinstance(n, str) and n != 'Totale'])

    players = _players_from(
        rice_eff, att_rtot, att_rpos, att_rexcl, att_rneg,
        battuta, muro, contr, free_ball_eff, errors
    )

    TOP_SECTIONS, BOTTOM_SECTIONS = TABELLINO_TOP_SECTIONS, TABELLINO_BOTTOM_SECTIONS

    # mappa DF -> dict per scrittura (con rinomina anche di 'muro' -> 'Muro')
    def _df_to_map(fr, subcols, kind):
        out = {p: {s: '' for s in subcols} for p in (players + ['Totale'])}
        if fr is None or fr.empty:
            return out
        fr = fr.copy()
        if kind == 'err':
            ren = {}
            if 'battuta' in fr.columns: ren['battuta'] = 'Bat'
            if 'attacco' in fr.columns: ren['attacco'] = 'Att'
            if 'muro' in fr.columns: ren['muro'] = 'Muro'
            if 'alzata' in fr.columns: ren['alzata'] = 'Alz'
            fr = fr.rename(columns=ren)
        for _, row in fr.iterrows():
            pl = row['Giocatore']
            if pl not in out:
                continue
            for s in subcols:
                if s in row:
                    out[pl][s] = row[s]
        return out

    # mappe blocco sopra
    m_rice = _df_to_map(rice_eff, TOP_SECTIONS[0][1], 'eff')
    m_rtot = _df_to_map(att_rtot, TOP_SECTIONS[1][1], 'eff')
    m_rpos = _df_to_map(att_rpos, TOP_SECTIONS[2][1], 'eff')
    m_rexc = _df_to_map(att_rexcl, TOP_SECTIONS[3][1], 'eff')
    m_rneg = _df_to_map(att_rneg, TOP_SECTIONS[4][1], 'eff')

    # mappe blocco sotto (nota m_fball)
    m_bat = _df_to_map(battuta, BOTTOM_SECTIONS[0][1], 'eff')
    m_con = _df_to_map(contr, BOTTOM_SECTIONS[1][1], 'eff')
    m_fball = _df_to_map(free_ball_eff, BOTTOM_SECTIONS[2][1], 'eff')
    m_muro = _df_to_map(muro, BOTTOM_SECTIONS[3][1], 'eff')
    m_err = _df_to_map(errors, BOTTOM_SECTIONS[4][1], 'err')

    return players, [m_rice, m_rtot, m_rpos, m_rexc, m_rneg], [m_bat, m_con, m_fball, m_muro, m_err]


def export_tabellino_to_xlsx(
    df,
    filepath,
    sheet_name="Tabellino",
    top_start_row=0,
    bottom_start_row=None,   # <-- può essere None/auto
    bottom_gap_rows=1        # <-- righe vuote tra blocco alto e basso
):
    """Esporta un tabellino con blocco Ricezione/Attacco(sopra) + Battuta/Contrattacco/Free Ball/Muro/Errori(sotto)."""
    import pandas as pd
    import xlsxwriter

    # ---------- helper ----------
    def _set_col_width_px(ws, first_col, last_col, pixels):
        if hasattr(ws, "set_column_pixels"):
            ws.set_column_pixels(first_col, last_col, int(pixels))
        else:
            width_chars = max(0.0, (float(pixels) - 5.0) / 7.0)
            ws.set_column(first_col, last_col, width_chars)

    def _safe_write(ws, r, c, val, fmt=None):
        if isinstance(val, str) and val[:1] in ("=", "+", "-", "@"):
            ws.write_string(r, c, val, fmt)
        else:
            ws.write(r, c, val, fmt)

    def _num(val):
        if val == '' or pd.isna(val):
            return None
        try:
            return float(val)
        except Exception:
            try:
                return int(val)
            except Exception:
                return val

    # ---------- calcoli (vedi compute_tabellino) ----------
    players, (m_rice, m_rtot, m_rpos, m_rexc, m_rneg), (m_bat, m_con, m_fball, m_muro, m_err) = compute_tabellino(df)
    TOP_SECTIONS, BOTTOM_SECTIONS = TABELLINO_TOP_SECTIONS, TABELLINO_BOTTOM_SECTIONS

    # ---------- workbook / formati ----------
    wb = xlsxwriter.Workbook(filepath)
    ws = wb.add_worksheet(sheet_name)

    cfg_header = {'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1}
    cfg_subhdr = {'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1}
    cfg_name = {'align': 'left', 'valign': 'vcenter', 'border': 1}
    cfg_int = {'num_format': '0', 'align': 'right', 'valign': 'vcenter', 'border': 1}
    cfg_pct = {'num_format': '0%', 'align': 'right', 'valign': 'vcenter', 'border': 1}

    def F(cfg, left=False, right=False):
        d = dict(cfg)
        if left: d['left'] = 5
        if right: d['right'] = 5
        return wb.add_format(d)

    fmt_header_merge_LR = F(cfg_header, left=True, right=True)
    fmt_sub_mid = F(cfg_subhdr)
    fmt_sub_L = F(cfg_subhdr, left=True)
    fmt_sub_R = F(cfg_subhdr, right=True)

    fmt_name = wb.add_format(cfg_name)
    fmt_int_mid = F(cfg_int)
    fmt_int_L = F(cfg_int, left=True)
    fmt_int_R = F(cfg_int, right=True)
    fmt_pct_mid = F(cfg_pct)
    fmt_pct_L = F(cfg_pct, left=True)
    fmt_pct_R = F(cfg_pct, right=True)

    # Totali: solo orizzontali thick neri; laterali solo prima/ultima cella
    cfg_total_name = {'bold': True, 'align': 'left', 'valign': 'vcenter', 'top': 5, 'bottom': 5}
    cfg_total_int = {'bold': True, 'num_format': '0', 'align': 'right', 'valign': 'vcenter', 'top': 5, 'bottom': 5}
    cfg_total_pct = {'bold': True, 'num_format': '0%', 'align': 'right', 'valign': 'vcenter', 'top': 5, 'bottom': 5}

    fmt_total_name_first = wb.add_format({**cfg_total_name, 'left': 5})
    fmt_total_name_mid = wb.add_format(cfg_total_name)
    fmt_total_int_mid = wb.add_format(cfg_total_int)
    fmt_total_int_last = wb.add_format({**cfg_total_int, 'right': 5})
    fmt_total_pct_mid = wb.add_format(cfg_total_pct)
    fmt_total_pct_last = wb.add_format({**cfg_total_pct, 'right': 5})

    # larghezze colonne
    _set_col_width_px(ws, 0, 0, 90)

    # ===== BLOCCO ALTO =====
    r0 = top_start_row
    col = 1
    _safe_write(ws, r0, 0, "", fmt_header_merge_LR)
    _safe_write(ws, r0 + 1, 0, "", fmt_sub_mid)

    top_eff_cols = []
    top_sections_bounds = []

    for titolo, subcols in TOP_SECTIONS:
        width = len(subcols)
        start = col
        ws.merge_range(r0, col, r0, col + width - 1, titolo, fmt_header_merge_LR)
        for j, lab in enumerate(subcols):
            fmt = fmt_sub_mid
            if j == 0: fmt = fmt_sub_L
            if j == width - 1: fmt = fmt_sub_R
            _safe_write(ws, r0 + 1, col + j, str(lab), fmt)
        if 'Eff' in subcols:
            top_eff_cols.append(col + subcols.index('Eff'))
        top_sections_bounds.append((start, width))
        col += width

    n_cols_top = col
    _set_col_width_px(ws, 1, n_cols_top - 1, 35)
    ws.freeze_panes(r0 + 2, 1)

    # dati giocatori (sopra)
    r = r0 + 2
    for p in players:
        _safe_write(ws, r, 0, str(p), fmt_name)
        c = 1
        for (start, width), (_, subs), m in zip(
            top_sections_bounds,
            TOP_SECTIONS,
            [m_rice, m_rtot, m_rpos, m_rexc, m_rneg]
        ):
            for j, s in enumerate(subs):
                val = _num(m[p].get(s, ''))
                left_edge = (j == 0)
                right_edge = (j == width - 1)
                if s == 'Eff':
                    fmt = fmt_pct_mid
                    if left_edge: fmt = fmt_pct_L
                    if right_edge: fmt = fmt_pct_R
                else:
                    fmt = fmt_int_mid
                    if left_edge: fmt = fmt_int_L
                    if right_edge: fmt = fmt_int_R
                ws.write(r, c, val, fmt)
                c += 1
        r += 1

    # riga Totale (sopra)
    total_row_top = r
    _safe_write(ws, total_row_top, 0, "Totale", fmt_total_name_first)
    c = 1
    last_col_top = n_cols_top - 1
    for (_, subs), m in zip(TOP_SECTIONS, [m_rice, m_rtot, m_rpos, m_rexc, m_rneg]):
        for j, s in enumerate(subs):
            val = _num(m['Totale'].get(s, ''))
            is_last_cell = (c == last_col_top)
            if s == 'Eff':
                fmt = fmt_total_pct_last if is_last_cell else fmt_total_pct_mid
            else:
                fmt = fmt_total_int_last if is_last_cell else fmt_total_int_mid
            ws.write(total_row_top, c, val, fmt)
            c += 1

    # ===== BLOCCO BASSO =====
    # Calcolo dinamico dello start del blocco basso (evita sovrapposizioni)
    min_r0b = total_row_top + 1 + int(bottom_gap_rows)  # riga subito dopo il totale + gap
    if isinstance(bottom_start_row, int):
        r0b = max(bottom_start_row, min_r0b)
    else:
        r0b = min_r0b

    col = 1
    _safe_write(ws, r0b, 0, "", fmt_header_merge_LR)
    _safe_write(ws, r0b + 1, 0, "", fmt_sub_mid)

    bottom_eff_cols = []
    bottom_sections_bounds = []

    for titolo, subcols in BOTTOM_SECTIONS:
        width = len(subcols)
        start = col
        ws.merge_range(r0b, col, r0b, col + width - 1, titolo, fmt_header_merge_LR)
        for j, lab in enumerate(subcols):
            fmt = fmt_sub_mid
            if j == 0: fmt = fmt_sub_L
            if j == width - 1: fmt = fmt_sub_R
            _safe_write(ws, r0b + 1, col + j, str(lab), fmt)
        if 'Eff' in subcols:
            bottom_eff_cols.append(col + subcols.index('Eff'))
        bottom_sections_bounds.append((start, width))
        col += width

    n_cols_bottom = col
    _set_col_width_px(ws, 1, max(n_cols_top, n_cols_bottom) - 1, 35)

    # dati giocatori (sotto)
    r = r0b + 2
    for p in players:
        _safe_write(ws, r, 0, str(p), fmt_name)
        c = 1
        for (start, width), (_, subs), m in zip(
            bottom_sections_bounds,
            BOTTOM_SECTIONS,
            [m_bat, m_con, m_fball, m_muro, m_err]
        ):
            for j, s in enumerate(subs):
                val = _num(m[p].get(s, ''))
                left_edge = (j == 0)
                right_edge = (j == width - 1)
                if s == 'Eff':
                    fmt = fmt_pct_mid
                    if left_edge: fmt = fmt_pct_L
                    if right_edge: fmt = fmt_pct_R
                else:
                    fmt = fmt_int_mid
                    if left_edge: fmt = fmt_int_L
                    if right_edge: fmt = fmt_int_R
                ws.write(r, c, val, fmt)
                c += 1
        r += 1

    # riga Totale (sotto)
    total_row_bottom = r
    _safe_write(ws, total_row_bottom, 0, "Totale", fmt_total_name_first)
    c = 1
    last_col_bottom = n_cols_bottom - 1
    for (_, subs), m in zip(BOTTOM_SECTIONS, [m_bat, m_con, m_fball, m_muro, m_err]):
        for j, s in enumerate(subs):
            val = _num(m['Totale'].get(s, ''))
            is_last_cell = (c == last_col_bottom)
            if s == 'Eff':
                fmt = fmt_total_pct_last if is_last_cell else fmt_total_pct_mid
            else:
                fmt = fmt_total_int_last if is_last_cell else fmt_total_int_mid
            ws.write(total_row_bottom, c, val, fmt)
            c += 1

    # ---------- scala colori su colonne 'Eff' ----------
    def _apply_color_scale(ws, first_row, last_row, col_idx):
        ws.conditional_format(first_row, col_idx, last_row, col_idx, {
            'type': '3_color_scale',
            'min_type': 'num', 'min_value': -1, 'min_color': '#FF0000',
            'mid_type': 'num', 'mid_value': 0, 'mid_color': '#FFFFFF',
            'max_type': 'num', 'max_value': 1, 'max_color': '#00B050',
        })

    data_start_top = (top_start_row + 2)
    for c in top_eff_cols:
        _apply_color_scale(ws, data_start_top, total_row_top, c)

    data_start_bottom = (r0b + 2)
    for c in bottom_eff_cols:
        _apply_color_scale(ws, data_start_bottom, total_row_bottom, c)

    ws.freeze_panes(top_start_row + 2, 1)
    wb.close()
    return filepath

