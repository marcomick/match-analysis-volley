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
from src.dvw import decimo_side, normalize_team_name, parse_dvw, read_dvw_header, role_codes, team_side
from src.efficiency import (
    SET_TABELLINO_TOTAL,
    compute_set_tabellino,
    export_set_tabellino_to_xlsx,
)
from src.match_report import (
    SIDE_OUT_LABELS,
    SIDE_OUT_OUTCOMES,
    rotation_difference_test,
    side_out_breakdown,
    side_out_rallies,
    POINT_SOURCE_COLORS_DARK,
    POINT_SOURCE_COLORS_LIGHT,
    POINT_SOURCES,
    RIGORI,
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
from src.match_report_pdf import NOT_APPLICABLE, build_match_report_pdf, build_mvp_rules_pdf
from src.social_graphics import (
    LOGO_PATH, PHOTOS_FOLDER, build_mvp_story, build_result_story, find_player_photo, opponent_logo_path,
    result_story_inputs, save_opponent_logo,
)
from src.mvp import (
    FUNDAMENTAL_LABELS, FUNDAMENTALS, match_awards, match_mvp, mvp_by_match_long, mvp_highlights, player_mvp_detail, scoring_rules,
    season_mvp,
)
from src.opponent_report_pdf import build_opponent_report_pdf
from src.player_report import build_player_report_base
from src.rankings import (
    build_ace_man,
    build_best_attack,
    build_best_block,
    build_best_rec,
    build_rigoristi,
    build_spike_guarantee,
    build_srv_shame,
    calculate_player_scores_by_type,
    without_rigori,
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
    RECEIVE_ROWS,
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
    """Le 6 classifiche di classifiche.ipynb, più i Rigoristi se nelle partite
    c'è almeno un rigore: [(titolo, tabella)]. Block Monster senza i rigori
    (richiesta dell'utente, 2026-11-02; esclusioni del muro per entrambe)."""
    srv = calculate_player_scores_by_type(season_df, "battuta", ignore_list=ignore["battuta"])
    att = calculate_player_scores_by_type(season_df, "attacco", ignore_list=ignore["attacco"])
    blk = calculate_player_scores_by_type(without_rigori(season_df), "muro", ignore_list=ignore["muro"])
    rec = calculate_player_scores_by_type(season_df, "ricezione", ignore_list=ignore["ricezione"])
    rigoristi = build_rigoristi(season_df, ignore_list=ignore["muro"])
    return [
        ("Ace Man", build_ace_man(srv)),
        ("Spike Leader", build_best_attack(att)),
        ("Spike Guarantee", build_spike_guarantee(att, min_attacchi=min_attacchi)),
        ("Block Monster", build_best_block(blk)),
        *([("Rigoristi", rigoristi)] if not rigoristi.empty else []),
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
    "Punti": "Punti", "Errori": "Errori", "p-e": "p-e/m", "Muri punto": "Muri punto", RIGORI: RIGORI,
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
        "side_out": side_out_rallies(source),
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
    if rules.get("premio"):
        st.markdown(
            f"**Miglior ricevitore della partita** (migliore efficienza di ricezione della squadra, almeno "
            f"{rules['min_ricezioni_per_set']:.1f}".replace(".", ",") + " ricezioni a set): premio di {_signed(rules['premio'])} meno i punti "
            "fatti in attacco, battuta e muro, mai sotto zero. Così può vincere anche il libero, e chi ha già fatto "
            "una grande partita in attacco non prende punti in più.")
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
    if "premio" in mvp and (mvp["premio"] > 0).any():
        shown["Premio ricezione"] = ["" if not v else _signed(v) for v in mvp["premio"]]
    shown["Totale"] = [_signed(v) for v in mvp["totale"]]
    shown["Set giocati"] = [f"{int(a)}/{int(b)}" for a, b in zip(mvp["set_giocati"], mvp["set_partita"])]
    shown["Punteggio"] = [_signed(v) for v in mvp["punteggio"]]
    return shown


def render_player_mvp_detail(match, mvp):
    """Le azioni di un giocatore con il loro valore MVP (src.mvp.
    player_mvp_detail), la quota del palleggiatore e la correzione per i set
    giocati."""
    giocatore = st.selectbox("Giocatore", list(mvp["giocatore"]), key="mvp_detail_player")
    detail = player_mvp_detail(load_report_source(match["file_path"]), giocatore)
    actions, row, cfg = detail["azioni"], detail["riga"], detail["config"]
    shown = pd.DataFrame({
        "Fondamentale": actions["fondamentale"], "Azione": actions["azione"], "Volte": actions["volte"],
        "Valore": ["0" if v == 0 else _signed(v) for v in actions["valore"]],
        "Totale": ["0" if v == 0 else _signed(v) for v in actions["totale"]],
    })

    def style(_):
        css = pd.DataFrame("", index=shown.index, columns=shown.columns)
        css["Totale"] = ["color: #1a7f37; font-weight: bold" if v > 0 else "color: #cf222e; font-weight: bold"
                         if v < 0 else "color: #8c959f" for v in actions["totale"]]
        return css

    st.dataframe(shown.style.apply(style, axis=None), hide_index=True, width="stretch",
                 height=35 * (len(shown) + 1) + 3)
    lines = []
    if row.get("alzati_n") and not pd.isna(row.get("alzati_n")):
        n, pt, pem, expected = int(row["alzati_n"]), int(row["alzati_pt"]), int(row["alzati_pem"]), row["alzati_attesi"]
        q = cfg["palleggiatore"]["quota"]
        dec = lambda v, d: f"{v:.{d}f}".replace(".", ",")
        lines.append(
            f"**Quota da palleggiatore**: {n} attacchi alzati dopo ricezione #, + o ! e free ball, con {pt} punti e "
            f"{pt - pem} errori o murate, cioè p-e/m {pem:+d}. Di solito sarebbe {_signed(expected, 1)} "
            f"({dec(cfg['palleggiatore']['pem_medio_per_attacco'], 2)} × {n}). Quota: ({pem} − {dec(expected, 1)}) × "
            f"{dec(q, 2)} = **{_signed(q * (pem - expected))}**, già nella colonna Alzata."
        )
    if row.get("premio"):
        p = cfg["miglior_ricevitore"]["premio"]
        other = row["attacco"] + row["battuta"] + row["muro"]
        lines.append(
            f"**Premio miglior ricevitore**: efficienza {row['ric_eff']:.0f}% su {int(row['ric_n'])} ricezioni, la migliore "
            f"della squadra. Premio = {_signed(p)} meno i punti fatti in attacco, battuta e muro ({_signed(other)}) = "
            f"**{_signed(row['premio'])}**, nella riga \"Premio\".")
    lines.append(f"**Totale**: {_signed(row['totale'])}.")
    if row["totale"] > 0 and row["set_giocati"] < row["set_partita"]:
        k = cfg["set_fittizi"]
        lines.append(f"**Set giocati**: {int(row['set_giocati'])} su {int(row['set_partita'])}, quindi punteggio = "
                     f"{_signed(row['totale'])} × ({int(row['set_partita'])} + {k}) / ({int(row['set_giocati'])} + {k}) "
                     f"= **{_signed(row['punteggio'])}**.")
    else:
        lines.append(f"**Punteggio**: {_signed(row['punteggio'])}"
                     + (" (ha giocato tutti i set)." if row["set_giocati"] >= row["set_partita"]
                        else " (punteggio negativo: non si rapporta ai set giocati)."))
    st.markdown("  \n".join(lines))


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
    awards = match_awards(load_report_source(match["file_path"]), mvp)
    parts = []
    if awards["ricevitore"]:
        r = awards["ricevitore"]
        parts.append(f"**Miglior ricevitore** {r['giocatore']} ({r['eff']:.0f}% su {r['n']}, premio {_signed(r['premio'])})")
    if awards["attaccante"]:
        a = awards["attaccante"]
        parts.append(f"**Miglior attaccante** {a['giocatore']} ({a['eff']:.0f}% su {a['n']}, {a['pt']} punti)")
    if awards["muro"]:
        parts.append(f"**Miglior muro** {awards['muro']['giocatore']} ({awards['muro']['pt']} muri punto)")
    if parts:
        st.markdown(" · ".join(parts))
    st.dataframe(mvp_display_table(mvp), hide_index=True, width="stretch", height=35 * (len(mvp) + 1) + 3)
    with st.expander("Dettaglio di un giocatore: da dove arrivano i suoi punti MVP"):
        render_player_mvp_detail(match, mvp)
    with st.expander("Come si guadagnano e si perdono i punti MVP"):
        render_mvp_rules()
        st.download_button("⬇️ Le regole in una pagina (PDF, da mandare al gruppo)",
                           data=build_mvp_rules_pdf(scoring_rules()), file_name="Punteggio MVP.pdf",
                           mime="application/pdf", key="mvp_rules_pdf")



ROTATIONS = [f"P{z}" for z in range(1, 7)]


def _pp(v):
    """Punti percentuali con segno e colore: verde se aiuta, rosso se pesa."""
    color = "#1a7f37" if v >= 3 else "#cf222e" if v <= -3 else "inherit"
    return f"<b style='color:{color}'>{'0' if round(v) == 0 else f'{v:+.0f}'}</b>"


def _side_out_tiles_html(rallies, scope="della partita nei set scelti"):
    """Il bilancio di ogni rotazione: quante ricezioni servono per fare un
    cambio palla (e quindi ruotare), i cambi palla vinti su scambi giocati e
    lo scarto dalla media (`scope`: di cosa) diviso tra ricezione e quello
    che facciamo dopo la ricezione (src.match_report.side_out_breakdown)."""
    b = side_out_breakdown(rallies)
    overall = rallies["vinto"].mean()
    tiles = []
    for rot in ROTATIONS:
        if rot not in b.index:
            tiles.append(f"<div class='so-tile'><div class='so-rot'>{rot}</div><div class='so-val'>-</div>"
                         "<div class='so-sub'>nessuno scambio</div></div>")
            continue
        r = b.loc[rot]
        per_so = f"{r['ricezioni_per_cambio']:.1f}".replace(".", ",") if r["vinti"] else "-"
        diff = r["scarto"]
        sign = "▲" if diff >= 5 else "▼" if diff <= -5 else "="
        color = "#1a7f37" if diff >= 5 else "#cf222e" if diff <= -5 else "inherit"
        rec_eff = "-" if pd.isna(r["ric_eff"]) else f"{r['ric_eff']:.0f}%"
        att_eff = "-" if pd.isna(r["att_eff"]) else f"{r['att_eff']:.0f}%"
        parts = {"la ricezione": r["parte_ricezione"], "il dopo ricezione": r["parte_dopo"]}
        worst = min(parts, key=parts.get)
        verdict = (f"pesa di più {worst}" if diff <= -5 and parts[worst] < 0 else
                   f"aiuta di più {max(parts, key=parts.get)}" if diff >= 5 else "in media")
        tiles.append(
            f"<div class='so-tile'><div class='so-rot'>{rot}</div>"
            f"<div class='so-val'>{per_so}<span class='so-of'> ricezioni per cambio palla</span></div>"
            f"<div class='so-sub'>{int(r['vinti'])} cambi palla su {int(r['scambi'])} · {r['perc']:.0f}%</div>"
            f"<div class='so-diff' style='color:{color}'>{sign} {diff:+.0f} punti % sulla media</div>"
            f"<div class='so-split'>Ricezione {_pp(r['parte_ricezione'])} <span class='so-of'>(eff {rec_eff})</span><br>"
            f"Dopo ricezione {_pp(r['parte_dopo'])} <span class='so-of'>(att {att_eff})</span></div>"
            f"<div class='so-verdict'>{verdict}</div></div>"
        )
    return (
        "<style>.so-grid{display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin:4px 0 4px}"
        ".so-tile{border:1px solid rgba(128,128,128,.35);border-radius:8px;padding:8px 10px}"
        ".so-rot{font-size:.8rem;opacity:.7;font-weight:600}.so-val{font-size:1.6rem;font-weight:700;line-height:1.2}"
        ".so-of{font-size:.78rem;font-weight:400;opacity:.75}.so-sub{font-size:.8rem;opacity:.8}"
        ".so-diff{font-size:.78rem;font-weight:600;margin-top:2px}"
        ".so-split{font-size:.8rem;margin-top:6px;padding-top:5px;border-top:1px solid rgba(128,128,128,.25)}"
        ".so-verdict{font-size:.75rem;font-style:italic;opacity:.8;margin-top:3px}</style>"
        f"<div class='so-grid'>{''.join(tiles)}</div>"
        f"<div style='font-size:.8rem;opacity:.7'>Media {scope}: {100 * overall:.0f}% "
        f"({int(rallies['vinto'].sum())} cambi palla su {len(rallies)} ricezioni, "
        + f"{len(rallies) / max(1, rallies['vinto'].sum()):.1f}".replace(".", ",")
        + " ricezioni per cambio palla). Lo scarto di ogni rotazione dalla media è diviso in due parti che "
        "sommate lo danno: <b>Ricezione</b> = quanto pesa ricevere peggio o meglio del solito (ace compresi), "
        "<b>Dopo ricezione</b> = quanto pesa, a parità di voto di ricezione, vincere più o meno scambi del solito "
        "(attacco dopo ricezione e seguito dello scambio). Tra parentesi l'efficienza di ricezione e quella "
        "dell'attacco dopo ricezione.</div>"
    )


def _attack_eff_text(rallies):
    v = rallies["attacco"].dropna()
    return f"{100 * ((v == '#').sum() - v.isin(['=', '/']).sum()) / len(v):.0f}%" if len(v) else "-"


def _side_out_detail_table(rallies):
    """Rotazioni in riga, esiti in colonna (prima i vinti, poi i persi), con
    il numero di scambi: fondo verde o rosso più intenso quanti più sono."""
    won_keys = [k for k, _, w in SIDE_OUT_OUTCOMES if w]
    lost_keys = [k for k, _, w in SIDE_OUT_OUTCOMES if not w]
    counts = rallies.groupby(["rotazione", "esito"]).size().unstack(fill_value=0)
    counts = counts.reindex(index=ROTATIONS, columns=won_keys + lost_keys, fill_value=0)
    columns = pd.MultiIndex.from_tuples([("Vinti", SIDE_OUT_LABELS[k]) for k in won_keys]
                                        + [("Persi", SIDE_OUT_LABELS[k]) for k in lost_keys])
    shown = pd.DataFrame(counts.to_numpy(), index=ROTATIONS, columns=columns)
    shown[("", "Saldo")] = counts[won_keys].sum(axis=1) - counts[lost_keys].sum(axis=1)
    peak = max(1, int(counts.to_numpy().max()))

    def style(_):
        css = pd.DataFrame("", index=shown.index, columns=shown.columns)
        for col in shown.columns:
            group = col[0]
            for rot in ROTATIONS:
                v = shown.loc[rot, col]
                if group == "Vinti" and v:
                    css.loc[rot, col] = f"background-color: rgba(26,127,55,{0.12 + 0.55 * v / peak:.2f})"
                elif group == "Persi" and v:
                    css.loc[rot, col] = f"background-color: rgba(207,34,46,{0.12 + 0.55 * v / peak:.2f})"
                elif group == "":
                    css.loc[rot, col] = ("font-weight: bold; color: "
                                         + ("#1a7f37" if v > 0 else "#cf222e" if v < 0 else "inherit"))
        return css

    formats = {c: (lambda v: f"{v:+d}" if v else "0") if c == ("", "Saldo") else (lambda v: str(v) if v else "")
               for c in shown.columns}
    return shown.style.apply(style, axis=None).format(formats)


def render_side_out_rotations(view):
    """Side-out per rotazione: come finiscono gli scambi iniziati con la
    battuta avversaria (src.match_report.side_out_rallies)."""
    st.subheader("Side-out per rotazione: come finiscono gli scambi in ricezione")
    data = view["side_out"]
    if data.empty:
        st.info("Nessuno scambio in ricezione rilevato.")
        return
    sets = sorted(data["set"].unique())
    chosen = st.multiselect("Set", sets, default=sets, key="partita_so_rot_sets", format_func=lambda s_: f"Set {s_}")
    rallies = data[data["set"].isin(chosen)]
    if rallies.empty:
        st.info("Seleziona almeno un set.")
        return
    st.caption(
        "Solo gli scambi iniziati con la battuta avversaria. Rotazione = zona del nostro palleggiatore a inizio "
        "scambio; accanto, cambi palla vinti su scambi giocati. Attacco dopo ricezione = il nostro attacco subito "
        "dopo la ricezione (al più con un'alzata in mezzo): dopo una ricezione / o un tocco avversario non c'è. "
        "\"Difeso\" vuol dire che lo scambio è continuato. Pochi scambi per rotazione: indica dove guardare nel "
        "video, non è una prova statistica."
    )
    st.html(_side_out_tiles_html(rallies))
    st.markdown("**Come sono finiti gli scambi, rotazione per rotazione**")
    st.dataframe(_side_out_detail_table(rallies), width="stretch")

    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown("**Cambi palla per rotazione e set** (vinti su giocati · efficienza dell'attacco dopo ricezione)")
        won = (data[data["vinto"]].groupby(["rotazione", "set"]).size().unstack(fill_value=0)
               .reindex(index=ROTATIONS, columns=sets, fill_value=0))
        played = data.groupby(["rotazione", "set"]).size().unstack(fill_value=0).reindex(index=ROTATIONS, columns=sets,
                                                                                          fill_value=0)
        won["Partita"], played["Partita"] = won.sum(axis=1), played.sum(axis=1)
        cols = sets + ["Partita"]
        names = {s_: f"Set {s_}" if s_ != "Partita" else s_ for s_ in cols}
        att_eff = {s_: {rot: _attack_eff_text(data[(data["rotazione"] == rot)
                                                   & ((data["set"] == s_) if s_ != "Partita" else True)])
                        for rot in ROTATIONS} for s_ in cols}
        shown = pd.DataFrame({names[s_]: [f"{int(w)}/{int(p)} · att {att_eff[s_][rot]}" if p else "-"
                                          for rot, w, p in zip(ROTATIONS, won[s_], played[s_])] for s_ in cols},
                             index=ROTATIONS)
        average = data["vinto"].mean()

        def style(_):
            # rosso sotto la media della partita, verde sopra, più intenso quanto più si scosta
            css = pd.DataFrame("", index=shown.index, columns=shown.columns)
            for s_ in cols:
                for r in ROTATIONS:
                    p = played.loc[r, s_]
                    if not p:
                        continue
                    diff = won.loc[r, s_] / p - average
                    alpha = min(0.65, 0.1 + abs(diff) * 1.1)
                    if abs(diff) >= 0.05:
                        rgb = "26,127,55" if diff > 0 else "207,34,46"
                        css.loc[r, names[s_]] = f"background-color: rgba({rgb},{alpha:.2f})"
            css["Partita"] = css["Partita"] + "; font-weight: bold; border-left: 2px solid rgba(128,128,128,0.6)"
            return css

        st.dataframe(shown.style.apply(style, axis=None), width="stretch")
        st.caption(f"Fondo verde sopra, rosso sotto la media dei cambi palla della partita ({100 * average:.0f}% su "
                   "tutti i set). att = (punti - errori - murate) / attacchi dopo ricezione; - se non ce ne sono.")
    with c2:
        st.markdown("**I loro battitori** (set scelti)")
        srv = rallies.groupby("battitore").agg(
            scambi=("vinto", "size"), persi=("vinto", lambda v: int((~v).sum())),
            ace=("esito", lambda e: int((e == "ace").sum())),
        ).sort_values(["persi", "scambi"], ascending=False)
        srv.columns = ["Scambi", "Persi da noi", "Ace"]
        st.dataframe(srv, width="stretch")

    with st.expander("Gli scambi uno per uno"):
        rot = st.radio("Rotazione", ROTATIONS, horizontal=True, key="partita_so_rot_detail")
        detail = rallies[rallies["rotazione"] == rot]
        if detail.empty:
            st.caption("Nessuno scambio in ricezione in questa rotazione nei set scelti.")
        else:
            st.dataframe(pd.DataFrame({
                "Set": detail["set"], "Punteggio": [f"{a}-{b}" for a, b in zip(detail["punti_noi"], detail["punti_loro"])],
                "Battuta": detail["battitore"],
                "Ricezione": [f"{c or ''} {v or ''}".strip() or "-" for c, v in zip(detail["ricevitore"], detail["ricezione"])],
                "Attacco dopo ricezione": [f"{a} {v} ({p})" if a else "-" for a, v, p in
                                  zip(detail["attaccante"], detail["attacco"], detail["palla"])],
                "Esito": [("✓ " if w else "✗ ") + SIDE_OUT_LABELS[e] for w, e in zip(detail["vinto"], detail["esito"])],
                "Ultima azione": detail["ultima"],
            }), hide_index=True, width="stretch")
            st.caption("Punteggio nostro-loro prima dello scambio; tipo di palla: H alta, T super, Q primo tempo, "
                       "N \"7\". Dove il tipo di alzata non è rilevato è stimato: super dopo ricezione #, + o ! e "
                       "free ball, alta dopo ricezione -, primo tempo per i centrali.")


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

    # 2a-bis) side-out per rotazione
    render_side_out_rotations(view)

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


KPI_SCORE_HEADERS = {"tot": "tot", "pt": "pt", "err": "err", "mur": "mur", "pe": "p-e/m", "eff": "eff"}
KPI_RECEIVE_HEADERS = {"tot": "tot", "hash": "#", "plus": "+", "ex": "!", "neg": "-", "err": "err", "eff": "eff"}


def _kpi_part_frame(table, part, headers, devs):
    """Una delle due tabelle del giocatore (fanno punto / ricezione e difesa),
    con le caselle senza senso barrate e i colori rispetto alla media."""
    rows = table[table["kpi"].isin(part)] if isinstance(part, tuple) else table[~table["kpi"].isin(RECEIVE_ROWS)]
    na = NOT_APPLICABLE

    def cell(r, c):
        if c in na.get(r["kpi"], ()):
            return "╱"
        v = r.get(c)
        if c == "pe" and v is not None and not pd.isna(v):
            return f"{int(v):+d}"
        return _fmt_cell(v, c == "eff")

    recs = rows.to_dict("records")
    shown = pd.DataFrame({"": [("  " + r["label"]) if r["kpi"] == "fb" else r["label"] for r in recs],
                          **{h: [cell(r, c) for r in recs] for c, h in headers.items()}})
    all_kpis, kpis = list(table["kpi"]), list(rows["kpi"])

    def style(_):
        css = pd.DataFrame("", index=shown.index, columns=shown.columns)
        for (row, col), z in devs.items():
            if all_kpis[row] in kpis and col in headers:
                css.iloc[kpis.index(all_kpis[row]), list(headers).index(col) + 1] = _deviation_css(z)
        for i, r in enumerate(recs):
            for j, c in enumerate(headers):
                if c in na.get(r["kpi"], ()):
                    css.iloc[i, j + 1] = "background-color: #e5e7eb; color: #cf222e; text-align: center"
        if not isinstance(part, tuple):
            css.iloc[-1] = [x + "; font-weight: bold" for x in css.iloc[-1]]
        return css

    return shown, style


def render_player_kpi_tables(tables, deviations, n_history):
    """Il tabellino per giocatore del report (src.set_analysis.player_kpi_table),
    tre per riga: fondamentali che fanno punto e, sotto, ricezione e difesa,
    con le celle colorate rispetto alla media personale."""
    st.caption(
        f"Colori: confronto con la media del giocatore nelle {n_history} partite precedenti (comprese quelle della "
        f"stagione precedente se servono), verde meglio del solito, rosso peggio, più intenso quanto più si discosta; "
        f"bianco = in media o meno di {MIN_HISTORY} partite con quel dato. err = errori (in attacco solo gli attacchi "
        "sbagliati), mur = murate, p-e/m = punti − errori − murate, eff = efficienza. Attacco SO = dopo ricezione, "
        "BP = tutti gli altri (free ball compresa). Ricezione e difesa: quante per voto (#, +, !, -), "
        "err errori (ricezione: rimandate di là e ace subiti). ╱ = non ha senso per quel fondamentale."
    )
    cols = st.columns(3)
    for i, (name, table) in enumerate(tables.items()):
        devs = deviations.get(name, {})
        with cols[i % 3]:
            st.markdown(f"**{name}**")
            for part, headers in ((None, KPI_SCORE_HEADERS), (RECEIVE_ROWS, KPI_RECEIVE_HEADERS)):
                shown, style = _kpi_part_frame(table, part, headers, devs)
                if shown.empty:
                    continue
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
        "contr_tot": "Contrattacco n", "fb_eff": "Free ball eff", "fb_tot": "Free ball n", "muro_pt": "Muri punto", "rigori": '"Rigori"',
    }
    tab = players[[c for c in names if c in players.columns]].rename(columns=names)
    pct_cols = ["Battuta eff", "Ricezione eff", "Attacco eff", "Side-out eff", "Contrattacco eff", "Free ball eff"]
    for c in tab.columns:
        if c != "Giocatore":
            tab[c] = [_fmt_cell(v, c in pct_cols) for v in tab[c]]
    st.dataframe(tab, hide_index=True, width="stretch", height=35 * (len(tab) + 1) + 3)
    st.caption("Errori = battute, attacchi e alzate sbagliate ed errori di muro (gli attacchi murati sono in \"Murati\", "
               "gli ace subiti in \"Ric. err\"), divisi per fondamentale. p-e/m = punti − errori − murate.")


# Commenti del report salvati per partita (richiesta dell'utente, 2026-11-09):
# nella cartella della stagione su Drive, come gli scout (mai nel repo), chiave =
# nome del file dello scout.
REPORT_COMMENTS_FILE = "commenti_report.json"


def load_report_comments(season):
    path = build_base_path(season) / REPORT_COMMENTS_FILE
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def save_report_comment(season, match, text):
    comments = load_report_comments(season)
    key = os.path.basename(match["file_path"])
    if text.strip():
        comments[key] = text
    else:
        comments.pop(key, None)
    with open(build_base_path(season) / REPORT_COMMENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(comments, f, ensure_ascii=False, indent=1)


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
    key = f"report_commento_{seq}"
    if key not in st.session_state:  # il commento salvato per questa partita
        st.session_state[key] = load_report_comments(season).get(os.path.basename(by_seq[seq]["file_path"]), "")
    comment = st.text_area(
        "Commento (dopo l'MVP e i punti per causa)", key=key, height=200,
        placeholder="Cosa è andato bene, cosa no, su cosa lavorare in settimana...",
        help="Formattazione: **testo** in grassetto; le righe che iniziano con \"- \" diventano un elenco puntato. "
             f"Si salva da solo ({REPORT_COMMENTS_FILE} nella cartella della stagione su Drive).",
        on_change=lambda: save_report_comment(season, by_seq[seq], st.session_state[key]),
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
                mvp_ranking=mvp, awards=match_awards(source, mvp) if not mvp.empty else None,
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

    render_instagram_stories(season, by_seq[seq], version)


def render_instagram_stories(season, match, version):
    """Le due storie Instagram della partita (src.social_graphics): MVP e
    risultato, 1080 x 1920, con anteprima e download."""
    st.subheader("Storie per Instagram")
    source = load_report_source(match["file_path"])
    data = result_story_inputs(source, match["opponent"], match["official_result"])
    mvp = load_match_mvp(match["file_path"], version)
    if mvp.empty:
        st.info("Nessuna azione rilevata per i nostri giocatori.")
        return
    row = mvp.iloc[0].to_dict()
    numero, _, cognome = str(row["giocatore"]).partition(" ")
    folder = build_base_path(season).parent / PHOTOS_FOLDER
    drive_photo = find_player_photo(cognome, folder)
    opp_logo = opponent_logo_path(match["opponent"])

    c1, c2 = st.columns(2)
    with c1:
        uploaded = st.file_uploader(
            f"Foto di {cognome} (facoltativa)", type=["jpg", "jpeg", "png"], key=f"story_photo_{match['match_seq']}",
            help=f"Se non la carichi uso quella del roster su Drive ({PHOTOS_FOLDER}/{cognome}.png), se c'è; "
                 "altrimenti il numero di maglia.",
        )
    with c2:
        logo_upload = st.file_uploader(
            f"Logo di {match['opponent']} (facoltativo)", type=["png", "jpg", "jpeg"],
            key=f"story_logo_{match['match_seq']}",
            help="Meglio un PNG con lo sfondo trasparente. Viene salvato in assets/loghi_avversari/ e usato anche "
                 "nelle prossime partite con la stessa squadra.",
        )
    if logo_upload is not None:
        opp_logo = save_opponent_logo(match["opponent"], logo_upload.getvalue())
    photo = uploaded.getvalue() if uploaded is not None else drive_photo
    st.caption(
        ("Foto: caricata qui." if uploaded is not None else
         f"Foto dal roster su Drive: {drive_photo.name}." if drive_photo else
         f"Nessuna foto di {cognome} in {PHOTOS_FOLDER}: al suo posto il numero di maglia.")
        + (f" Logo di {match['opponent']}: {opp_logo.name}." if opp_logo.exists() else
           f" Nessun logo di {match['opponent']}: c'è solo il nome.")
    )
    logos = {True: LOGO_PATH if LOGO_PATH.exists() else None, False: opp_logo if opp_logo.exists() else None}
    mvp_png = build_mvp_story(row, data["match_line"], data["date"], photo=photo)
    result_png = build_result_story(data["teams"], data["sets_won"], data["partials"], data["kpis"], data["date"],
                                    mvp=(cognome, numero), logos=[logos[ours] for _, ours in data["teams"]])
    base = f"{data['teams'][0][0]} - {data['teams'][1][0]} {data['date'] or ''}".strip().replace("/", "-")
    c1, c2 = st.columns(2)
    for col, png, label, suffix in ((c1, mvp_png, "MVP", "MVP"), (c2, result_png, "Risultato", "risultato")):
        with col:
            st.image(png, caption=label, width=330)
            st.download_button(f"⬇️ Scarica storia {label.lower()}", data=png, file_name=f"{base} {suffix}.png",
                               mime="image/png", key=f"story_{suffix}_{match['match_seq']}")


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
    # chi non gioca nelle partite scelte non compare tra le opzioni ma resta
    # escluso: senza questo, scegliere poche partite cancellava le esclusioni
    absent = {t: [c for c in names if c not in cognomi] for t, names in load_ranking_settings(season)["ignore"].items()}
    save_ranking_settings(season, {"min_attacchi": int(min_attacchi), "min_ricezioni": int(min_ricezioni),
                                   "ignore": {t: ignore[t] + absent.get(t, []) for t in ignore}})

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
        "sigle": role_codes(source["parsed"]["players"], source["side"]),
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


def opponent_labeler(sigle):
    """'11 Dolci' o 'Dolci' → 'Dolci (11, L)': numero e sigla di ruolo
    (src.dvw.role_codes) per ogni avversario nominato."""
    def label(text):
        text = str(text).strip()
        head, _, rest = text.partition(" ")
        cognome = rest if head.isdigit() and rest else text
        key = cognome.replace("\u2019", "'")
        if key not in sigle:
            return text
        numero, sigla = sigle[key]
        return f"{cognome} ({numero}, {sigla})" if sigla else f"{cognome} ({numero})"
    return label


def _receiver_text(rec, label=str):
    return (f"**{label(rec['giocatore'])}**: efficienza {rec['eff']:.0f}% su {rec['n']} ricezioni "
            f"({rec['perfette_pos']:.0f}% perfette o positive, {rec['errori']} ace subiti)")


def render_opponent_summary(df, rows, serves, sigle=None):
    """Riassunto iniziale dello studio dell'avversario (src.opponent_summary);
    i giocatori con numero e sigla di ruolo (`sigle`, opponent_labeler)."""
    label = opponent_labeler(sigle or {})
    st.subheader("In breve")
    c1, c2, c3 = st.columns(3)
    worst, best = worst_receiver(df), best_receiver(df)
    with c1:
        st.markdown("**🎯 Peggior ricevitore**")
        st.markdown(_receiver_text(worst, label) if worst else f"Nessuno con almeno {MIN_RECEPTIONS} ricezioni.")
    with c2:
        st.markdown("**🛡️ Miglior ricevitore**")
        st.markdown(_receiver_text(best, label) if best and best != worst
                    else f"Nessun altro con almeno {MIN_RECEPTIONS} ricezioni." if worst
                    else f"Nessuno con almeno {MIN_RECEPTIONS} ricezioni.")
    srv = dangerous_server(serves)
    with c3:
        st.markdown("**💥 Battitore più pericoloso**")
        if srv:
            st.markdown(f"**{label(srv['giocatore'])}**: la squadra vince il {srv['break']:.0f}% degli scambi sulla sua "
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
            st.markdown(f"**{label(clutch['giocatore'])}**: {clutch['n']} attacchi su {clutch['totale']} "
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
                st.markdown(f"- {situation_text(label(setter), k, r['scostamenti'][0])}")
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
    display = {k: v["names"].most_common(1)[0][0] for k, v in teams.items()}

    # stagione del modello di prestazione: un solo menu con tutte le squadre del
    # girone (anche senza scout), prima le indicazioni del modello e la scheda,
    # poi gli scout della squadra se ci sono
    from src.performance_model import RESULTS_PATH
    model = load_performance_results(RESULTS_PATH.stat().st_mtime if RESULTS_PATH.exists() else 0)
    model_season = model is not None and model["meta"]["config"]["stagioni"][-1] == season
    c1, c2 = st.columns([1, 2])
    if model_season:
        o = model["avversari"]
        o = o[o["chiave"] != "media"]
        to_ts = {k: model.get("nomi_dvw", {}).get(f"{season}|{k}") for k in teams}
        from_ts = {v: k for k, v in to_ts.items() if v}
        next_key, next_date = _pm_next_opponent(model)
        options = o.sort_values("posizione")["chiave"].tolist()

        def label(k):
            x = o[o["chiave"] == k].iloc[0]
            n = len(teams[from_ts[k]]["matches"]) if k in from_ts else 0
            lab = f"{x['nome']} ({int(x['posizione'])}ª, {n} scout)" if pd.notna(x["posizione"]) else x["nome"]
            return lab + (f" — prossima partita, {next_date}" if k == next_key else "")
        with c1:
            ts_key = st.selectbox("Squadra", options, index=options.index(next_key) if next_key in options else 0,
                                  format_func=label, key="opp_team_girone")
        st.subheader("Indicazioni per la partita")
        st.caption(f"Dal modello di prestazione (stima del {model['meta']['data_stima']}): rispetto a come giochiamo "
                   "di solito. Si aggiornano con \"Stima di nuovo il modello\" nella tab \"Modello di prestazione\".")
        _pm_opponent_view(model, ts_key)
        team_key = from_ts.get(ts_key)
        if team_key is None:
            st.info("Nessuno scout di questa squadra nella cartella della stagione: qui sotto non c'è altro.")
            return
        st.divider()
        st.subheader(f"Scout di {display[team_key]}")
    else:
        if not teams:
            st.info(f"Nessuno scout .dvw di altre squadre nella cartella della stagione {season} "
                    f"({build_base_path(season)}, sottocartelle comprese).")
            return
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
    sigle = {}
    for m in sorted(ms, key=lambda m: _date_key(m["header"]["data"])):  # numero e ruolo della partita più recente
        sigle.update(m.get("sigle", {}))
    render_opponent_summary(combined["noi"], all_rows, serves, sigle)
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
        st.info("Serve lo scout .dvw (il tipo di alzata non c'è negli Excel): nessuna partita selezionata lo ha.")
        return
    seqs = sorted(data["match_seq"].unique())
    volume = data.groupby("giocatore")["tot"].sum().sort_values(ascending=False)
    players = st.multiselect("Giocatori", list(volume.index), default=list(volume.index[:4]), key="ball_type_players")
    st.caption("p-e/m = punti − errori − murate negli attacchi del giocatore su quel tipo di alzata, partita per partita "
               "(passa il mouse per il numero di attacchi). Palla alta: alzata alta; palla veloce: super, quick (tutti "
               "gli attacchi dei centrali) e \"7\". Mezza e altro fuori da entrambe. Dove il tipo di alzata non è "
               "rilevato (2025-2026, G1 San Paolo) è stimato: super dopo ricezione #, + o ! e free ball, alta dopo "
               "ricezione - e in contrattacco.")
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


@st.cache_data(show_spinner="Scambi in ricezione...")
def load_side_out(file_path, version):
    return side_out_rallies(load_report_source(file_path))


def render_side_out_season(matches, selected_seqs, match_label_lookup, version):
    """Side-out per rotazione sommato sulle partite scelte: dove siamo più
    deboli in ricezione, accumulando i dati partita dopo partita."""
    st.header("Side-out per rotazione, sulla stagione")
    parts = []
    for m in matches:
        if m["match_seq"] in selected_seqs and str(m.get("file_path", "")).lower().endswith(".dvw"):
            d = load_side_out(m["file_path"], version)
            if not d.empty:
                parts.append(d.assign(match_seq=m["match_seq"]))
    if not parts:
        st.info("Serve lo scout .dvw: nessuna delle partite selezionate lo ha.")
        return
    data = pd.concat(parts, ignore_index=True)
    st.caption(
        f"{len(parts)} partite con lo scout .dvw tra quelle selezionate, {len(data)} scambi iniziati con la battuta "
        "avversaria. Rotazione = zona del nostro palleggiatore a inizio scambio. Ogni volta che facciamo il cambio "
        "palla ruotiamo: conta quante ricezioni ci servono per uscire da una rotazione. Le definizioni degli esiti "
        "sono quelle della tab Partita."
    )
    st.html(_side_out_tiles_html(data, scope="delle partite scelte"))
    p = rotation_difference_test(data)
    if p is not None:
        st.caption(f"Le rotazioni sono davvero diverse tra loro? p = {p:.2f} (test per permutazione sulla percentuale "
                   "di cambi palla): " + ("sotto 0,05, la differenza difficilmente è un caso." if p < 0.05 else
                                           "sopra 0,05, con questi dati la differenza può ancora essere un caso."))
    st.markdown("**Come sono finiti gli scambi, rotazione per rotazione**")
    st.dataframe(_side_out_detail_table(data), width="stretch")

    st.markdown("**Partita per partita** (cambi palla vinti su ricezioni)")
    seqs = sorted(data["match_seq"].unique())
    won = data[data["vinto"]].groupby(["rotazione", "match_seq"]).size().unstack(fill_value=0).reindex(
        index=ROTATIONS, columns=seqs, fill_value=0)
    played = data.groupby(["rotazione", "match_seq"]).size().unstack(fill_value=0).reindex(
        index=ROTATIONS, columns=seqs, fill_value=0)
    names = {ms: f"{ms + 1}. {match_label_lookup.get(ms, ms)}" for ms in seqs}
    shown = pd.DataFrame({names[ms]: [f"{int(w)}/{int(n)}" if n else "-" for w, n in zip(won[ms], played[ms])]
                          for ms in seqs}, index=ROTATIONS)
    average = data["vinto"].mean()

    def style(_):
        css = pd.DataFrame("", index=shown.index, columns=shown.columns)
        for ms in seqs:
            for r in ROTATIONS:
                n = played.loc[r, ms]
                if n:
                    diff = won.loc[r, ms] / n - average
                    if abs(diff) >= 0.05:
                        rgb = "26,127,55" if diff > 0 else "207,34,46"
                        css.loc[r, names[ms]] = f"background-color: rgba({rgb},{min(0.65, 0.1 + abs(diff) * 1.1):.2f})"
        return css

    st.dataframe(shown.style.apply(style, axis=None), width="stretch")
    st.caption(f"Verde sopra, rosso sotto la media delle partite scelte ({100 * average:.0f}%). Su una partita sola "
               "i numeri sono piccoli: contano le rotazioni che restano rosse partita dopo partita.")


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


# ---------------------------------------------------------------------------
# Tab "Modello di prestazione" (src/performance_model.py)
# ---------------------------------------------------------------------------
PM_SITUATION_ORDER = ["R#+", "R!", "R-", "FB", "CA"]
PM_REFERENCE_GREY = "#898781"


def _it(x, dec=1, sign=False):
    """Numero all'italiana (virgola decimale)."""
    s = f"{x:+.{dec}f}" if sign else f"{x:.{dec}f}"
    return s.replace(".", ",")


def _pct(x, dec=0):
    return _it(100 * x, dec) + "%"


@st.cache_data(show_spinner=False)
def load_performance_results(mtime):
    from src.performance_model import load_results
    return load_results()


def _pm_table(df):
    """Tabella senza barra di scorrimento: tutte le righe visibili."""
    st.dataframe(df, hide_index=True, width="stretch", height=35 * (len(df) + 1) + 3)


def _place_labels(x, y, names, x_range, y_range, width_px, height_px):
    """Spostamenti (xshift, yshift in pixel) delle etichette dei punti, scelti
    in modo che non si sovrappongano tra loro né ai punti: per ogni punto la
    prima posizione libera tra sopra, sotto, destra, sinistra e diagonali."""
    sx = width_px / (x_range[1] - x_range[0])
    sy = height_px / (y_range[1] - y_range[0])
    pts = [((xi - x_range[0]) * sx, (yi - y_range[0]) * sy) for xi, yi in zip(x, y)]
    boxes, shifts = [], []

    def overlaps(b):
        if any(not (b[2] < o[0] or b[0] > o[2] or b[3] < o[1] or b[1] > o[3]) for o in boxes):
            return True
        return any(b[0] - 5 < px < b[2] + 5 and b[1] - 5 < py < b[3] + 5 for px, py in pts)

    for (px, py), name in zip(pts, names):
        w, h = 6.6 * len(str(name)) + 4, 14
        candidates = [(0, 13), (0, -13), (w / 2 + 9, 0), (-w / 2 - 9, 0), (w / 2 + 5, 12), (-w / 2 - 5, 12),
                      (w / 2 + 5, -12), (-w / 2 - 5, -12), (0, 26), (0, -26)]
        chosen = candidates[0]
        for dx, dy in candidates:
            b = (px + dx - w / 2, py + dy - h / 2, px + dx + w / 2, py + dy + h / 2)
            if not overlaps(b):
                chosen = (dx, dy)
                break
        dx, dy = chosen
        boxes.append((px + dx - w / 2, py + dy - h / 2, px + dx + w / 2, py + dy + h / 2))
        shifts.append(chosen)
    return shifts


def _pm_layout(height, x_title, left_margin=150):
    layout = base_layout("", x_title, "")
    layout.update(height=height, hovermode="closest", showlegend=True,
                  margin=dict(t=50, b=50, l=left_margin, r=30))
    return layout


def _dot_interval_figure(df, label_col, x_title, color, text_col=None, ref_col=None, ref_name=None,
                         scale=100, zero_line=None, hover_col=None):
    """Pallino = stima, linea = intervallo all'80%; facoltativi un riferimento
    (rombo grigio) e un'etichetta a destra dell'intervallo."""
    colors = theme_colors()
    y = df[label_col].tolist()
    mean, lo, hi = (scale * df[c].to_numpy() for c in ("media", "basso", "alto"))
    fig = go.Figure()
    if ref_col is not None:
        fig.add_trace(go.Scatter(
            x=scale * df[ref_col], y=y, mode="markers", name=ref_name,
            marker=dict(symbol="diamond", size=9, color=PM_REFERENCE_GREY, line=dict(color=colors["surface"], width=2)),
            hovertemplate="%{y}<br>" + ref_name + ": %{x:.1f}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=mean, y=y, mode="markers", name="Stima (pallino) e intervallo all'80% (linea)",
        error_x=dict(type="data", array=hi - mean, arrayminus=mean - lo, thickness=2, width=0, color=color),
        marker=dict(size=11, color=color, line=dict(color=colors["surface"], width=2)),
        customdata=df[hover_col] if hover_col else None,
        hovertemplate=("%{customdata}" if hover_col else "%{y}: %{x:.1f}") + "<extra></extra>"))
    if text_col is not None:
        text_x = np.maximum(hi, scale * df[ref_col].to_numpy()) if ref_col is not None else hi
        fig.add_trace(go.Scatter(
            x=text_x, y=y, mode="text", text=df[text_col], textposition="middle right", showlegend=False,
            textfont=dict(size=12, color=colors["ink"]), hoverinfo="skip"))
    if zero_line is not None:
        fig.add_vline(x=zero_line, line=dict(color=colors["grid"], width=2))
    span = max(hi) - min(lo) if len(df) else 1
    fig.update_layout(**_pm_layout(max(260, 46 * len(df) + 110), x_title,
                                   left_margin=int(max(len(str(v)) for v in y) * 7.2) + 30 if y else 150))
    right = max(hi.max(), (scale * df[ref_col]).max()) if ref_col is not None else hi.max()
    left = min(lo.min(), (scale * df[ref_col]).min()) if ref_col is not None else lo.min()
    fig.update_xaxes(range=[left - 0.05 * span, right + 0.35 * span], zeroline=False)
    fig.update_yaxes(autorange="reversed", gridcolor=colors["grid"])
    return fig


def _pm_example(markdown_text, title="Esempio"):
    """Riquadro con un esempio pratico (icona + titolo, mai solo colore)."""
    with st.container(border=True):
        st.markdown(f"**💡 {title}**  \n{markdown_text}")


def _pm_set_after(r, points_per_set):
    """Set vinti contro una squadra media, prima e dopo `points_per_set` punti
    in più per set (spalmati su tutti gli scambi del set)."""
    from src.performance_model import p_set
    media = r["avversari"].set_index("chiave").loc["media"]
    n = r["esempi"]["set"]["scambi_per_set"]
    d = points_per_set / n
    return p_set(media["so_noi"], media["so_loro"]), p_set(media["so_noi"] + d, media["so_loro"] - d)


def _small_ratio(x, max_den=3):
    """x ≈ n / m con m piccolo: 1,56 → (3, 2) ("3 errori ogni 2 ace")."""
    best = min(((round(x * m), m) for m in range(1, max_den + 1)), key=lambda nm: abs(nm[0] / nm[1] - x))
    return best


def _pm_serve_licence(r):
    """
    Licenza di sbagliare in battuta, rispetto al livello playoff (battitore
    medio delle squadre tra le prime 4 in classifica, indicazione
    dell'utente del 2026-11-11). Squadra: errori massimi nel set con i nostri
    ace e le nostre ricezioni non positive (!, -, /) e, per ogni errore in
    più, ace o ricezioni non positive necessari (arrotondati per eccesso).
    Atleti: con la sua media di errori, quanti ace o quante ricezioni non
    positive dovrebbe fare su 100 battute.
    """
    import math
    e = r.get("esempi", {}).get("battuta")
    if not e or e["battitori"].empty or "riferimento_playoff" not in e:
        return
    wg, wb = e["valore_facile"], e["valore_difficile"]
    po, us = e["riferimento_playoff"], e["riferimento"]
    per_set = e["battute_per_set"]
    ceil = lambda v: max(0, math.ceil(v - 1e-9))

    def value(n, err, ace, diff):       # scambi vinti su n battute (le altre ricevute # o +)
        return ace + wb * diff + wg * (n - err - ace - diff)
    target_100 = value(100, po["errori"], po["ace"], po["difficili"])   # livello playoff su 100 battute

    def allowed(n, ace, diff):          # errori massimi su n battute per stare a livello playoff
        return (value(n, 0, ace, diff) - target_100 * n / 100) / wg

    def aces_needed(n, err, diff):
        return (target_100 * n / 100 - value(n, err, 0, diff)) / (1 - wg)

    def diff_needed(n, err, ace):
        return (target_100 * n / 100 - value(n, err, ace, 0)) / (wb - wg)

    ace_per_err, rec_per_err = ceil(e["ace_per_errore"]), ceil(e["ricezioni_per_errore"])
    s = per_set / 100
    us_set = {k: v * s for k, v in us.items()}
    po_set = {k: v * s for k, v in po.items()}
    max_err = allowed(per_set, us_set["ace"], us_set["difficili"])
    st.markdown("#### Licenza di sbagliare in battuta: il livello playoff")
    st.markdown(
        f"Riferimento: il battitore medio delle squadre tra le prime 4 in classifica "
        f"({', '.join(e['squadre_playoff']) or 'nessuna con abbastanza scout'}). Su un set (circa "
        f"{round(per_set)} battute) loro fanno {_it(po_set['errori'], 1)} errori, {_it(po_set['ace'], 1)} ace e "
        f"{_it(po_set['difficili'], 1)} ricezioni non positive (!, - o /); noi {_it(us_set['errori'], 1)} errori, "
        f"{_it(us_set['ace'], 1)} ace e {_it(us_set['difficili'], 1)} ricezioni non positive.")
    _pm_example(
        f"Con i nostri ace e le nostre ricezioni non positive, **per stare a livello playoff possiamo fare al massimo "
        f"{max(0, math.floor(max_err + 1e-9))} errori in battuta nel set** (oggi ne facciamo "
        f"{_it(us_set['errori'], 1)}). **Per ogni errore in più dobbiamo assicurare {ace_per_err} ace in più, oppure "
        f"{rec_per_err} ricezioni non positive in più.**",
        title="La regola per la squadra")
    rows = []
    for err in range(1, 6):
        rows.append({"Errori in battuta nel set": err,
                     f"Ace necessari nel set (con {round(us_set['difficili'])} ricezioni non positive)":
                         ceil(aces_needed(per_set, err, us_set["difficili"])),
                     f"Ricezioni non positive necessarie nel set (con {_it(us_set['ace'], 0)} ace)":
                         ceil(diff_needed(per_set, err, us_set["ace"]))})
    st.caption("Cosa serve nel set per stare a livello playoff con un certo numero di errori (una cosa o l'altra, "
               "oppure un misto), arrotondato per eccesso:")
    _pm_table(pd.DataFrame(rows))

    lic = e["battitori"].copy()
    lic["concessi"] = lic.apply(lambda x: allowed(100, x["ace_100"], x["difficili_100"]), axis=1)
    lic["ace_servono"] = lic.apply(lambda x: ceil(aces_needed(100, x["errori_100"], x["difficili_100"])), axis=1)
    lic["ric_servono"] = lic.apply(lambda x: ceil(diff_needed(100, x["errori_100"], x["ace_100"])), axis=1)
    # a livello playoff se, sui numeri interi mostrati, fa almeno gli ace che servono oppure crea almeno le
    # ricezioni non positive che servono
    lic["senza"] = ~((lic["ace_100"].round() >= lic["ace_servono"]) | (lic["difficili_100"].round() >= lic["ric_servono"]))
    st.markdown("**Atleta per atleta** (su 100 battute: per un titolare sono circa 25-30 set)")
    no = lic[lic["senza"]]
    if len(no):
        x = no.sort_values("errori_100", ascending=False).iloc[0]
        text = (f"**{x['giocatore']}**: con la sua media di **{round(x['errori_100'])} errori ogni 100 battute** "
                f"dovrebbe fare **{x['ace_servono']} ace** (ne fa {round(x['ace_100'])}), oppure creare "
                f"**{x['ric_servono']} ricezioni non positive** (ne crea {round(x['difficili_100'])}).")
        enough = no[no["battute"] >= 50]
        close = (enough if len(enough) else no).assign(gap=lambda d: d["ace_servono"] - d["ace_100"]) \
            .sort_values("gap").iloc[0]
        if close["giocatore"] != x["giocatore"]:
            text += (f"  \nIl più vicino è **{close['giocatore']}**: con {round(close['errori_100'])} errori ogni 100 "
                     f"battute gli servono {close['ace_servono']} ace (ne fa {round(close['ace_100'])}) oppure "
                     f"{close['ric_servono']} ricezioni non positive (ne crea {round(close['difficili_100'])}).")
        _pm_example(text)
    yes = lic[~lic["senza"] & (lic["errori_100"] < lic["concessi"] - 1)]
    if len(yes):
        y = yes.assign(m=lambda d: d["concessi"] - d["errori_100"]).sort_values("m", ascending=False).iloc[0]
        _pm_example(
            f"**{y['giocatore']}** a livello playoff si può permettere {round(y['concessi'])} errori ogni 100 battute "
            f"e ne fa {round(y['errori_100'])}: può battere più forte, purché ogni errore in più porti "
            f"{ace_per_err} ace o {rec_per_err} ricezioni non positive.", title="Chi ha margine")
    lic = lic.sort_values("errori_100")
    _pm_table(pd.DataFrame({
        "Battitore": lic["giocatore"], "Battute": lic["battute"],
        "Errori su 100": lic["errori_100"].map(lambda v: f"{v:.0f}"),
        "Ace che servono": lic["ace_servono"], "Ace che fa": lic["ace_100"].map(lambda v: f"{v:.0f}"),
        "Ricezioni non positive che servono": lic["ric_servono"],
        "Ricezioni non positive che crea": lic["difficili_100"].map(lambda v: f"{v:.0f}"),
        "A livello playoff": lic["senza"].map(lambda v: "❌ no" if v else "✅ sì"),
    }))
    st.caption("Su 100 battute, stime del modello (chi ha poche battute resta vicino alla media della squadra). "
               "\"Ace che servono\" con le sue ricezioni non positive, \"ricezioni che servono\" con i suoi ace: basta "
               "una delle due. Ricezioni non positive = !, - o / (l'ace è a parte).")


def _pm_attack_example(r, d, sit):
    """Licenza di sbagliare in attacco, a livello playoff (stesso schema della
    battuta, richiesta dell'utente del 2026-11-11): per la squadra, errori +
    murate massimi nel set nella situazione scelta e punti necessari per ogni
    errore in più; per l'atleta, su 100 attacchi nella sua zona, i punti
    necessari con la sua media di errori e murate."""
    import math
    from src.performance_model import SITUATION_LABELS, ZONE_LABELS
    lic = r.get("esempi", {}).get("attacco", {}).get("licenza", {}).get(sit)
    if not lic:
        return
    c, n = lic["c"], lic["attacchi_per_set"]
    ceil = lambda v: max(0, math.ceil(v - 1e-9))
    v_po = lic["playoff_punto"] + (1 - lic["playoff_punto"] - lic["playoff_errmur"]) * c
    target = n * v_po
    k_us, e_us = n * lic["noi_punto"], n * lic["noi_errmur"]
    max_err = (k_us + c * (n - k_us) - target) / c
    pts_per_err = ceil(c / (1 - c))
    sit_name = SITUATION_LABELS[sit].split(" (")[0].lower()
    st.markdown("#### Licenza di sbagliare in attacco: il livello playoff")
    st.markdown(
        f"In questa situazione ({sit_name}) un attacco vale per chi attacca: punto sempre, errore o murata mai, "
        f"pallone difeso dall'avversario il **{_pct(c)}** (si vince lo scambio dopo). Attacchiamo così circa "
        f"**{_it(n, 0)} volte a set**: su questi attacchi le squadre playoff fanno {_it(n * lic['playoff_punto'], 1)} "
        f"punti e {_it(n * lic['playoff_errmur'], 1)} errori o murate, noi {_it(k_us, 1)} punti e {_it(e_us, 1)} "
        "errori o murate.")
    if 0 <= max_err < 1:
        rule = (f"Con i punti che facciamo ({_it(k_us, 1)} a set), **per stare a livello playoff possiamo "
                f"permetterci meno di 1 errore o murata a set** in questa situazione (oggi {_it(e_us, 1)}): il "
                "margine va cercato soprattutto nei punti.")
    elif max_err >= 1:
        rule = (f"Con i punti che facciamo, **per stare a livello playoff possiamo fare al massimo "
                f"{math.floor(max_err + 1e-9)} errori o murate nel set** in questa situazione (oggi "
                f"{_it(e_us, 1)}).")
    else:
        rule = (f"Con i punti che facciamo oggi ({_it(k_us, 1)} a set) **non arriviamo al livello playoff nemmeno "
                f"senza errori**: servono più punti.")
    _pm_example(rule + f" **Per ogni errore o murata in più serve {pts_per_err} punto in più.**",
                title="La regola per la squadra")
    rows = []
    for err in range(0, 5):
        rows.append({"Errori o murate nel set": err,
                     "Punti necessari nel set": ceil((target - c * (n - err)) / (1 - c))})
    st.caption(f"Cosa serve nel set, su circa {_it(n, 0)} attacchi in questa situazione, per stare a livello playoff "
               f"(arrotondato per eccesso; oggi facciamo {_it(k_us, 1)} punti):")
    _pm_table(pd.DataFrame(rows))

    a = d.copy()
    a["errmur_100"] = 100 * (a["errore"] + a["murato"])
    a["punti_100"] = 100 * a["punto"]

    def need(x):
        ref = lic["per_zona"][x["zona"]]
        v = ref["punto"] + (1 - ref["punto"] - ref["errmur"]) * c
        return ceil((100 * v - c * (100 - round(x["errmur_100"]))) / (1 - c))
    a["servono"] = a.apply(need, axis=1)
    a["senza"] = a["punti_100"].round() < a["servono"]
    a["etichetta"] = a.apply(lambda x: f"{x['giocatore']} ({ZONE_LABELS[x['zona']]})", axis=1)
    st.markdown("**Atleta per atleta** (su 100 attacchi in questa situazione, confrontato con l'attaccante medio "
                "delle squadre playoff nella stessa zona)")
    pool = a[a["n"] >= 30] if (a["n"] >= 30).any() else a
    no = pool[pool["senza"]]
    if len(no):
        x = no.assign(gap=no["servono"] - no["punti_100"]).sort_values("gap", ascending=False).iloc[0]
        ref = lic["per_zona"][x["zona"]]
        v = ref["punto"] + (1 - ref["punto"] - ref["errmur"]) * c
        k = round(x["punti_100"])
        allowed = math.floor((k + c * (100 - k) - 100 * v) / c + 1e-9)
        alt = (f"oppure, con i {k} punti che fa, scendere a **{allowed} "
               f"{'errore o murata' if allowed == 1 else 'errori o murate'}**" if allowed >= 0
               else f"con i {k} punti che fa non basterebbe nemmeno non sbagliare mai")
        text = (f"**{x['etichetta']}**: con la sua media di **{round(x['errmur_100'])} errori o murate ogni 100 "
                f"attacchi** dovrebbe fare **{x['servono']} punti** (ne fa {k}); {alt}.")
        y = no.assign(gap=no["servono"] - no["punti_100"]).sort_values("gap").iloc[0]
        if y["etichetta"] != x["etichetta"]:
            text += (f"  \nIl più vicino è **{y['etichetta']}**: con {round(y['errmur_100'])} errori o murate gli "
                     f"servono {y['servono']} punti, ne fa {round(y['punti_100'])}.")
        _pm_example(text)
    yes = pool[~pool["senza"]]
    if len(yes):
        z = yes.assign(m=yes["punti_100"] - yes["servono"]).sort_values("m", ascending=False).iloc[0]
        _pm_example(
            f"**{z['etichetta']}** con {round(z['errmur_100'])} errori o murate ogni 100 attacchi fa "
            f"{round(z['punti_100'])} punti, {round(z['punti_100'] - z['servono'])} più di quelli che servono: può "
            f"rischiare di più, purché ogni errore o murata in più porti {pts_per_err} punto in più.",
            title="Chi ha margine")
    a = a.sort_values("servono")
    _pm_table(pd.DataFrame({
        "Attaccante": a["etichetta"], "Attacchi": a["n"],
        "Errori o murate su 100": a["errmur_100"].map(lambda v: f"{v:.0f}"),
        "Punti che servono": a["servono"], "Punti che fa": a["punti_100"].map(lambda v: f"{v:.0f}"),
        "A livello playoff": a["senza"].map(lambda v: "❌ no" if v else "✅ sì"),
    }))
    st.caption("Su 100 attacchi, stime del modello (chi ha pochi attacchi resta vicino alla media della squadra). "
               "\"Punti che servono\" con la sua media di errori e murate, rispetto all'attaccante medio delle "
               "squadre playoff nella stessa zona.")


def _pm_training_section(r):
    st.subheader("1. Cosa allenare")
    st.caption(
        "Quanti punti per set guadagneremmo se in ogni area fossimo come **le squadre più brave in quell'area** "
        "(il primo quarto: con tante squadre quante ne abbiamo, più o meno la terza o quarta migliore in "
        f"quell'area, non in classifica; {len(r['squadre_confronto'])} squadre con almeno 3 partite nei dati). "
        "Un punto = uno scambio "
        "vinto in più. Pallino = stima, linea = l'80% di probabilità.")
    a = r["aree"].copy().rename(columns={"punti_set_media": "media", "punti_set_basso": "basso",
                                         "punti_set_alto": "alto"})
    a["etichetta"] = a.apply(lambda x: f"  {_it(x['media'], 2, sign=True)} punti per set", axis=1)
    a["hover"] = a.apply(lambda x: (
        f"<b>{x['area']}</b><br>{_it(x['media'], 2, sign=True)} punti per set "
        f"(tra {_it(x['basso'], 2, sign=True)} e {_it(x['alto'], 2, sign=True)})<br>"
        f"{x['nota']}<br>probabilità che ci sia un guadagno: {_pct(x['p_guadagno'])}"), axis=1)
    fig = _dot_interval_figure(a, "area", "Punti per set guadagnati", theme_colors()["palette"][0],
                               text_col="etichetta", scale=1, zero_line=0, hover_col="hover")
    st.plotly_chart(fig, theme=None, key="pm_aree")
    best = a.iloc[0]
    if r.get("esempi"):
        before, after = _pm_set_after(r, best["media"])
        _pm_example(
            f"{_it(best['media'], 2, sign=True)} punti per set sembrano pochi: sono circa "
            f"{_it(best['media'] * 4, 1)} punti in una partita da quattro set. Contro una squadra media oggi vinciamo "
            f"il set il **{_pct(before)}** delle volte; con {best['area'].lower()} al livello delle squadre più brave "
            f"lo vinceremmo il **{_pct(after)}**. Sommando tutte le aree in cui c'è margine (+"
            f"{_it(a.loc[a['media'] > 0, 'media'].sum(), 1)} punti per set) arriveremmo al "
            f"**{_pct(_pm_set_after(r, a.loc[a['media'] > 0, 'media'].sum())[1])}**.")
    st.markdown(
        f"L'area con più margine è **{best['area'].lower()}**: {_it(best['media'], 2, sign=True)} punti per set "
        f"(tra {_it(best['basso'], 2, sign=True)} e {_it(best['alto'], 2, sign=True)}). "
        "Un'area con l'intervallo che tocca lo zero vuol dire che lì siamo già vicini ai migliori, o che i dati "
        "non bastano a dirlo.")
    table = pd.DataFrame({
        "Area": a["area"],
        "Noi": a.apply(lambda x: _pct(x["noi"], 1), axis=1),
        "Le più brave": a.apply(lambda x: _pct(x["quarto_migliore"], 1), axis=1),
        "Media campionato": a.apply(lambda x: _pct(x["media_campionato"], 1), axis=1),
        "Cosa misura": a["unita"],
        "Azioni per set": a["volume_set"].map(lambda v: _it(v, 1)),
        "Punti per set": a.apply(lambda x: f"{_it(x['media'], 2, sign=True)} ({_it(x['basso'], 2, sign=True)} / "
                                           f"{_it(x['alto'], 2, sign=True)})", axis=1),
    })
    _pm_table(table)


def _pm_serve_section(r):
    st.subheader("2. Battuta: rischiare o no")
    v = r["valori_battuta"].set_index("esito")
    _pm_serve_licence(r)
    st.markdown("#### Quanto vale ogni esito, e tutti i battitori del campionato")
    cols = st.columns([2, 3])
    with cols[0]:
        order = ["=", "/", "-", "!", "+", "#"]
        names = {"=": "Ace", "/": "Ricezione /", "-": "Ricezione -", "!": "Ricezione !",
                 "+": "Ricezione +", "#": "Ricezione #"}
        t = pd.DataFrame({
            "Esito della battuta": ["Errore"] + [names[o] for o in order],
            "Quota sulle battute in gioco": ["—"] + [_pct(v.loc[o, "quota"]) for o in order],
            "Scambi vinti da chi batte": ["0%"] + [_pct(v.loc[o, "media"]) for o in order],
        })
        _pm_table(t)
    srv = r["battitori"]
    colors = theme_colors()
    with cols[1]:
        fig = go.Figure()
        others, ours = srv[~srv["noi"]], srv[srv["noi"]]
        xmax = max(0.32, srv["err_media"].max() + 0.03)
        y_lo, y_hi = srv["in_gioco_media"].min() - 0.01, srv["in_gioco_media"].max() + 0.012
        for level in np.arange(0.36, 0.48, 0.02):
            e = np.linspace(0, xmax, 60)
            yy = level / (1 - e)
            keep = (yy >= y_lo) & (yy <= y_hi)
            if keep.sum() < 2:
                continue
            fig.add_trace(go.Scatter(x=100 * e[keep], y=100 * yy[keep], mode="lines", showlegend=False,
                                     line=dict(color=colors["grid"], width=1.5, dash="dot"), hoverinfo="skip"))
            fig.add_annotation(x=100 * e[keep][-1], y=100 * yy[keep][-1], text=f"{_it(100 * level, 0)}",
                               showarrow=False, xanchor="left", yanchor="bottom",
                               font=dict(size=10, color=PM_REFERENCE_GREY))
        hover = ("<b>%{customdata[0]}</b> (%{customdata[1]})<br>%{customdata[2]} battute<br>errori %{x:.1f}%<br>"
                 "in gioco: chi batte vince %{y:.1f}%<br>in totale: %{customdata[3]:.1f} break su 100<extra></extra>")
        for part, name, color, size, mode in ((others, "Altri battitori del campionato", PM_REFERENCE_GREY, 7,
                                               "markers"),
                                              (ours, "Decimo", colors["palette"][0], 11, "markers")):
            fig.add_trace(go.Scatter(
                x=100 * part["err_media"], y=100 * part["in_gioco_media"], mode=mode, name=name,
                marker=dict(size=size, color=color, opacity=0.55 if color == PM_REFERENCE_GREY else 1,
                            line=dict(color=colors["surface"], width=2)),
                customdata=np.c_[part["giocatore"], part["squadra"], part["n"], 100 * part["totale_media"]],
                hovertemplate=hover))
        x_rng, y_rng = [0, 100 * xmax + 2], [100 * y_lo, 100 * y_hi]
        shifts = _place_labels(100 * ours["err_media"], 100 * ours["in_gioco_media"], ours["giocatore"], x_rng,
                               y_rng, width_px=560, height_px=400)
        for (_, row), (dx, dy) in zip(ours.iterrows(), shifts):
            fig.add_annotation(x=100 * row["err_media"], y=100 * row["in_gioco_media"], text=row["giocatore"],
                               showarrow=False, xshift=dx, yshift=dy, font=dict(size=11, color=colors["ink"]))
        layout = _pm_layout(520, "Errori in battuta (%)", left_margin=60)
        layout.update(yaxis_title="Battute in gioco: scambi vinti da chi batte (%)",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
        fig.update_layout(**layout)
        fig.update_xaxes(range=x_rng)
        fig.update_yaxes(range=y_rng)
        st.plotly_chart(fig, theme=None, key="pm_battuta")
        st.caption(
            "Ogni pallino è un battitore (almeno 30 battute). Più a destra = più errori, più in alto = battute in "
            "gioco più efficaci. Le linee punteggiate uniscono i punti con lo **stesso rendimento totale** (break "
            "su 100 battute): spostarsi a destra conviene solo se si sale fino a una linea più alta.")


PM_DECISION_THRESHOLD = 0.7   # probabilità oltre la quale si dà un'indicazione


def _pm_next_opponent(r):
    """Chiave della prossima avversaria del Decimo (dal calendario del girone)."""
    from datetime import date
    us_title = r["squadra"].split("|", 1)[1]
    today = date.today()
    upcoming = []
    for m in r.get("calendario", []):
        if us_title not in (m["casa"], m["ospite"]) or m["giocata"]:
            continue
        try:
            g, mo, a = (int(x) for x in m["data"].split("/"))
        except ValueError:
            continue
        if date(a, mo, g) >= today:
            other = m["ospite"] if m["casa"] == us_title else m["casa"]
            upcoming.append((date(a, mo, g), other, m["data"]))
    if not upcoming:
        return None, None
    d, title, data = min(upcoming)
    return f"{r['squadra'].split('|')[0]}|{title}", data


def _pm_decision(p, yes, no, neutral, reference=False):
    """Indicazione con la sua probabilità: icona + testo (mai solo colore).
    Per l'avversario medio nessuna indicazione: è il riferimento."""
    if reference:
        return "Riferimento", None
    if p >= PM_DECISION_THRESHOLD:
        return f"⬆️ {yes}", p
    if p <= 1 - PM_DECISION_THRESHOLD:
        return f"⬇️ {no}", 1 - p
    return f"↔️ {neutral}", max(p, 1 - p)


def _pm_setter_line_courts(rows, key):
    """Due campetti della distribuzione da ricezione # o +: palleggiatore in
    seconda linea (P1/P6/P5, opposto davanti) e in prima linea (P2/P3/P4,
    opposto dietro)."""
    if rows is None or len(rows) == 0:
        return
    st.markdown("**Da ricezione # o +, secondo la linea del palleggiatore**")
    cols = st.columns(2)
    for col, (line, title) in zip(cols, (("dietro", "Palleggiatore dietro (P1, P6, P5): opposto davanti"),
                                         ("davanti", "Palleggiatore davanti (P2, P3, P4): opposto dietro"))):
        part = rows[rows["linea"] == line]
        with col:
            st.caption(f"{title} — {len(part)} attacchi")
            if len(part):
                st.plotly_chart(build_court_chart(zone_stats(part), compact=True), theme=None,
                                key=f"pm_court_{key}_{line}")
    st.caption("Esclusi gli attacchi del palleggiatore. Con meno di una ventina di attacchi per campetto la "
               "distribuzione va presa come indizio.")


def _pm_scouting_card(r, key):
    """Scheda dell'avversario: frasi con i dati che spiccano e tabelle degli
    scout di quest'anno e dell'anno prima (giocatori rimasti)."""
    from src.performance_model import SITUATION_LABELS, ZONE_LABELS
    card = r.get("schede", {}).get(key)
    st.markdown("#### Cosa dicono gli scout su di loro")
    st.caption("Riassunto automatico; il dettaglio di tutte le partite scoutate è più sotto in questa tab.")
    if card is None:
        return
    n = len(card["partite"])
    if n == 0 and card["attaccanti_prima"].empty:
        st.info("Nessuno scout di questa squadra, né di quest'anno né dell'anno scorso: le indicazioni sopra vengono "
                "solo dai risultati. Uno scout di una loro partita, anche contro un'altra squadra, basta per avere "
                "una scheda.")
        return
    if n:
        st.caption(f"Quest'anno: {n} {'partita' if n == 1 else 'partite'} scoutate ({', '.join(card['partite'])}), "
                   f"{card['n_scambi']} scambi. Con pochi scambi i numeri vanno presi come indizi da verificare in campo.")
    for frase in card["frasi"]:
        st.markdown(f"- {frase}")

    def show(df, kind):
        if df is None or df.empty:
            st.caption("Nessun dato.")
            return
        d = df.copy()
        if kind == "att":
            _pm_table(pd.DataFrame({
                "Attaccante": d["giocatore"], "Zona": d["zona"].map(ZONE_LABELS), "Attacchi": d["attacchi"],
                "Punto": d["punto"].map(_pct), "Errore o murato": d["errore_murato"].map(_pct),
                "Efficienza": d["efficienza"].map(_pct), "Scambi vinti": d["scambi_vinti"].map(_pct)}))
        elif kind == "ric":
            _pm_table(pd.DataFrame({
                "Ricevitore": d["giocatore"], "Ricezioni": d["ricezioni"], "Positive (# o +)": d["positive"].map(_pct),
                "Negative, / e ace": d["negative"].map(_pct), "Side-out": d["side_out"].map(_pct)}))
        else:
            _pm_table(pd.DataFrame({
                "Battitore": d["giocatore"], "Battute": d["battute"], "Errori": d["errori"].map(_pct),
                "Ace": d["ace"].map(_pct), "Break-point": d["break_point"].map(_pct)}))

    prev_label = ("Rimasti dall'anno scorso" if card["rosa_nota"] else "Anno scorso (rosa di quest'anno non nota)")
    tab_names = (["Quest'anno"] if n else []) + ([f"{prev_label}: {card['stagione_prima']}"]
                                                  if card["stagione_prima"] else [])
    for tab, name in zip(st.tabs(tab_names), tab_names):
        with tab:
            if name == "Quest'anno":
                sit = card["attacco_situazioni"]
                if len(sit):
                    st.markdown("**Attacco per situazione**")
                    _pm_table(pd.DataFrame({
                        "Situazione": sit["situazione"].map(SITUATION_LABELS), "Attacchi": sit["attacchi"],
                        "Punto": sit["punto"].map(_pct), "Errore o murato": sit["errore_murato"].map(_pct),
                        "Scambi vinti": sit["scambi_vinti"].map(_pct)}))
                zones = []
                for k, lab in (("R#+", "Da ricezione # o +"), ("R-", "Da ricezione -"), ("FB", "Da free ball")):
                    counts = card["zone"].get(k, {})
                    tot = sum(counts.values())
                    if tot:
                        zones.append(f"{lab} ({tot}): " + ", ".join(
                            f"{ZONE_LABELS[z]} {_pct(c / tot)}" for z, c in sorted(counts.items(), key=lambda kv: -kv[1])))
                if zones:
                    st.markdown("**Dove attaccano**  \n" + "  \n".join(zones))
                _pm_setter_line_courts(card["campetti"], key + "_ora")
                cols = st.columns(2)
                with cols[0]:
                    st.markdown("**Attaccanti**")
                    show(card["attaccanti"], "att")
                    st.markdown("**Battitori**")
                    show(card["battitori"], "srv")
                with cols[1]:
                    st.markdown("**Ricevitori**")
                    show(card["ricevitori"], "ric")
            else:
                if len(card.get("campetti_prima", [])):
                    st.caption(f"Stesso palleggiatore dell'anno scorso ({card['palleggiatore_prima']}): la sua "
                               "distribuzione di allora, con gli attaccanti di allora.")
                    _pm_setter_line_courts(card["campetti_prima"], key + "_prima")
                cols = st.columns(2)
                with cols[0]:
                    st.markdown("**Attaccanti**")
                    show(card["attaccanti_prima"], "att")
                    st.markdown("**Battitori**")
                    show(card["battitori_prima"], "srv")
                with cols[1]:
                    st.markdown("**Ricevitori**")
                    show(card["ricevitori_prima"], "ric")
    st.caption("Il dettaglio con i campetti delle zone e il palleggiatore è nella tab \"Studio avversario\".")


def _pm_opponent_view(r, key):
    """Indicazioni del modello contro la squadra `key` (squadra-stagione o
    'media'): cosa sappiamo, battuta/attacco/contrattacco, scheda degli scout,
    budget di errori. Nella tab "Studio avversario"."""
    o = r["avversari"].copy()
    base = o[o["chiave"] == "media"].iloc[0]
    x = o[o["chiave"] == key].iloc[0]

    if key != "media":
        n_now, n_prev = int(x["scout_stagione"]), int(x["scout_stagione_prima"])
        level = ("Indicazioni affidabili" if n_now >= 3 else
                 "Indicazioni orientative" if n_now + n_prev >= 3 else "Indicazioni deboli: solo dai risultati")
        known = [f"**{n_now}** scout di quest'anno"]
        if n_prev:
            known.append(f"**{n_prev}** scout dell'anno scorso ({x['stagione_prima']}): contano i giocatori rimasti")
        known.append(f"**{int(x['posizione'])}ª** in classifica ({int(x['punti'])} punti in {int(x['giocate'])} "
                     f"partite, punti nei set {int(x['punti_fatti'])}-{int(x['punti_subiti'])})")
        st.markdown(f"**{level}.** Cosa sappiamo: " + " · ".join(known) + ".")
        if n_now == 0:
            st.caption("Senza scout di quest'anno, il modello usa la loro forza nei risultati e, se ci sono, i "
                       "giocatori già visti: le indicazioni sono vicine a quelle per una squadra di quel livello. "
                       "Uno scout di una loro partita (anche contro un'altra squadra) le rende molto più precise.")

    cols = st.columns(3)
    is_ref = key == "media"
    head, prob = _pm_decision(x["p_battere_forte"], "Battere più forte del solito", "Battere più sicuro del solito",
                              "Battuta come al solito", reference=is_ref)
    with cols[0]:
        with st.container(border=True):
            st.markdown(f"**Battuta**  \n#### {head}")
            st.caption(f"Probabilità che sia la scelta giusta: {_pct(prob)}" if prob is not None
                       else "I valori di una squadra media: il metro per gli altri avversari")
            st.markdown(
                f"Un errore in più conviene se fa scendere almeno **{_it(x['batt_ricezioni_per_errore'], 1)} "
                f"ricezioni** da # a - (contro una squadra media: {_it(base['batt_ricezioni_per_errore'], 1)}). "
                f"Contro la nostra battuta ricevono positivo il {_pct(x['loro_ric_positiva'])} "
                f"(media {_pct(base['loro_ric_positiva'])}); dopo una loro ricezione - vinciamo noi "
                f"{_it(100 * x['batt_gap'], 0)} scambi su 100 in più che dopo una #.")
    for col, sit, title in ((cols[1], "R#+", "Primo attacco dopo ricezione"), (cols[2], "CA", "Contrattacco")):
        head, prob = _pm_decision(x[f"p_forzare_{sit}"], "Forzare di più", "Rischiare meno, tenere in gioco",
                                  "Rischio come al solito", reference=is_ref)
        with col:
            with st.container(border=True):
                st.markdown(f"**{title}**  \n#### {head}")
                st.caption(f"Probabilità che sia la scelta giusta: {_pct(prob)}" if prob is not None
                           else "I valori di una squadra media: il metro per gli altri avversari")
                st.markdown(
                    f"Un nostro attacco difeso da loro ci lascia **{_it(100 * x[f'c_{sit}'], 0)} scambi su 100** "
                    f"(contro una squadra media {_it(100 * base[f'c_{sit}'], 0)}): ogni errore o murata in più deve "
                    f"portare almeno **{_it(x[f'soglia_{sit}'], 2)} punti** in più "
                    f"(media {_it(base[f'soglia_{sit}'], 2)}). Il loro attacco su questa fase contro di noi: punto "
                    f"{_pct(x[f'loro_{sit}_punto'])}, murato da noi {_pct(x[f'loro_{sit}_murato'])} "
                    f"(media {_pct(base[f'loro_{sit}_punto'])} e {_pct(base[f'loro_{sit}_murato'])}).")
    st.caption("Indicazioni rispetto a come giochiamo di solito: \"forzare di più\" vuol dire che contro di loro un "
               "pallone rimesso in gioco vale meno che contro una squadra media (contrattaccano bene o li muriamo "
               "poco), quindi il rischio rende di più. Freccia orizzontale = i dati non indicano una direzione chiara "
               f"(meno del {_pct(PM_DECISION_THRESHOLD)} di probabilità).")

    if key != "media":
        _pm_scouting_card(r, key)

    # --- budget di errori --------------------------------------------------
    st.markdown("#### Budget di errori nel primo attacco dopo ricezione # o +")
    st.markdown(
        f"Con i valori di oggi, contro {'una squadra media' if key == 'media' else x['nome']} vinciamo un set il "
        f"**{_pct(x['p_set_ora'])}** delle volte (side-out nostro {_pct(x['so_noi'])}, loro {_pct(x['so_loro'])}). "
        "Per vincerlo il 50, 60 o 70% delle volte serve un side-out di "
        + ", ".join(f"**{_pct(x[f'so_richiesto_{t}'])}**" for t in (50, 60, 70))
        + ". Il grafico dice quanti punti servono nel primo attacco da R#/R+ per ogni livello di errori e murate, "
          "se tutto il resto (ricezione, battuta, contrattacco) resta com'è.")
    colors = theme_colors()
    fig = go.Figure()
    errs = [5, 10, 15, 20, 25]
    shades = {50: "#86b6ef", 60: colors["palette"][0], 70: "#1c4f8f"} if colors["surface"] != "#1a1a19" else \
        {50: "#1c4f8f", 60: colors["palette"][0], 70: "#9cc5f5"}
    for t in (50, 60, 70):
        ys = [100 * x[f"punti_richiesti_{t}_{e}"] for e in errs]
        fig.add_trace(go.Scatter(x=errs, y=ys, mode="lines", name=f"{t}% dei set vinti",
                                 line=dict(color=shades[t], width=2),
                                 hovertemplate=f"{t}% dei set: con %{{x}}% di errori+murate servono "
                                               "%{y:.0f}% di punti<extra></extra>"))
        fig.add_annotation(x=errs[-1], y=ys[-1], text=f"{t}% dei set", showarrow=False, xanchor="left", xshift=6,
                           font=dict(size=11, color=colors["ink"]))
    att = r["attaccanti"]
    att = att[(att["situazione"] == "R#+")].copy()
    att["err_mur"] = att["errore"] + att["murato"]
    from src.performance_model import ZONE_LABELS
    multi = att["giocatore"].duplicated(keep=False)
    att["etichetta"] = [f"{a['giocatore']} ({ZONE_LABELS[a['zona']]})" if m else a["giocatore"]
                        for (_, a), m in zip(att.iterrows(), multi)]
    fig.add_trace(go.Scatter(
        x=100 * att["err_mur"], y=100 * att["punto"], mode="markers", name="Nostri attaccanti (stima)",
        marker=dict(size=10, color=colors["palette"][1], line=dict(color=colors["surface"], width=2)),
        customdata=np.c_[att["etichetta"], att["n"]],
        hovertemplate="%{customdata[0]}: %{y:.0f}% punti, %{x:.0f}% errori+murate (%{customdata[1]} attacchi)"
                      "<extra></extra>"))
    x_rng = [0, 32]
    all_y = [100 * x[f"punti_richiesti_{t}_{e}"] for t in (50, 60, 70) for e in errs] + list(100 * att["punto"])
    y_rng = [max(0, min(all_y) - 5), min(100, max(all_y) + 9)]
    for (_, a), (dx, dy) in zip(att.iterrows(), _place_labels(100 * att["err_mur"], 100 * att["punto"],
                                                              att["etichetta"], x_rng, y_rng, 560, 360)):
        fig.add_annotation(x=100 * a["err_mur"], y=100 * a["punto"], text=a["etichetta"], showarrow=False,
                           xshift=dx, yshift=dy, font=dict(size=11, color=colors["ink"]))
    layout = _pm_layout(460, "Errori + murate (% degli attacchi)", left_margin=60)
    layout.update(yaxis_title="Punti (% degli attacchi)", margin=dict(t=50, b=50, l=60, r=90))
    fig.update_layout(**layout)
    fig.update_xaxes(range=x_rng, ticksuffix="%")
    fig.update_yaxes(range=y_rng, ticksuffix="%")
    st.plotly_chart(fig, theme=None, key=f"pm_budget_{key}")
    st.caption("Chi sta sopra una linea, con i suoi errori, fa abbastanza punti per quell'obiettivo; chi sta sotto "
               "deve fare più punti o sbagliare meno (la distanza in verticale e in orizzontale dice quale delle due "
               "è più vicina). I pallini sono le stime per ogni attaccante contro una squadra media. Chiedere tutto "
               "il miglioramento al solo primo attacco è un caso limite: nella realtà va diviso con ricezione, "
               "battuta e contrattacco (vedi \"Cosa allenare\").")
    _pm_table(pd.DataFrame({
        "Errori + murate": [f"{e}%" for e in errs],
        **{f"Punti per il {t}% dei set": [_pct(max(0, v)) + (" (fuori portata)" if v > 0.75 else "")
                                           for v in (x[f"punti_richiesti_{t}_{e}"] for e in errs)]
           for t in (50, 60, 70)},
    }))


def _pm_attackers_section(r):
    from src.performance_model import SITUATION_LABELS, ZONE_LABELS
    st.subheader("3. Chi servire")
    att = r["attaccanti"]
    sits = [s for s in PM_SITUATION_ORDER if (att["situazione"] == s).any()]
    sit = st.radio("Situazione", sits, format_func=lambda s: SITUATION_LABELS[s], horizontal=True, key="pm_sit")
    d = att[att["situazione"] == sit].copy().sort_values("ev_media", ascending=False)
    d["etichetta"] = d.apply(lambda x: f"{x['giocatore']} ({ZONE_LABELS[x['zona']]})", axis=1)
    d = d.rename(columns={"ev_media": "media", "ev_basso": "basso", "ev_alto": "alto"})
    d["testo"] = d.apply(lambda x: f"  {_pct(x['quota'])} dei palloni · {x['n']}", axis=1)
    d["hover"] = d.apply(lambda x: (
        f"<b>{x['etichetta']}</b><br>{_it(100 * x['media'], 1)} scambi vinti su 100 palloni "
        f"({_it(100 * x['basso'], 1)}–{_it(100 * x['alto'], 1)})<br>punto {_pct(x['punto'])} · errore "
        f"{_pct(x['errore'])} · murato {_pct(x['murato'])}<br>{x['n']} attacchi, {_pct(x['quota'])} dei nostri "
        f"palloni<br>probabilità di essere l'opzione migliore: {_pct(x['p_migliore'])}"), axis=1)
    st.caption(
        f"Scambi vinti su 100 palloni dati a ogni attaccante del Decimo in questa situazione (almeno "
        f"{r['meta']['config']['min_attacchi_giocatore']} attacchi). Il rombo grigio è la media del campionato "
        "nella stessa zona; a destra la quota dei nostri palloni e il numero di attacchi.")
    fig = _dot_interval_figure(d, "etichetta", "Scambi vinti su 100 palloni", theme_colors()["palette"][0],
                               text_col="testo", ref_col="ev_rif_zona", ref_name="Media del campionato nella zona",
                               hover_col="hover")
    st.plotly_chart(fig, theme=None, key=f"pm_att_{sit}")
    _pm_attack_example(r, d, sit)
    _pm_table(pd.DataFrame({
        "Attaccante": d["etichetta"], "Attacchi": d["n"], "Quota dei palloni": d["quota"].map(_pct),
        "Scambi vinti su 100": d.apply(lambda x: f"{_it(100 * x['media'], 1)} ({_it(100 * x['basso'], 1)}–"
                                                 f"{_it(100 * x['alto'], 1)})", axis=1),
        "Punto": d["punto"].map(_pct), "Errore": d["errore"].map(_pct), "Murato": d["murato"].map(_pct),
        "Probabilità di essere il migliore": d["p_migliore"].map(_pct),
    }))
    return sit


def _pm_final_section(r, sit):
    from src.performance_model import SITUATION_LABELS, ZONE_LABELS
    st.subheader("4. Finali di set")
    d = r["attaccanti"]
    d = d[d["situazione"] == sit].copy().sort_values("ev_fin_media", ascending=False)
    if d.empty:
        return
    d["etichetta"] = d.apply(lambda x: f"{x['giocatore']} ({ZONE_LABELS[x['zona']]})", axis=1)
    colors = theme_colors()
    c_norm, c_fin = colors["palette"][0], colors["palette"][1]
    fig = go.Figure()
    for _, x in d.iterrows():
        fig.add_trace(go.Scatter(x=[100 * x["ev_media"], 100 * x["ev_fin_media"]], y=[x["etichetta"]] * 2,
                                 mode="lines", line=dict(color=colors["grid"], width=2), showlegend=False,
                                 hoverinfo="skip"))
    for col, name, color in (("ev_media", "Resto del set", c_norm), ("ev_fin_media", "Finale di set", c_fin)):
        fig.add_trace(go.Scatter(
            x=100 * d[col], y=d["etichetta"], mode="markers", name=name,
            marker=dict(size=11, color=color, line=dict(color=colors["surface"], width=2)),
            customdata=np.c_[d["n_finale"], 100 * d["quota_finale"], 100 * d["p_meglio_in_finale"],
                             100 * d["p_migliore_finale"]],
            hovertemplate="%{y}<br>" + name + ": %{x:.1f} scambi vinti su 100<br>nei finali: %{customdata[0]} "
                          "attacchi (%{customdata[1]:.0f}% dei nostri)<br>probabilità di rendere di più nei finali: "
                          "%{customdata[2]:.0f}%<br>probabilità di essere il migliore nei finali: "
                          "%{customdata[3]:.0f}%<extra></extra>"))
    fig.update_layout(**_pm_layout(max(240, 42 * len(d) + 110), "Scambi vinti su 100 palloni",
                                   left_margin=int(d["etichetta"].str.len().max() * 7.2) + 30))
    fig.update_yaxes(autorange="reversed", gridcolor=colors["grid"])
    st.caption(
        f"{SITUATION_LABELS[sit]}. Finale = una squadra a 20 (10 al quinto) e scarto al massimo 3. Il modello parte "
        "dall'idea che nei finali nessuno renda diversamente dal solito e si sposta solo se i dati lo mostrano "
        "chiaramente: differenze piccole qui sono normali.")
    st.plotly_chart(fig, theme=None, key=f"pm_fin_{sit}")
    _pm_table(pd.DataFrame({
        "Attaccante": d["etichetta"], "Attacchi nei finali": d["n_finale"],
        "Quota dei palloni nei finali": d["quota_finale"].map(_pct),
        "Scambi vinti su 100 nei finali": d.apply(lambda x: f"{_it(100 * x['ev_fin_media'], 1)} "
                                                            f"({_it(100 * x['ev_fin_basso'], 1)}–"
                                                            f"{_it(100 * x['ev_fin_alto'], 1)})", axis=1),
        "Rende di più nei finali (probabilità)": d["p_meglio_in_finale"].map(_pct),
        "Il migliore nei finali (probabilità)": d["p_migliore_finale"].map(_pct),
    }))


def _pm_reception_section(r):
    st.subheader("5. Ricezione")
    rec = r["ricevitori"].sort_values("so_media", ascending=False).rename(
        columns={"so_media": "media", "so_basso": "basso", "so_alto": "alto"})
    if rec.empty:
        return
    league = float(r["aree"].set_index("area").loc["Ricezione", "media_campionato"])
    rec["testo"] = rec.apply(lambda x: f"  {x['n']} ricezioni", axis=1)
    rec["rif"] = league
    v = r["valori_battuta"].set_index("esito")["media"]
    st.caption(
        "Side-out su 100 battute avversarie in gioco ricevute da ognuno (contro un battitore medio), con la "
        "media del campionato come riferimento. Le differenze tra ricevitori in scambi vinti sono piccole: tra una "
        f"ricezione # e una - il side-out cambia di {_it(100 * (v['-'] - v['#']), 0)} punti su 100.")
    fig = _dot_interval_figure(rec, "giocatore", "Side-out su 100 ricezioni", theme_colors()["palette"][0],
                               text_col="testo", ref_col="rif", ref_name="Media del campionato")
    st.plotly_chart(fig, theme=None, key="pm_ric")
    e = r.get("esempi", {}).get("ricezione")
    if e:
        per_set = float(r["aree"].set_index("area").loc["Ricezione", "volume_set"])
        solid = rec[rec["n"] >= 100] if (rec["n"] >= 100).sum() >= 2 else rec  # esempio su chi riceve davvero
        top = solid.sort_values("positiva_oss", ascending=False).iloc[0]
        low = solid.sort_values("positiva_oss").iloc[0]
        gap = top["positiva_oss"] - low["positiva_oss"]
        _pm_example(
            f"Dopo una nostra ricezione # o + facciamo side-out il **{_pct(e['so_facile'])}** delle volte, dopo una "
            f"!, - o / il **{_pct(e['so_difficile'])}**: ogni ricezione portata da un gruppo all'altro vale "
            f"{_it(e['guadagno'], 2)} punti. Riceviamo circa {_it(per_set, 0)} battute a set: "
            f"**10 punti percentuali di ricezioni positive in più (2 ricezioni a set) valgono "
            f"{_it(0.1 * per_set * e['guadagno'], 2)} punti per set**.  \nEsempio: tra **{top['giocatore']}** "
            f"({_pct(top['positiva_oss'])} di positive) e **{low['giocatore']}** ({_pct(low['positiva_oss'])}) ci sono "
            f"{_it(100 * gap, 0)} punti percentuali: se tutte le ricezioni di un set le facesse il primo, sarebbero "
            f"{_it(gap * per_set * e['guadagno'], 2)} punti in più. Pesa di più l'ace subito: costa tutto lo scambio "
            "invece di una parte.")
    _pm_table(pd.DataFrame({
        "Ricevitore": rec["giocatore"], "Ricezioni": rec["n"],
        "Positive (# o +)": rec["positiva_oss"].map(_pct),
        "Side-out su 100": rec.apply(lambda x: f"{_it(100 * x['media'], 1)} ({_it(100 * x['basso'], 1)}–"
                                               f"{_it(100 * x['alto'], 1)})", axis=1),
        "Meglio della media (probabilità)": rec["p_meglio_media"].map(_pct),
    }))


def render_performance_model_tab():
    st.header("Modello di prestazione")
    st.markdown(
        "Cosa allenare di più e quanto rischiare, stimato su **tutte le partite di campionato con lo scout**, "
        "anche quelle tra altre squadre. Ogni azione vale quanto cambia la probabilità di vincere lo scambio "
        "(1 scambio = 1 punto). Il modello è bayesiano: squadre e giocatori con pochi dati restano vicini alla "
        "media finché i dati non dicono altro, e ogni stima ha il suo intervallo.")
    from src.performance_model import RESULTS_PATH
    mtime = RESULTS_PATH.stat().st_mtime if RESULTS_PATH.exists() else 0
    r = load_performance_results(mtime)
    cols = st.columns([3, 1])
    with cols[0]:
        if r is not None:
            m = r["meta"]
            seasons = ", ".join(f"{s}: {n}" for s, n in m["partite"].items())
            st.caption(f"Stima del {m['data_stima']} su {sum(m['partite'].values())} partite ({seasons}), "
                       f"{m['attacchi']} attacchi e {m['battute']} battute.")
    with cols[1]:
        refit = st.button("Stima di nuovo il modello", help=(
            "Rilegge tutti i .dvw di campionato delle stagioni in config/performance_model.json e ristima il "
            "modello (circa 3 minuti). Da fare dopo aver aggiunto partite."))
    if refit:
        from src.performance_model import fit_and_save
        with st.status("Stima del modello in corso (circa 3 minuti)…", expanded=True) as status:
            fit_and_save(progress=status.write)
            status.update(label="Modello stimato", state="complete")
        load_performance_results.clear()
        st.rerun()
    if r is None:
        st.info("Il modello non è ancora stato stimato: premi \"Stima di nuovo il modello\".")
        return
    _pm_training_section(r)
    st.caption("Le indicazioni contro un singolo avversario (battere forte o sicuro, forzare o no, budget di errori, "
               "scheda degli scout) sono nella tab \"Studio avversario\".")
    _pm_serve_section(r)
    sit = _pm_attackers_section(r)
    _pm_final_section(r, sit)
    _pm_reception_section(r)
    with st.expander("Come funziona il modello e quanto fidarsi"):
        m = r["meta"]
        diag = m["diagnostica"]
        st.markdown(
            "- **Dati**: tutti i `.dvw` di campionato nelle cartelle delle stagioni (anche partite senza il "
            "Decimo), dalla data di inizio del campionato; un file per partita. In più i risultati del girone dal "
            "sito federale.\n"
            "- **Squadre**: una per stagione. Il suo livello parte dalla forza nei risultati (punti fatti e subiti "
            "nei set), più la società (comune alle stagioni, per le squadre indicate come stesso gruppo in "
            "`config/performance_model.json`) e quanto la squadra di quest'anno se ne discosta: quanto conta "
            "l'anno prima lo stimano i dati. I giocatori sono società + cognome: chi è rimasto porta i suoi dati.\n"
            "- **Valore di un esito**: la quota di scambi vinti dopo quell'esito.\n"
            "- **Attacco**: l'esito (punto, +, !, -, errore, murato) dipende dalla situazione e dalla zona, dalla "
            "squadra che attacca in quella situazione, dall'attaccante, dalla squadra che difende (muro e difesa) "
            "e dal finale di set. Situazioni: side-out da R#/R+ e da R! (palla veloce), da R- (palla alta), free "
            "ball (D!), contrattacco. La difesa non è usata se non come free ball.\n"
            "- **Battuta**: l'errore dipende da squadra e battitore; il voto della ricezione da squadra e "
            "battitore che servono, squadra e giocatore che ricevono, finale di set. Il tipo di battuta non c'è.\n"
            "- **Cosa succede dopo**, per ogni coppia di squadre: chi vince lo scambio dopo un attacco rimasto in "
            "gioco, e dopo una ricezione con un certo voto. Da qui le indicazioni contro un avversario.\n"
            "- **Set**: la probabilità di vincerlo viene dai side-out delle due squadre, punto per punto.\n"
            "- **Limite**: le stime valgono con la distribuzione attuale. Se un attaccante riceve molti più "
            "palloni, gli avversari lo leggono e il suo rendimento scende: le indicazioni vanno applicate per "
            "gradi.")
        st.caption(
            "Convergenza (R-hat massimo, sotto 1,05 va bene; divergenze): "
            + "; ".join(f"{k} {_it(v['r_hat_max'], 3)}, {v['divergenze']}" for k, v in diag.items())
            + f". Stima in {m['durata_s']} s.")
        if m.get("non_abbinate"):
            st.warning("Squadre degli scout non trovate nel girone (aggiungerle in `nomi_fipav` di "
                       "config/performance_model.json): " + ", ".join(f"{n} ({s_})" for s_, n in m["non_abbinate"]))
        if m["scartati"]:
            st.caption("File non usati: " + "; ".join(f"{f} ({why})" for f, why in m["scartati"]))


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
                  "Andamento KPI", "Modello di prestazione"] + (["Pagella"] if show_pagella else []))
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
    with tabs[7]:
        render_performance_model_tab()
    if show_pagella:
        with tabs[8]:
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
    render_side_out_season(matches, selected_seqs, match_label_lookup, version)
    st.divider()
    render_mvp_trend_section(matches, selected_seqs, match_label_lookup, version)
    st.divider()
    render_ball_type_section(matches, selected_seqs, match_label_lookup)


if __name__ == "__main__":
    main()
