# dashboard/app.py
"""
Dashboard interattiva: confronto KPI andata vs ritorno (ed eventuale playout)
tra le partite della stagione, sia a livello squadra che per giocatore, più un
confronto multi-giocatore per singolo KPI lungo tutta la stagione.

Avvio: streamlit run dashboard/app.py

Barra laterale: solo la stagione (tra quelle attive in config/seasons.csv,
default l'ultima), "Ricarica dati da Drive" e l'interruttore della pagella.
Le partite da considerare e le impostazioni dei grafici (smoothing, esito
ricezione per "Attacco SO", soglia minima attacchi) si scelgono dentro ogni
sezione (richiesta dell'utente, 2026-10-30); la pagella resta sempre su tutta
la stagione registrata. Una partita appena
aggiunta a matches.csv compare da sola (la cache è legata alla data di modifica
dei CSV di config); "Ricarica dati" forza la rilettura degli Excel da Drive
(es. dopo aver corretto uno scout già caricato).

Tab "Partita": la singola partita in esame, interattiva (stessi dati del
report, src.match_report): punti per causa set per set (barre impilate),
KPI noi/loro lungo i set, tutti i KPI di un set, un giocatore set per set,
palleggiatori, tabellino. Solo per le partite con scout .dvw.

Tab "Report partita": il PDF post-partita da mandare ai giocatori (src.match_report,
src.match_report_pdf), con un commento libero dell'allenatore in apertura; solo
per le partite con scout .dvw.

Tab "Classifiche" e "Tabellino": le stesse informazioni dei report PDF
inviati in stagione — le 6 classifiche di classifiche.ipynb (src.rankings,
soglie ed esclusioni salvate per stagione e usate anche dal report partita) e
il tabellino set per set con totale ed efficienza
(src.efficiency.compute_set_tabellino), su una partita o sulle partite
selezionate; l'Excel scaricabile ha la stessa struttura
(src.efficiency.export_set_tabellino_to_xlsx).

Tab "Andamento KPI" e "Pagella" (sezione 4, solo se attivata dalla barra
laterale: è pensata per fine stagione). Andamento KPI, dall'alto: "Ultima
partita rispetto alle precedenti" (src.set_analysis.form_values), poi lungo
la stagione con le impostazioni in un expander (partite, smoothing, soglia
minima attacchi):

"Lungo la stagione, partita per partita" (render_season_lines_section, dal
2026-10-30 unisce le vecchie sezioni "Confronto tra giocatori" e "Confronto
KPI per una singola entità"): tutti i giocatori (e la squadra) su un KPI,
oppure tutti i KPI di un giocatore (o della squadra), una linea per voce
lungo le partite (andata, ritorno, playout), con striscia vinta/persa e
tabella min/mediana/max.

"Confronto per KPI — Squadra / Giocatore": per ogni avversario (in ordine di
giornata di andata) + una posizione a sé per l'eventuale playout: barre
raggruppate andata/ritorno/playout-andata/playout-ritorno, e due linee di
trend sovrapposte (andata, ritorno). Le linee sono lisciate con un kernel
gaussiano e si interrompono dove mancano dati (giocatore non in campo);
barre e/o trend sono attivabili/disattivabili.

KPI "Attacco SO" (side-out), "Attacco FB" e "Contrattacco": efficienza/
totale/punti/errori/murati sui tre sottoinsiemi di attacchi di
src.efficiency.separate_attack_types — SO = immediatamente dopo una
ricezione #, +, ! o - (DEFAULT_REC_VOTE, definizione unica del progetto),
Contrattacco = tutti gli altri. Niente efficienza di muro (Muro%).

Soglia minima attacchi (impostazioni della tab Andamento KPI): per i KPI della famiglia "attacco"
(Attacco*, Attacco SO*, Contrattacco*), se un giocatore in una partita non
raggiunge questo numero di attacchi della stessa famiglia, per quella
partita/KPI risulta senza dati (vedi
src.leg_comparison.apply_min_attacks_threshold) — non un valore a zero.

Sezione 4 — "Pagella giocatore" (src.player_report, in corso — base dati,
non ancora una vera pagella narrativa): una tab per giocatore Decimo
riconosciuto, con presenze/assenze (src.attendance) e i finding di
rendimento più notevoli (streak di partite consecutive sopra/sotto la
mediana stagionale, o cambio di livello nel punto di rottura più marcato
della stagione) su un sottoinsieme curato di KPI — ognuno con un grafico
di evidenza (la serie grezza del KPI lungo la stagione, con la porzione
del finding evidenziata in verde/rosso). Non applica la soglia minima
attacchi della tab Andamento KPI (i
finding sono calcolati sulla serie completa, i grafici devono restare
coerenti con quella stessa serie).
"""
import glob
import io
import itertools
import json
import os
import sys
from collections import Counter

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from docx import Document
from docx.shared import Inches, Pt
from plotly.subplots import make_subplots

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.leg_comparison import (
    ALL_KPIS,
    ATTACCO_FB_KPI_LABELS,
    ATTACCO_SO_KPI_LABELS,
    CONTRATTACCO_KPI_LABELS,
    DEFAULT_REC_VOTE,
    PERCENT_KPIS,
    active_season_matches,
    apply_min_attacks_threshold,
    build_attacco_so_dataset,
    build_comparison_dataset,
    build_match_outcomes_dataset,
    get_x_axis_order,
    load_all_matches,
)
from config.paths import ROOT, build_base_path, load_seasons
from src.dvw import decimo_side, normalize_team_name, parse_dvw, read_dvw_header, team_side
from src.efficiency import (
    SET_TABELLINO_TOTAL,
    compute_set_tabellino,
    export_set_tabellino_to_xlsx,
)
from src.match_report import (
    POINT_SOURCE_COLORS_DARK,
    POINT_SOURCE_COLORS_LIGHT,
    POINT_SOURCES,
    RECEPTION_GRADES,
    SET_KPI_COUNTS,
    SET_KPI_PERCENT,
    SET_KPI_VOLUME,
    TOTAL_LABEL,
    combine_matches,
    load_report_source,
    match_header,
    player_table,
    player_table_from_df,
    point_sources,
    point_sources_by_set,
    set_kpis,
    setter_table,
    side_out_by_reception,
    team_comparison,
)
from src.match_report_pdf import build_match_report_pdf
from src.mvp import (
    FUNDAMENTAL_LABELS, FUNDAMENTALS, match_mvp, mvp_by_match_long, mvp_highlights, scoring_rules, season_mvp,
)
from src.opponent_report_pdf import build_opponent_report_pdf
from src.player_report import build_player_report_base
from src.rankings import (
    build_ace_man,
    build_best_attack,
    build_best_block,
    build_best_rec,
    build_spike_guarantee,
    build_srv_shame,
    calculate_player_scores_by_type,
)
from src.player_season_report import build_player_season_report
from src.setter_distribution import (
    ALL_GRADES,
    ANTENNA_RED,
    CELL_INK,
    COURT_FLOOR,
    COURT_FLOOR_DARK,
    FREE_ZONE,
    FREE_ZONE_DARK,
    NET_COLOR,
    NET_MESH,
    ALPHA,
    CASE_DIRECTIONS,
    COUNTER_MODES,
    DIRECTIONS,
    MIN_PER_CATEGORY,
    MIN_TOTAL,
    RECEPTION_CASES,
    SIDE_OUT_DIRECTIONS,
    SITUATIONS,
    ZONE_GRID,
    attack_rows,
    counter_rows,
    direction_categories,
    distributions_by_setter,
    filter_rows,
    share_color,
    short_player,
    side_out_rows,
    test_reception_cases,
    positive_reception_by_receiver,
    test_setter_situations,
    zone_stats,
)
from src.opponent_summary import (
    CLUTCH_FROM,
    CLUTCH_FROM_TIEBREAK,
    CLUTCH_MAX_DIFF,
    DANGEROUS_SERVE_SHARE,
    MIN_RECEPTIONS,
    MIN_SERVES,
    SWING_INTRO,
    balance_rows,
    best_receiver,
    clutch_target,
    dangerous_server,
    points_by_set_outcome,
    serve_rows,
    set_results,
    situation_text,
    swing_sentences,
    worst_receiver,
)
from src.set_analysis import (
    FORM_KPIS,
    MIN_BASELINE_SETS,
    MIN_FORM_VOLUME,
    MIN_HISTORY,
    SET_PROFILE_KPIS,
    SIGNAL_P,
    ball_type_pe_by_match,
    compare_sets_to_baseline,
    form_values,
    kpi_deviations,
    personal_baseline,
    player_kpi_table,
)
from src.setter_report import (
    ATTACCO_ALZATO_ESCL_KPI_LABELS,
    ATTACCO_ALZATO_FB_KPI_LABELS,
    ATTACCO_ALZATO_POS_KPI_LABELS,
    build_setter_kpi_dataset,
)

# CSV di config la cui modifica deve invalidare la cache (una partita appena
# aggiunta a matches.csv, una terna appena registrata nel check terne).
CONFIG_FILES = ("matches.csv", "player_identities.csv", "players.csv", "seasons.csv")

# ALL_KPIS + i KPI "Attacco SO"/"Attacco FB"/"Contrattacco", con la
# definizione unica di side-out del progetto (attacco dopo una ricezione #, +,
# ! o -, DEFAULT_REC_VOTE; il selettore dell'esito di ricezione è stato tolto
# il 2026-10-30) — lista mostrata in tutti i multiselect della dashboard.
DISPLAY_KPIS = (
    [k for k in ALL_KPIS if k != "Muro%"]  # niente efficienza di muro (indicazione dell'utente, 2026-10-30)
    + list(ATTACCO_SO_KPI_LABELS.values())
    + list(ATTACCO_FB_KPI_LABELS.values())
    + list(CONTRATTACCO_KPI_LABELS.values())
)

# ---------------------------------------------------------------------------
# Palette — hue categoriche validate CVD-safe (vedi skill dataviz / palette.md),
# in due varianti (chiara/scura) perché il tema Streamlit attivo (light/dark)
# non deve mai lasciare testo scuro su sfondo scuro o viceversa: passiamo
# theme=None a st.plotly_chart per avere pieno controllo, e ricalcoliamo i
# colori ad ogni render in base a st.context.theme.type.
# Sezione 1: andata/ritorno restano lo stesso colore sia in barra che in linea
# di trend (il colore segue l'entità "girone"); i playout usano la stessa hue
# con opacità ridotta (encoding secondario) invece di nuovi colori.
# Sezione 2/3: colore per entità/KPI assegnato in ordine fisso sulla lista
# completa delle opzioni disponibili, non su quelle selezionate — così il
# filtro non ridipinge le serie superstiti.
# ---------------------------------------------------------------------------
PALETTE_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
PALETTE_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
ENTITY_DASHES = ["solid", "dash", "dot", "dashdot"]

LEG_LABELS = {"A": "Andata", "R": "Ritorno", "POA": "Playout andata", "POR": "Playout ritorno"}
LEG_ORDER = ["A", "R", "POA", "POR"]

# Palette di stato (vinta/persa) — fissa, non dipende dal tema (come da dataviz
# skill: i colori di stato non sono mai tematizzati né riusati come colori di
# serie), usata dalla striscia risultato sotto i grafici 2/3.
WIN_COLOR = "#0ca30c"
LOSS_COLOR = "#d03b3b"
UNKNOWN_COLOR = "#898781"


def theme_colors():
    """Colori/palette dipendenti dal tema Streamlit attivo (light/dark)."""
    is_dark = (st.context.theme.type or "light") == "dark"
    return {
        "surface": "#1a1a19" if is_dark else "#fcfcfb",
        "grid": "#2c2c2a" if is_dark else "#e1e0d9",
        "ink": "#ffffff" if is_dark else "#0b0b0b",
        "palette": PALETTE_DARK if is_dark else PALETTE_LIGHT,
    }


def hex_to_rgba(hex_color, alpha):
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"

# Abbreviazioni per i nomi avversario più lunghi, usate come etichette sull'asse X
# del grafico "Confronto tra giocatori" (fino a ~28 posizioni, poco spazio per ciascuna).
OPPONENT_ABBR = {
    "Monterotondo": "M.tondo",
    "Stella Mantus": "S.Mantus",
    "Civitavecchia": "Civitav.",
    "Astrolabio": "Astrol.",
    "Poolstars": "Poolst.",
}


def abbr_opponent(name):
    return OPPONENT_ABBR.get(name, name)


_PLAYOUT_TICK_SUFFIX = {"POA": " (PO-A)", "POR": " (PO-R)"}


def build_match_axis(team_df):
    """
    Asse X dei grafici lungo la stagione (sezioni 2/3, pagella): (match_seq
    in ordine cronologico, etichette) — solo le partite presenti in `team_df`
    (quindi registrate e selezionate), non un numero fisso di partite.
    Etichetta = nome avversario (abbreviato se necessario), ripetuto per
    andata e ritorno; playout con suffisso per distinguere andata/ritorno.
    """
    rows = team_df[["match_seq", "opponent", "leg"]].drop_duplicates("match_seq").sort_values("match_seq")
    seqs = [int(ms) for ms in rows["match_seq"]]
    labels = [abbr_opponent(o) + _PLAYOUT_TICK_SUFFIX.get(leg, "") for o, leg in zip(rows["opponent"], rows["leg"])]
    return seqs, labels

st.set_page_config(page_title="Decimo Roma — Match analysis", layout="wide")


def config_version():
    """Data di modifica dei CSV di config, passata a ogni loader cacheato come
    parte della chiave: aggiungere una partita a matches.csv (o registrare una
    terna) invalida la cache da sé, senza riavviare la dashboard."""
    return tuple((ROOT / "config" / name).stat().st_mtime for name in CONFIG_FILES)


def active_seasons():
    seasons = load_seasons()
    return list(seasons[seasons["active"] == 1]["season"])


# `version` (config_version()) non è usato nel corpo dei loader: serve solo
# come chiave di cache.
@st.cache_data(show_spinner="Caricamento partite della stagione da Google Drive...")
def load_matches_cached(season, version):
    """Cache separata dal resto: è la parte lenta (lettura Excel da Drive), riusata
    sia dal dataset KPI fisso sia da quello parametrico "Attacco SO"."""
    return load_all_matches(season)


@st.cache_data(show_spinner=False)
def load_data(season, version):
    matches = load_matches_cached(season, version)
    return build_comparison_dataset(season, matches=matches)


@st.cache_data(show_spinner=False)
def load_attacco_so_data(season, rec_vote, version):
    matches = load_matches_cached(season, version)
    return build_attacco_so_dataset(season, rec_vote=rec_vote, matches=matches)


@st.cache_data(show_spinner=False)
def load_match_outcomes(season, version):
    matches = load_matches_cached(season, version)
    return build_match_outcomes_dataset(season, matches=matches)


@st.cache_data(show_spinner=False)
def load_setter_kpi_data(season, version):
    """"Attacco Alzato" (ruolo palleggiatore, src.setter_report) — NON riusa
    load_matches_cached: quel `matches` è già filtrato/normalizzato, non
    adatto alla ricostruzione formazioni/identificazione palleggiatore (vedi
    docstring di src.setter_report)."""
    return build_setter_kpi_dataset(season)


@st.cache_data(show_spinner="Calcolo pagella giocatore...")
def load_player_report(season, rec_vote, version):
    matches = load_matches_cached(season, version)
    return build_player_report_base(season, rec_vote=rec_vote, matches=matches)


@st.cache_data(show_spinner="Calcolo report di sintesi stagionale...")
def load_player_season_report(season, rec_vote, version):
    matches = load_matches_cached(season, version)
    return build_player_season_report(season, rec_vote=rec_vote, matches=matches)


def kernel_smooth(y, bandwidth):
    """
    Smoothing gaussiano (Nadaraya-Watson) su una serie con eventuali NaN.
    Ogni punto valido viene ricalcolato come media pesata (kernel gaussiano
    sulla distanza in posizione) di tutti gli altri punti validi della serie;
    le posizioni originariamente NaN restano NaN — il "buco" nella linea
    (giocatore non in campo quella partita) resta visibile.
    bandwidth <= 0 disattiva lo smoothing (ritorna la serie grezza).
    """
    y = np.asarray(y, dtype=float)
    valid = ~np.isnan(y)
    if bandwidth <= 0 or valid.sum() < 2:
        return y.copy()

    idx = np.arange(len(y))
    valid_idx = idx[valid]
    valid_y = y[valid]
    out = np.full(len(y), np.nan)
    for i in valid_idx:
        d = valid_idx - i
        w = np.exp(-0.5 * (d / bandwidth) ** 2)
        out[i] = np.sum(w * valid_y) / np.sum(w)
    return out


def categorical_style(key, master_order):
    """
    Colore + tratteggio fissi per una chiave categorica (entità o KPI), in base
    alla sua posizione nell'elenco completo (non in quello filtrato/selezionato)
    — così cambiare selezione non ridipinge le serie superstiti.
    """
    palette = theme_colors()["palette"]
    i = master_order.index(key)
    color = palette[i % len(palette)]
    dash = ENTITY_DASHES[(i // len(palette)) % len(ENTITY_DASHES)]
    return color, dash


def base_layout(title, xaxis_title, yaxis_title, extra_xaxis=None):
    colors = theme_colors()
    xaxis = dict(gridcolor=colors["grid"])
    if extra_xaxis:
        xaxis.update(extra_xaxis)
    return dict(
        title=title,
        xaxis_title=xaxis_title,
        yaxis_title=yaxis_title,
        xaxis=xaxis,
        yaxis=dict(gridcolor=colors["grid"], zerolinecolor=colors["grid"]),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        plot_bgcolor=colors["surface"],
        paper_bgcolor=colors["surface"],
        font=dict(family="system-ui, -apple-system, 'Segoe UI', sans-serif", color=colors["ink"]),
        margin=dict(t=90),
        hovermode="x unified",
    )


def add_result_strip(fig, match_outcomes_df, x_positions, tick_labels, row=2, col=1):
    """
    Aggiunge, alla riga subplot indicata, una striscia di marker quadrati:
    verde = partita vinta, rosso = persa, grigio = esito indeterminato (vedi
    partita_vinta in compute_match_outcome — capita quando manca il file dei
    risultati ufficiali per quella partita) — alle stesse posizioni X
    (match_seq) del grafico principale sopra, così il confronto è immediato.
    """
    lookup = match_outcomes_df.set_index("match_seq")["partita_vinta"]
    colors, texts = [], []
    for x in x_positions:
        esito = lookup.get(x)
        # NON usare 'is True'/'is False': un valore booleano estratto da una
        # colonna pandas è un numpy.bool_, non un bool nativo, e
        # 'numpy.True_ is True' vale False in Python — con 'is' la striscia
        # risultava sempre grigia, qualunque fosse il vero esito (bug
        # riscontrato in verifica il 2026-08-12, mai notato prima perché la
        # differenza visiva grigio/colorato passava inosservata a uno sguardo
        # superficiale). 'esito is None' resta corretto per la chiave
        # mancante: Series.get() ritorna None (non NaN) di default.
        if esito is None:
            colors.append(UNKNOWN_COLOR)
            texts.append("Esito indeterminato")
        elif esito:
            colors.append(WIN_COLOR)
            texts.append("Vinta")
        else:
            colors.append(LOSS_COLOR)
            texts.append("Persa")

    fig.add_trace(
        go.Scatter(
            x=x_positions, y=[0] * len(x_positions), mode="markers",
            marker=dict(color=colors, size=14, symbol="square"),
            text=texts, hovertemplate="%{text}<extra></extra>",
            showlegend=False,
        ),
        row=row, col=col,
    )
    fig.update_yaxes(visible=False, range=[-1, 1], row=row, col=col)
    fig.update_xaxes(
        tickmode="array", tickvals=x_positions, ticktext=tick_labels,
        row=row, col=col,
    )
    fig.update_xaxes(showticklabels=False, row=row - 1, col=col)


def build_kpi_chart(df, kpi, entity_label, x_axis_order, show_bars, show_trend, bandwidth):
    """Grafico per avversario: barre raggruppate per leg e/o 2 linee di trend (andata/ritorno)."""
    sub = df[df["kpi"] == kpi]
    if sub.empty:
        return None

    palette = theme_colors()["palette"]
    color_a, color_r = palette[0], palette[1]
    leg_colors = {
        "A": color_a, "R": color_r,
        "POA": hex_to_rgba(color_a, 0.45), "POR": hex_to_rgba(color_r, 0.45),
    }

    fig = go.Figure()

    if show_bars:
        for leg in LEG_ORDER:
            leg_df = sub[sub["leg"] == leg].set_index("x_label").reindex(x_axis_order).dropna(subset=["value"])
            if leg_df.empty:
                continue
            fig.add_trace(go.Bar(
                x=leg_df.index, y=leg_df["value"],
                name=LEG_LABELS[leg], marker_color=leg_colors[leg],
                customdata=[format_kpi_value(kpi, v) for v in leg_df["value"]],
                hovertemplate="%{x}<br>%{customdata}<extra>" + LEG_LABELS[leg] + "</extra>",
            ))

    if show_trend:
        for leg, color in (("A", color_a), ("R", color_r)):
            leg_df = sub[sub["leg"] == leg].set_index("x_label").reindex(x_axis_order)
            values = leg_df["value"].to_numpy()
            if np.count_nonzero(~np.isnan(values)) < 2:
                continue
            smoothed = kernel_smooth(values, bandwidth)
            fig.add_trace(go.Scatter(
                x=x_axis_order, y=smoothed, mode="lines+markers",
                name=f"Trend {LEG_LABELS[leg].lower()}",
                line=dict(color=color, width=2, shape="spline", smoothing=0.8),
                marker=dict(size=6), connectgaps=False,
                customdata=[format_kpi_value(kpi, v) if not np.isnan(v) else "" for v in values],
                hovertemplate="%{x}<br>valore reale: %{customdata}<extra>Trend " + LEG_LABELS[leg].lower() + "</extra>",
            ))

    fig.update_layout(
        barmode="group",
        **base_layout(
            f"{kpi} — {entity_label}", "Avversario", kpi,
            extra_xaxis=dict(categoryorder="array", categoryarray=x_axis_order),
        ),
    )
    return fig


def build_player_comparison_chart(
    kpi, selected_entities, team_df, player_df, entity_master_order, bandwidth, match_axis,
    match_outcomes_df,
):
    """
    Grafico di confronto multi-entità: una linea continua per entità sulle
    partite di `match_axis` (vedi build_match_axis), con una striscia
    vinta/persa sotto (vedi add_result_strip).
    """
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.85, 0.15], vertical_spacing=0.04)
    match_seq_full, tick_labels = match_axis
    stats_rows = []

    for entity in selected_entities:
        df = team_df if entity == "Squadra" else player_df[player_df["player"] == entity]
        sub = df[df["kpi"] == kpi].set_index("match_seq").reindex(match_seq_full)
        values = sub["value"].to_numpy()

        if np.count_nonzero(~np.isnan(values)) == 0:
            continue

        color, dash = categorical_style(entity, entity_master_order)
        smoothed = kernel_smooth(values, bandwidth)
        fig.add_trace(go.Scatter(
            x=match_seq_full, y=smoothed, mode="lines+markers",
            name=entity, line=dict(color=color, width=2, dash=dash, shape="spline", smoothing=0.8),
            marker=dict(size=5), connectgaps=False,
            customdata=[format_kpi_value(kpi, v) if not np.isnan(v) else "" for v in values],
            hovertemplate="%{x}<br>valore reale: %{customdata}<extra>" + entity + "</extra>",
        ), row=1, col=1)

        raw_valid = values[~np.isnan(values)]
        stats_rows.append({
            "Entità": entity,
            "Min": float(np.min(raw_valid)),
            "Mediana": float(np.median(raw_valid)),
            "Max": float(np.max(raw_valid)),
        })

    add_result_strip(fig, match_outcomes_df, match_seq_full, tick_labels, row=2, col=1)
    fig.update_layout(**base_layout(f"Confronto giocatori — {kpi}", "Avversario", kpi))
    return fig, pd.DataFrame(stats_rows)


