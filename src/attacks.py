# src/attacks.py
"""
Funzioni di visualizzazione (grafici matplotlib) estratte da notebooks/tabellino.ipynb:
punti per giocatore, efficienza attacco, trend per set, radar per set.

Nota sul refactor: nel notebook queste funzioni leggevano direttamente le variabili
globali `write_files` e `file_path1` (definite nella cella di configurazione della
partita) per decidere se/dove salvare i PNG. Un modulo importato non vede i globali
del notebook chiamante, quindi qui `write_files` e `save_dir` sono parametri espliciti:
il notebook li passa esplicitamente dai propri globali ad ogni chiamata.
"""
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from src.efficiency import (
    SRV_POS,
    SRV_NEG,
    eff_scalar,
    calcola_efficienza,
    separate_attacks_counterattacks,
    eff_from_calcola,
    compute_set_metrics,
)


# ============================================================
# Punti per giocatore (Muro / Battuta / Attacco / Totale)
# ============================================================

def norm_tipo(val):
    """Normalize 'Tipo' into one of: 'muro', 'battuta', 'attacco', or 'altro'."""
    s = str(val).strip().lower()
    if "mur" in s:          # muro, murata, ecc.
        return "muro"
    if "batt" in s or "serv" in s:   # battuta / servizio
        return "battuta"
    if "att" in s:          # attacco
        return "attacco"
    return "altro"


def _pick_player_name_row(row, df, prefer_surname=True, player_candidates=None):
    """
    Given a row, choose a displayable player name.
    Priority:
      1) 'Cognome' if present and non-empty (when prefer_surname=True)
      2) first non-empty among `player_candidates`
      3) first object-like column with non-empty value
      4) fallback to the row index (string)
    """
    if player_candidates is None:
        player_candidates = ["Giocatore", "Cognome", "Player", "Atleta", "Autore",
                             "Nome Giocatore", "Nome_giocatore", "Numero"]
    if prefer_surname and "Cognome" in df.columns:
        val = row.get("Cognome")
        if isinstance(val, str) and val.strip():
            return val.strip()

    for cand in player_candidates:
        if cand in df.columns:
            vv = row.get(cand)
            if isinstance(vv, str) and vv.strip():
                return vv.strip()

    # Fallback: first object-like column
    for c in df.columns:
        if getattr(df[c], "dtype", None) == object:
            vv = row.get(c)
            if isinstance(vv, str) and vv.strip():
                return vv.strip()

    return str(row.name)


def compute_points_table(df, tipo_col="Tipo", voto_col="Voto",
                         prefer_surname=True, player_candidates=None):
    """
    Build an aggregated table with points per player:
    columns = ['Punti Muro', 'Punti Battuta', 'Punti Attacco', 'Totale']
    index   = player name
    Logic: a point is counted when Voto == '#' and Tipo ∈ {muro, battuta, attacco}.
    The function does NOT modify the input df in place.
    """
    if tipo_col not in df.columns or voto_col not in df.columns:
        raise ValueError("DataFrame must contain columns '{}' and '{}'.".format(tipo_col, voto_col))

    # Player names (do not write back to df)
    players = df.apply(_pick_player_name_row, axis=1, args=(df, prefer_surname, player_candidates))

    # Normalize tipo & voto
    tipo_norm = df[tipo_col].map(norm_tipo)
    voto_str  = df[voto_col].astype(str).str.strip()

    # Keep scoring events
    mask = (voto_str == "#") & (tipo_norm.isin(["muro", "battuta", "attacco"]))
    points = pd.DataFrame({"Giocatore": players[mask], "Tipo": tipo_norm[mask]})

    if points.empty:
        # Return empty, well-formed table
        empty = pd.DataFrame(columns=["Punti Muro", "Punti Battuta", "Punti Attacco", "Totale"])
        empty.index.name = "Giocatore"
        return empty

    # Aggregate counts
    counts = points.value_counts(["Giocatore", "Tipo"]).unstack(fill_value=0)

    # Ensure all three fundamentals exist
    for col in ["muro", "battuta", "attacco"]:
        if col not in counts.columns:
            counts[col] = 0

    # Rename & compute total
    counts = counts[["muro", "battuta", "attacco"]]
    counts.columns = ["Punti Muro", "Punti Battuta", "Punti Attacco"]
    counts["Totale"] = counts.sum(axis=1)

    # Keep only players with >= 1 point, sort desc
    counts = counts[counts["Totale"] > 0].sort_values("Totale", ascending=False)
    counts.index.name = "Giocatore"
    return counts


