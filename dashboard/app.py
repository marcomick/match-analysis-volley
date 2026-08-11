# dashboard/app.py
"""
Dashboard interattiva: confronto KPI andata vs ritorno (con playoff per JVC)
tra le partite della stagione, sia a livello squadra che per giocatore, più un
confronto multi-giocatore per singolo KPI lungo tutta la stagione.

Avvio: streamlit run dashboard/app.py

Sezione 1 — "Confronto per KPI — Squadra / Giocatore":
per ogni avversario (13, in ordine di giornata di andata) + una 14ª posizione
per il playoff (solo JVC): barre raggruppate andata/ritorno/playoff-andata/
playoff-ritorno, e due linee di trend sovrapposte (andata, ritorno) che
uniscono i valori delle 13 giornate di ciascun girone. Le linee sono lisciate
con un kernel gaussiano e si interrompono dove mancano dati (giocatore non in
campo); barre e/o trend sono attivabili/disattivabili.

Sezione 2 — "Confronto tra giocatori":
per un KPI scelto, una linea continua per ciascuna entità selezionata
(Squadra e/o giocatori) lungo tutte le 28 partite della stagione (andata,
ritorno, playoff andata = 27ª, playoff ritorno = 28ª), con tabella
riepilogativa min/mediana/max per entità.

Sezione 3 — "Confronto KPI per una singola entità":
speculare alla 2 — una entità (Squadra o un giocatore), più KPI selezionati
sovrapposti come linee continue lungo tutta la stagione. Include anche i KPI
assoluti (conteggi: punti, ace, errori per fondamentale, non solo percentuali).

KPI "Attacco SO" (side-out) e "Contrattacco": efficienza/totale/punti/errori/
murati sui due sottoinsiemi di attacchi determinati da
separate_attacks_counterattacks — SO = immediatamente dopo una ricezione con
voto scelto dalla sidebar ("Esito ricezione per Attacco SO"), Contrattacco =
tutti gli altri. Ricalcolati ad ogni cambio del filtro (vedi
src.leg_comparison.build_attacco_so_dataset), a differenza degli altri KPI
che sono fissi per la stagione.

Soglia minima attacchi (sidebar): per i KPI della famiglia "attacco"
(Attacco*, Attacco SO*, Contrattacco*), se un giocatore in una partita non
raggiunge questo numero di attacchi della stessa famiglia, per quella
partita/KPI risulta senza dati (vedi
src.leg_comparison.apply_min_attacks_threshold) — non un valore a zero.
"""
import os
import sys

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.leg_comparison import (
    ALL_KPIS,
    ATTACCO_SO_KPI_LABELS,
    CONTRATTACCO_KPI_LABELS,
    DEFAULT_REC_VOTE,
    PERCENT_KPIS,
    apply_min_attacks_threshold,
    build_attacco_so_dataset,
    build_comparison_dataset,
    build_match_outcomes_dataset,
    get_x_axis_order,
    load_all_matches,
)

SEASON = "2025-2026"
N_REGULAR_MATCHES = 28  # 13 andata + 13 ritorno + 2 playoff (POA, POR)
REC_VOTE_OPTIONS = ["#", "+", "!", "-", "/", "="]

# ALL_KPIS + i KPI "Attacco SO"/"Contrattacco" (parametrici sull'esito ricezione,
# vedi sidebar) — lista mostrata in tutti i multiselect della dashboard.
DISPLAY_KPIS = ALL_KPIS + list(ATTACCO_SO_KPI_LABELS.values()) + list(CONTRATTACCO_KPI_LABELS.values())

