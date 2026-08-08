# src/efficiency.py
"""
Funzioni core per il calcolo dell'efficienza per fondamentale e la generazione
del tabellino giocatori, estratte da notebooks/tabellino.ipynb.

Nota sulla dedup: il notebook definiva due volte (in celle diverse) calcola_efficienza,
find_errors e separate_attacks_counterattacks, con find_errors divergente tra le due copie
(una contava anche gli errori di muro, l'altra no) — la copia "senza muro" era quella
effettivamente in uso per il tabellino standard, l'altra per l'export xlsx con Free Ball.
Qui la logica è unificata in un'unica versione che include sempre gli errori di muro.
"""
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


def find_errors(df):
    """
    Conta gli errori per giocatore su battuta/attacco/alzata (Voto == '=') e
    sul muro (Voto == '/', l'errore di muro non usa '=').
    """
    error_types = ['battuta', 'attacco', 'muro', 'alzata']
    errors_df = df[((df['Tipo'].isin(['battuta', 'attacco', 'alzata'])) & (df['Voto'] == '=')) |
                   ((df['Tipo'] == 'muro') & (df['Voto'] == '/'))]
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
    """
    if "Tipo" not in df.columns or "Voto" not in df.columns:
        raise ValueError("Mancano colonne 'Tipo' o 'Voto' nel DataFrame.")
    d = df.copy()
    d["_tipo_lc"] = d["Tipo"].astype(str).str.lower()
    d["_voto"] = d["Voto"].astype(str).str.strip()
    idx_after_rec, idx_counter = [], []
    for i in range(len(d)):
        if d.iloc[i]["_tipo_lc"] != "attacco":
            continue
        after_reception = False
        if i - 1 >= 0 and d.iloc[i - 1]["_tipo_lc"] == "ricezione" and d.iloc[i - 1]["_voto"] in rec_vote:
            after_reception = True
        if not after_reception and i - 2 >= 0:
            if (d.iloc[i - 2]["_tipo_lc"] == "ricezione" and d.iloc[i - 2]["_voto"] in rec_vote
                    and d.iloc[i - 1]["_tipo_lc"] == "alzata"):
                after_reception = True
        (idx_after_rec if after_reception else idx_counter).append(df.index[i])
    return df.loc[idx_after_rec], df.loc[idx_counter]


def separate_free_ball(df):
    """
    Ritorna gli attacchi che avvengono subito dopo una difesa con voto '!'
    (o dopo un'alzata che segue una difesa '!').
    """
    if "Tipo" not in df.columns or "Voto" not in df.columns:
        raise ValueError("Mancano colonne 'Tipo' o 'Voto' nel DataFrame.")
    d = df.copy()
    d["_tipo_lc"] = d["Tipo"].astype(str).str.lower()
    d["_voto"] = d["Voto"].astype(str).str.strip()
    idx_free_ball = []
    for i in range(1, len(d)):
        if (d.iloc[i - 1]["_tipo_lc"] == "difesa" and d.iloc[i - 1]["_voto"] == "!") or \
           (i - 2 >= 0 and d.iloc[i - 2]["_tipo_lc"] == "difesa" and d.iloc[i - 2]["_voto"] == "!"
                and d.iloc[i - 1]["_tipo_lc"] == "alzata"):
            if d.iloc[i]["_tipo_lc"] == "attacco":
                idx_free_ball.append(d.index[i])
    return df.loc[idx_free_ball]


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

        err_mask = (d[voto_col].astype(str).str.strip() == "=") & (d[tipo_col].isin(["battuta", "attacco", "alzata"]))
        errors = int(err_mask.sum())

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

    # ---------- layout ----------
    # mantengo un set ampio di voti per coprire ogni evenienza negli attacchi
    ATT_VOTES = ['-', '#', '+', '/', '=', '!']
    REC_VOTES = ['#', '+', '!', '-', '/', '=']

    # BLOCCO SOPRA (Free Ball non c'è qui)
    TOP_SECTIONS = [
        ("Ricezione", ['Eff', 'Tot'] + REC_VOTES),
        ("Attacco(R tot)", ['Eff', 'Tot'] + ATT_VOTES),
        ("Attacco(R#+)", ['Eff', 'Tot'] + ATT_VOTES),
        ("Attacco(R!)", ['Eff', 'Tot'] + ATT_VOTES),
        ("Attacco(R-)", ['Eff', 'Tot'] + ATT_VOTES),
    ]

    # BLOCCO SOTTO (Free Ball aggiunto qui, dopo Contrattacco)
    BOTTOM_SECTIONS = [
        ("Battuta", ['Eff', 'Tot'] + ATT_VOTES),
        ("Contrattacco", ['Eff', 'Tot'] + ATT_VOTES),
        ("Attacco(Free Ball)", ['Eff', 'Tot'] + ATT_VOTES),
        ("Muro", ['Eff', 'Tot'] + ['#', '+', '!', '/', '=']),
        ("Errori", ['Bat', 'Att', 'Muro', 'Alz']),
    ]

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


def create_player_summary_df(df):
    """
    Crea il tabellino giocatori (colonne MultiIndex per fondamentale):
    Battuta, Ricezione, Attacco(rice tot), Att(R#+), Att(R!), Att(R-),
    Contrattacco, Muro, Errori.
    """
    battuta_eff = calcola_efficienza(df, 'battuta', pos=['#', '+', '/', '!'], neg=['='])
    attacco_eff = calcola_efficienza(df, 'attacco', pos=['#'], neg=['=', '/'])
    muro_eff = calcola_efficienza(df, 'muro', pos=['#', '+'], neg=['=', '/'])
    rice_eff = calcola_efficienza(df, 'ricezione', pos=['#', '+'], neg=['=', '/'])
    errors = find_errors(df)

    # Att(R#+): ricezione positiva
    pos_rec_attacks, _ = separate_attacks_counterattacks(df, rec_vote=["#", "+"])
    pos_rec_att_eff = calcola_efficienza(pos_rec_attacks, 'attacco', pos=['#'], neg=['=', '/'])

    # Att(R-): ricezione negativa
    neg_rec_attacks, _ = separate_attacks_counterattacks(df, rec_vote=["-"])
    neg_rec_att_eff = calcola_efficienza(neg_rec_attacks, 'attacco', pos=['#'], neg=['=', '/'])

    # Att(R!): ricezione esclamativa
    escl_rec_attacks, _ = separate_attacks_counterattacks(df, rec_vote=["!"])
    escl_rec_att_eff = calcola_efficienza(escl_rec_attacks, 'attacco', pos=['#'], neg=['=', '/'])

    # Totale ricezione e contrattacchi
    all_rec_attacks, counterattacks = separate_attacks_counterattacks(df)  # default ["#","+","!","-"]
    all_rec_att_eff = calcola_efficienza(all_rec_attacks, 'attacco', pos=['#'], neg=['=', '/'])
    counterattacks_eff = calcola_efficienza(counterattacks, 'attacco', pos=['#'], neg=['=', '/'])

    # Unione elenco giocatori su tutte le tabelle disponibili
    all_players = set(battuta_eff['Giocatore']).union(
        rice_eff['Giocatore'],
        all_rec_att_eff['Giocatore'],
        pos_rec_att_eff['Giocatore'],
        neg_rec_att_eff['Giocatore'],
        escl_rec_att_eff['Giocatore'],
        counterattacks_eff['Giocatore'],
        muro_eff['Giocatore'],
        errors['Giocatore']
    )

    # MultiIndex: con rinomina delle tre colonne richieste
    sections = [
        'Battuta', 'Ricezione', 'Attacco(rice tot)',
        'Att(R#+)', 'Att(R!)', 'Att(R-)',
        'Contrattacco', 'Muro', 'Errori'
    ]
    multiindex_cols = pd.MultiIndex.from_product([sections, []])

    # DataFrame vuoto
    player_summary_df = pd.DataFrame(index=sorted(list(all_players)), columns=multiindex_cols, dtype=object)

    # Helper per popolare (aggiunge dinamicamente le sotto-colonne)
    def populate_stats(df_source, col_level1, df_dest):
        if df_source is not None and not df_source.empty:
            for _, row in df_source.iterrows():
                player = row['Giocatore']
                existing_level2 = df_dest.columns.get_level_values(level=1)[
                    df_dest.columns.get_level_values(level=0) == col_level1
                ].tolist()
                new_level2 = [col for col in row.drop('Giocatore').index.tolist() if col not in existing_level2]
                if new_level2:
                    new_cols = pd.MultiIndex.from_product([[col_level1], new_level2])
                    df_dest = df_dest.reindex(columns=df_dest.columns.tolist() + new_cols.tolist())
                for col in row.drop('Giocatore').index:
                    df_dest.loc[player, (col_level1, col)] = row[col]
        return df_dest

    # Popola tutte le sezioni
    player_summary_df = populate_stats(battuta_eff, 'Battuta', player_summary_df)
    player_summary_df = populate_stats(rice_eff, 'Ricezione', player_summary_df)
    player_summary_df = populate_stats(all_rec_att_eff, 'Attacco(rice tot)', player_summary_df)
    player_summary_df = populate_stats(pos_rec_att_eff, 'Att(R#+)', player_summary_df)
    player_summary_df = populate_stats(escl_rec_att_eff, 'Att(R!)', player_summary_df)
    player_summary_df = populate_stats(neg_rec_att_eff, 'Att(R-)', player_summary_df)
    player_summary_df = populate_stats(counterattacks_eff, 'Contrattacco', player_summary_df)
    player_summary_df = populate_stats(muro_eff, 'Muro', player_summary_df)
    player_summary_df = populate_stats(errors, 'Errori', player_summary_df)

    # Formattazione: percentuali su 'Eff'/'Pos', conteggi come int, vuoti -> '-'
    for col_level1 in sections:
        if col_level1 in player_summary_df.columns.get_level_values(level=0):
            for col_level2 in player_summary_df[col_level1].columns:
                if col_level2 in ['Eff', 'Pos']:
                    numeric_col = pd.to_numeric(player_summary_df[(col_level1, col_level2)], errors='coerce')
                    player_summary_df[(col_level1, col_level2)] = numeric_col.apply(
                        lambda x: '{:.0%}'.format(x) if pd.notna(x) else '-'
                    )
                else:
                    player_summary_df[(col_level1, col_level2)] = (
                        pd.to_numeric(player_summary_df[(col_level1, col_level2)], errors='coerce')
                        .fillna(-1).astype(int).replace(-1, '-')
                    )
    return player_summary_df