def plot_points_grouped(counts, sq, title="Points by Player — Block, Serve, Attack, Total",
                        rotate_labels=45, save_dir=None, write_files=True):
    """
    Plot grouped bars: for each player (index), four bars:
    [Punti Muro, Punti Battuta, Punti Attacco, Totale].
    Uses matplotlib only (no seaborn), no explicit colors.
    Se `write_files` è True e `save_dir` è indicato, salva il PNG in quella cartella.
    Returns the matplotlib Axes.
    """
    if counts.empty:
        raise ValueError("The provided 'counts' table is empty — nothing to plot.")

    players = counts.index.tolist()
    muro_vals    = counts["Punti Muro"].to_numpy()
    batt_vals    = counts["Punti Battuta"].to_numpy()
    att_vals     = counts["Punti Attacco"].to_numpy()
    tot_vals     = counts["Totale"].to_numpy()

    x = np.arange(len(players))
    width = 0.2
    fig_w = max(8, len(players) * 0.8)

    fig = plt.figure(figsize=(fig_w, 5))
    ax = plt.subplot(111)

    ax.bar(x - 1.5*width, muro_vals, width, label="Muro")
    ax.bar(x - 0.5*width, batt_vals, width, label="Battuta")
    ax.bar(x + 0.5*width, att_vals,  width, label="Attacco")
    ax.bar(x + 1.5*width, tot_vals,  width, label="Totali")

    ax.set_title(title)
    ax.set_ylabel("Punti")
    ax.set_xticks(x)
    ax.set_xticklabels(players, rotation=rotate_labels, ha="right")
    ax.legend()

    # Numeric labels on bars
    def _add_labels(xpos, values):
        for i, v in enumerate(values):
            ax.text(x[i] + xpos, v + 0.05, str(int(v)), ha="center", va="bottom", fontsize=8)

    _add_labels(-1.5*width, muro_vals)
    _add_labels(-0.5*width, batt_vals)
    _add_labels(+0.5*width, att_vals)
    _add_labels(+1.5*width, tot_vals)

    plt.tight_layout()
    if write_files and save_dir:
      ax.figure.savefig(save_dir + f"/[{sq}] punti_giocatori.png", dpi=300, bbox_inches="tight", pad_inches=0.1, facecolor="white")

    return ax


# ============================================================
# Efficienza attacco: Totale • Dopo ricezione • Contrattacco
# ============================================================

def compute_attack_eff_breakdown(df, tipo_col="Tipo", player_col=None,
                                 pos=("#",), neg=("/", "="), rec_vote=None):
    """
    Per-player table con:
      - Eff_Tot%, N_Tot  (tutti gli attacchi)
      - Eff_SO%,  N_SO   (attacchi dopo ricezione)
      - Eff_Ctr%, N_Ctr  (contrattacchi)
    """
    # player id
    if player_col is None:
        for cand in ("Giocatore", "Cognome", "Numero"):
            if cand in df.columns:
                player_col = cand
                break
        if player_col is None:
            obj_cols = [c for c in df.columns if getattr(df[c], "dtype", None) == object]
            player_col = obj_cols[0] if obj_cols else df.columns[0]

    # Lavoro su una copia con Tipo lower-case: tutto il resto resta invariato
    d = df.copy()
    d[tipo_col] = d[tipo_col].astype(str).str.strip().str.lower()

    # Split SO / CTR direttamente su d (che è già lowercase)
    if rec_vote is None:
        so_d, ctr_d = separate_attacks_counterattacks(d)
    else:
        so_d, ctr_d = separate_attacks_counterattacks(d, rec_vote=list(rec_vote))

    # Tieni solo ATTACCO in minuscolo
    att_all = d.loc[d[tipo_col] == "attacco"]
    so_d    = so_d.loc[so_d[tipo_col] == "attacco"]
    ctr_d   = ctr_d.loc[ctr_d[tipo_col] == "attacco"]

    if att_all.empty and so_d.empty and ctr_d.empty:
        out = pd.DataFrame(columns=["Eff_Tot%", "N_Tot", "Eff_SO%", "N_SO", "Eff_Ctr%", "N_Ctr"])
        out.index.name = "Giocatore"
        return out

    recs = {}

    # Totale
    for player, g in att_all.groupby(att_all[player_col].astype(str).str.strip(), sort=False):
        recs[player] = {
            "N_Tot": int(len(g)),
            "Eff_Tot%": round(eff_from_calcola(g, "attacco", pos, neg), 1)
        }

    # Dopo ricezione / Contrattacco
    for part_df, n_key, e_key in ((so_d, "N_SO", "Eff_SO%"), (ctr_d, "N_Ctr", "Eff_Ctr%")):
        for player, g in part_df.groupby(part_df[player_col].astype(str).str.strip(), sort=False):
            if player not in recs:
                recs[player] = {"N_Tot": 0, "Eff_Tot%": np.nan}
            recs[player][n_key] = int(len(g))
            recs[player][e_key] = round(eff_from_calcola(g, "attacco", pos, neg), 1)

    rows = []
    for p, dct in recs.items():
        rows.append({
            "Giocatore": p,
            "Eff_Tot%": dct.get("Eff_Tot%", np.nan), "N_Tot": dct.get("N_Tot", 0),
            "Eff_SO%":  dct.get("Eff_SO%",  np.nan), "N_SO":  dct.get("N_SO",  0),
            "Eff_Ctr%": dct.get("Eff_Ctr%", np.nan), "N_Ctr": dct.get("N_Ctr", 0),
        })

    tbl = (pd.DataFrame(rows)
           .set_index("Giocatore")
           .sort_values(["Eff_Tot%", "N_Tot"], ascending=[False, False]))
    return tbl