# ---------------------------------------------------------------------------
# Palette — hue categoriche validate CVD-safe (vedi skill dataviz / palette.md),
# in due varianti (chiara/scura) perché il tema Streamlit attivo (light/dark)
# non deve mai lasciare testo scuro su sfondo scuro o viceversa: passiamo
# theme=None a st.plotly_chart per avere pieno controllo, e ricalcoliamo i
# colori ad ogni render in base a st.context.theme.type.
# Sezione 1: andata/ritorno restano lo stesso colore sia in barra che in linea
# di trend (il colore segue l'entità "girone"); i playoff usano la stessa hue
# con opacità ridotta (encoding secondario) invece di nuovi colori.
# Sezione 2/3: colore per entità/KPI assegnato in ordine fisso sulla lista
# completa delle opzioni disponibili, non su quelle selezionate — così il
# filtro non ridipinge le serie superstiti.
# ---------------------------------------------------------------------------
PALETTE_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
PALETTE_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
ENTITY_DASHES = ["solid", "dash", "dot", "dashdot"]

LEG_LABELS = {"A": "Andata", "R": "Ritorno", "POA": "Playoff andata", "POR": "Playoff ritorno"}
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
# del grafico "Confronto tra giocatori" (28 posizioni, poco spazio per ciascuna).
OPPONENT_ABBR = {
    "Monterotondo": "M.tondo",
    "Stella Mantus": "S.Mantus",
    "Civitavecchia": "Civitav.",
    "Astrolabio": "Astrol.",
    "Poolstars": "Poolst.",
}


def abbr_opponent(name):
    return OPPONENT_ABBR.get(name, name)


def match_seq_tick_labels(opponent_order):
    """
    Etichette asse X del grafico "Confronto tra giocatori": nome avversario
    (abbreviato se necessario), ripetuto per andata (0..12) e ritorno (13..25);
    playoff (26, 27) con suffisso per distinguere andata/ritorno.
    """
    labels = [abbr_opponent(o) for o in opponent_order]
    labels += [abbr_opponent(o) for o in opponent_order]
    labels += [f"{abbr_opponent('JVC')} (PO-A)", f"{abbr_opponent('JVC')} (PO-R)"]
    return labels

st.set_page_config(page_title="Decimo Roma — Andata vs Ritorno", layout="wide")


@st.cache_data(show_spinner="Caricamento partite della stagione da Google Drive...")
def load_matches_cached(season):
    """Cache separata dal resto: è la parte lenta (lettura Excel da Drive), riusata
    sia dal dataset KPI fisso sia da quello parametrico "Attacco SO"."""
    return load_all_matches(season)


@st.cache_data(show_spinner=False)
def load_data(season):
    matches = load_matches_cached(season)
    return build_comparison_dataset(season, matches=matches)


@st.cache_data(show_spinner=False)
def load_attacco_so_data(season, rec_vote):
    matches = load_matches_cached(season)
    return build_attacco_so_dataset(season, rec_vote=rec_vote, matches=matches)