# PERCENT_KPIS (src.leg_comparison) copre solo i 32 KPI "fissi" della
# dashboard — "Attacco Alzato%" (ruolo palleggiatore, src.setter_report) non
# ne fa parte ma va comunque formattato come percentuale nella pagella.
PAGELLA_PERCENT_KPIS = set(PERCENT_KPIS) | {
    ATTACCO_ALZATO_POS_KPI_LABELS["eff"], ATTACCO_ALZATO_ESCL_KPI_LABELS["eff"], ATTACCO_ALZATO_FB_KPI_LABELS["eff"],
}


def format_kpi_value(kpi, value):
    """Formatta un valore GREZZO di singola partita (sempre un intero per i
    conteggi), per tabelle/hover — vedi format_kpi_average per le medie."""
    if kpi in PAGELLA_PERCENT_KPIS:
        return f"{value:.1f}%"
    return f"{value:.0f}"


def format_kpi_average(kpi, value):
    """
    Formatta una MEDIA (mediana stagionale, media di uno streak, media
    prima/seconda metà) — a differenza di format_kpi_value, i conteggi non
    sono arrotondati a intero: una media come 0.15 conteggi/partita non va
    confusa con "0" (bug riscontrato e corretto nella pagella giocatore:
    "Ricezione = (ace subiti): da 0 a 0" con format_kpi_value su medie
    frazionarie — la differenza reale (es. 0.15 vs 0.35) restava invisibile).
    """
    if kpi in PAGELLA_PERCENT_KPIS:
        return f"{value:.1f}%"
    return f"{value:.2f}"


def build_kpi_comparison_chart(
    entity, selected_kpis, team_df, player_df, kpi_master_order, bandwidth, match_axis,
    match_outcomes_df,
):
    """
    Grafico di confronto multi-KPI per una singola entità: una linea continua
    per KPI sulle partite di `match_axis` (vedi build_match_axis), con una
    striscia vinta/persa sotto (vedi add_result_strip).
    """
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.85, 0.15], vertical_spacing=0.04)
    match_seq_full, tick_labels = match_axis
    stats_rows = []

    df = team_df if entity == "Squadra" else player_df[player_df["player"] == entity]

    for kpi in selected_kpis:
        sub = df[df["kpi"] == kpi].set_index("match_seq").reindex(match_seq_full)
        values = sub["value"].to_numpy()

        if np.count_nonzero(~np.isnan(values)) == 0:
            continue

        color, dash = categorical_style(kpi, kpi_master_order)
        smoothed = kernel_smooth(values, bandwidth)
        fig.add_trace(go.Scatter(
            x=match_seq_full, y=smoothed, mode="lines+markers",
            name=kpi, line=dict(color=color, width=2, dash=dash, shape="spline", smoothing=0.8),
            marker=dict(size=5), connectgaps=False,
            customdata=[format_kpi_value(kpi, v) if not np.isnan(v) else "" for v in values],
            hovertemplate="%{x}<br>valore reale: %{customdata}<extra>" + kpi + "</extra>",
        ), row=1, col=1)

        raw_valid = values[~np.isnan(values)]
        stats_rows.append({
            "KPI": kpi,
            "Min": format_kpi_value(kpi, np.min(raw_valid)),
            "Mediana": format_kpi_value(kpi, np.median(raw_valid)),
            "Max": format_kpi_value(kpi, np.max(raw_valid)),
        })

    add_result_strip(fig, match_outcomes_df, match_seq_full, tick_labels, row=2, col=1)
    fig.update_layout(**base_layout(f"Confronto KPI — {entity}", "Avversario", "Valore"))
    return fig, pd.DataFrame(stats_rows)


def render_kpi_section(team_df, player_df, x_axis_order, entity_options):
    st.header("Confronto per KPI — Squadra / Giocatore")

    c1, c2, c3, c4 = st.columns([2, 2, 1, 1])
    selected_kpis = c1.multiselect("KPI", DISPLAY_KPIS, default=[DISPLAY_KPIS[0]], key="kpi1")
    selected_entities = c2.multiselect("Squadra / Giocatori", entity_options, default=["Squadra"], key="entity1")
    display_mode = c3.radio("Mostra", ["Barre e trend", "Solo barre", "Solo trend"], key="mode1")
    show_table = c4.checkbox("Tabella dati sotto ogni grafico", key="table1")

    if not selected_kpis or not selected_entities:
        st.info("Seleziona almeno un KPI e un'entità (Squadra o un giocatore).")
        return

    show_bars = display_mode != "Solo trend"
    show_trend = display_mode != "Solo barre"

    for entity in selected_entities:
        st.subheader(entity)
        entity_df = team_df if entity == "Squadra" else player_df[player_df["player"] == entity]

        cols = st.columns(2) if len(selected_kpis) > 1 else [st.container()]
        for i, kpi in enumerate(selected_kpis):
            fig = build_kpi_chart(
                entity_df, kpi, entity, x_axis_order, show_bars, show_trend,
                st.session_state["bandwidth"],
            )
            with cols[i % len(cols)]:
                if fig is None:
                    st.warning(f"Nessun dato per {kpi} — {entity}.")
                    continue
                st.plotly_chart(fig, theme=None)
                if show_table:
                    table = (
                        entity_df[entity_df["kpi"] == kpi][["x_label", "leg", "value"]]
                        .rename(columns={"x_label": "avversario"})
                        .sort_values(["avversario", "leg"])
                        .reset_index(drop=True)
                    )
                    st.dataframe(table, width="stretch")


def render_season_lines_section(team_df, player_df, entity_options, match_axis, match_outcomes_df):
    """Lungo le partite selezionate, una linea per voce: tutti i giocatori (e
    la squadra) su un KPI, oppure tutti i KPI di un giocatore (o della
    squadra) — le due vecchie sezioni "Confronto tra giocatori" e "Confronto
    KPI per una singola entità", unite con lo stesso interruttore della
    sezione "Ultima partita rispetto alle precedenti"."""
    st.header("Lungo la stagione, partita per partita")
    st.caption("Una linea per voce lungo le partite selezionate (asse X: avversario, andata poi ritorno; in coda "
               "l'eventuale playout) — striscia sotto: esito partita (verde = vinta, rosso = persa).")
    mode = st.radio("Vista", ["Tutti i giocatori su un KPI", "Tutti i KPI di un giocatore (o della squadra)"],
                    horizontal=True, key="season_lines_mode")
    if mode.startswith("Tutti i giocatori"):
        c1, c2 = st.columns(2)
        kpi = c1.selectbox("KPI", DISPLAY_KPIS, key="season_lines_kpi")
        default_entities = entity_options[:3] if len(entity_options) >= 3 else entity_options
        entities = c2.multiselect("Squadra / Giocatori", entity_options, default=default_entities,
                                  key="season_lines_entities")
        if not entities:
            st.info("Seleziona almeno un giocatore o la squadra.")
            return
        fig, stats_df = build_player_comparison_chart(
            kpi, entities, team_df, player_df, entity_options, st.session_state["bandwidth"], match_axis,
            match_outcomes_df,
        )
        st.plotly_chart(fig, theme=None)
        if stats_df.empty:
            st.warning(f"Nessun dato per {kpi} sulle voci selezionate.")
            return
        fmt = "%.1f%%" if kpi in PERCENT_KPIS else "%.0f"
        st.dataframe(stats_df, width="stretch", hide_index=True, column_config={
            c: st.column_config.NumberColumn(format=fmt) for c in ("Min", "Mediana", "Max")})
    else:
        c1, c2 = st.columns(2)
        entity = c1.selectbox("Squadra / Giocatore", entity_options, key="season_lines_entity")
        kpis = c2.multiselect("KPI", DISPLAY_KPIS, default=DISPLAY_KPIS[:3], key="season_lines_kpis")
        if not kpis:
            st.info("Seleziona almeno un KPI.")
            return
        if any(k in PERCENT_KPIS for k in kpis) and any(k not in PERCENT_KPIS for k in kpis):
            st.caption("⚠️ KPI percentuali e conteggi sullo stesso asse: le scale non sono direttamente comparabili.")
        fig, stats_df = build_kpi_comparison_chart(
            entity, kpis, team_df, player_df, DISPLAY_KPIS, st.session_state["bandwidth"], match_axis,
            match_outcomes_df,
        )
        st.plotly_chart(fig, theme=None)
        if stats_df.empty:
            st.warning(f"Nessun dato per {entity} sui KPI selezionati.")
        else:
            st.dataframe(stats_df, width="stretch", hide_index=True)


_LEG_WORD = {"A": "andata", "R": "ritorno", "POA": "playout andata", "POR": "playout ritorno"}


def build_match_label_lookup(team_df):
    """
    match_seq -> 'Avversario (andata/ritorno/playout andata/playout ritorno)'
    — per riferirsi ai finding della pagella per squadra e leg invece che per
    numero di partita nudo (richiesto esplicitamente dall'utente il
    2026-08-13; il numero resta disponibile tra parentesi in format_finding_text).
    """
    rows = team_df[["match_seq", "opponent", "leg"]].drop_duplicates("match_seq").set_index("match_seq")
    return {
        ms: f"{abbr_opponent(row['opponent'])} ({_LEG_WORD[row['leg']]})"
        for ms, row in rows.iterrows()
    }


def _match_ref(ms, match_label_lookup):
    """'partita N' come fallback se il match_seq non è nel lookup (non
    dovrebbe succedere con dati validi, ma meglio un fallback esplicito che
    un KeyError)."""
    label = match_label_lookup.get(ms, f"partita {ms + 1}")
    return f"{label} (partita {ms + 1})"




def format_finding_text(finding, match_label_lookup):
    """Frase discorsiva per un finding di src.player_report (streak o cambio
    di livello) — vedi build_finding_chart per l'evidenza grafica."""
    kpi = finding["kpi"]
    direzione = finding["direzione"]
    if finding["tipo_finding"] == "streak":
        return (
            f"**{kpi}** — periodo **{direzione}** da {_match_ref(finding['start_match_seq'], match_label_lookup)} "
            f"a {_match_ref(finding['end_match_seq'], match_label_lookup)} ({finding['length']} partite di fila): "
            f"media {format_kpi_average(kpi, finding['mean_value'])} contro una mediana stagionale di "
            f"{format_kpi_average(kpi, finding['baseline'])}."
        )
    return (
        f"**{kpi}** — cambio di livello **{direzione}** da {_match_ref(finding['split_match_seq'], match_label_lookup)} "
        f"in poi: da {format_kpi_average(kpi, finding['prima_media'])} a "
        f"{format_kpi_average(kpi, finding['dopo_media'])}."
    )


def _add_finding_annotation(fig, finding, colors, match_seqs):
    """
    Aggiunge a `fig` l'evidenza grafica di UN finding (rettangolo per uno
    streak, linea di spartiacque + medie per un cambio di livello) — verde
    se favorevole/rosso se sfavorevole (stessa convenzione status color di
    add_result_strip). Fattorizzata da build_finding_chart per essere
    riusata anche da build_unified_finding_chart (più finding sullo stesso
    KPI in un unico grafico, per il report Word — vedi generate_player_report_docx).
    """
    highlight_color = WIN_COLOR if finding["direzione"] == "positivo" else LOSS_COLOR
    if finding["tipo_finding"] == "streak":
        fig.add_vrect(
            x0=finding["start_match_seq"] - 0.5, x1=finding["end_match_seq"] + 0.5,
            fillcolor=hex_to_rgba(highlight_color, 0.18), line_width=0,
        )
        fig.add_hline(
            y=finding["baseline"], line_dash="dot", line_color=colors["grid"],
            annotation_text="mediana stagionale", annotation_position="bottom right",
        )
    else:
        # split_match_seq è la PRIMA partita del "dopo" — la linea di
        # spartiacque va appena prima di quel punto.
        split = finding["split_match_seq"] - 0.5
        fig.add_vline(x=split, line_dash="dash", line_color=colors["grid"], annotation_text="cambio di livello")
        fig.add_shape(
            type="line", x0=match_seqs[0], x1=split,
            y0=finding["prima_media"], y1=finding["prima_media"],
            line=dict(color=colors["grid"], dash="dot", width=2),
        )
        fig.add_shape(
            type="line", x0=split, x1=match_seqs[-1],
            y0=finding["dopo_media"], y1=finding["dopo_media"],
            line=dict(color=highlight_color, dash="dot", width=2),
        )


def _build_base_kpi_figure(kpi, player_label, player_df_all, match_seq_full):
    """Traccia di base (serie grezza del KPI lungo la stagione) condivisa
    da build_finding_chart e build_unified_finding_chart."""
    sub = player_df_all[(player_df_all["player"] == player_label) & (player_df_all["kpi"] == kpi)]
    sub = sub.set_index("match_seq").reindex(match_seq_full)
    values = sub["value"].to_numpy()

    colors = theme_colors()
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=match_seq_full, y=values, mode="lines+markers",
        line=dict(color=colors["palette"][0], width=2), marker=dict(size=6),
        connectgaps=False, showlegend=False,
        customdata=[format_kpi_value(kpi, v) if not np.isnan(v) else "" for v in values],
        hovertemplate="%{x}<br>%{customdata}<extra></extra>",
    ))
    return fig, colors


def build_finding_chart(kpi, player_label, finding, player_df_all, match_axis):
    """
    Grafico di evidenza per un singolo finding: la serie grezza del KPI lungo
    tutta la stagione, con la porzione che ha generato il
    finding evidenziata — verde = favorevole, rosso = sfavorevole (stessa
    convenzione status color di add_result_strip, qui applicata al
    finding invece che all'esito partita).
    """
    match_seq_full, tick_labels = match_axis
    fig, colors = _build_base_kpi_figure(kpi, player_label, player_df_all, match_seq_full)
    _add_finding_annotation(fig, finding, colors, match_seq_full)

    fig.update_xaxes(tickmode="array", tickvals=match_seq_full, ticktext=tick_labels)
    fig.update_layout(**base_layout(f"{kpi} — {player_label}", "Avversario", kpi))
    fig.update_layout(height=320, margin=dict(t=60, b=40))
    return fig


def build_unified_finding_chart(kpi, player_label, findings, player_df_all, match_axis):
    """
    Come build_finding_chart, ma con PIÙ finding sullo stesso KPI evidenziati
    su un unico grafico invece di uno per finding — usata dal report Word
    (richiesto esplicitamente dall'utente il 2026-08-15: "unifica gli
    eventi notevoli (streak o trend) in un unico grafico, se si riferiscono
    allo stesso kpi"), non dalla tab dashboard (che resta con un grafico per
    finding, già verificata).
    """
    match_seq_full, tick_labels = match_axis
    fig, colors = _build_base_kpi_figure(kpi, player_label, player_df_all, match_seq_full)
    for finding in findings:
        _add_finding_annotation(fig, finding, colors, match_seq_full)

    fig.update_xaxes(tickmode="array", tickvals=match_seq_full, ticktext=tick_labels)
    fig.update_layout(**base_layout(f"{kpi} — {player_label}", "Avversario", kpi))
    fig.update_layout(height=320, margin=dict(t=60, b=40))
    return fig


EFFICIENCY_FAMILY_LABELS = {
    "ricezione": "Ricezione%",
    "attacco": "Attacco%",
    "attacco_so": "Attacco SO%",
    "attacco_fb": "Attacco FB%",
    "contrattacco": "Contrattacco%",
    "battuta": "Battuta%",
    "attacco_alzato_pos": ATTACCO_ALZATO_POS_KPI_LABELS["eff"],
    "attacco_alzato_escl": ATTACCO_ALZATO_ESCL_KPI_LABELS["eff"],
    "attacco_alzato_fb": ATTACCO_ALZATO_FB_KPI_LABELS["eff"],
}


def render_season_summary(season_data, match_label_lookup):
    """
    Sintesi stagionale per un giocatore (src.player_season_report,
    richiesta esplicitamente dall'utente il 2026-08-14): efficienze
    aggregate e conteggi medi/partita curati per ruolo, punti di
    forza/debolezza (soglie assolute + confronto con i compagni), obiettivi
    per la prossima stagione (mediana di riferimento), partita migliore/
    peggiore per punteggio composito e per singolo KPI notevole.
    """
    st.subheader("Sintesi stagionale")
    st.caption(f"{season_data['n_partite_giocate']} partite giocate")

    efficienze = {k: v for k, v in season_data["efficienze"].items() if v[0] is not None}
    if efficienze:
        st.markdown("**Efficienze aggregate di stagione**")
        cols = st.columns(min(len(efficienze), 4))
        for i, (key, (eff, tot)) in enumerate(efficienze.items()):
            label = EFFICIENCY_FAMILY_LABELS.get(key, key)
            cols[i % len(cols)].metric(label, f"{eff:.1f}%", help=f"{tot} totali in stagione")

    conteggi = {k: v for k, v in season_data["conteggi_medi_partita"].items() if v[0] is not None}
    if conteggi:
        st.markdown("**Conteggi medi a partita**")
        table = pd.DataFrame([
            {"KPI": k, "Media/partita": round(media, 2), "Totale stagione": tot, "Partite con dati": n}
            for k, (media, tot, n) in conteggi.items()
        ])
        st.dataframe(table, width="stretch", hide_index=True)

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**Punti di forza**")
        if season_data["punti_forza"]:
            for f in season_data["punti_forza"]:
                st.markdown(f"✅ **{f['area']}** — {f['motivo']}")
        else:
            st.caption("Nessuno di particolarmente notevole.")
    with col_b:
        st.markdown("**Punti deboli**")
        if season_data["punti_deboli"]:
            for f in season_data["punti_deboli"]:
                st.markdown(f"⚠️ **{f['area']}** — {f['motivo']}")
        else:
            st.caption("Nessuno di particolarmente notevole.")

    if season_data["obiettivi_prossima_stagione"]:
        st.markdown("**Obiettivi per la prossima stagione**")
        for t in season_data["obiettivi_prossima_stagione"]:
            label = EFFICIENCY_FAMILY_LABELS.get(t["kpi"], t["kpi"]) if t["tipo"] == "eff" else t["kpi"]
            unit = "%" if t["tipo"] == "eff" else ""
            st.markdown(f"🎯 {label}: {t['valore']:.1f}{unit} (mediana di riferimento {t['mediana']:.1f}{unit})")

    best, worst = season_data["partita_migliore"], season_data["partita_peggiore"]
    if best or worst:
        st.markdown(f"**Partita migliore/peggiore** ({season_data['criterio_migliore_peggiore']})")
        col_c, col_d = st.columns(2)
        if best:
            ref = match_label_lookup.get(best["match_seq"], f"partita {best['match_seq'] + 1}")
            if "netto" in best:
                col_c.metric("Migliore", f"+{best['netto']}", help=f"{ref} — {best['punti']} punti, {best['errori']} errori")
            else:
                col_c.metric("Migliore", f"{best['value']:.1f}%", help=ref)
        if worst:
            ref = match_label_lookup.get(worst["match_seq"], f"partita {worst['match_seq'] + 1}")
            if "netto" in worst:
                col_d.metric("Peggiore", f"{worst['netto']:+d}", help=f"{ref} — {worst['punti']} punti, {worst['errori']} errori")
            else:
                col_d.metric("Peggiore", f"{worst['value']:.1f}%", help=ref)

    if season_data["partite_notevoli"]:
        st.markdown("**Partite singole notevoli**")
        for n in season_data["partite_notevoli"]:
            ref_best = match_label_lookup.get(n["migliore"]["match_seq"], f"partita {n['migliore']['match_seq'] + 1}")
            ref_worst = match_label_lookup.get(n["peggiore"]["match_seq"], f"partita {n['peggiore']['match_seq'] + 1}")
            if n.get("tipo") == "efficienza":
                st.markdown(
                    f"📊 **Efficienza {n['kpi']}** — migliore {ref_best} ({n['migliore']['value']:.1f}%), "
                    f"peggiore {ref_worst} ({n['peggiore']['value']:.1f}%)"
                )
            else:
                st.markdown(
                    f"📊 **Bilancio {n['kpi']}** — migliore {ref_best} ({n['migliore']['value']:+.0f}), "
                    f"peggiore {ref_worst} ({n['peggiore']['value']:+.0f})"
                )


# ----------------------------------------------------------------------------
# Report Word della pagella giocatore (pulsante "Genera report", richiesto
# esplicitamente dall'utente il 2026-08-15) — vedi generate_player_report_docx.
# ----------------------------------------------------------------------------

ROLE_DISPLAY_NAMES = {"L": "libero", "M": "schiacciatore", "C": "centrale", "O": "opposto", "P": "palleggiatore"}