def plot_attack_eff_breakdown_bars(
    tbl,
    sq="",
    title="Efficienza attacco — Totale • Dopo ricezione • Contrattacco",
    rotate_labels=45,
    bar_width=0.36,
    gap=0.01,
    min_attacks=3,
    zero_eps=1e-9,
    group_gap=.50,  # 0 = gruppi contigui; 0.5 = +50% di spazio tra gruppi
    save_dir=None,
    write_files=True,
):
    """
    Mostra solo i giocatori con N_Tot >= min_attacks.
    Tre barre per giocatore: Totale, Dopo ricezione, Contrattacco.
    Etichette:
      - se eff != 0:   percentuale FUORI + "(n)" DENTRO
      - se eff == 0:   percentuale FUORI e SOTTO di essa "(n)"
    Ordinamento per N_Tot decrescente.
    `group_gap` aumenta lo spazio ORIZZONTALE tra i gruppi (giocatori).
    """
    if tbl.empty:
        raise ValueError("Tabella vuota: nessun attacco rilevato.")

    # Filtra e ordina
    tbl = tbl[tbl["N_Tot"] >= min_attacks]
    if tbl.empty:
        raise ValueError(f"Nessun giocatore con almeno {min_attacks} attacchi totali.")
    tbl = tbl.sort_values("N_Tot", ascending=False)

    players = tbl.index.tolist()
    eff_tot, n_tot = tbl["Eff_Tot%"].to_numpy(), tbl["N_Tot"].to_numpy()
    eff_so,  n_so  = tbl["Eff_SO%"].to_numpy(),  tbl["N_SO"].to_numpy()
    eff_ctr, n_ctr = tbl["Eff_Ctr%"].to_numpy(), tbl["N_Ctr"].to_numpy()

    # NaN -> 0 per il plotting
    eff_tot = np.nan_to_num(eff_tot, nan=0.0)
    eff_so  = np.nan_to_num(eff_so,  nan=0.0)
    eff_ctr = np.nan_to_num(eff_ctr, nan=0.0)

    # Posizioni dei gruppi con gap extra
    gg = max(0.0, float(group_gap))
    x = np.arange(len(players), dtype=float) * (1.0 + gg)

    # Figura più larga se aumenta il gap tra gruppi
    fig_w = max(10, len(players) * (1.0 + gg))
    fig = plt.figure(figsize=(fig_w, 5))
    ax = plt.subplot(111)

    sep = bar_width + gap
    bars_tot = ax.bar(x - sep, eff_tot, width=bar_width, label="Totale")
    bars_so  = ax.bar(x,       eff_so,  width=bar_width, label="Dopo ricezione")
    bars_ctr = ax.bar(x + sep, eff_ctr, width=bar_width, label="Contrattacco")

    ax.set_title(f"[{sq}] "+title)
    ax.set_ylabel("Efficienza attacco (%)")
    ax.set_xticks(x)
    ax.set_xticklabels(players, rotation=rotate_labels, ha="right")
    ax.legend()

    # ---- Limiti Y dinamici per NON tagliare le etichette ----
    eff_max = float(max(eff_tot.max(), eff_so.max(), eff_ctr.max()))
    eff_min = float(min(eff_tot.min(), eff_so.min(), eff_ctr.min()))
    rng = max(1.0, eff_max - eff_min)
    extra_margin = max(6.0, 0.12 * rng)
    y_min = min(0.0, eff_min) - extra_margin
    y_max = max(0.0, eff_max) + extra_margin
    ax.set_ylim(y_min, y_max)
    pad = max(2.0, 0.035 * (y_max - y_min))  # spazio verticale per la percentuale fuori

    def _inside_text_color(bar):
        r, g, b_col, a = bar.get_facecolor()
        lum = 0.2126*r + 0.7152*g + 0.0722*b_col
        return "black" if lum > 0.6 else "white"

    def _annotate(container, eff_arr, n_arr):
        for i, b in enumerate(container):
            eff = float(eff_arr[i])
            n   = int(n_arr[i])
            xc  = b.get_x() + b.get_width()/2.0

            # Percentuale FUORI (sopra se >=0, sotto se <0)
            is_pos = eff >= 0
            y_out = eff + (pad if is_pos else -pad)
            va_out = "bottom" if is_pos else "top"

            if abs(eff) < zero_eps:
                # Caso 0%: percentuale + riga sotto con (n)
                ax.text(xc, y_out, f"{int(round(eff))}%", ha="center", va=va_out, fontsize=9)
                line_gap = max(1.5, 0.025 * (y_max - y_min))
                ax.text(xc, y_out - line_gap, f"({n})", ha="center", va="top", fontsize=9)
            else:
                # Caso ≠ 0%: percentuale FUORI + (n) DENTRO la barra
                ax.text(xc, y_out, f"{int(round(eff))}%", ha="center", va=va_out, fontsize=9)
                y_in = eff / 2.0
                ax.text(xc, y_in, f"({n})", ha="center", va="center", fontsize=9, color=_inside_text_color(b))

    _annotate(bars_tot, eff_tot, n_tot)
    _annotate(bars_so,  eff_so,  n_so)
    _annotate(bars_ctr, eff_ctr, n_ctr)

    plt.tight_layout()
    if write_files and save_dir:
      ax.figure.savefig(save_dir + f"/[{sq}] eff_attacco_giocatori.png", dpi=300, bbox_inches="tight", pad_inches=0.1, facecolor="white")
    return ax


def create_attack_eff_plots(df, sq="", save_dir=None, write_files=True):
  eff_tbl = compute_attack_eff_breakdown(
      df,
      pos=("#",),          # positive votes
      neg=("/", "="),      # negative votes
      )
  _ = plot_attack_eff_breakdown_bars(
      eff_tbl,
      sq=sq,
      title="Efficienza attacco — Totale • Dopo ricezione • Contrattacco",
      bar_width=0.3,       # barre strette come richiesto
      save_dir=save_dir,
      write_files=write_files,
      )