@st.cache_data(show_spinner=False)
def load_match_outcomes(season):
    matches = load_matches_cached(season)
    return build_match_outcomes_dataset(season, matches=matches)


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
    partita_vinta in compute_match_outcome — capita solo per i due playoff
    quando manca il file dei risultati ufficiali) — alle stesse posizioni X
    (match_seq) del grafico principale sopra, così il confronto è immediato.
    """
    lookup = match_outcomes_df.set_index("match_seq")["partita_vinta"]
    colors, texts = [], []
    for x in x_positions:
        esito = lookup.get(x)
        if esito is True:
            colors.append(WIN_COLOR)
            texts.append("Vinta")
        elif esito is False:
            colors.append(LOSS_COLOR)
            texts.append("Persa")
        else:
            colors.append(UNKNOWN_COLOR)
            texts.append("Esito indeterminato")

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
    kpi, selected_entities, team_df, player_df, entity_master_order, bandwidth, opponent_order,
    match_outcomes_df,
):
    """
    Grafico di confronto multi-entità: una linea continua per entità su tutte
    le 28 partite, con una striscia vinta/persa sotto (vedi add_result_strip).
    """
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.85, 0.15], vertical_spacing=0.04)
    match_seq_full = list(range(N_REGULAR_MATCHES))
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

    tick_labels = match_seq_tick_labels(opponent_order)
    add_result_strip(fig, match_outcomes_df, match_seq_full, tick_labels, row=2, col=1)
    fig.update_layout(**base_layout(f"Confronto giocatori — {kpi}", "Avversario", kpi))
    return fig, pd.DataFrame(stats_rows)


def format_kpi_value(kpi, value):
    """Formatta un valore per la tabella riepilogativa, in base al tipo di KPI (percentuale o conteggio)."""
    if kpi in PERCENT_KPIS:
        return f"{value:.1f}%"
    return f"{value:.0f}"


def build_kpi_comparison_chart(
    entity, selected_kpis, team_df, player_df, kpi_master_order, bandwidth, opponent_order,
    match_outcomes_df,
):
    """
    Grafico di confronto multi-KPI per una singola entità: una linea continua
    per KPI su tutte le 28 partite, con una striscia vinta/persa sotto (vedi
    add_result_strip).
    """
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.85, 0.15], vertical_spacing=0.04)
    match_seq_full = list(range(N_REGULAR_MATCHES))
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

    tick_labels = match_seq_tick_labels(opponent_order)
    add_result_strip(fig, match_outcomes_df, match_seq_full, tick_labels, row=2, col=1)
    fig.update_layout(**base_layout(f"Confronto KPI — {entity}", "Avversario", "Valore"))
    return fig, pd.DataFrame(stats_rows)


def render_kpi_section(team_df, player_df, x_axis_order, entity_options):
    st.header("Confronto per KPI — Squadra / Giocatore")

    with st.sidebar:
        st.subheader("Confronto per KPI")
        selected_kpis = st.multiselect("KPI", DISPLAY_KPIS, default=[DISPLAY_KPIS[0]], key="kpi1")
        selected_entities = st.multiselect("Squadra / Giocatori", entity_options, default=["Squadra"], key="entity1")
        display_mode = st.radio("Mostra", ["Barre e trend", "Solo barre", "Solo trend"], key="mode1")
        show_table = st.checkbox("Mostra tabella dati sotto ogni grafico", key="table1")

    if not selected_kpis or not selected_entities:
        st.info("Seleziona almeno un KPI e un'entità (Squadra o un giocatore) dalla barra laterale.")
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


def render_comparison_section(team_df, player_df, entity_options, opponent_order, match_outcomes_df):
    st.header("Confronto tra giocatori")
    st.caption("Linea continua per entità lungo tutta la stagione (asse X: avversario, andata poi ritorno; in coda il playoff, sempre JVC) — striscia sotto: esito partita (verde=vinta, rosso=persa)")

    col_a, col_b = st.columns(2)
    with col_a:
        compare_kpis = st.multiselect("KPI", DISPLAY_KPIS, default=[DISPLAY_KPIS[0]], key="kpi2")
    with col_b:
        default_entities = entity_options[:3] if len(entity_options) >= 3 else entity_options
        compare_entities = st.multiselect(
            "Squadra / Giocatori da confrontare", entity_options, default=default_entities, key="entity2"
        )

    if not compare_kpis or not compare_entities:
        st.info("Seleziona almeno un KPI e almeno un'entità da confrontare.")
        return

    for kpi in compare_kpis:
        fig, stats_df = build_player_comparison_chart(
            kpi, compare_entities, team_df, player_df, entity_options, st.session_state["bandwidth"],
            opponent_order, match_outcomes_df,
        )
        st.plotly_chart(fig, theme=None)

        if not stats_df.empty:
            fmt = "%.1f%%" if kpi in PERCENT_KPIS else "%.0f"
            st.dataframe(
                stats_df,
                column_config={
                    "Min": st.column_config.NumberColumn(format=fmt),
                    "Mediana": st.column_config.NumberColumn(format=fmt),
                    "Max": st.column_config.NumberColumn(format=fmt),
                },
                width="stretch",
                hide_index=True,
            )
        else:
            st.warning(f"Nessun dato per {kpi} sulle entità selezionate.")


def render_kpi_comparison_section(team_df, player_df, entity_options, opponent_order, match_outcomes_df):
    st.header("Confronto KPI per una singola entità")
    st.caption("Linea continua per KPI, per Squadra o un giocatore, lungo tutta la stagione — striscia sotto: esito partita (verde=vinta, rosso=persa)")

    col_a, col_b = st.columns(2)
    with col_a:
        compare_entity = st.selectbox("Squadra / Giocatore", entity_options, key="entity3")
    with col_b:
        default_kpis = DISPLAY_KPIS[:3]
        compare_kpis2 = st.multiselect("KPI da confrontare", DISPLAY_KPIS, default=default_kpis, key="kpi3")

    if not compare_kpis2:
        st.info("Seleziona almeno un KPI da confrontare.")
        return

    if any(k in PERCENT_KPIS for k in compare_kpis2) and any(k not in PERCENT_KPIS for k in compare_kpis2):
        st.caption("⚠️ Stai confrontando KPI percentuali e conteggi assoluti sullo stesso asse — le scale non sono direttamente comparabili.")

    fig, stats_df = build_kpi_comparison_chart(
        compare_entity, compare_kpis2, team_df, player_df, DISPLAY_KPIS, st.session_state["bandwidth"],
        opponent_order, match_outcomes_df,
    )
    st.plotly_chart(fig, theme=None)

    if not stats_df.empty:
        st.dataframe(stats_df, width="stretch", hide_index=True)
    else:
        st.warning(f"Nessun dato per {compare_entity} sui KPI selezionati.")


def main():
    st.title("Decimo Roma — Confronto Andata vs Ritorno")
    st.caption(f"Stagione {SEASON} · playoff (solo JVC) mostrato come 14ª posizione / 27ª-28ª partita")

    with st.sidebar:
        st.header("Impostazioni generali")
        st.slider(
            "Smoothing linee di trend (kernel gaussiano)", 0.0, 3.0, 1.2, 0.1,
            key="bandwidth",
            help="0 = linea grezza punto a punto; valori più alti = curva più smussata",
        )
        selected_rec_vote = st.multiselect(
            'Esito ricezione per "Attacco SO"', REC_VOTE_OPTIONS,
            default=list(DEFAULT_REC_VOTE), key="rec_vote",
            help=(
                'Un attacco è "SO" (side-out) se segue immediatamente una ricezione con uno '
                'di questi voti (al più con un\'alzata di mezzo) — vedi i KPI "Attacco SO...". '
                "Cambiare la selezione ricalcola quei 4 KPI."
            ),
        )
        if not selected_rec_vote:
            st.warning('Nessun esito ricezione selezionato: i KPI "Attacco SO..." saranno vuoti.')

        min_attacks = st.number_input(
            "Soglia minima attacchi (per giocatore/partita)", min_value=0, value=0, step=1,
            help=(
                'Per i KPI della famiglia "attacco" (Attacco*, Attacco SO*, Contrattacco*): se in una '
                "partita un giocatore non raggiunge questo numero di attacchi della stessa famiglia, "
                "per quella partita risulta senza dati per quel KPI (come se non avesse giocato quel "
                "fondamentale) — non uno zero. Non si applica a Battuta/Ricezione/Muro/Errori. 0 = nessun filtro."
            ),
        )
        st.divider()

    team_df_fixed, player_df_fixed = load_data(SEASON)
    team_df_so, player_df_so = load_attacco_so_data(SEASON, tuple(selected_rec_vote))
    team_df = pd.concat([team_df_fixed, team_df_so], ignore_index=True)
    player_df = pd.concat([player_df_fixed, player_df_so], ignore_index=True)
    player_df = apply_min_attacks_threshold(player_df, min_attacks)

    match_outcomes_df = load_match_outcomes(SEASON)

    x_axis_order = get_x_axis_order(SEASON)
    players = sorted(player_df["player"].unique())
    entity_options = ["Squadra"] + players
    opponent_order = x_axis_order[:-1]  # senza PLAYOFF_LABEL, per il grafico 2

    render_kpi_section(team_df, player_df, x_axis_order, entity_options)
    st.divider()
    render_comparison_section(team_df, player_df, entity_options, opponent_order, match_outcomes_df)
    st.divider()
    render_kpi_comparison_section(team_df, player_df, entity_options, opponent_order, match_outcomes_df)


if __name__ == "__main__":
    main()