def generate_narrative_summary(cognome, season_data, presenze):
    """
    Giudizio sintetico testuale in apertura del report Word — composto da
    regole a partire dalle statistiche già calcolate (punti di forza/
    debolezza, assenze, punteggio composito/ricezione, obiettivi), non un
    testo libero: ogni frase è tracciabile a un numero specifico del report.
    """
    ruolo_nome = ROLE_DISPLAY_NAMES.get(season_data["ruolo_bucket"], "giocatore")
    parti = [f"{cognome} ha giocato {season_data['n_partite_giocate']} partite nel ruolo di {ruolo_nome} nella stagione."]

    if presenze:
        tasso = presenze["tasso_assenza"] * 100
        if tasso == 0:
            parti.append("Presenza perfetta ad allenamenti e partite, senza assenze registrate.")
        elif tasso < 10:
            parti.append(f"Presenza regolare, con un tasso di assenza contenuto ({tasso:.1f}%).")
        elif tasso < 20:
            parti.append(f"Tasso di assenza nella norma ({tasso:.1f}%).")
        else:
            parti.append(f"Tasso di assenza elevato nel corso della stagione ({tasso:.1f}%), un aspetto da monitorare.")

    punti_forza = season_data["punti_forza"]
    punti_deboli = season_data["punti_deboli"]
    if punti_forza and punti_deboli:
        parti.append(
            f"Sul piano del rendimento, i punti di forza principali sono {', '.join(f['area'] for f in punti_forza)}, "
            f"mentre le aree su cui lavorare restano {', '.join(f['area'] for f in punti_deboli)}."
        )
    elif punti_forza:
        parti.append(
            f"Sul piano del rendimento emergono soprattutto i punti di forza in "
            f"{', '.join(f['area'] for f in punti_forza)}, senza criticità particolarmente marcate rispetto ai compagni di ruolo."
        )
    elif punti_deboli:
        parti.append(
            f"Sul piano del rendimento le aree principali su cui lavorare sono "
            f"{', '.join(f['area'] for f in punti_deboli)}, senza punti di forza particolarmente marcati rispetto ai compagni di ruolo."
        )
    else:
        parti.append("Il rendimento stagionale resta nella norma, senza punti di forza o criticità particolarmente marcati rispetto ai compagni di ruolo.")

    best, worst = season_data["partita_migliore"], season_data["partita_peggiore"]
    if best and worst:
        criterio = season_data["criterio_migliore_peggiore"]
        if "netto" in best:
            parti.append(
                f"La partita migliore della stagione (per {criterio.lower()}) ha visto un bilancio di "
                f"{best['netto']:+d}, contro il {worst['netto']:+d} della partita peggiore."
            )
        else:
            parti.append(
                f"La partita migliore della stagione (per {criterio.lower()}) ha toccato il {best['value']:.1f}%, "
                f"contro il {worst['value']:.1f}% della partita peggiore."
            )

    n_obiettivi = len(season_data["obiettivi_prossima_stagione"])
    if n_obiettivi:
        parti.append(f"Per la prossima stagione emergono {n_obiettivi} obiettivi specifici di miglioramento, dettagliati più sotto.")

    return " ".join(parti)


def _docx_add_markdown_bold_paragraph(doc, text):
    """
    Aggiunge un paragrafo interpretando i marcatori '**bold**' in stile
    Markdown (usati da format_finding_text, pensato per st.markdown) come
    grassetto reale — altrimenti nel documento Word restano asterischi
    letterali (bug riscontrato in verifica: "**Attacco SO%** — periodo...").
    """
    p = doc.add_paragraph()
    parts = text.split("**")
    for i, part in enumerate(parts):
        run = p.add_run(part)
        run.bold = bool(i % 2)
    return p


def _docx_add_table(doc, headers, rows):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Light Grid Accent 1"
    for cell, header in zip(table.rows[0].cells, headers):
        cell.text = str(header)
    for row_values in rows:
        cells = table.add_row().cells
        for cell, value in zip(cells, row_values):
            cell.text = str(value)
    return table


def generate_player_report_docx(cognome, data, season_data, player_df_all, team_df, match_axis, match_label_lookup):
    """
    Assembla il report Word della pagella giocatore, sezione per sezione
    (ordine richiesto esplicitamente dall'utente il 2026-08-15): giudizio
    sintetico, ruolo/assenze, sintesi stagionale, conteggi medi a partita,
    punti di forza/deboli (sempre presenti, anche vuoti), partita migliore/
    peggiore e partite notevoli, grafici dell'andamento (un grafico per KPI,
    non per finding — vedi build_unified_finding_chart), obiettivi per la
    prossima stagione (senza la spiegazione della mediana, a differenza
    della tab dashboard). Ritorna i byte del documento .docx.
    """
    doc = Document()
    doc.add_heading(f"Pagella — {cognome}", level=0)

    doc.add_heading("Giudizio sintetico", level=1)
    doc.add_paragraph(generate_narrative_summary(cognome, season_data, data["presenze"]))

    doc.add_heading("Ruolo e presenze", level=1)
    pres = data["presenze"]
    doc.add_paragraph(f"Ruolo: {data['ruolo'] or 'n/d'}")
    if pres:
        doc.add_paragraph(
            f"Assenza: {pres['tasso_assenza'] * 100:.1f}% — presente/convocato: {pres['n_presente']}/{pres['n_convocato']}"
        )
    else:
        doc.add_paragraph("Presenze: nessun dato")

    doc.add_heading("Sintesi stagionale", level=1)
    doc.add_paragraph(f"{season_data['n_partite_giocate']} partite giocate")
    efficienze = {k: v for k, v in season_data["efficienze"].items() if v[0] is not None}
    if efficienze:
        _docx_add_table(
            doc, ["KPI", "Efficienza", "Totali in stagione"],
            [(EFFICIENCY_FAMILY_LABELS.get(k, k), f"{eff:.1f}%", tot) for k, (eff, tot) in efficienze.items()],
        )

    doc.add_heading("Conteggi medi a partita", level=1)
    conteggi = {k: v for k, v in season_data["conteggi_medi_partita"].items() if v[0] is not None}
    if conteggi:
        _docx_add_table(
            doc, ["KPI", "Media/partita", "Totale stagione", "Partite con dati"],
            [(k, f"{media:.2f}", tot, n) for k, (media, tot, n) in conteggi.items()],
        )
    else:
        doc.add_paragraph("Nessun conteggio disponibile per questo ruolo.")

    doc.add_heading("Punti di forza", level=1)
    if season_data["punti_forza"]:
        for f in season_data["punti_forza"]:
            doc.add_paragraph(f"{f['area']} — {f['motivo']}", style="List Bullet")
    else:
        doc.add_paragraph("Nessuno di particolarmente notevole.")

    doc.add_heading("Punti deboli", level=1)
    if season_data["punti_deboli"]:
        for f in season_data["punti_deboli"]:
            doc.add_paragraph(f"{f['area']} — {f['motivo']}", style="List Bullet")
    else:
        doc.add_paragraph("Nessuno di particolarmente notevole.")

    doc.add_heading("Partita migliore e peggiore", level=1)
    best, worst = season_data["partita_migliore"], season_data["partita_peggiore"]
    if best or worst:
        doc.add_paragraph(f"Criterio: {season_data['criterio_migliore_peggiore']}")
        if best:
            ref = match_label_lookup.get(best["match_seq"], f"partita {best['match_seq'] + 1}")
            if "netto" in best:
                doc.add_paragraph(f"Migliore: {ref} — {best['punti']} punti, {best['errori']} errori (netto {best['netto']:+d})", style="List Bullet")
            else:
                doc.add_paragraph(f"Migliore: {ref} — {best['value']:.1f}%", style="List Bullet")
        if worst:
            ref = match_label_lookup.get(worst["match_seq"], f"partita {worst['match_seq'] + 1}")
            if "netto" in worst:
                doc.add_paragraph(f"Peggiore: {ref} — {worst['punti']} punti, {worst['errori']} errori (netto {worst['netto']:+d})", style="List Bullet")
            else:
                doc.add_paragraph(f"Peggiore: {ref} — {worst['value']:.1f}%", style="List Bullet")
    else:
        doc.add_paragraph("Nessun dato sufficiente.")

    doc.add_heading("Partite notevoli", level=1)
    if season_data["partite_notevoli"]:
        for n in season_data["partite_notevoli"]:
            ref_best = match_label_lookup.get(n["migliore"]["match_seq"], f"partita {n['migliore']['match_seq'] + 1}")
            ref_worst = match_label_lookup.get(n["peggiore"]["match_seq"], f"partita {n['peggiore']['match_seq'] + 1}")
            if n.get("tipo") == "efficienza":
                doc.add_paragraph(
                    f"Efficienza {n['kpi']} — migliore {ref_best} ({n['migliore']['value']:.1f}%), "
                    f"peggiore {ref_worst} ({n['peggiore']['value']:.1f}%)", style="List Bullet",
                )
            else:
                doc.add_paragraph(
                    f"Bilancio {n['kpi']} — migliore {ref_best} ({n['migliore']['value']:+.0f}), "
                    f"peggiore {ref_worst} ({n['peggiore']['value']:+.0f})", style="List Bullet",
                )
    else:
        doc.add_paragraph("Nessuna di particolarmente notevole.")

    doc.add_heading("Andamento nel corso della stagione", level=1)
    player_label = data["player_label"]
    findings = data["findings"]
    if not findings or player_label is None:
        doc.add_paragraph("Nessun finding notevole per questo giocatore.")
    else:
        findings_by_kpi = {}
        for finding in findings:
            findings_by_kpi.setdefault(finding["kpi"], []).append(finding)
        for kpi, kpi_findings in findings_by_kpi.items():
            doc.add_heading(kpi, level=2)
            for finding in kpi_findings:
                _docx_add_markdown_bold_paragraph(doc, format_finding_text(finding, match_label_lookup))
            fig = build_unified_finding_chart(kpi, player_label, kpi_findings, player_df_all, match_axis)
            png_bytes = fig.to_image(format="png", width=900, height=320, scale=2)
            doc.add_picture(io.BytesIO(png_bytes), width=Inches(6))

    doc.add_heading("Obiettivi per la prossima stagione", level=1)
    if season_data["obiettivi_prossima_stagione"]:
        for t in season_data["obiettivi_prossima_stagione"]:
            label = EFFICIENCY_FAMILY_LABELS.get(t["kpi"], t["kpi"]) if t["tipo"] == "eff" else t["kpi"]
            unit = "%" if t["tipo"] == "eff" else ""
            doc.add_paragraph(f"{label}: {t['valore']:.1f}{unit}", style="List Bullet")
    else:
        doc.add_paragraph("Nessun obiettivo specifico individuato.")

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def render_player_report_section(report, not_in_registry, season_report, player_df_all, team_df, match_axis):
    st.header("Pagella giocatore")
    st.caption(
        "Presenze/assenze e finding di rendimento più notevoli per giocatore — streak (media mobile "
        "di 3 partite giocate consecutive sopra/sotto la mediana stagionale, per intercettare anche "
        "una crescita/calo momentaneo) o cambio di livello (il punto di rottura più marcato della "
        "stagione, non necessariamente a metà), su un sottoinsieme curato di KPI (percentuali "
        "principali + pochi conteggi mirati). Per i KPI percentuali, le partite con troppi pochi "
        "tentativi (< 5 su circa 120 azioni totali a partita) sono escluse dal calcolo. Per i "
        "palleggiatori la famiglia attacco generica è sostituita da \"Attacco Alzato\": l'efficienza "
        "di attacco dei loro attaccanti nelle sole azioni in cui il palleggiatore in campo è "
        "identificabile con sufficiente sicurezza."
    )
    if not_in_registry:
        st.caption(
            "Nel foglio presenze ma non nel registro giocatori (non mostrati qui): "
            + ", ".join(not_in_registry)
        )

    cognomi = sorted(report.keys())
    if not cognomi:
        st.info("Nessun giocatore trovato.")
        return

    match_label_lookup = build_match_label_lookup(team_df)
    tabs = st.tabs(cognomi)
    for tab, cognome in zip(tabs, cognomi):
        with tab:
            data = report[cognome]
            pres = data["presenze"]

            col1, col2, col3 = st.columns(3)
            col1.metric("Ruolo", data["ruolo"] or "n/d")
            if pres:
                col2.metric("Assenza", f"{pres['tasso_assenza'] * 100:.1f}%")
                col3.metric("Presente / Convocato", f"{pres['n_presente']}/{pres['n_convocato']}")
            else:
                col2.metric("Assenza", "n/d")

            season_data = season_report.get(cognome)
            if season_data:
                st.divider()
                render_season_summary(season_data, match_label_lookup)

            st.divider()
            st.subheader("Andamento nel corso della stagione")
            player_label = data["player_label"]
            if not data["findings"]:
                st.info("Nessun finding notevole per questo giocatore (dati insufficienti o troppo stabili).")
            elif player_label is None:
                st.warning("Nessuna serie KPI disponibile per questo giocatore.")
            else:
                for finding in data["findings"]:
                    st.markdown(format_finding_text(finding, match_label_lookup))
                    fig = build_finding_chart(finding["kpi"], player_label, finding, player_df_all, match_axis)
                    st.plotly_chart(fig, theme=None)

            if season_data:
                st.divider()
                report_key = f"report_docx_{cognome}"
                if st.button("📄 Genera report", key=f"genera_report_{cognome}"):
                    with st.spinner("Generazione report Word..."):
                        st.session_state[report_key] = generate_player_report_docx(
                            cognome, data, season_data, player_df_all, team_df, match_axis, match_label_lookup,
                        )
                if report_key in st.session_state:
                    st.download_button(
                        "⬇️ Scarica report",
                        data=st.session_state[report_key],
                        file_name=f"pagella_{cognome}.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        key=f"scarica_report_{cognome}",
                    )


# ---------------------------------------------------------------------------
# Classifiche (come il PDF di classifiche.ipynb)
# ---------------------------------------------------------------------------
# Default di classifiche.ipynb: soglie minime di volume sulla stagione e
# giocatori esclusi per fondamentale (scelta editoriale: es. il libero non
# compare nelle classifiche di battuta/attacco/muro). Modificabili dal tab.
RANKING_DEFAULT_IGNORE = {
    "battuta": ["Pedico", "Amore"],
    "attacco": ["Pedico"],
    "muro": ["Pedico"],
    "ricezione": ["Amore", "Caranzetti"],
}
RANKING_DEFAULT_MIN = {"attacchi": 50, "ricezioni": 40}
# Soglie ed esclusioni scelte nel tab "Classifiche", salvate per stagione e
# usate anche dal report partita (richiesta dell'utente, 2026-10-30).
RANKING_SETTINGS_FILE = ROOT / "config" / "ranking_settings.json"


def load_ranking_settings(season, cognomi=None):
    """{min_attacchi, min_ricezioni, ignore {fondamentale: [cognomi]}} della
    stagione: quelle salvate, altrimenti i default di classifiche.ipynb
    (esclusioni limitate ai cognomi presenti, se passati)."""
    try:
        saved = json.loads(RANKING_SETTINGS_FILE.read_text()).get(season, {})
    except (OSError, ValueError):
        saved = {}
    ignore = saved.get("ignore") or {t: list(names) for t, names in RANKING_DEFAULT_IGNORE.items()}
    if cognomi is not None:
        ignore = {t: [c for c in names if c in cognomi] for t, names in ignore.items()}
    return {
        "min_attacchi": int(saved.get("min_attacchi", RANKING_DEFAULT_MIN["attacchi"])),
        "min_ricezioni": int(saved.get("min_ricezioni", RANKING_DEFAULT_MIN["ricezioni"])),
        "ignore": {t: ignore.get(t, []) for t in RANKING_DEFAULT_IGNORE},
    }