# ============================================================
# Trend per set (barre) e radar
# ============================================================

def _align_zero_two_axes(ax, ax2, right_data_max, pad_ratio=0.25, allow_negative_right=True):
    """
    Allinea la posizione dello zero dei due assi y (ax a sinistra, ax2 a destra).
    - right_data_max: massimo dato 'naturale' dell'asse destro (es. max errori)
    - pad_ratio: padding percentuale sopra (es. 0.25 = +25%)
    - allow_negative_right: se True consente min y2 < 0 per allineare esattamente lo zero;
                            se False, forza y2_min=0 e porta lo zero in basso su ax.
    """
    y_min, y_max = ax.get_ylim()
    if y_max <= y_min + 1e-9:
        return  # range degenerato, niente da fare

    # frazione (0 in [y_min, y_max])
    frac = (0.0 - y_min) / (y_max - y_min)

    # top dell'asse destro con un po' di aria
    y2_top = float(right_data_max) * (1.0 + pad_ratio)
    if y2_top <= 0:
        y2_top = 1.0  # fallback

    if allow_negative_right:
        # scegli y2_min così che 0 cada alla stessa frazione
        denom = max(1e-9, (1.0 - frac))
        y2_min = -frac * y2_top / denom
    else:
        # niente valori negativi sull'asse destro: y2_min = 0
        y2_min = 0.0
        # per coerenza porta lo zero in basso anche a sinistra
        if y_min < 0:
            ax.set_ylim(0.0, y_max)
            y_min, y_max = ax.get_ylim()
            frac = 0.0  # ora zero è in basso

    ax2.set_ylim(y2_min, y2_top)

    # linea dello zero (aiuta a verificare l'allineamento)
    ax.axhline(0, color="0.5", lw=1, alpha=0.6)


def plot_set_efficiency_groups(
    tbl,
    sq="",
    title="Efficienze per Set (+ Errori)",
    rotate_labels=0,
    bar_width=0.22,
    intra_gap=0.06,
    group_gap=1.2,
    force_sets=True,
    num_sets=5,
    annotate=True,
    include_total_group=True,
    # --- per costruire il gruppo "Tot" ---
    df_all=None,
    set_col="Numero Set",
    tipo_col="Tipo",
    voto_col="Voto",
    rec_vote=None,
    pos_srv=SRV_POS,             neg_srv=SRV_NEG,
    pos_rec=("#", "+"),         neg_rec=("/", "="),
    pos_att=("#",),             neg_att=("/", "="),
    pos_blk=("#", "+"),         neg_blk=("/", "="),
    # --- riquadro rosso intorno al gruppo Tot ---
    highlight_total_group=True,
    highlight_color="red",
    highlight_pad=0.06,  # frazione: 0.06 = 6% di padding lato e top/bottom
    save_dir=None,
    write_files=True,
):
    from matplotlib.patches import Rectangle

    def _eff_or_nan(sub_df, tipo, pos, neg):
        ss = sub_df[sub_df[tipo_col] == tipo]
        if ss.empty:
            return float("nan")
        return eff_scalar(calcola_efficienza(ss, tipo=tipo, pos=list(pos), neg=list(neg), total_efficiency=True))

    if tbl.empty:
        raise ValueError("Tabella vuota: nessun set disponibile.")

    metrics_pct = ["Battuta%", "Ricezione%", "Attacco SO%", "Contrattacco%", "Muro%"]
    metric_errs = "Errori"
    missing = [c for c in metrics_pct + [metric_errs] if c not in tbl.columns]
    if missing:
        raise ValueError(f"Mancano colonne in tbl: {missing}")

    # Forza set 1..num_sets e marca quelli reali
    tbl_pad = tbl.copy()
    try:
        tbl_pad.index = pd.to_numeric(tbl_pad.index, errors="coerce")
    except Exception:
        pass

    if force_sets:
        desired_sets = list(range(1, num_sets + 1))
        existing = set(tbl_pad.index.tolist())
        tbl_pad = tbl_pad.reindex(desired_sets)
        annot_mask = [s in existing for s in desired_sets]
    else:
        desired_sets = list(tbl_pad.index)
        annot_mask = [True] * len(desired_sets)

    plot_pct = tbl_pad[metrics_pct].copy().fillna(0.0)
    plot_err = tbl_pad[metric_errs].copy().fillna(0.0)
    labels = list(desired_sets)

    # Gruppo Tot
    if include_total_group:
        if df_all is None:
            raise ValueError("Per aggiungere il gruppo 'Tot' serve df_all (dati grezzi).")
        real_sets = [s for s, ex in zip(desired_sets, annot_mask) if ex]
        df_sub = df_all[df_all[set_col].isin(real_sets)].copy()
        df_sub[tipo_col] = df_sub[tipo_col].astype(str).str.strip().str.lower()

        so_d, ctr_d = separate_attacks_counterattacks(df_sub) if rec_vote is None \
                      else separate_attacks_counterattacks(df_sub, rec_vote=list(rec_vote))
        so_d  = so_d [so_d [tipo_col] == "attacco"]
        ctr_d = ctr_d[ctr_d[tipo_col] == "attacco"]

        totals = {
            "Battuta%":      _eff_or_nan(df_sub, "battuta",  pos_srv, neg_srv),
            "Ricezione%":    _eff_or_nan(df_sub, "ricezione",pos_rec, neg_rec),
            "Attacco SO%":   (eff_scalar(calcola_efficienza(so_d,  tipo="attacco", pos=list(pos_att), neg=list(neg_att), total_efficiency=True)) if not so_d.empty else float("nan")),
            "Contrattacco%": (eff_scalar(calcola_efficienza(ctr_d, tipo="attacco", pos=list(pos_att), neg=list(neg_att), total_efficiency=True)) if not ctr_d.empty else float("nan")),
            "Muro%":         _eff_or_nan(df_sub, "muro",     pos_blk, neg_blk),
        }
        err_mask = (df_sub[voto_col].astype(str).str.strip() == "=") & (df_sub[tipo_col].isin(["battuta","attacco","alzata"]))
        tot_err = int(err_mask.sum())

        plot_pct.loc["Tot"] = [totals[k] if np.isfinite(totals[k]) else 0.0 for k in metrics_pct]
        plot_err.loc["Tot"] = tot_err
        labels.append("Tot")
        annot_mask.append(True)

    # Posizioni X
    n_groups = len(labels)
    x = np.arange(n_groups, dtype=float) * (1.0 + group_gap)
    m = len(metrics_pct) + 1
    offsets = [(i - (m - 1) / 2) * (bar_width + intra_gap) for i in range(m)]

    fig = plt.figure(figsize=(max(10, n_groups * (1.2 + group_gap)), 5))
    ax = plt.subplot(111)
    cmap = plt.get_cmap("tab10")
    colors = [cmap(i) for i in range(6)]

    # Barre % (asse sinistro)
    containers_pct = []
    for i, col in enumerate(metrics_pct):
        cont = ax.bar(x + offsets[i], plot_pct[col].to_numpy(), width=bar_width, label=col, color=colors[i])
        containers_pct.append(cont)

    # Limiti Y sinistra
    eff_vals = plot_pct.to_numpy().ravel()
    eff_max = float(np.nanmax(eff_vals)) if eff_vals.size else 0.0
    eff_min = float(np.nanmin(eff_vals)) if eff_vals.size else 0.0
    rng = max(1.0, eff_max - eff_min)
    extra = max(6.0, 0.12 * rng)
    y_min = min(0.0, eff_min) - extra
    y_max = max(0.0, eff_max) + extra
    ax.set_ylim(y_min, y_max)

    # Errori (asse destro) + allineamento zero
    ax2 = ax.twinx()
    cont_err = ax2.bar(x + offsets[-1], plot_err.to_numpy(), width=bar_width, label="Errori", color=colors[5])
    err_max = float(plot_err.max()) if len(plot_err) else 1.0
    _align_zero_two_axes(ax, ax2, right_data_max=err_max, pad_ratio=0.25, allow_negative_right=True)

    # === Riquadro rosso che incornicia il gruppo "Tot" ===
    if include_total_group and highlight_total_group:
        last = n_groups - 1
        # estensione orizzontale del gruppo Tot (barre di % e barre "Errori")
        group_left  = min(x[last] + off - bar_width/2 for off in offsets)
        group_right = max(x[last] + off + bar_width/2 for off in offsets)
        # padding orizzontale
        pad_lr = highlight_pad * (group_right - group_left)
        group_left  -= pad_lr
        group_right += pad_lr
        # estensione verticale (usa l'intero range dell'asse sinistro con un po' di padding)
        y0, y1 = ax.get_ylim()
        pad_tb = highlight_pad * (y1 - y0)
        y0 += pad_tb
        y1 -= pad_tb
        rect = Rectangle((group_left, y0), group_right - group_left, y1 - y0,
                         fill=False, ec=highlight_color, lw=2.2, zorder=6, clip_on=False)
        ax.add_patch(rect)

    # Assi / legenda
    ax.set_title(f"[{sq}] "+title)
    ax.set_xlabel("Numero Set")
    ax.set_ylabel("Efficienza (%)")
    ax2.set_ylabel("Errori (conteggio)")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=rotate_labels, ha="center")

    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="best", ncol=3)

    # Annotazioni (solo set reali + Tot)
    if annotate:
        pad_pct = max(2.0, 0.035 * (y_max - y_min))
        for i, col in enumerate(metrics_pct):
            vals = plot_pct[col].to_numpy()
            for j, b in enumerate(containers_pct[i]):
                if j >= len(annot_mask) or not annot_mask[j]:
                    continue
                v = float(vals[j])
                y_out = v + (pad_pct if v >= 0 else -pad_pct)
                va = "bottom" if v >= 0 else "top"
                ax.text(b.get_x() + b.get_width()/2, y_out, f"{int(round(v))}%", ha="center", va=va, fontsize=9)

        y2_min, y2_max = ax2.get_ylim()
        pad_err = max(0.5, 0.03 * (y2_max - y2_min))
        for j, b in enumerate(cont_err):
            if j >= len(annot_mask) or not annot_mask[j]:
                continue
            v = int(plot_err.iloc[j])
            ax2.text(b.get_x() + b.get_width()/2, b.get_height() + pad_err, str(v), ha="center", va="bottom", fontsize=9)

    plt.tight_layout()

    fig = ax.get_figure()
    if write_files and save_dir:
      fig.savefig(save_dir + f"/[{sq}] bar_eff_per_set.png", dpi=300, bbox_inches="tight", pad_inches=0.1, facecolor="white")
    return ax, ax2