def save_ranking_settings(season, settings):
    try:
        data = json.loads(RANKING_SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        data = {}
    if data.get(season) == settings:
        return
    data[season] = settings
    RANKING_SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def build_rankings(season_df, ignore, min_attacchi=0, min_ricezioni=0):
    """Le 6 classifiche di classifiche.ipynb: [(titolo, tabella)]."""
    srv = calculate_player_scores_by_type(season_df, "battuta", ignore_list=ignore["battuta"])
    att = calculate_player_scores_by_type(season_df, "attacco", ignore_list=ignore["attacco"])
    blk = calculate_player_scores_by_type(season_df, "muro", ignore_list=ignore["muro"])
    rec = calculate_player_scores_by_type(season_df, "ricezione", ignore_list=ignore["ricezione"])
    return [
        ("Ace Man", build_ace_man(srv)),
        ("Spike Leader", build_best_attack(att)),
        ("Spike Guarantee", build_spike_guarantee(att, min_attacchi=min_attacchi)),
        ("Block Monster", build_best_block(blk)),
        ("Miglior ricevitore", build_best_rec(rec, min_ricezioni=min_ricezioni)),
        ("Muro della vergogna (battute sbagliate)", build_srv_shame(srv)),
    ]


# ---------------------------------------------------------------------------
# Report post-partita (PDF per i giocatori, src.match_report/_pdf)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Partita: la singola partita in esame, interattiva (stessi dati del report)
# ---------------------------------------------------------------------------
SET_KPI_LABELS = {
    "Battuta%": "Battuta", "Ricezione%": "Ricezione", "Attacco%": "Attacco", "Attacco SO%": "Attacco side-out",
    "Contrattacco%": "Contrattacco", "Attacco FB%": "Attacco da free ball", "Muro%": "Muro",
    "Punti": "Punti", "Errori": "Errori", "p-e": "p-e/m", "Muri punto": "Muri punto",
}
SETTER_FAMILIES = {"R+#": "Da ricezione positiva (+ o #)", "R!": "Da ricezione !", "FB": "Da free ball"}


@st.cache_data(show_spinner="Analisi della partita...")
def load_match_view(file_path, official_result, version):
    source = load_report_source(file_path)
    return {
        "header": match_header(source, official_result),
        "nome_noi": source["nome_noi"], "nome_loro": source["nome_loro"],
        "points": point_sources_by_set(source),
        "kpi": {"noi": set_kpis(source["noi"]), "loro": set_kpis(source["loro"])},
        "setters": setter_table(source),
        "players": player_table(source),
        "dfs": {"noi": source["noi"], "loro": source["loro"]},
    }


def _team_colors():
    palette = theme_colors()["palette"]
    return palette[0], palette[1]


def _secondary_colors():
    """Tinte per giocatori/KPI/palleggiatori: la palette senza blu e arancio,
    riservati alle due squadre."""
    return theme_colors()["palette"][2:]


def _padded_range(fig):
    """Asse y con spazio sopra e sotto per le etichette dei valori delle barre."""
    vals = [v for tr in fig.data for v in tr.y if v is not None and not np.isnan(v)] or [0]
    lo, hi = min(0, min(vals)), max(0, max(vals))
    pad = max(hi - lo, 10) * 0.18
    return [lo - (pad if lo < 0 else 0), hi + pad]


def _fmt_cell(v, pct):
    """Valore di tabella: '-' se il fondamentale non è stato giocato."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "-"
    return f"{v:.0f}%" if pct else f"{v:.0f}"


def _pct_text(v):
    return "" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.0f}%"


def _set_axis(view):
    """Etichette dei set ('S1'..), con '*' sui set rilevati solo in parte."""
    incomplete = {n for n, _, _, completo in view["header"]["set_scout"] if not completo}
    sets = [s for s in view["kpi"]["noi"]["set"].unique() if s != TOTAL_LABEL]
    return sets, [f"S{s}*" if int(s) in incomplete else f"S{s}" for s in sets]


def _kpi_value(kpi_df, entity, set_label, kpi):
    row = kpi_df[(kpi_df["entita"] == entity) & (kpi_df["set"] == set_label) & (kpi_df["kpi"] == kpi)]
    return row["value"].iloc[0] if not row.empty else None


def _match_header_html(view):
    h, noi, loro = view["header"], *_team_colors()
    color_casa, color_ospite = (noi, loro) if h["casa"] == view["nome_noi"] else (loro, noi)
    badge = "display:inline-block;padding:4px 12px;border-radius:7px;color:#fff;font-weight:700"
    pills = " ".join(
        f"<span style='{badge};font-size:0.9rem;background:{color_casa if a > b else color_ospite}'>{a}-{b}</span>"
        for a, b in h["parziali"]
    )
    incompleti = [f"{n}° set rilevato fino al {a}-{b}" for n, a, b, ok in h["set_scout"] if not ok]
    note = f"<div style='opacity:0.7;font-size:0.85rem;margin-top:6px'>Scout parziale: {', '.join(incompleti)}</div>" if incompleti else ""
    return (
        f"<div style='margin:4px 0 12px 0'>"
        f"<span style='{badge};font-size:1.1rem;background:{color_casa}'>{h['casa']}</span>"
        f"<span style='font-size:2rem;font-weight:800;margin:0 14px;vertical-align:middle'>"
        f"{h['sets_casa']} - {h['sets_ospite']}</span>"
        f"<span style='{badge};font-size:1.1rem;background:{color_ospite}'>{h['ospite']}</span>"
        f"<span style='margin-left:16px;opacity:0.75'>{h['data'] or ''}</span>"
        f"<div style='margin-top:10px'>{pills}</div>{note}</div>"
    )


def build_points_by_set_chart(view, as_percent):
    """
    Barre orizzontali impilate, un riquadro per set (e uno per la partita)
    con titolo e punteggio del set, e dentro le due barre delle squadre:
    segmenti = cause dei punti (come nell'app di rilevazione, ma con una sola
    causa per punto). I set separati in riquadri distinti: in un unico
    grafico con 12 barre di fila si faceva fatica a leggerli (feedback
    dell'utente, 2026-10-24).
    """
    noi_color, loro_color = _team_colors()
    pts = view["points"]
    h = view["header"]
    noi_casa = h["casa"] == view["nome_noi"]
    sets = [s_ for s_ in pts["set"].unique() if s_ != TOTAL_LABEL] + [TOTAL_LABEL]

    def score(label):
        if label == TOTAL_LABEL:
            a, b = h["sets_casa"], h["sets_ospite"]
        else:
            i = int(label) - 1
            a, b = h["parziali"][i] if i < len(h["parziali"]) else (None, None)
        if a is None:
            return ""
        noi_pts, loro_pts = (a, b) if noi_casa else (b, a)
        return f"{noi_pts}-{loro_pts}"

    scouted = {str(n): f"{a}-{b}" if noi_casa else f"{b}-{a}" for n, a, b, ok in h["set_scout"] if not ok}
    titles = [
        f"<b>{'Partita' if s_ == TOTAL_LABEL else 'Set ' + s_}</b>   {score(s_)}"
        + (f"   <i>(nello scout fino al {scouted[s_]})</i>" if s_ in scouted else "")
        for s_ in sets
    ]
    teams = (("noi", view["nome_noi"], noi_color), ("loro", view["nome_loro"], loro_color))
    return _points_panels_chart(pts, list(zip(sets, titles)), teams, as_percent)


def _points_panels_chart(pts, panels, teams, as_percent, unit="set", decimals=0):
    """Il grafico di build_points_by_set_chart per riquadri qualsiasi:
    `pts` [set, squadra, causa, n] con 'set' = etichetta del riquadro,
    `panels` [(etichetta, titolo)], `teams` [(chiave, nome, colore)];
    `decimals` > 0 per valori medi (punti per set)."""
    colors = POINT_SOURCE_COLORS_DARK if (st.context.theme.type or "light") == "dark" else POINT_SOURCE_COLORS_LIGHT
    sets = [label for label, _ in panels]
    titles = [title for _, title in panels]
    height = 140 + 105 * len(sets)
    # 34 px tra un riquadro e l'altro, per il titolo del successivo
    spacing = min(34 / (height - 95 - 80), 0.9 / max(len(sets) - 1, 1))
    fig = make_subplots(rows=len(sets), cols=1, shared_xaxes=True, vertical_spacing=spacing, subplot_titles=titles)
    y_text = [f"<span style='color:{color}'>■</span> <b>{name}</b>" for _, name, color in teams]
    shown = set()
    for row, label in enumerate(sets, 1):
        sub = pts[pts["set"] == label]
        totals = sub.groupby("squadra")["n"].sum()
        for causa in POINT_SOURCES:
            if pts[pts["causa"] == causa]["n"].sum() == 0:
                continue
            n = [round(float(sub[(sub["squadra"] == key) & (sub["causa"] == causa)]["n"].sum()), decimals)
                 for key, _, _ in teams]
            share = [100 * v / totals.get(key, 0) if totals.get(key, 0) else 0 for v, (key, _, _) in zip(n, teams)]
            fig.add_trace(go.Bar(
                y=y_text, x=n, orientation="h", name=causa, legendgroup=causa, showlegend=causa not in shown,
                marker_color=colors[causa], marker_line=dict(color=theme_colors()["surface"], width=1.5),
                text=[f"{v:.{decimals}f}" if v else "" for v in n], textposition="inside", insidetextanchor="middle",
                textfont=dict(color="#ffffff"), customdata=np.round(share, 0),
                hovertemplate=causa + ": %{x:." + str(decimals) + "f} punti (%{customdata:.0f}% del " + unit
                + ")<extra></extra>",
            ), row=row, col=1)
            shown.add(causa)
        fig.update_yaxes(autorange="reversed", row=row, col=1, gridcolor=theme_colors()["grid"])
    # titoli dei riquadri allineati a sinistra, sopra le barre
    for ann in fig.layout.annotations:
        ann.update(x=0, xanchor="left", font=dict(size=14, color=theme_colors()["ink"]))
    longest = max(len(name) for _, name, _ in teams) + 3
    layout = base_layout("", "", "")
    layout.update(
        barmode="stack", barnorm="percent" if as_percent else None, hovermode="closest",
        height=height, margin=dict(t=95, b=80, l=int(longest * 7.5) + 20),
        # legenda 40 px sopra l'area dei grafici, sopra il titolo del primo riquadro
        legend=dict(orientation="h", yanchor="bottom", y=1 + 40 / (height - 95 - 80), xanchor="left", x=0,
                    traceorder="normal"),
        bargap=0.25,
    )
    fig.update_layout(**layout)
    fig.update_xaxes(gridcolor=theme_colors()["grid"], ticksuffix="%" if as_percent else "")
    fig.update_xaxes(title_text=f"% dei punti del {unit}" if as_percent else "Punti", row=len(sets), col=1)
    return fig


def _set_trend_figure(view, series, is_pct, title, volume_word=None, show_totals=True):
    """
    Grafico set per set delle due squadre. `series`: [(nome, colore, valori
    per set, volumi per set, totale partita)]. Il totale della partita è una
    linea tratteggiata con l'etichetta in una fascia libera a destra
    dell'ultimo set (mai sopra i punti), distanziate se le linee sono vicine.
    Il valore di ogni set è scritto vicino al suo punto, nel colore della
    squadra: sopra per la più alta in quel set, sotto per l'altra.
    """
    sets, ticks = _set_axis(view)
    fig = go.Figure()
    tots = []
    for name, color, vals, vols, tot in series:
        fig.add_trace(go.Scatter(
            x=ticks, y=[np.nan if v is None else v for v in vals], name=name, mode="lines+markers",
            line=dict(color=color, width=3), marker=dict(size=10, line=dict(color=theme_colors()["surface"], width=2)),
            customdata=[f"{v:.0f}%" if is_pct and v is not None else (f"{v:.0f}" if v is not None else "-") for v in vals],
            text=[f" su {int(n)} {volume_word}" if volume_word and n else "" for n in vols],
            hovertemplate=name + ": %{customdata}%{text}<extra></extra>",
        ))
        if show_totals and tot is not None and not (isinstance(tot, float) and np.isnan(tot)):
            tots.append((tot, name, color))
    for i, tick in enumerate(ticks):
        pair = [(vals[i], color) for _, color, vals, _, _ in series if vals[i] is not None and not np.isnan(vals[i])]
        for k, (v, color) in enumerate(sorted(pair, key=lambda t: -t[0])):
            above = k == 0 or len(pair) == 1
            fig.add_annotation(x=tick, y=v, yshift=15 if above else -15, showarrow=False,
                               text=f"<b>{v:.0f}{'%' if is_pct else ''}</b>", font=dict(color=color, size=11),
                               bgcolor=hex_to_rgba(theme_colors()["surface"], 0.75))
    tots.sort(key=lambda t: t[0])
    all_vals = [v for tr in fig.data for v in tr.y if v is not None and not np.isnan(v)] + [t[0] for t in tots]
    span = max((max(all_vals) - min(all_vals)) if all_vals else 1, 1)
    ypos = [t[0] for t in tots]
    min_gap = span * 0.34  # due righe di testo per riquadro
    if len(ypos) == 2 and ypos[1] - ypos[0] < min_gap:
        mid = (ypos[0] + ypos[1]) / 2
        ypos = [mid - min_gap / 2, mid + min_gap / 2]
    for (tot, name, color), y in zip(tots, ypos):
        fig.add_hline(y=tot, line_dash="dash", line_color=color, line_width=1.5)
        fig.add_annotation(
            x=len(sets) - 1 + 0.3, y=y, xanchor="left", showarrow=False,
            text=f"<b>Tot {name}<br>{tot:.0f}{'%' if is_pct else ''}</b>", align="left",
            font=dict(color=theme_colors()["ink"], size=11), bgcolor=theme_colors()["surface"],
            bordercolor=color, borderwidth=1.5, borderpad=3,
        )
    fig.update_xaxes(range=[-0.3, len(sets) - 1 + 1.6])
    if all_vals:
        lo, hi = min(all_vals + ypos), max(all_vals + ypos)
        fig.update_yaxes(range=[lo - span * 0.3, hi + span * 0.3])
    layout = base_layout(f"<b>{title}</b>", "", "%" if is_pct else "")
    layout.update(height=330, margin=dict(t=80, r=20), hovermode="x unified")
    fig.update_layout(**layout)
    if is_pct:
        fig.update_yaxes(ticksuffix="%")
    return fig


def build_set_trend_chart(view, kpi):
    """Un KPI set per set, le due squadre, con il totale della partita."""
    sets, _ = _set_axis(view)
    noi_color, loro_color = _team_colors()
    volume = SET_KPI_VOLUME.get(kpi)
    series = []
    for key, name, color in (("noi", view["nome_noi"], noi_color), ("loro", view["nome_loro"], loro_color)):
        k = view["kpi"][key]
        series.append((
            name, color, [_kpi_value(k, "Squadra", s_, kpi) for s_ in sets],
            [_kpi_value(k, "Squadra", s_, volume) if volume else None for s_ in sets],
            _kpi_value(k, "Squadra", TOTAL_LABEL, kpi),
        ))
    title = SET_KPI_LABELS.get(kpi, kpi)
    is_count = kpi in ("Errori", "Punti")
    if is_count:
        title += "  —  totale partita: " + " · ".join(f"{name} {tot:.0f}" for name, _, _, _, tot in series)
    return _set_trend_figure(view, series, kpi in SET_KPI_PERCENT, title,
                             volume.lower() if volume else None, show_totals=not is_count)


def build_side_out_chart(view, rec_vote):
    """Efficienza del primo attacco dopo ricezione, per gli esiti di
    ricezione scelti, set per set: le due squadre."""
    sets, _ = _set_axis(view)
    noi_color, loro_color = _team_colors()
    series = []
    for key, name, color in (("noi", view["nome_noi"], noi_color), ("loro", view["nome_loro"], loro_color)):
        df = side_out_by_reception(view["dfs"][key], rec_vote).set_index("set")
        series.append((
            name, color, [df["eff"].get(s_) for s_ in sets], [df["n"].get(s_) for s_ in sets],
            df["eff"].get(TOTAL_LABEL),
        ))
    grades = ", ".join(RECEPTION_GRADES[v] for v in rec_vote)
    return _set_trend_figure(view, series, True, f"Primo attacco dopo ricezione {grades}", "attacchi")


def build_set_overview_chart(view, set_label, colors=None):
    """Tutti i KPI di efficienza di un set (o della partita), noi e loro
    affiancati (`colors`: (noi, loro), default blu/arancio)."""
    noi_color, loro_color = colors or _team_colors()
    labels = [SET_KPI_LABELS[k] for k in SET_KPI_PERCENT]
    fig = go.Figure()
    for key, name, color in (("noi", view["nome_noi"], noi_color), ("loro", view["nome_loro"], loro_color)):
        k = view["kpi"][key]
        vals = [_kpi_value(k, "Squadra", set_label, kpi) for kpi in SET_KPI_PERCENT]
        vols = [_kpi_value(k, "Squadra", set_label, SET_KPI_VOLUME[kpi]) for kpi in SET_KPI_PERCENT]
        fig.add_trace(go.Bar(
            x=labels, y=[np.nan if v is None else v for v in vals], name=name, marker_color=color,
            text=[_pct_text(v) for v in vals], textposition="outside",
            customdata=[f"su {int(n)} azioni" if n else "" for n in vols],
            hovertemplate=name + ": %{y:.0f}% %{customdata}<extra></extra>",
        ))
    layout = base_layout("", "", "Efficienza")
    layout.update(barmode="group", height=400, hovermode="x unified", bargap=0.25, margin=dict(t=60))
    fig.update_layout(**layout)
    fig.update_traces(cliponaxis=False)
    fig.update_yaxes(ticksuffix="%", zeroline=True, zerolinecolor=theme_colors()["ink"], zerolinewidth=1,
                     range=_padded_range(fig))
    return fig


def build_player_set_chart(view, player, kpis, counts=False):
    """Andamento set per set di più KPI per un giocatore: un colore per KPI."""
    sets, ticks = _set_axis(view)
    k = view["kpi"]["noi"]
    palette = _secondary_colors()
    master = SET_KPI_COUNTS if counts else SET_KPI_PERCENT
    fig = go.Figure()
    for kpi in kpis:
        i = master.index(kpi)
        color = palette[i % len(palette)]
        dash = "solid" if i < len(palette) else "dot"
        vals = [_kpi_value(k, player, s_, kpi) for s_ in sets]
        volume = SET_KPI_VOLUME.get(kpi)
        vols = [_kpi_value(k, player, s_, volume) if volume else None for s_ in sets]
        fig.add_trace(go.Scatter(
            x=ticks, y=[np.nan if v is None else v for v in vals], name=SET_KPI_LABELS[kpi], mode="lines+markers",
            line=dict(color=color, width=3, dash=dash), marker=dict(size=10, line=dict(color=theme_colors()["surface"], width=2)),
            connectgaps=False,
            customdata=[f" su {int(n)}" if volume and n else "" for n in vols],
            hovertemplate=SET_KPI_LABELS[kpi] + ": %{y:.0f}" + ("" if counts else "%") + "%{customdata}<extra></extra>",
        ))
    layout = base_layout("", "", "" if counts else "Efficienza")
    layout.update(height=340, hovermode="x unified", margin=dict(t=60))
    fig.update_layout(**layout)
    if not counts:
        fig.update_yaxes(ticksuffix="%")
    fig.update_yaxes(zeroline=True, zerolinecolor=theme_colors()["grid"])
    return fig


def build_setter_chart(view, setters, family):
    """Efficienza dell'attacco della squadra con il palleggiatore in campo,
    per set e sulla partita, per la famiglia scelta (R+#, R!, FB)."""
    palette = _secondary_colors()
    all_setters = list(view["setters"])
    fig = go.Figure()
    for name in setters:
        df = view["setters"][name]
        x = [f"Set {s_}" if s_ != TOTAL_LABEL else "Partita" for s_ in df["set"]]
        eff, n = df[f"{family} eff"], df[f"{family} n"]
        fig.add_trace(go.Bar(
            x=x, y=eff, name=name, marker_color=palette[all_setters.index(name) % len(palette)],
            text=[f"{e:.0f}% ({k})" if k else "" for e, k in zip(eff, n)], textposition="outside",
            customdata=n, hovertemplate=name + ": %{y:.0f}% su %{customdata} attacchi<extra></extra>",
        ))
    layout = base_layout("", "", "Efficienza d'attacco")
    layout.update(barmode="group", height=400, hovermode="x unified", margin=dict(t=60))
    fig.update_layout(**layout)
    fig.update_traces(cliponaxis=False)
    fig.update_yaxes(ticksuffix="%", zeroline=True, zerolinecolor=theme_colors()["ink"], zerolinewidth=1,
                     range=_padded_range(fig))
    return fig


# ---------------------------------------------------------------------------
# MVP della partita (src.mvp, criteri e valori in config/mvp.json)
# ---------------------------------------------------------------------------
def render_mvp_rules():
    """Le regole del punteggio MVP (stesse dell'appendice del report, dai
    valori di config/mvp.json)."""
    rules = scoring_rules()
    st.caption("Ogni azione vale quanto cambia la probabilità di vincere lo scambio (calcolata su tutte le partite "
               "rilevate); il punteggio è la somma delle azioni. Valori in config/mvp.json.")
    cols = st.columns(3)
    for i, (title, rows) in enumerate(rules["per_fondamentale"]):
        with cols[i % 3]:
            st.markdown(f"**{title}**")
            st.dataframe(pd.DataFrame({"Punti": ["0" if v == 0 else _signed(v) for v, _ in rows],
                                       "Azione": [d for _, d in rows]}).style.apply(
                lambda col: [f"color: {'#1a7f37' if str(x).startswith('+') else '#cf222e' if str(x).startswith('-') else '#8c959f'};"
                             " font-weight: bold" for x in col], subset=["Punti"]),
                hide_index=True, width="stretch")
    q, media, k = rules["quota"], rules["media"], rules["set_fittizi"]
    st.markdown(
        f"**Palleggiatore**: oltre alle sue azioni, {_signed(q)} per ogni punto e {_signed(-q)} per ogni errore o murata "
        f"dei suoi attaccanti (dopo ricezione #, + o ! e free ball), ma solo sopra o sotto il solito: in media un "
        f"attacco vale {media:.2f} di punti meno errori e murate.\n\n"
        f"**Chi gioca meno set**: un punteggio positivo diventa totale × (set della partita + {k}) / (set giocati + {k})."
    )


@st.cache_data(show_spinner="Punteggio MVP...")
def load_match_mvp(file_path, version):
    return match_mvp(load_report_source(file_path))


def season_mvp_inputs(matches, seqs, version):
    """[(match_seq, match_mvp)] delle partite .dvw tra `seqs` e i cognomi dei
    giocatori registrati (esclusi IGNORED e terne non gestite)."""
    per_match = [(m["match_seq"], load_match_mvp(m["file_path"], version)) for m in matches
                 if m["match_seq"] in seqs and str(m.get("file_path", "")).lower().endswith(".dvw")]
    players = sorted({c for m in matches for c in m["df"]["Cognome"].dropna().unique()})
    return per_match, players


def _signed(v, digits=2):
    return "" if v is None or pd.isna(v) else f"{v:+.{digits}f}".replace(".", ",")


def mvp_display_table(mvp):
    """La classifica della partita da mostrare: contributi per fondamentale,
    totale, set giocati e punteggio."""
    used = [f for f in FUNDAMENTALS if (mvp[f].abs() >= 0.005).any()]
    shown = pd.DataFrame({"": range(1, len(mvp) + 1), "Giocatore": mvp["giocatore"]})
    for f in used:
        shown[FUNDAMENTAL_LABELS[f]] = [_signed(v) for v in mvp[f]]
    shown["Totale"] = [_signed(v) for v in mvp["totale"]]
    shown["Set giocati"] = [f"{int(a)}/{int(b)}" for a, b in zip(mvp["set_giocati"], mvp["set_partita"])]
    shown["Punteggio"] = [_signed(v) for v in mvp["punteggio"]]
    return shown


def render_match_mvp(match, version):
    st.subheader("MVP della partita")
    mvp = load_match_mvp(match["file_path"], version)
    if mvp.empty:
        st.info("Nessuna azione rilevata per i nostri giocatori.")
        return
    best = mvp.iloc[0].to_dict()
    played = (f" in {int(best['set_giocati'])} set su {int(best['set_partita'])}"
              if best["set_giocati"] < best["set_partita"] else "")
    lines = "".join(f"<li><b>{label}</b> ({_signed(v)}): {text}</li>" for label, v, text in mvp_highlights(best))
    st.html(
        f"<div style='border-left: 6px solid #e5b80b; padding: 8px 14px; border-radius: 6px; "
        f"background: rgba(229,184,11,0.10)'>"
        f"<div style='font-size: 1.5rem; font-weight: 700'>🏆 {best['giocatore']} "
        f"<span style='font-weight: 400; font-size: 1.1rem'>{_signed(best['punteggio'])}{played}</span></div>"
        f"<ul style='margin: 6px 0 0 0'>{lines}</ul></div>"
    )
    st.dataframe(mvp_display_table(mvp), hide_index=True, width="stretch", height=35 * (len(mvp) + 1) + 3)
    with st.expander("Come si guadagnano e si perdono i punti MVP"):
        render_mvp_rules()



def render_match_section(season, matches, match_label_lookup, version):
    st.header("Partita")
    dvw_matches = sorted(
        (m for m in matches if str(m.get("file_path", "")).lower().endswith(".dvw")), key=lambda m: m["match_seq"],
    )
    if not dvw_matches:
        st.info("Questa sezione richiede lo scout in formato .dvw (azioni di entrambe le squadre).")
        return
    by_seq = {m["match_seq"]: m for m in dvw_matches}
    seq = st.selectbox(
        "Partita", list(by_seq), index=len(by_seq) - 1, key="partita_sel",
        format_func=lambda ms: _match_option_label(ms, match_label_lookup),
    )
    match = by_seq[seq]
    view = load_match_view(match["file_path"], match["official_result"], version)
    noi, loro = view["nome_noi"], view["nome_loro"]
    st.html(_match_header_html(view))

    render_match_mvp(match, version)

    # 1) punti set per set
    st.subheader("Da dove arrivano i punti, set per set")
    tot = view["points"][view["points"]["set"] == TOTAL_LABEL].groupby("squadra")["n"].sum()
    c1, c2, c3 = st.columns([1, 1, 2])
    c1.metric(f"Punti {noi}", int(tot.get("noi", 0)))
    c2.metric(f"Punti {loro}", int(tot.get("loro", 0)))
    with c3:
        as_percent = st.radio("Lunghezza delle barre", ["Punti", "% del set"], horizontal=True, key="partita_pts_mode") == "% del set"
    st.caption("Ogni punto ha una sola causa: l'azione vincente di chi l'ha fatto, altrimenti l'errore di chi l'ha perso "
               "(scambi rilevati nello scout). Passa il mouse su un segmento per i dettagli.")
    st.plotly_chart(build_points_by_set_chart(view, as_percent), theme=None)

    # 2) KPI lungo i set
    st.subheader("Noi e loro, KPI per KPI lungo i set")
    all_kpis = SET_KPI_PERCENT + ["Errori", "Punti", "Muri punto"]
    chosen = st.multiselect("KPI", all_kpis, default=SET_KPI_PERCENT, key="partita_kpi_trend",
                            format_func=lambda k: SET_KPI_LABELS[k])
    cols = st.columns(2)
    for i, kpi in enumerate(chosen):
        with cols[i % 2]:
            st.plotly_chart(build_set_trend_chart(view, kpi), theme=None)

    # 2a) i set rispetto alla nostra media
    render_sets_vs_baseline(season, matches, match, view, version)

    # 2b) primo attacco dopo ricezione, esiti a scelta
    st.subheader("Primo attacco dopo ricezione")
    rec_vote = st.multiselect(
        "Esiti della ricezione da considerare", list(RECEPTION_GRADES), default=["#", "+"], key="partita_so_grades",
        format_func=lambda v: RECEPTION_GRADES[v],
        help="Efficienza dell'attacco subito dopo una ricezione con uno di questi voti (al più con un'alzata di mezzo): "
             "(punti - errori - murati) / attacchi. Dopo una ricezione = (ace subito) non c'è attacco.",
    )
    if rec_vote:
        st.plotly_chart(build_side_out_chart(view, rec_vote), theme=None)
    else:
        st.info("Seleziona almeno un esito della ricezione.")

    # 3) tutti i KPI di un set
    st.subheader("Tutti i KPI di un set")
    sets, ticks = _set_axis(view)
    options = [TOTAL_LABEL] + sets
    set_label = st.radio(
        "Set", options, horizontal=True, key="partita_set_overview",
        format_func=lambda s_: "Partita intera" if s_ == TOTAL_LABEL else f"Set {s_}",
    )
    st.plotly_chart(build_set_overview_chart(view, set_label), theme=None)
    m1, m2, m3, m4 = st.columns(4)
    for col, key, name in ((m1, "noi", noi), (m2, "loro", loro)):
        col.metric(f"Errori {name}", int(_kpi_value(view["kpi"][key], "Squadra", set_label, "Errori")))
    pts_set = view["points"][view["points"]["set"] == set_label].groupby("squadra")["n"].sum()
    m3.metric(f"Punti {noi}", int(pts_set.get("noi", 0)))
    m4.metric(f"Punti {loro}", int(pts_set.get("loro", 0)))

    # 4) giocatori set per set
    st.subheader("Un giocatore, set per set")
    k = view["kpi"]["noi"]
    players = [e for e in k["entita"].unique() if e != "Squadra"]
    c1, c2 = st.columns([1, 2])
    with c1:
        player = st.selectbox("Giocatore", players, key="partita_player")
    played = [kpi for kpi in SET_KPI_PERCENT if _kpi_value(k, player, TOTAL_LABEL, kpi) is not None]
    with c2:
        kpis = st.multiselect("KPI", SET_KPI_PERCENT, default=played, key=f"partita_player_kpi_{player}",
                              format_func=lambda x: SET_KPI_LABELS[x])
    if kpis:
        st.plotly_chart(build_player_set_chart(view, player, kpis), theme=None)
    st.plotly_chart(build_player_set_chart(view, player, SET_KPI_COUNTS, counts=True), theme=None)
    table = (k[(k["entita"] == player) & k["kpi"].isin(SET_KPI_PERCENT + SET_KPI_COUNTS)]
             .pivot(index="kpi", columns="set", values="value")
             .reindex(index=SET_KPI_PERCENT + SET_KPI_COUNTS, columns=sets + [TOTAL_LABEL]))
    table = pd.DataFrame(
        [[_fmt_cell(v, kpi in SET_KPI_PERCENT) for v in row] for kpi, row in zip(table.index, table.to_numpy())],
        index=[SET_KPI_LABELS[i] for i in table.index],
        columns=[f"Set {c}" if c != TOTAL_LABEL else "Partita" for c in table.columns],
    )
    st.dataframe(table, width="stretch")

    # 5) palleggiatori
    if view["setters"]:
        st.subheader("Palleggiatori")
        c1, c2 = st.columns([1, 2])
        with c1:
            setters = st.multiselect("Palleggiatore in campo", list(view["setters"]), default=list(view["setters"]),
                                     key="partita_setters")
        with c2:
            family = st.radio("Attacco", list(SETTER_FAMILIES), horizontal=True, key="partita_setter_family",
                              format_func=lambda f: SETTER_FAMILIES[f])
        st.caption("Efficienza d'attacco della squadra nelle azioni con quel palleggiatore in campo "
                   "(punti - errori - murati) / attacchi; tra parentesi il numero di attacchi.")
        if setters:
            st.plotly_chart(build_setter_chart(view, setters, family), theme=None)

    # 6) tabellino
    st.subheader("Tabellino giocatori")
    tables = player_kpi_table(match["df"])
    history = _baseline_matches(season, matches, match, PERSONAL_HISTORY, True, version)
    baseline = personal_baseline([m["df"] for m in history])
    render_player_kpi_tables(tables, {n: kpi_deviations(n, t, baseline) for n, t in tables.items()}, len(history))
    with st.expander("Tabellino completo, una riga per giocatore"):
        render_player_table(view["players"])


# partite precedenti per le medie personali dei tabellini (report e tab Partita)
PERSONAL_HISTORY = 10


def _baseline_matches(season, matches, match, n, with_previous, version):
    """Le `n` partite giocate prima di `match`: quelle della stagione, dalla
    più recente, poi (se richiesto) quelle della stagione precedente."""
    before = sorted((m for m in matches if m["match_seq"] < match["match_seq"]), key=lambda m: -m["match_seq"])
    if with_previous:
        seasons = active_seasons()
        i = seasons.index(season) if season in seasons else -1
        if i > 0:
            prev = load_matches_cached(seasons[i - 1], version)
            before += sorted(prev, key=lambda m: -m["match_seq"])
    return before[:n]


def _set_columns(view):
    """Etichette dei set con punteggio ed esito, dal punto di vista nostro."""
    h = view["header"]
    noi_casa = h["casa"] == view["nome_noi"]
    labels, won = {}, {}
    for i, (a, b) in enumerate(h["parziali"], 1):
        noi, loro = (a, b) if noi_casa else (b, a)
        won[i] = noi > loro
        labels[i] = f"Set {i} · {noi}-{loro} {'vinto' if noi > loro else 'perso'}" + (" *" if i == 5 else "")
    return labels, won


def _fmt_set_value(v, is_pct):
    if v is None or pd.isna(v):
        return "-"
    return f"{v:.0f}%" if is_pct else (f"{v:.0f}" if float(v).is_integer() else f"{v:.1f}")


def build_sets_vs_baseline_chart(comp, labels):
    """Mappa KPI × set: colore = scostamento dalla media nel verso giusto
    (verde meglio, rosso peggio, in deviazioni standard), testo = valore e
    media, ★ dove il set è fuori dal 90% centrale dei set di riferimento."""
    kpis = list(dict.fromkeys(comp["label"]))
    sets = sorted(comp["set"].unique())
    verso = {label: v for _, label, _, v in SET_PROFILE_KPIS}
    z, text, hover = [], [], []
    for kpi in kpis:
        zr, tr, hr = [], [], []
        for s_ in sets:
            r = comp[(comp["label"] == kpi) & (comp["set"] == s_)].iloc[0]
            good = None if r["z"] is None or pd.isna(r["z"]) else max(-2.5, min(2.5, r["z"] * verso[kpi]))
            zr.append(good)
            star = " ★" if r["segnale"] else ""
            tr.append(f"<b>{_fmt_set_value(r['valore'], r['percentuale'])}{star}</b><br>"
                      f"<span style='font-size:10px'>media {_fmt_set_value(r['media'], r['percentuale'])}</span>")
            hr.append(f"{kpi}, {labels.get(s_, f'Set {s_}')}<br>valore {_fmt_set_value(r['valore'], r['percentuale'])}, "
                      f"media {_fmt_set_value(r['media'], r['percentuale'])} su {r['n_rif']} set<br>"
                      + (f"più alto del {r['percentile']:.0f}% dei set di riferimento, p = {r['p']:.2f}"
                         if r["percentile"] is not None and not pd.isna(r["percentile"]) else "nessun dato"))
        z.append(zr)
        text.append(tr)
        hover.append(hr)
    fig = go.Figure(go.Heatmap(
        z=z, x=[labels.get(s_, f"Set {s_}") for s_ in sets], y=kpis, text=text, texttemplate="%{text}",
        customdata=hover, hovertemplate="%{customdata}<extra></extra>", zmin=-2.5, zmax=2.5, xgap=3, ygap=3,
        colorscale=[[0, "#d9534f"], [0.5, "#f4f4f1"], [1, "#3fa34d"]], textfont=dict(color="#1f2328", size=12),
        colorbar=dict(title=dict(text="peggio / meglio<br>della media", side="right"), tickvals=[-2, 0, 2],
                      ticktext=["−2 dev. st.", "media", "+2 dev. st."]),
    ))
    layout = base_layout("", "", "")
    layout.update(height=90 + 46 * len(kpis), margin=dict(t=30, l=230, r=10, b=10))
    fig.update_layout(**layout)
    fig.update_yaxes(autorange="reversed", showgrid=False, ticks="", tickfont=dict(size=12))
    fig.update_xaxes(side="top", showgrid=False)
    return fig


def render_sets_vs_baseline(season, matches, match, view, version):
    st.subheader("I set rispetto alla nostra media")
    c1, c2 = st.columns([1, 2])
    n = c1.number_input("Partite precedenti per la media", 1, 60, 10, key="partita_base_n")
    with_prev = c2.checkbox("Comprendi la stagione precedente", value=True, key="partita_base_prev",
                            help="Se le partite di questa stagione prima di questa non bastano, si continua con "
                                 "le più recenti della stagione precedente.")
    base = _baseline_matches(season, matches, match, int(n), with_prev, version)
    if not base:
        st.info("Nessuna partita precedente da usare come riferimento.")
        return
    comp = compare_sets_to_baseline(match["df"], [m["df"] for m in base])
    labels, won = _set_columns(view)
    n_sets = int(comp["n_rif"].max()) if len(comp) else 0
    st.caption(
        f"Ogni set confrontato con i {n_sets} set delle {len(base)} partite precedenti (dalla più recente"
        f"{', comprese quelle della stagione precedente' if with_prev else ''}). Colore: quanto il set è sopra (verde) "
        "o sotto (rosso) la media, nel verso giusto (per gli errori meno è meglio), in deviazioni standard. ★ = "
        f"segnale: il set è fuori dal 90% centrale dei set di riferimento (p ≤ {SIGNAL_P:.2f}, servono almeno "
        f"{MIN_BASELINE_SETS} set). Conteggi per set; * = quinto set: valori reali, media e distribuzione di "
        "riferimento riportate a un set da 15 punti (× 15/25). "
        "Con 11 KPI per set qualche ★ "
        "può uscire anche per caso: leggili insieme all'esito del set."
    )
    st.plotly_chart(build_sets_vs_baseline_chart(comp, labels), theme=None, key="partita_sets_baseline")
    for s_ in sorted(comp["set"].unique()):
        sig = comp[(comp["set"] == s_) & comp["segnale"]]
        label = labels.get(s_, f"Set {s_}")
        if sig.empty:
            st.markdown(f"- **{label}**: nessun KPI fuori dal normale." if n_sets >= MIN_BASELINE_SETS else
                        f"- **{label}**: troppo pochi set di riferimento ({n_sets}, ne servono almeno "
                        f"{MIN_BASELINE_SETS}) per dire se qualcosa è fuori dal normale.")
            continue
        parts = []
        for r in sig.sort_values("p").itertuples():
            verso = "meglio" if r.buono > 0 else "peggio"
            parts.append(f"**{r.label}** {verso} del solito ({_fmt_set_value(r.valore, r.percentuale)} contro "
                         f"{_fmt_set_value(r.media, r.percentuale)} di media)")
        st.markdown(f"- **{label}**: " + "; ".join(parts) + ".")


def _deviation_css(z, scale=2.5):
    """Stessa scala del PDF: verde meglio della media personale, rosso peggio."""
    v = max(-1.0, min(1.0, z / scale)) * 0.6
    end = (0, 176, 80) if v >= 0 else (255, 0, 0)
    r, g, b = (round(255 + (e - 255) * abs(v)) for e in end)
    return f"background-color: rgb({r},{g},{b}); color: #000"


KPI_TABLE_HEADERS = {"tot": "tot", "pt": "pt", "err": "err", "mur": "mur", "pe": "p-e/m", "eff": "eff"}


def render_player_kpi_tables(tables, deviations, n_history):
    """Il tabellino per giocatore del report (src.set_analysis.player_kpi_table),
    tre per riga, con le celle colorate rispetto alla media personale."""
    st.caption(
        f"Colori: confronto con la media del giocatore nelle {n_history} partite precedenti (comprese quelle della "
        f"stagione precedente se servono), verde meglio del solito, rosso peggio, più intenso quanto più si discosta; "
        f"bianco = in media o meno di {MIN_HISTORY} partite con quel dato. err = errori (in attacco solo gli attacchi "
        "sbagliati), mur = murate, p-e/m = punti − errori − murate, eff = efficienza. Attacco SO = dopo ricezione, "
        "BP = tutti gli altri (free ball compresa)."
    )
    cols = st.columns(3)
    for i, (name, table) in enumerate(tables.items()):
        shown = pd.DataFrame({
            "": [("  " + r["label"]) if r["kpi"] == "fb" else r["label"] for r in table.to_dict("records")],
            **{h: [_fmt_cell(v, c == "eff") if not (c == "pe" and v is not None and not pd.isna(v))
                   else f"{int(v):+d}" for v in table.get(c, pd.Series([None] * len(table)))]
               for c, h in KPI_TABLE_HEADERS.items()},
        })
        devs = deviations.get(name, {})

        def style(_, devs=devs):
            css = pd.DataFrame("", index=shown.index, columns=shown.columns)
            for (row, col), z in devs.items():
                css.iloc[row, list(KPI_TABLE_HEADERS).index(col) + 1] = _deviation_css(z)
            css.iloc[-1] = [x + "; font-weight: bold" for x in css.iloc[-1]]
            return css

        with cols[i % 3]:
            st.markdown(f"**{name}**")
            st.dataframe(shown.style.apply(style, axis=None), hide_index=True, width="stretch",
                         height=35 * (len(shown) + 1) + 3)


def render_player_table(players):
    """Il tabellino giocatori di src.match_report.player_table, con gli errori
    divisi per fondamentale."""
    names = {
        "giocatore": "Giocatore", "pt": "Punti", "err": "Errori", "err_bat": "di cui battuta",
        "err_att": "di cui attacco", "err_alz": "di cui alzata", "err_muro": "di cui muro", "p-e": "p-e/m",
        "bat_tot": "Battute", "bat_err": "Batt. err", "bat_ace": "Ace", "bat_eff": "Battuta eff",
        "ric_tot": "Ricezioni", "ric_err": "Ric. err", "ric_eff": "Ricezione eff", "att_tot": "Attacchi",
        "att_err": "Att. err", "att_mur": "Murati", "att_pt": "Att. punti", "att_eff": "Attacco eff",
        "so_eff": "Side-out eff", "so_tot": "Side-out n", "contr_eff": "Contrattacco eff",
        "contr_tot": "Contrattacco n", "fb_eff": "Free ball eff", "fb_tot": "Free ball n", "muro_pt": "Muri punto",
    }
    tab = players[[c for c in names if c in players.columns]].rename(columns=names)
    pct_cols = ["Battuta eff", "Ricezione eff", "Attacco eff", "Side-out eff", "Contrattacco eff", "Free ball eff"]
    for c in tab.columns:
        if c != "Giocatore":
            tab[c] = [_fmt_cell(v, c in pct_cols) for v in tab[c]]
    st.dataframe(tab, hide_index=True, width="stretch", height=35 * (len(tab) + 1) + 3)
    st.caption("Errori = battute, attacchi e alzate sbagliate ed errori di muro (gli attacchi murati sono in \"Murati\", "
               "gli ace subiti in \"Ric. err\"), divisi per fondamentale. p-e/m = punti − errori − murate.")


def render_match_report_section(season, matches, match_label_lookup, version):
    st.header("Report partita")
    st.caption(
        "Il PDF da mandare ai giocatori dopo la partita, impaginato per il telefono: risultato, il tuo "
        "commento, l'MVP della partita, da dove arrivano i punti (nostri e loro), noi e loro set per set, tabellino giocatori, "
        "palleggiatori e classifiche della stagione aggiornate a questa partita (con le soglie e le esclusioni "
        "impostate nel tab Classifiche)."
    )
    dvw_matches = sorted(
        (m for m in matches if str(m.get("file_path", "")).lower().endswith(".dvw")), key=lambda m: m["match_seq"],
    )
    if not dvw_matches:
        st.info("Il report richiede lo scout in formato .dvw (azioni di entrambe le squadre): nessuna partita disponibile.")
        return
    by_seq = {m["match_seq"]: m for m in dvw_matches}
    seq = st.selectbox(
        "Partita", list(by_seq), index=len(by_seq) - 1,
        format_func=lambda ms: _match_option_label(ms, match_label_lookup), key="report_partita",
    )
    comment = st.text_area(
        "Commento (in apertura del report)", key=f"report_commento_{seq}", height=130,
        placeholder="Cosa è andato bene, cosa no, su cosa lavorare in settimana...",
    )
    if st.button("📄 Genera PDF", key="report_genera"):
        match = by_seq[seq]
        with st.spinner("Generazione del report..."):
            source = load_report_source(match["file_path"])
            season_df = pd.concat([m["df"] for m in matches if m["match_seq"] <= seq], ignore_index=True)
            cognomi = set(season_df["Cognome"])
            ranking = load_ranking_settings(season, cognomi)
            header = match_header(source, match["official_result"])
            tables = player_kpi_table(source["noi"])
            history = _baseline_matches(season, matches, match, PERSONAL_HISTORY, True, version)
            baseline = personal_baseline([m["df"] for m in history])
            deviations = {name: kpi_deviations(name, t, baseline) for name, t in tables.items()}
            mvp = load_match_mvp(match["file_path"], version)
            mvp_row = mvp.iloc[0].to_dict() if not mvp.empty else None
            rankings = build_rankings(season_df, ranking["ignore"], ranking["min_attacchi"], ranking["min_ricezioni"])
            per_match, mvp_players = season_mvp_inputs(matches, {m["match_seq"] for m in matches if m["match_seq"] <= seq},
                                                       version)
            mvp_season = season_mvp(per_match, mvp_players)
            if not mvp_season.empty:
                mvp_season["Punti"] = [_signed(v) for v in mvp_season["Punti MVP"]]
                rankings.append(("MVP della stagione", mvp_season[["Pos", "Giocatore", "Punti", "MVP"]]))
            pdf = build_match_report_pdf(
                header, comment, point_sources(source), point_sources_by_set(source), team_comparison(source),
                tables,
                setter_table(source),
                rankings,
                source["nome_noi"], source["nome_loro"],
                n_partite_stagione=sum(1 for m in matches if m["match_seq"] <= seq),
                ranking_mins=(ranking["min_attacchi"], ranking["min_ricezioni"]), deviations=deviations,
                distributions=distributions_by_setter(attack_rows(source["parsed"], source["side"])),
                mvp=(mvp_row, mvp_highlights(mvp_row)) if mvp_row else None,
            )
            name = f"Report {header['casa']} - {header['ospite']} {header['data'] or ''}".strip().replace("/", "-")
            st.session_state["report_pdf"] = (seq, name, pdf)
    stored = st.session_state.get("report_pdf")
    if stored and stored[0] == seq:
        st.download_button(
            "⬇️ Scarica PDF", data=stored[2], file_name=f"{stored[1]}.pdf", mime="application/pdf",
            key="report_scarica",
        )
        st.caption("Se cambi il commento, premi di nuovo \"Genera PDF\" prima di scaricarlo.")


def render_classifiche_section(season, matches, match_label_lookup, version):
    st.header("Classifiche")
    seqs = match_picker(sorted(m["match_seq"] for m in matches), match_label_lookup, key=f"classifiche_partite_{season}")
    if not seqs:
        st.info("Seleziona almeno una partita.")
        return
    season_df = pd.concat([m["df"] for m in matches if m["match_seq"] in seqs], ignore_index=True)
    st.caption(f"Sulle {len(seqs)} partite selezionate — stesse regole del PDF di classifiche.ipynb.")

    cognomi = sorted(season_df["Cognome"].unique())
    saved = load_ranking_settings(season, cognomi)
    with st.expander("Impostazioni classifiche (soglie minime ed esclusioni)"):
        c1, c2 = st.columns(2)
        min_attacchi = c1.number_input("Min. attacchi (Spike Guarantee)", 0, value=saved["min_attacchi"], step=1,
                                       key=f"rank_min_att_{season}")
        min_ricezioni = c2.number_input("Min. ricezioni (Miglior ricevitore)", 0, value=saved["min_ricezioni"], step=1,
                                        key=f"rank_min_ric_{season}")
        st.caption(
            "Le soglie sono sul totale delle partite selezionate: a inizio stagione abbassale, "
            "altrimenti Spike Guarantee e Miglior ricevitore restano quasi vuote. Ace Man: entra chi ha fatto "
            "almeno un ace; Muro della vergogna: chi ha sbagliato almeno una battuta. Soglie ed esclusioni sono "
            "salvate per la stagione (config/ranking_settings.json) e valgono anche per il report partita."
        )
        ignore = {}
        cols = st.columns(len(RANKING_DEFAULT_IGNORE))
        for col, tipo in zip(cols, RANKING_DEFAULT_IGNORE):
            ignore[tipo] = col.multiselect(
                f"Esclusi da {tipo}", cognomi, default=saved["ignore"][tipo], key=f"ignore_{tipo}_{season}",
            )
    save_ranking_settings(season, {"min_attacchi": int(min_attacchi), "min_ricezioni": int(min_ricezioni),
                                   "ignore": ignore})

    tables = build_rankings(season_df, ignore, min_attacchi, min_ricezioni)
    for row_start in range(0, len(tables), 3):
        cols = st.columns(3)
        for col, (title, table) in zip(cols, tables[row_start:row_start + 3]):
            with col:
                st.subheader(title)
                if table.empty:
                    st.caption("Nessun giocatore sopra la soglia minima.")
                else:
                    st.dataframe(table, hide_index=True, width="stretch")

    render_mvp_ranking(matches, seqs, version)


def mvp_ranking_display(table):
    shown = table.copy()
    shown["Punti MVP"] = [_signed(v) for v in shown["Punti MVP"]]
    shown["Media"] = [_signed(v) for v in shown["Media"]]
    return shown


def render_mvp_ranking(matches, seqs, version):
    st.subheader("MVP della stagione")
    per_match, players = season_mvp_inputs(matches, seqs, version)
    if not per_match:
        st.caption("Serve lo scout .dvw: nessuna delle partite selezionate lo ha.")
        return
    table = season_mvp(per_match, players)
    st.dataframe(mvp_ranking_display(table), hide_index=True, width="stretch")
    st.caption(f"Somma dei punteggi MVP nelle {len(per_match)} partite selezionate con lo scout .dvw (Media = per "
               "partita giocata, MVP = partite in cui è stato il migliore). Come si calcola il punteggio: tab Partita.")


# ---------------------------------------------------------------------------
# Tabellino set per set (src.efficiency.compute_set_tabellino)
# ---------------------------------------------------------------------------
def _eff_background(value):
    """Stessa scala a 3 colori dell'export Excel: -100% rosso, 0 bianco, +100% verde."""
    v = max(-1.0, min(1.0, value))
    red, white, green = (0xFF, 0x00, 0x00), (0xFF, 0xFF, 0xFF), (0x00, 0xB0, 0x50)
    end = green if v >= 0 else red
    t = abs(v)
    r, g, b = (round(w + (e - w) * t) for w, e in zip(white, end))
    return f"background-color: rgb({r},{g},{b}); color: #000"


SET_SEPARATOR = "3px solid #5b6b82"
SET_BAND = "rgba(91, 107, 130, 0.09)"  # fondo dei set alterni


def _set_tabellino_html(title, table):
    """Una sezione del tabellino set per set (src.efficiency.compute_set_tabellino):
    colonne per set (o partita) e totale, Eff colorata come nell'Excel. Set
    separati da una linea spessa e a fondo alterno, il totale in grassetto."""
    def _fmt(v, measure):
        if pd.isna(v):
            return "-"
        return f"{v:.0f}%" if measure == "Eff" else f"{int(v)}"

    display = table.copy().astype(object)
    for col in table.columns:
        display[col] = [_fmt(v, col[1]) for v in table[col]]
    groups = list(dict.fromkeys(c[0] for c in table.columns))
    starts = [i for i, c in enumerate(table.columns) if i and c[0] != table.columns[i - 1][0]]

    def _style(_):
        styles = pd.DataFrame("", index=table.index, columns=table.columns)
        for i, col in enumerate(table.columns):
            band = groups.index(col[0]) % 2 == 1
            cells = [_eff_background(v / 100) if col[1] == "Eff" and pd.notna(v)
                     else (f"background-color: {SET_BAND}" if band else "") for v in table[col]]
            if i in starts:
                cells = [x + f"; border-left: {SET_SEPARATOR}" for x in cells]
            if col[0] == SET_TABELLINO_TOTAL:
                cells = [x + "; font-weight: bold" for x in cells]
            styles[col] = cells
        styles.loc["Totale"] = [x + "; font-weight: bold; border-top: 2px solid currentColor" for x in styles.loc["Totale"]]
        return styles

    header_styles = [{"selector": f"th.col_heading.col{i}", "props": f"border-left: {SET_SEPARATOR}"} for i in starts]
    header_styles += [{"selector": "th.col_heading.level0", "props": "font-size: 0.85rem;"}]
    styler = (
        display.style.apply(_style, axis=None)
        .set_table_styles([
            {"selector": "", "props": "border-collapse: collapse; font-size: 0.8rem;"},
            {"selector": "th, td", "props": "border: 1px solid rgba(128,128,128,0.4); padding: 2px 6px; text-align: center;"},
            {"selector": "th.row_heading", "props": "text-align: left; white-space: nowrap;"},
        ] + header_styles)
    )
    return f"<h4>{title}</h4><div style='overflow-x: auto'>{styler.to_html()}</div>"


def render_tabellino_section(matches, match_label_lookup):
    st.header("Tabellino")
    by_seq = {m["match_seq"]: m for m in matches}
    all_seqs = sorted(by_seq)
    seqs = match_picker(all_seqs, match_label_lookup, key="tabellino_partite", default=all_seqs[-1:],
                        help="Una partita: colonne per set. Più partite: colonne per partita, con il totale.")
    if not seqs:
        st.info("Seleziona almeno una partita.")
        return
    # ignore_index: le funzioni di separazione side-out/contrattacco indicizzano
    # per etichetta, servono indici univoci anche concatenando più partite.
    # Una partita: una colonna per set; più partite: una colonna per partita.
    df = pd.concat([by_seq[ms]["df"].assign(_gruppo=(by_seq[ms]["df"]["Numero Set"].map(lambda n: f"Set {int(n)}")
                                                     if len(seqs) == 1 else match_label_lookup[ms]))
                    for ms in seqs], ignore_index=True)

    _, _, sections = compute_set_tabellino(df, group_col="_gruppo")
    st.caption(
        ("Set per set e sul totale della partita. " if len(seqs) == 1 else "Partita per partita e sul totale. ")
        + "Tot = azioni, Eff = efficienza sul totale: ricezione (# + +) − (= + /); attacco # − (= + /). Battuta: "
        "totale, errori e ace; muro: muri punto; errori divisi per fondamentale: battuta, attacco, alzata e muro (/), "
        "e totale. R#+ / R! / R−: attacco dopo ricezione con quel voto; contrattacco con le free ball."
    )
    for title, table in sections:
        st.html(_set_tabellino_html(title, table))

    buffer = io.BytesIO()
    export_set_tabellino_to_xlsx(sections, buffer)
    name = match_label_lookup[seqs[0]] if len(seqs) == 1 else f"{len(seqs)} partite"
    st.download_button(
        "⬇️ Scarica tabellino Excel", data=buffer.getvalue(),
        file_name=f"tabellino {name}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ---------------------------------------------------------------------------
# Distribuzione del palleggiatore (src.setter_distribution) e studio
# dell'avversario
# ---------------------------------------------------------------------------
ROTATIONS = [f"P{i}" for i in range(1, 7)]
BALL_TYPE_ORDER = ["Alta", "Super", "Quick", "7", "Mezza", "Fast", "Altro"]


def _rounded_rect_path(x0, y0, x1, y1, r):
    return (f"M {x0 + r},{y0} L {x1 - r},{y0} Q {x1},{y0} {x1},{y0 + r} L {x1},{y1 - r} Q {x1},{y1} {x1 - r},{y1} "
            f"L {x0 + r},{y1} Q {x0},{y1} {x0},{y1 - r} L {x0},{y0 + r} Q {x0},{y0} {x0 + r},{y0} Z")


def build_court_chart(stats, title="", compact=False, height=None):
    """
    Metà campo vista da chi attacca, rete in alto (banda scura con le
    antenne), linea dei 3 metri, 9 zone (src.setter_distribution.ZONE_GRID).
    In ogni zona: quota delle alzate (colore e numero grande), attacchi ed
    efficienza, giocatori serviti; tutti i dettagli nel tooltip.
    """
    is_dark = (st.context.theme.type or "light") == "dark"
    colors = theme_colors()
    by_zone = stats.set_index("zona")
    shapes = [dict(type="rect", x0=-0.35, y0=-0.35, x1=9.35, y1=9.1, fillcolor=FREE_ZONE_DARK if is_dark else FREE_ZONE,
                   line=dict(width=0), layer="below"),
              dict(type="rect", x0=0, y0=0, x1=9, y1=9, fillcolor=COURT_FLOOR_DARK if is_dark else COURT_FLOOR,
                   line=dict(width=0), layer="below")]
    annotations, hx, hy, htext = [], [], [], []
    size_pct, size_detail, size_players = (19, 10, 9) if compact else (30, 12.5, 11.5)
    for r, zone_row in enumerate(ZONE_GRID):
        for c, z in enumerate(zone_row):
            x0, y1 = 3 * c, 9 - 3 * r
            x1, y0 = x0 + 3, y1 - 3
            cx, cy = x0 + 1.5, y0 + 1.5
            s = by_zone.loc[z]
            ink = colors["ink"] if is_dark else CELL_INK
            if s["n"]:
                fill, white = share_color(s["pct"])
                ink = "#ffffff" if white else CELL_INK
                shapes.append(dict(type="path", path=_rounded_rect_path(x0 + 0.13, y0 + 0.13, x1 - 0.13, y1 - 0.13, 0.32),
                                   fillcolor=fill, line=dict(width=0)))
                eff = "-" if s["eff"] is None else f"{s['eff']:.0f}%"
                best = s["best"]
                annotations += [
                    dict(x=cx, y=cy + 0.5, text=f"<b>{s['pct']:.0f}%</b>", font=dict(size=size_pct, color=ink)),
                    dict(x=cx, y=cy - 0.35, text=f"{int(s['n'])} att. · eff {eff}", font=dict(size=size_detail, color=ink)),
                    dict(x=cx, y=cy - 0.9, text=f"🏆 {short_player(best[0])} p-e/m {best[1]:+d}" if best else "",
                         font=dict(size=size_players, color=ink)),
                ]
                pt_pct = "-" if s["pt_pct"] is None else f"{s['pt_pct']:.0f}%"
                lines = [f"<b>Zona {z}</b>: {int(s['n'])} attacchi, {s['pct']:.0f}% delle alzate",
                         f"punti {s['pt']} ({pt_pct}) · errori {s['err']} · murati {s['mur']}",
                         f"efficienza {eff}", "", "🏆 = miglior p-e/m (punti − errori − murate) nella zona, se maggiore di 2", ""]
                lines += [f"{p}: {k} attacchi, {pt} punti, p-e/m {pe:+d}" for p, k, pt, pe in s["giocatori"]]
                hx.append(cx)
                hy.append(cy)
                htext.append("<br>".join(lines))
            else:
                annotations.append(dict(x=cx, y=cy, text="–", font=dict(size=size_detail + 4, color=colors["ink"]), opacity=0.35))
            annotations.append(dict(x=x0 + 0.3, y=y1 - 0.32, text=f"<b>{z}</b>", font=dict(size=size_detail, color=ink),
                                    opacity=0.6))
    # righe del campo sopra le celle: linee divisorie delle zone (tratteggiate,
    # non sono linee vere), linea dei 3 metri e perimetro
    for x in (3, 6):
        shapes.append(dict(type="line", x0=x, y0=0, x1=x, y1=9, line=dict(color="#ffffff", width=1, dash="dot")))
    shapes.append(dict(type="line", x0=0, y0=3, x1=9, y1=3, line=dict(color="#ffffff", width=1, dash="dot")))
    shapes.append(dict(type="line", x0=0, y0=6, x1=9, y1=6, line=dict(color="#ffffff", width=3.5)))
    shapes.append(dict(type="rect", x0=0, y0=0, x1=9, y1=9, line=dict(color="#ffffff", width=3.5)))
    # rete: banda scura con le maglie, nastro bianco in alto, antenne a strisce
    shapes.append(dict(type="rect", x0=-0.35, y0=9.1, x1=9.35, y1=9.75, fillcolor=NET_COLOR, line=dict(width=0)))
    shapes += [dict(type="line", x0=x, y0=9.12, x1=x, y1=9.64, line=dict(color=NET_MESH, width=1))
               for x in np.arange(-0.2, 9.3, 0.25)]
    shapes.append(dict(type="rect", x0=-0.35, y0=9.64, x1=9.35, y1=9.78, fillcolor="#ffffff", line=dict(width=0)))
    for xa in (0, 9):
        shapes += [dict(type="rect", x0=xa - 0.06, x1=xa + 0.06, y0=8.95 + 0.22 * k, y1=8.95 + 0.22 * (k + 1),
                        fillcolor=ANTENNA_RED if k % 2 else "#ffffff", line=dict(color=ANTENNA_RED, width=0.5))
                   for k in range(7)]
    annotations.append(dict(x=4.5, y=9.4, text="<b>R E T E</b>", font=dict(size=11 if compact else 13, color="#ffffff"),
                            bgcolor=NET_COLOR, borderpad=2))
    if not compact:
        annotations.append(dict(x=9.4, y=6, text="3 m", xanchor="left", font=dict(size=10, color=colors["ink"]), opacity=0.6))
    for a in annotations:
        a.setdefault("showarrow", False)

    fig = go.Figure(go.Scatter(
        x=hx, y=hy, mode="markers", text=htext, hovertemplate="%{text}<extra></extra>",
        marker=dict(symbol="square", size=70 if not compact else 42, color="rgba(0,0,0,0)"),
    ))
    fig.update_layout(
        shapes=shapes, annotations=annotations, showlegend=False,
        title=dict(text=f"<b>{title}</b>" if title else "", x=0, xanchor="left", font=dict(size=13 if compact else 15)),
        xaxis=dict(visible=False, range=[-0.45, 9.75 if not compact else 9.45], fixedrange=True),
        yaxis=dict(visible=False, range=[-0.25, 10.3], scaleanchor="x", fixedrange=True),
        plot_bgcolor=colors["surface"], paper_bgcolor=colors["surface"],
        font=dict(family="system-ui, -apple-system, 'Segoe UI', sans-serif", color=colors["ink"]),
        height=height or (430 if compact else 640), margin=dict(t=40 if title else 10, b=10, l=10, r=10),
        hoverlabel=dict(align="left"),
    )
    return fig


def _eff_text(sub):
    n = len(sub)
    if not n:
        return "-"
    return f"{100 * ((sub['voto'] == '#').sum() - sub['voto'].isin(['=', '/']).sum()) / n:.0f}%"


def _attackers_table(rows):
    """Chi riceve le alzate: attacchi, quota, zone, punti ed efficienza."""
    out = []
    for name, sub in rows.groupby("attaccante"):
        zones = sub["zona"].value_counts()
        out.append({
            "Giocatore": name, "Attacchi": len(sub), "% alzate": f"{100 * len(sub) / len(rows):.0f}%",
            "Zone": " · ".join(f"{int(z)}: {k}" for z, k in zones.items()),
            "Punti": f"{100 * (sub['voto'] == '#').mean():.0f}%", "Eff": _eff_text(sub),
        })
    return pd.DataFrame(out).sort_values("Attacchi", ascending=False)


def _category_label(direction, cat):
    if direction == "tipo_ricevitore":
        return f"Riceve: {str(cat).lower()}"
    if direction == "ricezione":
        return f"Ricezione {cat}"
    if direction == "ricevitore":
        return f"Riceve {short_player(cat)}"
    return str(cat)


def _deviation_text(direction, d):
    where = {"rotazione": f"in {d['categoria']}", "ricezione": f"dopo una ricezione {d['categoria']}",
             "ricevitore": f"quando riceve {d['categoria']}",
             "tipo_ricevitore": f"quando riceve il {str(d['categoria']).lower()}",
             "situazione_punteggio": f"{str(d['categoria']).lower()}", "partita": f"in {d['categoria']}"}[direction]
    verso = "più" if d["residuo"] > 0 else "meno"
    return (f"{where}: **{verso}** palloni in **zona {d['zona']}**, {d['pct']:.0f}% delle alzate "
            f"(contro il {d['pct_totale']:.0f}% sul totale)")


@st.cache_data(show_spinner="Test sul palleggiatore...")
def cached_setter_tests(rows):
    return test_setter_situations(rows)


def _court_block(sub, key, empty_text):
    """Metriche, campo e tabella di chi attacca per un insieme di attacchi."""
    if sub.empty:
        st.info(empty_text)
        return
    m1, m2, m3 = st.columns(3)
    m1.metric("Attacchi", len(sub))
    m2.metric("Efficienza", _eff_text(sub))
    m3.metric("Punti", f"{100 * (sub['voto'] == '#').mean():.0f}%")
    st.plotly_chart(build_court_chart(zone_stats(sub), height=520), theme=None, key=f"{key}_court")
    with st.expander("Chi attacca"):
        st.dataframe(_attackers_table(sub), hide_index=True, width="stretch")


def render_setter_situation_tests(rows, key):
    """Per palleggiatore: da R+/#, da R! e da free ball la distribuzione è
    diversa dal resto dei suoi palloni? (src.setter_distribution.test_setter_situations)"""
    results = cached_setter_tests(rows)
    setters = sorted({s for s, _ in results})
    if not setters:
        st.markdown("Nessun palleggiatore identificato.")
        return
    for setter in setters:
        lines = []
        for k in SITUATIONS:
            res = results[(setter, k)]
            if res is None:
                lines.append(f"- {SITUATIONS[k]}: dati insufficienti")
            elif res["significativo"]:
                devs = "; ".join(
                    f"{'più' if d['residuo'] > 0 else 'meno'} in zona {d['zona']} ({d['pct']:.0f}% contro "
                    f"{d['pct_totale']:.0f}%)" for d in res["scostamenti"][:3])
                lines.append(f"- {SITUATIONS[k]} ({res['n_situazione']} att.): **sì**, p = {res['p_holm']:.3f} — {devs}")
            else:
                lines.append(f"- {SITUATIONS[k]} ({res['n_situazione']} att.): no, p = {res['p_holm']:.3f}")
        st.markdown(f"**{setter}**\n" + "\n".join(lines))
    st.caption(
        "Per ogni palleggiatore e situazione: i palloni di quella situazione vanno in zone diverse dal resto dei suoi "
        "palloni (con lui in campo)? Tra parentesi la quota nella situazione contro quella nel resto. Test chi quadro "
        f"con p-value per permutazione, Holm su tutti i test insieme; servono almeno {MIN_PER_CATEGORY} attacchi nella "
        f"situazione e {MIN_TOTAL} in tutto."
    )


@st.cache_data(show_spinner="Analisi per ricevitore...")
def cached_positive_by_receiver(rows):
    return positive_reception_by_receiver(rows)


def render_positive_reception_by_receiver(rows, key):
    """Solo side-out da ricezione + o # (insieme): un campo per libero,
    martello in prima linea e martello in seconda linea, test e quanto si
    serve il martello di prima linea quando riceve lui
    (src.setter_distribution.positive_reception_by_receiver)."""
    res = cached_positive_by_receiver(rows)
    cols = st.columns(3)
    for i, (tipo, d) in enumerate(res["per_tipo"].items()):
        with cols[i]:
            if d["n"]:
                eff = "-" if d["eff"] is None else f"{d['eff']:.0f}%"
                st.plotly_chart(build_court_chart(d["stats"], f"Riceve: {tipo.lower()} · {d['n']} att. · efficienza totale {eff}",
                                                  compact=True), theme=None, key=f"{key}_pos_rec_{i}")
            else:
                st.markdown(f"**Riceve: {tipo.lower()}** — nessun attacco.")
    test = res["test"]
    if test is None:
        st.markdown(f"**Test** — dati insufficienti (servono almeno due ricevitori con {MIN_PER_CATEGORY} attacchi e "
                    f"{MIN_TOTAL} attacchi in tutto).")
    elif test["p"] < ALPHA:
        st.markdown(f"**Sì, la distribuzione cambia a seconda di chi riceve** (p = {test['p']:.3f}, intensità "
                    f"{test['cramer_v']:.2f} su 1).")
        for dev in test["scostamenti"][:4]:
            st.markdown(f"- {_deviation_text('tipo_ricevitore', dev).replace('attesa a parità di ricezione', 'sul totale')}")
    else:
        st.markdown(f"**No, la distribuzione non cambia in modo significativo a seconda di chi riceve** "
                    f"(p = {test['p']:.3f}).")
    front = res["martello_davanti"]
    if front:
        (a_self, n_self), (a_other, n_other) = front["riceve_lui"], front["riceve_altro"]
        verdict = "differenza significativa" if front["p"] < ALPHA else "differenza compatibile con il caso"
        st.markdown(f"**Il martello di prima linea quando riceve lui** attacca {a_self} palloni su {n_self} "
                    f"(**{100 * a_self / n_self:.0f}%**); quando riceve un altro (libero o martello di seconda linea) "
                    f"{a_other} su {n_other} (**{100 * a_other / n_other:.0f}%**): {verdict} (p = {front['p']:.3f}).")
    else:
        st.markdown(f"**Il martello di prima linea quando riceve lui** — dati insufficienti (servono almeno "
                    f"{MIN_PER_CATEGORY} attacchi sia quando riceve lui sia quando riceve un altro).")
    st.caption(
        "Solo il primo attacco dopo una ricezione + o # (insieme), con i filtri in alto (palleggiatore, rotazione, "
        "tipo di alzata). Chi riceve: ruolo registrato nello scout e linea dalla posizione di rotazione al momento della "
        "ricezione; opposti e centrali in ricezione esclusi. Test chi quadro zona × ricevitore con p-value per "
        "permutazione, su questa sola domanda (nessuna correzione per confronti multipli). Martello di prima linea: lo "
        "schiacciatore in prima linea al momento della ricezione; il confronto è tra gli scambi in cui riceve lui e "
        "quelli in cui riceve un altro, con lui comunque in prima linea."
    )


def render_distribution(rows, key):
    """Filtri, i due campi (side-out e contrattacco), chi attacca, confronto
    lungo una direzione e test di significatività. `rows`:
    src.setter_distribution.attack_rows."""
    st.caption(
        "Da dove attaccano i giocatori serviti dal palleggiatore, escluse le sue seconde intenzioni. Centrali sempre "
        "da 3 (e sempre con alzata veloce), schiacciatori in seconda linea da 8 (pipe), opposti in seconda linea da 9; "
        "schiacciatori e opposti in prima linea dalla zona di partenza rilevata con la direzione d'attacco."
    )
    if rows.empty:
        st.info("Nessun attacco nelle partite selezionate.")
        return
    c1, c2, c3 = st.columns(3)
    setters = sorted(rows["palleggiatore"].unique())
    setters_sel = c1.multiselect("Palleggiatore in campo", setters, default=setters, key=f"{key}_setter")
    rot_opts = [r for r in ROTATIONS if r in set(rows["rotazione"].dropna())]
    rot_sel = c2.multiselect("Rotazione (zona del palleggiatore)", rot_opts, default=rot_opts, key=f"{key}_rot")
    balls = set(rows["tipo_alzata"].dropna())
    ball_opts = [b for b in BALL_TYPE_ORDER if b in balls] + sorted(balls - set(BALL_TYPE_ORDER))
    ball_sel = c3.multiselect("Tipo di alzata", ball_opts, default=ball_opts, key=f"{key}_ball")
    base = filter_rows(rows, setters_sel, rot_sel, ball_sel)

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("#### Side-out (dopo ricezione)")
        rec_opts = [g for g in ALL_GRADES if g in set(rows["ricezione"].dropna())]
        rec_sel = st.multiselect("Effetto della ricezione", rec_opts, default=rec_opts, key=f"{key}_rec",
                                 format_func=lambda v: RECEPTION_GRADES.get(v, v))
        so = side_out_rows(base, rec_sel)
        _court_block(so, f"{key}_so", "Nessun attacco di side-out con questi filtri.")
    with right:
        st.markdown("#### Contrattacco (break-point)")
        mode = st.radio("Attacchi", list(COUNTER_MODES), format_func=COUNTER_MODES.get, key=f"{key}_ca_mode")
        ca = counter_rows(base, mode)
        _court_block(ca, f"{key}_ca", "Nessun contrattacco con questi filtri.")
    n_est = int(pd.concat([so, ca])["zona_stimata"].sum())
    if n_est:
        st.caption(f"⚠️ {n_est} attacchi di schiacciatori e opposti in prima linea senza direzione rilevata: zona "
                   "stimata dal ruolo (schiacciatore 4, opposto 2), che non vede gli scambi di lato in P1.")
    st.caption("Passa il mouse su una zona per punti, errori, murati e tutti i giocatori serviti.")

    st.markdown("#### Da R+ / R#: la distribuzione cambia a seconda di chi riceve?")
    render_positive_reception_by_receiver(base, key)

    st.markdown("#### Il palleggiatore alza in modo diverso da R+ / R#, da R! o da free ball?")
    render_setter_situation_tests(base, key)

    render_direction_comparison(base, key)

    st.markdown("#### La distribuzione cambia in modo significativo?")
    render_reception_case_tests(base)


MAX_COMPARISON_COURTS = 18


def render_direction_comparison(base, key):
    """Piccoli multipli lungo una o più direzioni insieme (una griglia per
    combinazione di categorie), sul side-out dopo i segni di ricezione scelti
    (default R+ e R#), più il contrattacco se richiesto."""
    st.markdown("#### Confronto lungo una o più direzioni")
    c1, c2, c3 = st.columns([2, 2, 1])
    rec_opts = [g for g in ALL_GRADES if g in set(base["ricezione"].dropna())]
    grades = c1.multiselect("Segno della ricezione", rec_opts, default=[g for g in ("#", "+") if g in rec_opts],
                            key=f"{key}_cmp_rec", format_func=lambda v: RECEPTION_GRADES.get(v, v))
    directions = [d for d in DIRECTIONS if d != "partita" or base["partita"].nunique() > 1]
    dirs = c2.multiselect("Direzioni (una o più insieme)", directions, default=["ricezione"], key=f"{key}_cmp_dirs",
                          format_func=DIRECTIONS.get)
    with_ca = c3.checkbox("Aggiungi il contrattacco", key=f"{key}_cmp_ca",
                          help="Le direzioni sulla ricezione (qualità, chi riceve) valgono solo per il side-out.")
    rows = side_out_rows(base, grades)
    if with_ca and not any(d in SIDE_OUT_DIRECTIONS for d in dirs):
        rows = pd.concat([rows, counter_rows(base)])
    st.caption("Side-out dopo una ricezione con i segni scelti" + (", più il contrattacco" if with_ca else "")
               + ", con i filtri in alto. Più direzioni insieme = una griglia per ogni combinazione: i dati si "
               "dividono in fretta, guarda il numero di attacchi di ogni griglia.")
    if not dirs or rows.empty:
        return
    categories = []
    for d in dirs:
        cats = [g for g in rec_opts if g in grades] if d == "ricezione" else direction_categories(rows, d)
        categories.append(cats)
    combos = []
    for combo in itertools.product(*categories):
        mask = pd.Series(True, index=rows.index)
        for d, c in zip(dirs, combo):
            mask &= rows[d] == c
        if mask.any():
            combos.append((combo, rows[mask]))
    if len(combos) > MAX_COMPARISON_COURTS:
        st.caption(f"⚠️ {len(combos)} combinazioni: mostrate le {MAX_COMPARISON_COURTS} con più attacchi.")
        combos = sorted(combos, key=lambda t: -len(t[1]))[:MAX_COMPARISON_COURTS]
    cols = st.columns(3)
    for i, (combo, sub) in enumerate(combos):
        title = " · ".join(_category_label(d, c) for d, c in zip(dirs, combo))
        with cols[i % 3]:
            st.plotly_chart(
                build_court_chart(zone_stats(sub), f"{title} · {len(sub)} att. · efficienza totale {_eff_text(sub)}",
                                  compact=True),
                theme=None, key=f"{key}_cmp_{i}",
            )


@st.cache_data(show_spinner="Test di significatività...")
def cached_case_tests(rows):
    return test_reception_cases(rows)


def _test_lines(results, labels):
    for d, res in results.items():
        if res is None:
            st.markdown(f"- **{labels[d]}** — dati insufficienti (servono almeno due categorie con "
                        f"{MIN_PER_CATEGORY} attacchi e {MIN_TOTAL} attacchi in tutto).")
        elif res["significativo"]:
            devs = "; ".join(_deviation_text(d, dev) for dev in res["scostamenti"][:3])
            st.markdown(f"- **{labels[d]} — sì** (p = {res['p_holm']:.3f}, intensità {res['cramer_v']:.2f} su 1): "
                        f"{devs}")
        else:
            st.markdown(f"- **{labels[d]} — no** (p = {res['p_holm']:.3f})")


def render_reception_case_tests(base):
    """Test nei due casi, side-out da R+/R# e da R!, e confronto tra i due
    (src.setter_distribution.test_reception_cases), con i filtri in alto."""
    res = cached_case_tests(base)
    q = res["qualita"]
    if q is None:
        st.markdown("**Qualità della ricezione, R+ / R# contro R!** — dati insufficienti.")
    elif q["p"] < ALPHA:
        devs = "; ".join(f"da {d['categoria']} **{'più' if d['residuo'] > 0 else 'meno'}** palloni in **zona "
                         f"{d['zona']}** ({d['pct']:.0f}% contro il {d['pct_totale']:.0f}% sul totale)"
                         for d in q["scostamenti"][:4])
        st.markdown(f"**Qualità della ricezione, R+ / R# contro R! — sì** (p = {q['p']:.3f}, intensità "
                    f"{q['cramer_v']:.2f} su 1): {devs}.")
    else:
        st.markdown(f"**Qualità della ricezione, R+ / R# contro R! — no** (p = {q['p']:.3f}).")
    labels = {d: DIRECTIONS[d] for d in CASE_DIRECTIONS}
    labels["ricevitore"] = "Chi riceve"
    labels["tipo_ricevitore"] = "Libero o martello (davanti/dietro) in ricezione"
    for key, (title, _) in RECEPTION_CASES.items():
        st.markdown(f"**{title}**")
        _test_lines(res[key], labels)
    st.caption(
        "Solo side-out, con i filtri in alto (palleggiatore, rotazione, tipo di alzata). Test chi quadro zona × "
        "categoria con p-value per permutazione (affidabile anche con pochi attacchi); dentro ciascun caso corretto per "
        "il numero di direzioni guardate insieme (Holm): significativo se p < 0.05 dopo la correzione. Categorie con "
        f"meno di {MIN_PER_CATEGORY} attacchi escluse; scostamenti = zone con residuo standardizzato oltre ±2. Opposti e "
        "centrali in ricezione esclusi da \"libero o martello\". Rotazione: con il palleggiatore in prima linea (P2-P4) "
        "gli attaccanti davanti sono due e l'opposto attacca da dietro, quindi una parte della differenza tra rotazioni "
        "è strutturale."
    )


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


@st.cache_data(show_spinner="Lettura dello scout...")
def load_attack_rows(file_path, team, label, mtime):
    """attack_rows di una partita: del Decimo (team=None) o della squadra
    `team`. `mtime` (data di modifica del file) solo come chiave di cache."""
    parsed = parse_dvw(file_path)
    side = decimo_side(parsed) if team is None else team_side(parsed, team)
    return attack_rows(parsed, side, label)


def render_distribution_section(matches, match_label_lookup):
    st.header("Distribuzione alzate")
    dvw_seqs = sorted(m["match_seq"] for m in matches if str(m.get("file_path", "")).lower().endswith(".dvw"))
    if not dvw_seqs:
        st.info("Questa sezione richiede lo scout in formato .dvw: nessuna partita della stagione lo ha.")
        return
    selected = match_picker(dvw_seqs, match_label_lookup, key="dist_partite")
    dvw = [m for m in matches if m["match_seq"] in selected]
    if not dvw:
        st.info("Seleziona almeno una partita.")
        return
    rows = pd.concat([
        load_attack_rows(m["file_path"], None, match_label_lookup[m["match_seq"]], _mtime(m["file_path"]))
        for m in sorted(dvw, key=lambda m: m["match_seq"])
    ], ignore_index=True)
    st.caption(f"Decimo, {len(dvw)} partite.")
    render_distribution(rows, key="dist_decimo")


@st.cache_data(show_spinner="Ricerca degli scout .dvw della stagione...")
def load_dvw_catalog(season, version):
    """Tutti i .dvw nella cartella della stagione (sottocartelle comprese):
    [{path, file, data, teams, sets}], letti solo nell'intestazione."""
    out = []
    for path in sorted(glob.glob(str(build_base_path(season) / "**" / "*.dvw"), recursive=True)):
        try:
            out.append({"path": path, "file": os.path.basename(path), **read_dvw_header(path)})
        except Exception as e:  # file illeggibile: segnalato, non blocca gli altri
            out.append({"path": path, "file": os.path.basename(path), "data": None, "teams": {}, "sets": [],
                        "errore": str(e)})
    # stessa partita in più file (stessa data e stesse squadre, es. una versione
    # parziale e una completa dello scout): si usa il file più grande, gli
    # altri sono marcati come duplicati e segnalati
    groups = {}
    for e in out:
        if e["data"] and len([n for n in e["teams"].values() if n]) == 2:
            key = (e["data"], tuple(sorted(normalize_team_name(n) for n in e["teams"].values())))
            groups.setdefault(key, []).append(e)
    for entries in groups.values():
        keep = max(entries, key=lambda e: os.path.getsize(e["path"]))
        for e in entries:
            if e is not keep:
                e["duplicato_di"] = keep["file"]
    return out


def _date_key(data):
    try:
        d, m, y = (int(x) for x in str(data).split("/"))
        return (y, m, d)
    except ValueError:
        return (9999, 0, 0)


@st.cache_data(show_spinner="Analisi delle partite...")
def load_team_match(file_path, team, mtime):
    """Una partita della squadra `team` (nome nel .dvw) per lo studio
    dell'avversario: azioni sue e dei suoi avversari, punti per causa, KPI,
    risultato e attacchi per la distribuzione."""
    source = load_report_source(file_path, team=team)
    header = match_header(source, None)
    pts = point_sources_by_set(source)
    return {
        "nome_noi": source["nome_noi"], "nome_loro": source["nome_loro"], "casa": source["side"] == "*",
        "header": header, "noi": source["noi"], "loro": source["loro"],
        "points": pts[pts["set"] == TOTAL_LABEL], "points_by_set": pts,
        "set_results": set_results(source["parsed"], source["side"]),
        "serves": serve_rows(source),
        "kpi": {"noi": set_kpis(source["noi"]), "loro": set_kpis(source["loro"])},
        "setters": setter_table(source),
        "rows": attack_rows(source["parsed"], source["side"], f"{header['data'] or ''} vs {source['nome_loro']}"),
    }


def points_balance_table(by_outcome, n_sets, team_name):
    """Per causa: media per set di squadra e avversari e saldo, nei set vinti
    e persi, più la differenza di saldo tra set vinti e persi."""
    rows = []
    for r in balance_rows(by_outcome, n_sets):
        row = {"Causa": r["causa"]}
        for esito in ("vinti", "persi"):
            if r[esito]:
                noi, loro, saldo = r[esito]
                row[f"{team_name} (set {esito})"] = f"{noi:.1f}"
                row[f"Avversari (set {esito})"] = f"{loro:.1f}"
                row[f"Saldo (set {esito})"] = f"{saldo:+.1f}"
        if r["vinti"] and r["persi"]:
            row["Saldo vinti − persi"] = f"{r['vinti'][2] - r['persi'][2]:+.1f}"
        rows.append(row)
    return pd.DataFrame(rows)


def _receiver_text(rec):
    return (f"**{rec['giocatore']}**: efficienza {rec['eff']:.0f}% su {rec['n']} ricezioni "
            f"({rec['perfette_pos']:.0f}% perfette o positive, {rec['errori']} ace subiti)")


def render_opponent_summary(df, rows, serves):
    """Riassunto iniziale dello studio dell'avversario (src.opponent_summary)."""
    st.subheader("In breve")
    c1, c2, c3 = st.columns(3)
    worst, best = worst_receiver(df), best_receiver(df)
    with c1:
        st.markdown("**🎯 Peggior ricevitore**")
        st.markdown(_receiver_text(worst) if worst else f"Nessuno con almeno {MIN_RECEPTIONS} ricezioni.")
    with c2:
        st.markdown("**🛡️ Miglior ricevitore**")
        st.markdown(_receiver_text(best) if best and best != worst
                    else f"Nessun altro con almeno {MIN_RECEPTIONS} ricezioni." if worst
                    else f"Nessuno con almeno {MIN_RECEPTIONS} ricezioni.")
    srv = dangerous_server(serves)
    with c3:
        st.markdown("**💥 Battitore più pericoloso**")
        if srv:
            st.markdown(f"**{srv['giocatore']}**: la squadra vince il {srv['break']:.0f}% degli scambi sulla sua "
                        f"battuta (media della squadra {srv['break_squadra']:.0f}%), {srv['quota']:.0f}% di battute "
                        f"#, + o / su {srv['n']} ({srv['ace']} ace, {srv['errori']} "
                        f"{'errore' if srv['errori'] == 1 else 'errori'})")
        else:
            st.markdown(f"Nessuno con almeno {MIN_SERVES} battute e almeno il {DANGEROUS_SERVE_SHARE}% #, + o /.")
        st.caption(f"Tra chi ha almeno {MIN_SERVES} battute e almeno il {DANGEROUS_SERVE_SHARE}% #, + o /, quello "
                   "con cui la squadra fa più punti in battuta: è il punto fatto che sposta il set, più della sola "
                   "quota di battute difficili (rischiare di più vuol dire anche sbagliare di più).")
    c4, c5 = st.columns(2)
    clutch = clutch_target(rows)
    with c4:
        st.markdown("**🔥 Più servito nei finali di set**")
        if clutch:
            st.markdown(f"**{clutch['giocatore']}**: {clutch['n']} attacchi su {clutch['totale']} "
                        f"({clutch['quota']:.0f}%, contro il {clutch['quota_partita']:.0f}% sul totale degli attacchi "
                        f"della squadra), {clutch['punti']} punti, efficienza {clutch['eff']:.0f}%")
        else:
            st.markdown("Nessun attacco nei finali di set.")
        st.caption(f"Da {CLUTCH_FROM} punti di una delle due squadre ({CLUTCH_FROM_TIEBREAK} al quinto set), con "
                   f"scarto di al più {CLUTCH_MAX_DIFF} punti.")
    tests = cached_setter_tests(rows)
    sig = [(setter, k, r) for (setter, k), r in tests.items() if r and r["significativo"] and r["scostamenti"]]
    with c5:
        st.markdown("**🧭 Tendenze del palleggiatore**")
        if sig:
            for setter, k, r in sig:
                st.markdown(f"- {situation_text(setter, k, r['scostamenti'][0])}")
        else:
            st.markdown("Nessuna tendenza significativa da R+ / R#, da R! o da free ball.")
    st.divider()


def render_swing_summary(by_outcome, n_sets, team_name):
    """Testo: dove la squadra va sotto nei set persi, dove guadagna nei set
    vinti, cosa cambia di più tra gli uni e gli altri (src.opponent_summary.swing_sentences)."""
    lines = swing_sentences(by_outcome, n_sets)
    if lines:
        st.markdown(f"**{team_name}**, {SWING_INTRO}:\n" + "\n".join(f"- {line}" for line in lines))


def build_opponent_report_data(team_name, ms, combined, all_rows, serves, note):
    """I dati del report PDF sull'avversario (src.opponent_report_pdf), dalle
    stesse funzioni della tab."""
    partite = []
    for m in ms:
        (a, b), parziali = _team_score(m)
        incompleti = any(not ok for _, _, _, ok in m["header"]["set_scout"])
        partite.append({"data": m["header"]["data"], "avversario": m["nome_loro"], "set": f"{a}-{b}" + ("*" if incompleti else ""),
                        "parziali": "  ".join(f"{x}-{y}" for x, y in parziali), "vinta": a > b, "parziale": incompleti})
    tests = cached_setter_tests(all_rows)
    by_outcome, n_sets = points_by_set_outcome([(m["points_by_set"], m["set_results"]) for m in ms])
    kpi_team, kpi_opp = set_kpis(combined["noi"]), set_kpis(combined["loro"])
    kpi = []
    # il muro nel report si misura sui muri punto, non sull'efficienza
    for x in SET_KPI_PERCENT + ["Muri punto", "Errori", "Punti"]:
        va, vb = _kpi_value(kpi_team, "Squadra", TOTAL_LABEL, x), _kpi_value(kpi_opp, "Squadra", TOTAL_LABEL, x)
        pct = x in SET_KPI_PERCENT
        kpi.append((SET_KPI_LABELS[x], _fmt_cell(va, pct), _fmt_cell(vb, pct), (va, vb) if pct else (None, None)))
    totals = setter_table({"noi": combined["noi"]})
    setters = {}
    for s_ in sorted({s_ for m in ms for s_ in m["setters"]}):
        rows = [(f"{m['header']['data']} vs {m['nome_loro']}", m["setters"][s_].iloc[-1]) for m in ms if s_ in m["setters"]]
        setters[s_] = rows + [("Totale", totals[s_].iloc[-1])]
    pts = pd.concat([m["points"] for m in ms], ignore_index=True)
    gen = pts[pts["causa"] == "Punti generici"].groupby("squadra")["n"].sum()
    return {
        "squadra": team_name, "generato": pd.Timestamp.now().strftime("%d/%m/%Y"), "note": note,
        "partite": partite,
        "riassunto": {
            "peggior": worst_receiver(combined["noi"]), "miglior": best_receiver(combined["noi"]),
            "battitore": dangerous_server(serves), "finali": clutch_target(all_rows),
            "palleggiatore": [(s_, k, r["scostamenti"][0]) for (s_, k), r in tests.items()
                              if r and r["significativo"] and r["scostamenti"]],
        },
        "bilancia": {"frasi": swing_sentences(by_outcome, n_sets), "righe": balance_rows(by_outcome, n_sets),
                     "n_sets": n_sets},
        "kpi": kpi, "palleggiatori": setters, "distribuzioni": distributions_by_setter(all_rows),
        "test_palleggiatori": tests, "ricevitori": cached_positive_by_receiver(all_rows),
        "giocatori": player_table_from_df(combined["noi"], int(gen.get("noi", 0)), int(gen.get("loro", 0))),
    }


def render_opponent_report_button(team_key, team_name, ms, combined, all_rows, serves):
    """Note facoltative, generazione e download del report PDF sull'avversario."""
    with st.expander("📄 Report da stampare prima della partita"):
        st.caption("PDF con il riassunto, cosa sposta l'ago della bilancia, i palleggiatori (con le tre griglie e le "
                   "tendenze), chi riceve da R+ / R#, la squadra per fondamentale, le partite e il tabellino, sulle "
                   "partite selezionate qui sopra. Pagine strette: si leggono sul telefono e si stampano su A4 "
                   "adattate alla pagina.")
        note = st.text_area("Note (in apertura del report)", key=f"opp_report_note_{team_key}", height=110,
                            placeholder="Come vogliamo battere, chi mettere sotto pressione, cosa aspettarsi...")
        if st.button("Genera PDF", key=f"opp_report_btn_{team_key}"):
            with st.spinner("Generazione del report..."):
                pdf = build_opponent_report_pdf(build_opponent_report_data(team_name, ms, combined, all_rows, serves, note))
            st.session_state["opp_report_pdf"] = (team_key, tuple(m["header"]["data"] for m in ms), pdf)
        stored = st.session_state.get("opp_report_pdf")
        if stored and stored[0] == team_key and stored[1] == tuple(m["header"]["data"] for m in ms):
            st.download_button("⬇️ Scarica PDF", data=stored[2], mime="application/pdf",
                               file_name=f"Report avversario {team_name} {pd.Timestamp.now():%d-%m-%Y}.pdf",
                               key=f"opp_report_dl_{team_key}")
            st.caption("Se cambi le note o le partite, premi di nuovo \"Genera PDF\" prima di scaricarlo.")


def _team_score(m):
    """Set e parziali dal punto di vista della squadra studiata."""
    h = m["header"]
    sets = (h["sets_casa"], h["sets_ospite"]) if m["casa"] else (h["sets_ospite"], h["sets_casa"])
    parziali = [(a, b) if m["casa"] else (b, a) for a, b in h["parziali"]]
    return sets, parziali


def render_opponent_section(season, version):
    st.header("Studio avversario")
    catalog = load_dvw_catalog(season, version)
    teams = {}
    unnamed = [e["file"] for e in catalog if len([n for n in e["teams"].values() if n]) < 2 or e.get("errore")]
    duplicates = [f"{e['file']} (uguale a {e['duplicato_di']}, più completo)" for e in catalog if e.get("duplicato_di")]
    for e in catalog:
        if e["file"] in unnamed or e.get("duplicato_di"):
            continue
        for side, name in e["teams"].items():
            key = normalize_team_name(name) if name else ""
            if not key or "decimo" in key:
                continue
            t = teams.setdefault(key, {"names": Counter(), "matches": []})
            t["names"][name] += 1
            t["matches"].append((e, name))
    if not teams:
        st.info(f"Nessuno scout .dvw di altre squadre nella cartella della stagione {season} "
                f"({build_base_path(season)}, sottocartelle comprese).")
        return
    display = {k: v["names"].most_common(1)[0][0] for k, v in teams.items()}
    c1, c2 = st.columns([1, 2])
    with c1:
        team_key = st.selectbox("Squadra", sorted(teams, key=lambda k: display[k].casefold()), key="opp_team",
                                format_func=lambda k: f"{display[k]} ({len(teams[k]['matches'])} "
                                                      f"{'partita' if len(teams[k]['matches']) == 1 else 'partite'})")
    entries = sorted(teams[team_key]["matches"], key=lambda en: _date_key(en[0]["data"]))
    labels = {en[0]["path"]: f"{en[0]['data'] or '?'} · {en[0]['teams'].get('*', '?')} - {en[0]['teams'].get('a', '?')}"
              for en in entries}
    with c2:
        chosen = st.multiselect("Partite da considerare", list(labels), default=list(labels), format_func=labels.get,
                                key=f"opp_matches_{team_key}")
    if unnamed:
        st.caption("Scout senza il nome di una squadra (o illeggibili), non attribuibili: " + ", ".join(unnamed))
    if duplicates:
        st.caption("Stessa partita in più file, non usati: " + "; ".join(duplicates))
    if not chosen:
        st.info("Seleziona almeno una partita.")
        return
    name_by_path = {en[0]["path"]: en[1] for en in entries}
    ms = [load_team_match(p, name_by_path[p], _mtime(p)) for p in chosen]
    team_name = display[team_key]
    team_color, others_color = theme_colors()["palette"][1], UNKNOWN_COLOR
    combined = {key: combine_matches([m[key] for m in ms]) for key in ("noi", "loro")}
    all_rows = pd.concat([m["rows"] for m in ms], ignore_index=True)

    serves = pd.concat([m["serves"] for m in ms], ignore_index=True)
    render_opponent_summary(combined["noi"], all_rows, serves)
    render_opponent_report_button(team_key, team_name, ms, combined, all_rows, serves)

    # 1) partite
    st.subheader("Partite")
    rows = []
    for m in ms:
        (a, b), parziali = _team_score(m)
        incompleti = [n for n, _, _, ok in m["header"]["set_scout"] if not ok]
        rows.append({"Data": m["header"]["data"], "Avversario": m["nome_loro"],
                     "Set": f"{a}-{b}" + ("*" if incompleti else ""),
                     "Parziali": "  ".join(f"{x}-{y}" for x, y in parziali),
                     "Scout": "completo" if not incompleti else f"parziale (set {', '.join(map(str, incompleti))})"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if any("parziale" in r["Scout"] for r in rows):
        st.caption("* Scout parziale: risultato e parziali fin dove arriva la rilevazione.")

    # 2) KPI
    st.subheader(f"{team_name} e i suoi avversari")
    view = {"nome_noi": team_name, "nome_loro": "Avversari affrontati",
            "kpi": {key: set_kpis(df) for key, df in combined.items()}}
    st.plotly_chart(build_set_overview_chart(view, TOTAL_LABEL, colors=(team_color, others_color)), theme=None,
                    key="opp_overview")
    table = []
    for m in ms:
        k = m["kpi"]["noi"]
        table.append({"Partita": f"{m['header']['data']} vs {m['nome_loro']}",
                      **{SET_KPI_LABELS[x]: _fmt_cell(_kpi_value(k, "Squadra", TOTAL_LABEL, x), x in SET_KPI_PERCENT)
                         for x in SET_KPI_PERCENT + ["Muri punto", "Errori", "Punti"]}})
    k = view["kpi"]["noi"]
    table.append({"Partita": "Totale", **{SET_KPI_LABELS[x]: _fmt_cell(_kpi_value(k, "Squadra", TOTAL_LABEL, x), x in SET_KPI_PERCENT)
                                          for x in SET_KPI_PERCENT + ["Muri punto", "Errori", "Punti"]}})
    st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")

    # 3) punti per causa, set vinti e set persi
    st.subheader("Da dove arrivano i punti")
    by_outcome, n_sets = points_by_set_outcome([(m["points_by_set"], m["set_results"]) for m in ms])
    panels = [(k, f"<b>Set {k}</b>   {n_sets[k]} set · media per set") for k in ("vinti", "persi") if n_sets[k]]
    if not panels:
        st.info("Nessun set concluso nello scout delle partite selezionate.")
    else:
        st.caption(f"Punti per causa in media per set, nei set che {team_name} vince e in quelli che perde: per capire "
                   "per cosa vince e per cosa perde. Ogni punto ha una sola causa (l'azione vincente di chi l'ha fatto, "
                   "altrimenti l'errore di chi l'ha perso); solo i set conclusi nello scout.")
        as_percent = st.radio("Lunghezza delle barre", ["Punti", "% del set"], horizontal=True,
                              key="opp_pts_mode") != "Punti"
        st.plotly_chart(_points_panels_chart(by_outcome, panels, (("noi", team_name, team_color),
                                                                  ("loro", "Avversari", others_color)),
                                             as_percent, unit="set", decimals=1), theme=None, key="opp_points")
        render_swing_summary(by_outcome, n_sets, team_name)
        st.dataframe(points_balance_table(by_outcome, n_sets, team_name), hide_index=True, width="stretch")
        st.caption(f"Saldo = punti di {team_name} meno punti dei suoi avversari per quella causa, in media per set. "
                   "\"Errori avversari\" = punti regalati dall'altra squadra.")

    # 4) giocatori
    st.subheader("Giocatori, tutte le partite selezionate")
    pts = pd.concat([m["points"] for m in ms], ignore_index=True)
    gen = pts[pts["causa"] == "Punti generici"].groupby("squadra")["n"].sum()
    render_player_table(player_table_from_df(combined["noi"], int(gen.get("noi", 0)), int(gen.get("loro", 0))))

    # 5) palleggiatori
    setters = sorted({s for m in ms for s in m["setters"]})
    if setters:
        st.subheader("Palleggiatori")
        st.caption("Efficienza d'attacco della squadra con lui in campo (tra parentesi il numero di attacchi): "
                   "da ricezione positiva (+ o #), da ricezione ! e da free ball.")
        totals = setter_table({"noi": combined["noi"]})
        cols = st.columns(min(len(setters), 3))
        for i, s in enumerate(setters):
            rows = []
            for m in ms:
                if s in m["setters"]:
                    rows.append((f"{m['header']['data']} vs {m['nome_loro']}", m["setters"][s].iloc[-1]))
            rows.append(("Totale", totals[s].iloc[-1]))
            df = pd.DataFrame([{"Partita": label, **{SETTER_FAMILIES[f]: (f"{_pct_text(r[f'{f} eff'])} ({r[f'{f} n']})"
                                                                          if r[f"{f} n"] else "-") for f in SETTER_FAMILIES}}
                               for label, r in rows])
            with cols[i % len(cols)]:
                st.markdown(f"**{s}**")
                st.dataframe(df, hide_index=True, width="stretch")

    # 6) distribuzione
    st.subheader("Distribuzione alzate")
    render_distribution(all_rows, key=f"opp_{team_key}")


FORM_GOOD, FORM_BAD, FORM_NEUTRAL = "#1f9d55", "#d03b3b", "#2a78d6"


def _match_short_label(m, current_season, season):
    label = f"{abbr_opponent(m['opponent'])} ({_LEG_WORD[m['leg']]})"
    return label if season == current_season else f"{label}, {season}"


def build_form_chart(items, shared_x, is_pct_shared=None):
    """
    Una riga per voce (KPI o giocatore): pallini grigi = partite precedenti,
    fascia = abituale (media ± 1 deviazione standard, con almeno 3 partite),
    pallino grande = partita in esame, verde/rosso se fuori dalla fascia nel
    verso buono/cattivo, blu se dentro. `items`: [{label, prev [(partita,
    valore)], current, verso, pct}].
    """
    colors = theme_colors()
    fig = make_subplots(rows=len(items), cols=1, shared_xaxes=shared_x, vertical_spacing=0.35 / max(len(items), 1))
    for i, it in enumerate(items, 1):
        vals = np.array([v for _, v in it["prev"]], dtype=float)
        unit = "%" if it["pct"] else ""
        fmt = (lambda v: f"{v:.0f}{unit}") if it["pct"] else (lambda v: f"{v:.2f}".rstrip("0").rstrip("."))
        mean = std = None
        if len(vals) >= 3:
            mean, std = vals.mean(), vals.std(ddof=1)
            fig.add_trace(go.Bar(x=[2 * std if std > 0 else 0.001], base=[mean - std], y=[it["label"]], orientation="h",
                                 marker_color=hex_to_rgba(colors["ink"], 0.12), width=0.7, hoverinfo="skip",
                                 showlegend=False), row=i, col=1)
            fig.add_trace(go.Scatter(x=[mean], y=[it["label"]], mode="markers", showlegend=False,
                                     marker=dict(symbol="line-ns", size=22, line=dict(width=2, color=colors["ink"])),
                                     hovertemplate=f"media {fmt(mean)} (fascia {fmt(mean - std)} – {fmt(mean + std)})"
                                                   "<extra></extra>"), row=i, col=1)
        if len(vals):
            fig.add_trace(go.Scatter(x=vals, y=[it["label"]] * len(vals), mode="markers", showlegend=False,
                                     marker=dict(size=10, color=hex_to_rgba(colors["ink"], 0.45)),
                                     text=[f"{lbl}: {fmt(v)}" for lbl, v in it["prev"]],
                                     hovertemplate="%{text}<extra></extra>"), row=i, col=1)
        cur = it["current"]
        if cur is not None:
            color = FORM_NEUTRAL
            if mean is not None and std is not None and abs(cur - mean) > std:
                color = FORM_GOOD if (cur - mean) * it["verso"] > 0 else FORM_BAD
            fig.add_trace(go.Scatter(x=[cur], y=[it["label"]], mode="markers+text", showlegend=False,
                                     marker=dict(size=17, color=color, line=dict(color=colors["surface"], width=2)),
                                     text=[f"<b>{fmt(cur)}</b>"], textposition="top center",
                                     textfont=dict(color=color, size=12),
                                     hovertemplate=f"partita in esame: {fmt(cur)}<extra></extra>"), row=i, col=1)
        fig.update_yaxes(showgrid=False, row=i, col=1, tickfont=dict(size=12))
        fig.update_xaxes(gridcolor=colors["grid"], zeroline=False, row=i, col=1,
                         ticksuffix="%" if it["pct"] else "")
    layout = base_layout("", "", "")
    layout.update(height=80 + 78 * len(items), margin=dict(t=20, b=30, l=200, r=20), barmode="overlay",
                  hovermode="closest", showlegend=False)
    fig.update_layout(**layout)
    return fig


def render_form_section(season, matches, version):
    """Una partita (o alcuni suoi set) contro le partite precedenti: tutti i
    KPI di un giocatore o della squadra, oppure tutti i giocatori su un KPI
    (src.set_analysis.form_values)."""
    st.header("Ultima partita rispetto alle precedenti")
    ordered = sorted(matches, key=lambda m: m["match_seq"])
    if not ordered:
        return
    c1, c2, c3, c4 = st.columns([2, 2, 1, 1])
    labels = {m["match_seq"]: _match_short_label(m, season, season) for m in ordered}
    seq = c1.selectbox("Partita in esame", list(labels), index=len(labels) - 1, format_func=labels.get, key="form_match")
    match = next(m for m in ordered if m["match_seq"] == seq)
    all_sets = sorted(int(x) for x in match["df"]["Numero Set"].dropna().unique())
    sets = c2.multiselect("Set della partita in esame", all_sets, default=all_sets, key=f"form_sets_{seq}",
                          format_func=lambda x: f"Set {x}")
    n_prev = c3.number_input("Partite precedenti", 1, 30, 5, key="form_n")
    with_prev = c4.checkbox("Anche la stagione precedente", value=True, key="form_prev_season")
    if not sets:
        st.info("Seleziona almeno un set.")
        return
    history = _baseline_matches(season, matches, match, int(n_prev), with_prev, version)
    current = form_values(match["df"], sets)
    seasons = active_seasons()
    prev_season = seasons[seasons.index(season) - 1] if season in seasons and seasons.index(season) > 0 else None
    this_season = {id(m) for m in matches}
    hist = [(_match_short_label(m, season, season if id(m) in this_season else prev_season), form_values(m["df"]))
            for m in history]
    names = dict(current.pop("_nomi", {}))
    for _, h in hist:
        for k, v in h.pop("_nomi", {}).items():
            names.setdefault(k, v)
    entities = ["squadra"] + sorted((k for k in current if k != "squadra"), key=lambda k: names[k])

    mode = st.radio("Vista", ["Tutti i KPI di un giocatore (o della squadra)", "Tutti i giocatori su un KPI"],
                    horizontal=True, key="form_mode")
    kpi_info = {k: (label, verso, pct) for k, label, _, _, _, verso, pct in FORM_KPIS}
    items = []
    if mode.startswith("Tutti i KPI"):
        who = st.selectbox("Giocatore", entities, format_func=lambda k: names.get(k, k), key="form_entity")
        for k, (label, verso, pct) in kpi_info.items():
            cur = current.get(who, {}).get(k)
            prev = [(lbl, h[who][k]) for lbl, h in hist if k in h.get(who, {})]
            if cur is not None or prev:
                items.append({"label": label, "prev": prev, "current": cur, "verso": verso, "pct": pct})
        shared = False
    else:
        k = st.selectbox("KPI", list(kpi_info), format_func=lambda x: kpi_info[x][0], key="form_kpi")
        label, verso, pct = kpi_info[k]
        for who in entities:
            cur = current.get(who, {}).get(k)
            prev = [(lbl, h[who][k]) for lbl, h in hist if k in h.get(who, {})]
            if cur is not None:
                items.append({"label": names.get(who, who), "prev": prev, "current": cur, "verso": verso, "pct": pct})
        shared = True
    st.caption(
        f"Pallini grigi: le {len(history)} partite precedenti (dalla più recente"
        f"{', comprese quelle della stagione precedente' if with_prev else ''}); fascia: abituale, media ± 1 deviazione "
        "standard (con almeno 3 partite), trattino = media; pallino grande: la partita in esame sui set scelti, verde o "
        "rosso se fuori dalla fascia in meglio o in peggio, blu se dentro. Conteggi per set giocato (confrontabili "
        f"anche scegliendo solo alcuni set); efficienze solo con almeno {MIN_FORM_VOLUME} azioni nella partita. "
        "p-e/m = punti − errori − murate."
    )
    if not items:
        st.info("Nessun dato per questa scelta.")
        return
    st.plotly_chart(build_form_chart(items, shared), theme=None, key="form_chart")


def build_ball_type_chart(data, tipo, players, all_players, match_label_lookup, seqs):
    """p-e partita per partita su un tipo di palla, una linea per giocatore."""
    colors = theme_colors()
    fig = go.Figure()
    x = [match_label_lookup[ms] for ms in seqs]
    for player in players:
        sub = data[(data["tipo"] == tipo) & (data["giocatore"] == player)].set_index("match_seq").reindex(seqs)
        color, dash = categorical_style(player, all_players)
        fig.add_trace(go.Scatter(
            x=x, y=sub["pe"], name=player, mode="lines+markers", connectgaps=False,
            line=dict(color=color, width=2.5, dash=dash), marker=dict(size=8),
            customdata=np.stack([sub["tot"].fillna(0), sub["pt"].fillna(0), sub["err"].fillna(0), sub["mur"].fillna(0)],
                                axis=-1),
            hovertemplate=player + ": p-e/m %{y:+d} su %{customdata[0]:.0f} attacchi "
                          "(%{customdata[1]:.0f} punti, %{customdata[2]:.0f} errori, %{customdata[3]:.0f} murati)<extra></extra>",
        ))
    fig.add_hline(y=0, line_color=colors["grid"], line_width=1.5)
    layout = base_layout(f"<b>{tipo}</b>", "", "p-e/m (punti − errori − murate)")
    layout.update(height=380, hovermode="closest", legend=dict(orientation="h", y=-0.25))
    fig.update_layout(**layout)
    fig.update_xaxes(categoryorder="array", categoryarray=x)
    return fig


def render_ball_type_section(matches, selected_seqs, match_label_lookup):
    """Andamento del p-e dei giocatori su palla alta e su palla veloce
    (src.set_analysis.ball_type_pe_by_match), partite .dvw selezionate."""
    st.header("Palla alta e palla veloce")
    data = ball_type_pe_by_match([m for m in matches if m["match_seq"] in selected_seqs])
    if data.empty:
        st.info("Serve lo scout .dvw (il tipo di alzata non c'è negli Excel 2025-2026): nessuna partita selezionata lo ha.")
        return
    seqs = sorted(data["match_seq"].unique())
    volume = data.groupby("giocatore")["tot"].sum().sort_values(ascending=False)
    players = st.multiselect("Giocatori", list(volume.index), default=list(volume.index[:4]), key="ball_type_players")
    st.caption("p-e/m = punti − errori − murate negli attacchi del giocatore su quel tipo di alzata, partita per partita "
               "(passa il mouse per il numero di attacchi). Palla alta: alzata alta; palla veloce: super, quick (tutti "
               "gli attacchi dei centrali) e \"7\". Mezza e altro fuori da entrambe.")
    if not players:
        return
    c1, c2 = st.columns(2)
    for col, tipo in ((c1, "Palla alta"), (c2, "Palla veloce")):
        with col:
            st.plotly_chart(build_ball_type_chart(data, tipo, players, list(volume.index), match_label_lookup, seqs),
                            theme=None,
                            key=f"ball_type_{tipo}")
    tot = data[data["giocatore"].isin(players)].groupby(["giocatore", "tipo"])[["tot", "pt", "err", "mur", "pe"]].sum()
    tot["eff"] = 100 * (tot["pt"] - tot["err"] - tot["mur"]) / tot["tot"]
    tot = tot.rename(columns={"pe": "p-e/m"})
    table = tot.unstack("tipo")
    table.columns = [f"{t} · {m}" for m, t in table.columns]
    order = [f"{t} · {m}" for t in ("Palla alta", "Palla veloce") for m in ("tot", "pt", "err", "mur", "p-e/m", "eff")]
    table = table.reindex(columns=[c for c in order if c in table.columns])
    fmt = {"eff": lambda v: f"{v:.0f}%", "p-e/m": lambda v: f"{int(v):+d}"}
    for c in table.columns:
        f = fmt.get(c.split(" · ")[1], lambda v: f"{int(v)}")
        table[c] = [f(v) if pd.notna(v) else "-" for v in table[c]]
    st.dataframe(table, width="stretch")
    st.caption("Totali sulle partite selezionate (tot attacchi, pt punti, err errori, mur murate, p-e/m punti − errori − murate, eff "
               "efficienza).")


def build_mvp_trend_chart(long, players, all_players, match_label_lookup, seqs, column, title):
    """Punteggio MVP partita per partita (o cumulato), una linea per giocatore."""
    colors = theme_colors()
    fig = go.Figure()
    x = [match_label_lookup[ms] for ms in seqs]
    for player in players:
        sub = long[long["nome"] == player].set_index("match_seq").reindex(seqs)
        if column == "cumulato":
            sub["cumulato"] = sub["cumulato"].ffill()
        color, dash = categorical_style(player, all_players)
        played = [f"{int(a)}/{int(b)} set, totale {_signed(t)}" if pd.notna(a) else "non ha giocato"
                  for a, b, t in zip(sub["set_giocati"], sub["set_partita"], sub["totale"])]
        fig.add_trace(go.Scatter(
            x=x, y=sub[column], name=player, mode="lines+markers", connectgaps=column == "cumulato",
            line=dict(color=color, width=2.5, dash=dash), marker=dict(size=8), customdata=played,
            hovertemplate=player + ": %{y:+.2f} (%{customdata})<extra></extra>",
        ))
    fig.add_hline(y=0, line_color=colors["grid"], line_width=1.5)
    layout = base_layout(f"<b>{title}</b>", "", "Punteggio MVP")
    layout.update(height=400, hovermode="closest", legend=dict(orientation="h", y=-0.25))
    fig.update_layout(**layout)
    fig.update_xaxes(categoryorder="array", categoryarray=x)
    return fig


def render_mvp_trend_section(matches, selected_seqs, match_label_lookup, version):
    """Punteggio MVP dei giocatori partita per partita e cumulato, con la
    classifica della stagione (src.mvp)."""
    st.header("Punteggio MVP")
    per_match, players = season_mvp_inputs(matches, selected_seqs, version)
    long = mvp_by_match_long(per_match, players)
    if long.empty:
        st.info("Serve lo scout .dvw: nessuna delle partite selezionate lo ha.")
        return
    seqs = sorted(long["match_seq"].unique())
    ranking = season_mvp(per_match, players)
    order = list(long.groupby("nome")["punteggio"].sum().sort_values(ascending=False).index)
    chosen = st.multiselect("Giocatori", order, default=order[:5], key="mvp_trend_players")
    if chosen:
        c1, c2 = st.columns(2)
        with c1:
            st.plotly_chart(build_mvp_trend_chart(long, chosen, order, match_label_lookup, seqs, "punteggio",
                                                  "Partita per partita"), theme=None, key="mvp_trend_match")
        with c2:
            st.plotly_chart(build_mvp_trend_chart(long, chosen, order, match_label_lookup, seqs, "cumulato",
                                                  "Cumulato"), theme=None, key="mvp_trend_cum")
    st.dataframe(mvp_ranking_display(ranking), hide_index=True, width="stretch")
    st.caption("Punteggio MVP di ogni partita (come si calcola: tab Partita) e la sua somma lungo la stagione, sulle "
               "partite selezionate con lo scout .dvw. Passa il mouse per i set giocati e il totale prima della "
               "correzione per i set.")


def _match_option_label(match_seq, match_label_lookup):
    return f"{match_seq + 1}. {match_label_lookup[match_seq]}"


def match_picker(seqs, match_label_lookup, key, default=None, label="Partite", help=None):
    """Selettore delle partite di una sezione (al posto del filtro unico della
    barra laterale): `default` None = tutte."""
    chosen = st.multiselect(label, list(seqs), default=list(seqs) if default is None else default, key=key,
                            format_func=lambda ms: _match_option_label(ms, match_label_lookup), help=help)
    return sorted(chosen)


def _filter_by_matches(df, selected_seqs):
    return df[df["match_seq"].isin(selected_seqs)] if not df.empty else df


def main():
    st.title("Decimo Roma — Match analysis")

    seasons = active_seasons()
    with st.sidebar:
        st.header("Stagione e partite")
        season = st.selectbox("Stagione", seasons, index=len(seasons) - 1, key="season")
        if st.button("🔄 Ricarica dati da Drive", help=(
            "Rilegge gli scout della stagione e riscarica i risultati dal sito federale. "
            "Una partita appena aggiunta a config/matches.csv compare già da sola: serve se "
            "hai modificato uno scout già caricato o per un risultato pubblicato da poco."
        )):
            st.cache_data.clear()

    version = config_version()
    matches = load_matches_cached(season, version)
    loaded_keys = {(m["leg"], m["opponent"]) for m in matches}
    not_loaded = [
        f"{r.opponent} ({LEG_LABELS.get(r.leg, r.leg).lower()}) — {r.path}"
        for r in active_season_matches(season).itertuples() if (r.leg, r.opponent) not in loaded_keys
    ]
    if not_loaded:
        st.error(
            "Partite registrate in config/matches.csv ma non lette: file non trovato (path sbagliato "
            "o Drive non ancora sincronizzato) oppure Excel nel formato 2026-2027, non supportato — "
            "registra il file .dvw della partita. Poi premi \"Ricarica dati da Drive\".\n\n- "
            + "\n- ".join(not_loaded)
        )
    if not matches:
        if not not_loaded:
            st.info(
                f"Nessuna partita caricata per la stagione {season}. Per aggiungerne una: carica "
                "il file .dvw dello scout sul Drive, aggiungi una riga in config/matches.csv "
                "(season, leg, opponent, path relativo alla cartella della stagione, active=1) "
                "e fai il check terne in notebooks/classifiche.ipynb per i giocatori nuovi."
            )
        st.stop()

    unregistered = sorted({t for m in matches for t in m["unregistered"]})
    if unregistered:
        st.warning(
            "Giocatori Decimo presenti negli scout ma non ancora registrati in "
            "config/player_identities.csv — esclusi da tutti i KPI finché non li "
            "gestisci nel check terne di notebooks/classifiche.ipynb: "
            + ", ".join(unregistered)
        )

    team_df_fixed, player_df_fixed = load_data(season, version)
    match_label_lookup_full = build_match_label_lookup(team_df_fixed)
    with st.sidebar:
        show_pagella = st.toggle(
            "Mostra pagella giocatore", value=False,
            help="Pagella e finding di rendimento: pensati per fine stagione, calcolo più lento.",
        )
        st.caption("Le partite da considerare e le impostazioni dei grafici si scelgono dentro ogni sezione.")
    st.caption(f"Stagione {season} · {len(matches)} partite registrate")

    tab_names = (["Partita", "Report partita", "Distribuzione alzate", "Studio avversario", "Classifiche", "Tabellino",
                  "Andamento KPI"] + (["Pagella"] if show_pagella else []))
    tabs = st.tabs(tab_names)
    with tabs[0]:
        render_match_section(season, matches, match_label_lookup_full, version)
    with tabs[1]:
        render_match_report_section(season, matches, match_label_lookup_full, version)
    with tabs[2]:
        render_distribution_section(matches, match_label_lookup_full)
    with tabs[3]:
        render_opponent_section(season, version)
    with tabs[4]:
        render_classifiche_section(season, matches, match_label_lookup_full, version)
    with tabs[5]:
        render_tabellino_section(matches, match_label_lookup_full)
    with tabs[6]:
        render_trend_tab(season, version, matches, team_df_fixed, player_df_fixed, match_label_lookup_full)
    if show_pagella:
        with tabs[7]:
            rec_vote_for_report = DEFAULT_REC_VOTE
            player_report, player_report_missing = load_player_report(season, rec_vote_for_report, version)
            season_report = load_player_season_report(season, rec_vote_for_report, version)
            team_df_so, player_df_so = load_attacco_so_data(season, rec_vote_for_report, version)
            team_df_all = pd.concat([team_df_fixed, team_df_so], ignore_index=True)
            # player_df_all: senza soglia minima attacchi e su tutta la stagione — è la
            # base su cui src.player_report ha calcolato i finding della pagella, i
            # grafici di evidenza devono usare la stessa serie. Include anche
            # "Attacco Alzato" (ruolo palleggiatore).
            player_df_all = pd.concat([player_df_fixed, player_df_so, load_setter_kpi_data(season, version)],
                                      ignore_index=True)
            render_player_report_section(
                player_report, player_report_missing, season_report, player_df_all, team_df_all,
                build_match_axis(team_df_all),
            )


def render_trend_tab(season, version, matches, team_df_fixed, player_df_fixed, match_label_lookup):
    """Tab "Andamento KPI": prima l'ultima partita contro le precedenti, poi
    lungo la stagione (linee partita per partita, andata contro ritorno per
    avversario) con le loro impostazioni, infine palla alta e palla veloce."""
    render_form_section(season, matches, version)
    st.divider()
    st.header("Lungo la stagione")
    all_seqs = sorted(match_label_lookup)
    with st.expander("Partite e impostazioni dei grafici lungo la stagione", expanded=False):
        selected_seqs = match_picker(all_seqs, match_label_lookup, key=f"partite_{season}")
        c1, c3 = st.columns(2)
        c1.slider("Smoothing linee di trend (kernel gaussiano)", 0.0, 3.0, 1.2, 0.1, key="bandwidth",
                  help="0 = linea grezza punto a punto; valori più alti = curva più smussata")
        min_attacks = c3.number_input(
            "Soglia minima attacchi (per giocatore/partita)", min_value=0, value=0, step=1,
            help='Per i KPI della famiglia "attacco": sotto questo numero di attacchi della stessa famiglia, in quella '
                 "partita il giocatore risulta senza dati per quel KPI (non uno zero). 0 = nessun filtro.",
        )
    st.caption(f"{len(selected_seqs)} di {len(all_seqs)} partite · smoothing {st.session_state['bandwidth']:.1f} · "
               f"soglia attacchi {min_attacks} (per cambiarle apri le impostazioni qui sopra)")
    if not selected_seqs:
        st.info("Seleziona almeno una partita nelle impostazioni qui sopra.")
        return

    team_df_so, player_df_so = load_attacco_so_data(season, DEFAULT_REC_VOTE, version)
    team_df_all = pd.concat([team_df_fixed, team_df_so], ignore_index=True)
    player_df_all = pd.concat([player_df_fixed, player_df_so, load_setter_kpi_data(season, version)], ignore_index=True)
    team_df = _filter_by_matches(team_df_all, selected_seqs)
    player_df = apply_min_attacks_threshold(_filter_by_matches(player_df_all, selected_seqs), min_attacks)
    match_outcomes_df = load_match_outcomes(season, version)
    present_labels = set(team_df["x_label"])
    x_axis_order = [x for x in get_x_axis_order(season) if x in present_labels]
    entity_options = ["Squadra"] + sorted(player_df["player"].unique())
    match_axis = build_match_axis(team_df)

    render_season_lines_section(team_df, player_df, entity_options, match_axis, match_outcomes_df)
    st.divider()
    render_kpi_section(team_df, player_df, x_axis_order, entity_options)
    st.divider()
    render_mvp_trend_section(matches, selected_seqs, match_label_lookup, version)
    st.divider()
    render_ball_type_section(matches, selected_seqs, match_label_lookup)


if __name__ == "__main__":
    main()