def create_metrics_plot(df, sq="", save_dir=None, write_files=True):
  tbl_set = compute_set_metrics(df)
  _ = plot_set_efficiency_groups(
      tbl_set,
      sq=sq,
      df_all=df,                 # per calcolare il gruppo "Tot"
      include_total_group=True,  # gruppo totale attivo
      highlight_total_group=True,# riquadro rosso intorno al gruppo Tot
      save_dir=save_dir,
      write_files=write_files,
      )


def plot_set_radar(
    df_raw,
    df_raw_b=None,         # opzionale: secondo raw dataframe
    tbl_b=None,            # opzionale: seconda tabella (se None, riusa `tbl`)
    sets=None,
    title="Radar per Set — Battuta / Ricezione / Attacco SO / Contrattacco / Muro",
    metrics=None,
    rmin=None, rmax=None,
    annotate=True,
    start_with_green=True,
    label_offset=0.08,
    show_errors="legend",  # "legend" | "annotate" | "spoke"
    errors_col="Errori",
    error_spoke_label="Errori (norm.)",
    errors_panel_side="right",
    errors_panel_pad=0.08,
    labels=("Aggregato A", "Aggregato B"),
    colors=None,
    save_dir=None,
    write_files=True,
    save_name=None,
):
    tbl = compute_set_metrics(df_raw)
    if df_raw_b is not None:
        tbl_b = compute_set_metrics(df_raw_b)

    # --- check input base ---
    if tbl is None or getattr(tbl, "empty", True):
        raise ValueError("`tbl` è vuoto: nessun set disponibile per il primo dataset.")
    if df_raw is None or len(df_raw) == 0:
        raise ValueError("`df_raw` è richiesto e deve contenere 'Numero Set','Tipo','Voto'.")
    if df_raw_b is not None and tbl_b is None:
        tbl_b = tbl

    if metrics is None:
        metrics = ["Battuta%", "Ricezione%", "Attacco SO%", "Contrattacco%", "Muro%"]

    # --- colonne disponibili (case-insensitive) su tbl A ---
    cols_lower_map = {str(c).strip().lower(): c for c in tbl.columns}
    sel_cols = []
    for m in metrics:
        key = str(m).strip().lower()
        if key not in cols_lower_map:
            raise ValueError(f"Colonna '{m}' non trovata in `tbl`. Disponibili: {list(tbl.columns)}")
        sel_cols.append(cols_lower_map[key])
    if errors_col not in tbl.columns:
        raise ValueError(f"Colonna errori '{errors_col}' non trovata in `tbl`.")

    # --- helper: estrai scalare float robusto ---
    def _to_float_scalar(x, context=""):
        try:
            return float(x)
        except Exception:
            pass

        if isinstance(x, (pd.Series, pd.Index)):
            xx = pd.to_numeric(x, errors="coerce").dropna()
            if len(xx) > 0:
                return float(xx.iloc[0])

        if isinstance(x, pd.DataFrame):
            xx = pd.to_numeric(x.stack(), errors="coerce").dropna()
            if len(xx) > 0:
                return float(xx.iloc[0])

        if isinstance(x, dict):
            for v in x.values():
                try:
                    return float(v)
                except Exception:
                    continue

        try:
            flat = np.ravel(np.asarray(x, dtype=object))
            for v in flat:
                try:
                    return float(v)
                except Exception:
                    continue
        except Exception:
            pass

        raise ValueError(f"Valore non numerico per {context}: tipo={type(x).__name__}, repr={repr(x)[:120]}")

    # --- helper: porta in percentuale se necessario ---
    def _to_percent(val):
        try:
            v = float(val)
        except Exception:
            return val
        # se il valore è verosimilmente fra -1 e 1, interpretalo come frazione -> percento
        if np.isfinite(v) and abs(v) <= 1.0:
            return v * 100.0
        return v

    # --- wrapper efficienza coerente con i segni ---
    set_col, tipo_col, voto_col = "Numero Set", "Tipo", "Voto"

    def _eff_or_nan(sub_df, tipo, pos, neg, context=""):
        if sub_df.empty:
            return float("nan")
        ss = sub_df[sub_df[tipo_col] == tipo]
        if ss.empty:
            return float("nan")
        val = calcola_efficienza(
            ss, tipo=tipo, pos=list(pos), neg=list(neg), total_efficiency=True
        )
        val = _to_float_scalar(val, context=context)
        return _to_percent(val)

    def _prepare_dataset(_df_raw, _tbl):
        data_tbl = _tbl.copy()
        try:
            data_tbl.index = pd.to_numeric(data_tbl.index, errors="coerce")
        except Exception:
            pass

        if sets is not None:
            keep = [s for s in sets if s in data_tbl.index]
            if not keep:
                raise ValueError("Nessuno dei set richiesti è presente nella tabella fornita.")
            data_tbl = data_tbl.loc[keep]

        chosen = list(data_tbl.index.astype(int)) if len(data_tbl.index) else []
        if not chosen:
            try:
                chosen = sorted(
                    pd.to_numeric(_df_raw["Numero Set"], errors="coerce").dropna().astype(int).unique().tolist()
                )
            except Exception:
                pass

        d = _df_raw.copy()
        for col in (set_col, tipo_col, voto_col):
            if col not in d.columns:
                raise ValueError("Il df grezzo deve contenere le colonne 'Numero Set','Tipo','Voto'.")
        d[set_col] = pd.to_numeric(d[set_col], errors="coerce")
        if chosen:
            d = d[d[set_col].isin(chosen)]
        d[tipo_col] = d[tipo_col].astype(str).str.strip().str.lower()
        d[voto_col] = d[voto_col].astype(str).str.strip()
        return d, chosen

    # segni (attenzione alle tuple a 1 elemento: serve la virgola)
    pos_srv, neg_srv = SRV_POS, SRV_NEG
    pos_rec, neg_rec = ("#", "+"), ("/", "=")
    pos_att, neg_att = ("#",), ("/", "=")
    pos_blk, neg_blk = ("#", "+"), ("/", "=")

    def _aggregate(_df_raw, _tbl, ds_name="A"):
        d, chosen_sets = _prepare_dataset(_df_raw, _tbl)

        # split SO / Contrattacco
        try:
            so_d, ctr_d = separate_attacks_counterattacks(d)
        except TypeError:
            so_d, ctr_d = separate_attacks_counterattacks(d, rec_vote=None)

        # normalizza anche qui il campo Tipo (può arrivare con maiuscole)
        if so_d is None:
            so_d = pd.DataFrame(columns=d.columns)
        if ctr_d is None:
            ctr_d = pd.DataFrame(columns=d.columns)
        if len(so_d) > 0 and "Tipo" in so_d.columns:
            so_d["Tipo"] = so_d["Tipo"].astype(str).str.strip().str.lower()
        if len(ctr_d) > 0 and "Tipo" in ctr_d.columns:
            ctr_d["Tipo"] = ctr_d["Tipo"].astype(str).str.strip().str.lower()

        so_d  = so_d [so_d ["Tipo"] == "attacco"] if len(so_d)  else so_d
        ctr_d = ctr_d[ctr_d["Tipo"] == "attacco"] if len(ctr_d) else ctr_d

        vals_map = {
            "Battuta%":      _eff_or_nan(d,    "battuta",   pos_srv, neg_srv, context=f"{ds_name}: Battuta%"),
            "Ricezione%":    _eff_or_nan(d,    "ricezione", pos_rec, neg_rec, context=f"{ds_name}: Ricezione%"),
            "Attacco SO%":   (_to_percent(_to_float_scalar(
                                    calcola_efficienza(so_d,  tipo="attacco", pos=list(pos_att), neg=list(neg_att), total_efficiency=True)
                               , context=f"{ds_name}: Attacco SO%")) if not so_d.empty else float("nan")),
            "Contrattacco%": (_to_percent(_to_float_scalar(
                                    calcola_efficienza(ctr_d, tipo="attacco", pos=list(pos_att), neg=list(neg_att), total_efficiency=True)
                               , context=f"{ds_name}: Contrattacco%")) if not ctr_d.empty else float("nan")),
            "Muro%":         _eff_or_nan(d,    "muro",      pos_blk, neg_blk, context=f"{ds_name}: Muro%"),
        }

        vals = np.array([vals_map.get(m, np.nan) for m in metrics], dtype=float)
        vals = np.nan_to_num(vals, nan=0.0)

        err_mask = (d[voto_col] == "=") & (d[tipo_col].isin(["battuta","attacco","alzata"]))
        err_total = int(err_mask.sum())
        return vals, err_total, chosen_sets

    # --- aggregazioni ---
    vals_a, err_a, sets_a = _aggregate(df_raw, tbl, ds_name="A")
    has_b = (df_raw_b is not None)
    if has_b:
        vals_b, err_b, sets_b = _aggregate(df_raw_b, tbl_b, ds_name="B")
    else:
        vals_b = err_b = sets_b = None

    # --- angoli & labels ---
    use_spoke = (show_errors == "spoke")
    M = len(metrics) + (1 if use_spoke else 0)
    theta = np.linspace(0, 2*np.pi, M, endpoint=False)
    theta_closed = np.concatenate([theta, theta[:1]])
    labels_ang = list(metrics) + ([error_spoke_label] if use_spoke else [])

    # --- limiti radiali condivisi ---
    def _vals_for_limits(v, err_tot):
        if not use_spoke:
            return v
        e_norm = np.array([100.0 if err_tot > 0 else 0.0], dtype=float)
        return np.concatenate([v, e_norm])

    vals_for_limits = _vals_for_limits(vals_a, err_a)
    if has_b:
        vals_for_limits = np.concatenate([vals_for_limits, _vals_for_limits(vals_b, err_b)])

    finite_vals = vals_for_limits[np.isfinite(vals_for_limits)]
    if finite_vals.size == 0:
        finite_vals = np.array([0.0])
    dmin, dmax = float(np.nanmin(finite_vals)), float(np.nanmax(finite_vals))
    if rmin is None or rmax is None:
        span = max(1.0, dmax - dmin)
        pad  = max(5.0, 0.12 * span)
        if rmin is None: rmin = min(0.0, dmin) - pad
        if rmax is None: rmax = max(0.0, dmax) + pad
        if rmax - rmin < 1e-6:
            rmin, rmax = rmin - 1.0, rmax + 1.0

    # --- colori & label legenda ---
    cmap = plt.get_cmap("tab10")
    if colors is not None:
        color_a, color_b = (colors + (None, None))[:2]
    else:
        color_a = cmap(2) if start_with_green else cmap(0)
        color_b = cmap(3) if start_with_green else cmap(1)

    label_a = labels[0] if labels and len(labels) > 0 else "Aggregato A"
    label_b = labels[1] if labels and len(labels) > 1 else "Aggregato B"

    # --- plot ---
    fig = plt.figure(figsize=(7.8, 6))
    ax = plt.subplot(111, polar=True)
    ax.set_title(title, pad=20)
    ax.set_xticks(theta)
    ax.set_xticklabels(labels_ang)
    ax.set_rlim(rmin, rmax)
    ax.grid(True, linestyle=":")
    if rmin < 0 < rmax:
        ax.plot(np.linspace(0, 2*np.pi, 360), np.zeros(360), linewidth=1.2)

    # Serie A
    vals_plot_a = vals_a.copy()
    if use_spoke:
        e_norm_a = np.array([100.0 if err_a > 0 else 0.0], dtype=float)
        vals_plot_a = np.concatenate([vals_plot_a, e_norm_a])
    vals_closed_a = np.concatenate([vals_plot_a, vals_plot_a[:1]])
    line_a, = ax.plot(theta_closed, vals_closed_a, linewidth=2.0, color=color_a, label=label_a)
    ax.fill(theta_closed, vals_closed_a, alpha=0.15, color=color_a)

    # Serie B (opzionale)
    if has_b:
        vals_plot_b = vals_b.copy()
        if use_spoke:
            e_norm_b = np.array([100.0 if err_b > 0 else 0.0], dtype=float)
            vals_plot_b = np.concatenate([vals_plot_b, e_norm_b])
        vals_closed_b = np.concatenate([vals_plot_b, vals_plot_b[:1]])
        line_b, = ax.plot(theta_closed, vals_closed_b, linewidth=2.0, color=color_b, label=label_b)
        ax.fill(theta_closed, vals_closed_b, alpha=0.15, color=color_b)

    # --- annotazioni ---
    if annotate:
        off = float(label_offset) * (rmax - rmin)
        for t, v in zip(theta[:len(metrics)], vals_a):
            r_text = v + (off if v >= 0 else -off)
            ax.text(t, r_text, f"{int(round(v))}%", ha="center", va="center", fontsize=9, color=color_a)
        if use_spoke:
            t_err = theta[-1]; v_err = vals_plot_a[-1]
            r_text = v_err + (off if v_err >= 0 else -off)
            ax.text(t_err, r_text, f"err {err_a}", ha="center", va="center", fontsize=9, color=color_a)

        if has_b:
            for t, v in zip(theta[:len(metrics)], vals_b):
                r_text = v + (off if v >= 0 else -off)
                ax.text(t, r_text, f"{int(round(v))}%", ha="center", va="center", fontsize=9, color=color_b)
            if use_spoke:
                t_err = theta[-1]; v_err = vals_plot_b[-1]
                r_text = v_err + (off if v_err >= 0 else -off)
                ax.text(t_err, r_text, f"err {err_b}", ha="center", va="center", fontsize=9, color=color_b)

    # --- pannelli laterali (err/set) ---
    x_panel = 1.08 if errors_panel_side == "right" else -0.10
    ha_panel = "left" if errors_panel_side == "right" else "right"
    y0 = 0.92

    sets_str_a = "[" + ",".join(str(int(s)) for s in (sets_a or [])) + "]" if sets_a else "[]"
    ax.text(x_panel, y0, f"err tot {err_a}", transform=ax.transAxes, ha=ha_panel, va="center",
            fontsize=9, color=color_a, bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=color_a, lw=0.8, alpha=0.9))
    ax.text(x_panel, y0 - errors_panel_pad*1.1, f"Set: {sets_str_a}", transform=ax.transAxes, ha=ha_panel, va="center",
            fontsize=9, color=color_a, bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=color_a, lw=0.8, alpha=0.9))

    if has_b:
        sets_str_b = "[" + ",".join(str(int(s)) for s in (sets_b or [])) + "]" if sets_b else "[]"
        ax.text(x_panel, y0 - errors_panel_pad*2.2, f"err tot {err_b}", transform=ax.transAxes, ha=ha_panel, va="center",
                fontsize=9, color=color_b, bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=color_b, lw=0.8, alpha=0.9))
        ax.text(x_panel, y0 - errors_panel_pad*3.3, f"Set: {sets_str_b}", transform=ax.transAxes, ha=ha_panel, va="center",
                fontsize=9, color=color_b, bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=color_b, lw=0.8, alpha=0.9))

    # --- legenda dataset: SEMPRE visibile e interna ---
    handles = [line_a] + ([line_b] if has_b else [])
    labels_leg = [label_a] + ([label_b] if has_b else [])
    ax.legend(handles, labels_leg, loc="upper left", bbox_to_anchor=(0.02, 0.98))

    plt.tight_layout()

    if write_files and save_dir:
        name = save_name or (f"radar_tot_eff_{int(sets[0])}" if sets else "radar_tot_eff")
        ax.figure.savefig(save_dir + f"/{name}.png", dpi=300, bbox_inches="tight", pad_inches=0.1, facecolor="white")

    return ax
