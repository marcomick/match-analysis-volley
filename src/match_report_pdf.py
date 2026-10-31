# src/match_report_pdf.py
"""
PDF del report post-partita (dati: src/match_report.py), impaginato per la
lettura da telefono: pagine strette (PAGE_W x PAGE_H mm), una colonna,
grafici matplotlib inseriti come immagini, tabelle fpdf2.

Identità delle squadre sempre a colpo d'occhio (richiesta dell'utente,
2026-10-23): blu = Decimo, arancio = avversario, mostrati come "badge"
colorati col nome della squadra nell'intestazione e sopra ogni grafico (al
posto della legenda di matplotlib), e come etichette dirette nei grafici. Un
grafico per KPI a tutta larghezza, con le etichette dei set su ognuno e i
totali della partita come linee orizzontali etichettate. Un solo asse per
grafico (dataviz skill); il testo dei valori resta in inchiostro neutro.

Ordine delle sezioni: intestazione, commento, da dove arrivano i punti, noi e
loro per KPI, palleggiatori (con la distribuzione delle alzate), tabellino
giocatori, classifiche.
"""
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patheffects
import matplotlib.ticker
import numpy as np
from fpdf import FPDF
from fpdf.fonts import FontFace

from src.mvp import scoring_rules
from src.match_report import POINT_SOURCE_COLORS_LIGHT, POINT_SOURCES, SET_METRICS, TOTAL_LABEL
from src.setter_distribution import (
    ANTENNA_RED,
    CELL_INK,
    COURT_FLOOR,
    FREE_ZONE,
    NET_COLOR,
    NET_MESH,
    ZONE_GRID,
    share_color,
    short_player,
)

PAGE_W, PAGE_H = 120, 205  # mm
MARGIN = 6
CONTENT_W = PAGE_W - 2 * MARGIN
BOTTOM = 10  # mm riservati al numero di pagina

NOI_COLOR = "#2a78d6"
LORO_COLOR = "#eb6834"
NAVY = "#17243a"
INK = "#1f2328"
MUTED = "#6e7781"
GRID = "#e5e7eb"
HEADER_FILL = (238, 241, 245)
TOTAL_FILL = (222, 235, 251)
WHITE = (255, 255, 255)
MEDALS = [(229, 184, 11), (168, 169, 173), (205, 127, 50)]  # oro, argento, bronzo

TABLE_SIZE = 7.2
LINE_H = 4.0
PAD = 0.7
INT_W, PCT_W = 6.5, 10.0  # mm: colonne dei conteggi / delle percentuali
WIDE_COLS = {"muro_pt": 9.0, "p-e": 8.0}  # colonne sole sotto un'intestazione di gruppo più lunga (in mm)

MM_PER_INCH = 25.4

# Titolo e spiegazione di ciascun KPI per set (sopra il suo grafico).
METRIC_INFO = {
    "Battuta%": ("Battuta", "efficienza: ace e battute che mettono in difficoltà, meno gli errori"),
    "Ricezione%": ("Ricezione", "efficienza: perfette e positive, meno errori e palle rimandate di là"),
    "Attacco SO%": ("Attacco side-out", "efficienza dell'attacco subito dopo la ricezione"),
    "Contrattacco%": ("Contrattacco", "efficienza dell'attacco dopo una difesa o una free ball"),
    "Muro punti": ("Muro", "muri punto: numero"),
    "Errori": ("Errori", "battuta, attacco, alzata e muro: numero, meno è meglio"),
}
# KPI per set che sono conteggi (il totale è una somma, fuori scala rispetto ai set)
COUNT_METRICS = ("Errori", "Muro punti")

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 8, "text.color": INK,
    "axes.edgecolor": GRID, "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
})


def _t(text):
    """Testo compatibile con i font standard del PDF (latin-1)."""
    text = str(text).replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-").replace("…", "...")
    return text.encode("latin-1", "replace").decode("latin-1")


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _is_missing(v):
    return v is None or (isinstance(v, float) and np.isnan(v))


def _diverging_fill(value, scale=100.0):
    """Scala rosso/bianco/verde del tabellino Excel, attenuata per lasciare
    leggibile il testo: -scale rosso, 0 bianco, +scale verde."""
    if _is_missing(value):
        return None
    v = max(-1.0, min(1.0, value / scale)) * 0.6
    end = (0, 176, 80) if v >= 0 else (255, 0, 0)
    return tuple(round(255 + (e - 255) * abs(v)) for e in end)


def _fmt_int(v):
    return "." if _is_missing(v) else f"{int(v)}"


def _fmt_pct(v):
    return "." if _is_missing(v) else f"{v:.0f}%"


def _fig_to_png(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=250, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    buf.seek(0)
    return buf


class _ReportPDF(FPDF):
    def footer(self):
        self.set_y(-8)
        self.set_font("helvetica", size=6)
        self.set_text_color(*_rgb(MUTED))
        self.cell(0, 4, _t(f"{self.page_no()}"), align="R")


# ---------------------------------------------------------------------------
# Elementi di base
# ---------------------------------------------------------------------------
def _ensure_space(pdf, height_mm):
    """Nuova pagina se il blocco che segue (titolo compreso) non ci sta: mai
    un titolo in fondo a una pagina e il suo contenuto sulla successiva."""
    if pdf.get_y() + height_mm > PAGE_H - BOTTOM:
        pdf.add_page()


def _table_height(n_rows, n_heading_rows=1):
    return (n_rows + n_heading_rows) * (LINE_H + 2 * PAD) + 2


def _section_title(pdf, title, keep_with=0):
    _ensure_space(pdf, 12 + keep_with)
    pdf.ln(3)
    y = pdf.get_y()
    pdf.set_fill_color(*_rgb(NAVY))
    pdf.rect(MARGIN, y + 0.6, 1.6, 5.2, style="F")
    pdf.set_xy(MARGIN + 3.2, y)
    pdf.set_font("helvetica", "B", 11.5)
    pdf.set_text_color(*_rgb(NAVY))
    pdf.cell(0, 6.4, _t(title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_rgb(INK))
    pdf.ln(1)


def _note(pdf, text, size=7.2, markdown=False):
    pdf.set_font("helvetica", size=size)
    pdf.set_text_color(*_rgb(MUTED))
    pdf.multi_cell(0, 3.6, _t(text), new_x="LMARGIN", new_y="NEXT", markdown=markdown)
    pdf.set_text_color(*_rgb(INK))


def _chip(pdf, x, y, text, color, size=8.5, h=5.6):
    """Badge della squadra: rettangolo arrotondato nel colore della squadra
    con il nome in bianco. Ritorna la larghezza usata."""
    pdf.set_font("helvetica", "B", size)
    w = pdf.get_string_width(_t(text)) + 5
    pdf.set_fill_color(*_rgb(color))
    pdf.rect(x, y, w, h, style="F", round_corners=True, corner_radius=1.6)
    pdf.set_text_color(*WHITE)
    pdf.set_xy(x, y)
    pdf.cell(w, h, _t(text), align="C")
    pdf.set_text_color(*_rgb(INK))
    return w


def _team_legend(pdf, nome_noi, nome_loro):
    """I due badge delle squadre, in fila: la legenda di ogni grafico."""
    y = pdf.get_y()
    w = _chip(pdf, MARGIN, y, nome_noi, NOI_COLOR, size=7.5, h=4.8)
    _chip(pdf, MARGIN + w + 2.5, y, nome_loro, LORO_COLOR, size=7.5, h=4.8)
    pdf.set_xy(MARGIN, y + 6.5)


def _image(pdf, png, height_mm, shrink_to_fit=0.0):
    """`shrink_to_fit`: se l'immagine non sta nello spazio rimasto nella
    pagina, la si rimpicciolisce (centrata) fino a questa frazione invece di
    passare alla pagina successiva."""
    room = PAGE_H - BOTTOM - pdf.get_y() - 1.5
    if shrink_to_fit and height_mm > room >= height_mm * shrink_to_fit:
        w = CONTENT_W * room / height_mm
        pdf.image(png, x=MARGIN + (CONTENT_W - w) / 2, w=w)
        pdf.ln(1.5)
        return
    _ensure_space(pdf, height_mm)
    pdf.image(png, x=MARGIN, w=CONTENT_W)
    pdf.ln(1.5)


# ---------------------------------------------------------------------------
# Intestazione e commento
# ---------------------------------------------------------------------------
def _header(pdf, header, nome_noi, nome_loro):
    """Banda scura con le due squadre come badge, il risultato in grande e i
    parziali come pillole del colore di chi ha vinto il set."""
    noi_casa = header["casa"] == nome_noi
    color_casa, color_ospite = (NOI_COLOR, LORO_COLOR) if noi_casa else (LORO_COLOR, NOI_COLOR)
    band_h = 40
    pdf.set_fill_color(*_rgb(NAVY))
    pdf.rect(0, 0, PAGE_W, band_h, style="F")

    pdf.set_xy(MARGIN, 4.5)
    pdf.set_font("helvetica", "B", 7.5)
    pdf.set_text_color(185, 196, 214)
    pdf.cell(CONTENT_W, 4, _t(f"REPORT PARTITA  ·  {header['data'] or ''}".replace("·", "-")), align="C")

    # badge casa, risultato, badge ospite
    pdf.set_font("helvetica", "B", 24)
    score = f"{header['sets_casa']} - {header['sets_ospite']}"
    score_w = pdf.get_string_width(score) + 6
    pdf.set_font("helvetica", "B", 9)
    w_casa = pdf.get_string_width(_t(header["casa"])) + 5
    w_ospite = pdf.get_string_width(_t(header["ospite"])) + 5
    x = MARGIN + max(0, (CONTENT_W - (w_casa + score_w + w_ospite)) / 2)
    _chip(pdf, x, 13.5, header["casa"], color_casa, size=9, h=6.4)
    pdf.set_xy(x + w_casa, 10.7)
    pdf.set_font("helvetica", "B", 24)
    pdf.set_text_color(*WHITE)
    pdf.cell(score_w, 12, score, align="C")
    _chip(pdf, x + w_casa + score_w, 13.5, header["ospite"], color_ospite, size=9, h=6.4)

    # parziali
    pills = list(header["parziali"])
    pill_w, gap = 16, 2
    x = MARGIN + (CONTENT_W - (len(pills) * pill_w + (len(pills) - 1) * gap)) / 2
    for a, b in pills:
        pdf.set_fill_color(*_rgb(color_casa if a > b else color_ospite))
        pdf.rect(x, 25.5, pill_w, 6, style="F", round_corners=True, corner_radius=1.8)
        pdf.set_xy(x, 25.5)
        pdf.set_font("helvetica", "B", 8.5)
        pdf.set_text_color(*WHITE)
        pdf.cell(pill_w, 6, f"{a}-{b}", align="C")
        x += pill_w + gap

    incompleti = [f"{n}° set rilevato fino al {a}-{b}" for n, a, b, completo in header["set_scout"] if not completo]
    if incompleti:
        pdf.set_xy(MARGIN, 33.5)
        pdf.set_font("helvetica", "I", 6.8)
        pdf.set_text_color(185, 196, 214)
        pdf.cell(CONTENT_W, 4, _t("Scout parziale: " + ", ".join(incompleti)), align="C")
    pdf.set_text_color(*_rgb(INK))
    pdf.set_xy(MARGIN, band_h + 3)


def _comment(pdf, comment):
    if not comment or not comment.strip():
        return
    pdf.set_font("helvetica", size=9)
    text = _t(comment.strip())
    lines = len(pdf.multi_cell(CONTENT_W - 7, 4.6, text, dry_run=True, output="LINES"))
    h = lines * 4.6 + 5
    _section_title(pdf, "Il commento", keep_with=min(h, 40))
    pdf.set_font("helvetica", size=9)
    _ensure_space(pdf, h)
    y = pdf.get_y()
    pdf.set_fill_color(*TOTAL_FILL)
    pdf.rect(MARGIN, y, CONTENT_W, h, style="F", round_corners=True, corner_radius=1.5)
    pdf.set_fill_color(*_rgb(NOI_COLOR))
    pdf.rect(MARGIN, y, 1.4, h, style="F")
    pdf.set_xy(MARGIN + 4, y + 2.5)
    pdf.multi_cell(CONTENT_W - 7, 4.6, text, new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(y + h + 1)


# ---------------------------------------------------------------------------
# MVP della partita (src.mvp)
# ---------------------------------------------------------------------------
MVP_NOTE = "Come si guadagnano e si perdono i punti MVP: in appendice, in fondo al report."


def _fmt_signed(v):
    return f"{v:+.2f}".replace(".", ",")


def _mvp(pdf, row, highlights):
    """Il riquadro dell'MVP: coppa, nome, punteggio e i fondamentali che
    hanno pesato di più, ciascuno con i suoi numeri."""
    if row is None:
        return
    text_w = CONTENT_W - 8
    pdf.set_font("helvetica", size=8.4)
    lines = []
    for label, value, text in highlights:
        body = f"**{label}** ({_fmt_signed(value)}): {text}"
        n = len(pdf.multi_cell(text_w, 4.2, _t(body), dry_run=True, output="LINES", markdown=True))
        lines.append((body, n))
    h = 19 + sum(n for _, n in lines) * 4.2 + 3
    _ensure_space(pdf, h + 6)
    pdf.ln(2)
    y = pdf.get_y()
    pdf.set_fill_color(253, 246, 222)
    pdf.rect(MARGIN, y, CONTENT_W, h, style="F", round_corners=True, corner_radius=1.5)
    pdf.set_fill_color(*TROPHY_GOLD)
    pdf.rect(MARGIN, y, 1.6, h, style="F")
    _trophy(pdf, MARGIN + 5, y + 5, 10)
    pdf.set_xy(MARGIN + 16, y + 2)
    pdf.set_font("helvetica", "B", 7)
    pdf.set_text_color(*TROPHY_DARK)
    pdf.cell(CONTENT_W - 20, 3.5, "MVP DELLA PARTITA")
    pdf.set_xy(MARGIN + 16, y + 5)
    _fit_font(pdf, _t(_display_name(row["giocatore"])), "B", 16, CONTENT_W - 20)
    pdf.set_text_color(*_rgb(NAVY))
    pdf.cell(CONTENT_W - 20, 7, _t(_display_name(row["giocatore"])), new_x="LMARGIN", new_y="NEXT")
    pdf.set_xy(MARGIN + 16, y + 12)
    pdf.set_font("helvetica", size=8.5)
    pdf.set_text_color(*_rgb(MUTED))
    played = (f" · in {int(row['set_giocati'])} set su {int(row['set_partita'])}"
              if row["set_giocati"] < row["set_partita"] else "")
    pdf.cell(CONTENT_W - 20, 4.5, _t(f"{_fmt_signed(row['punteggio'])} punti MVP{played}"))
    pdf.set_text_color(*_rgb(INK))
    pdf.set_xy(MARGIN + 4.5, y + 19)
    pdf.set_left_margin(MARGIN + 4.5)
    pdf.set_font("helvetica", size=8.4)
    for body, _ in lines:
        pdf.multi_cell(text_w, 4.2, _t(body), new_x="LMARGIN", new_y="NEXT", markdown=True)
    pdf.set_left_margin(MARGIN)
    pdf.set_y(y + h + 1)
    _note(pdf, MVP_NOTE, size=6.6)

# ---------------------------------------------------------------------------
# Da dove arrivano i punti
# ---------------------------------------------------------------------------
def _points_tiles(pdf, sources, nome_noi, nome_loro):
    """Due "tessere" con i punti totali di ciascuna squadra."""
    y = pdf.get_y()
    w = (CONTENT_W - 3) / 2
    for i, (key, name, color) in enumerate((("noi", nome_noi, NOI_COLOR), ("loro", nome_loro, LORO_COLOR))):
        x = MARGIN + i * (w + 3)
        pdf.set_fill_color(*HEADER_FILL)
        pdf.rect(x, y, w, 9, style="F", round_corners=True, corner_radius=1.5)
        pdf.set_fill_color(*_rgb(color))
        pdf.rect(x, y, 1.4, 9, style="F")
        number = str(sum(sources[key].values()))
        pdf.set_font("helvetica", "B", 14)
        nw = pdf.get_string_width(number)
        pdf.set_font("helvetica", "B", 7)
        label = _t(f"punti {name}")
        lw = pdf.get_string_width(label)
        x0 = x + (w - nw - 2 - lw) / 2
        pdf.set_xy(x0, y + 1)
        pdf.set_font("helvetica", "B", 14)
        pdf.cell(nw, 7, number)
        pdf.set_xy(x0 + nw + 2, y + 1.6)
        pdf.set_font("helvetica", "B", 7)
        pdf.set_text_color(*_rgb(MUTED))
        pdf.cell(lw, 7, label)
        pdf.set_text_color(*_rgb(INK))
    pdf.set_xy(MARGIN, y + 11)


def _points_by_set_chart(points, header, nome_noi, nome_loro):
    """
    Da dove arrivano i punti set per set, come nella dashboard (richiesta
    dell'utente, 2026-10-30): un riquadro per set con il punteggio, dentro le
    due barre delle squadre impilate per causa, lunghe quanto i punti (stessa
    scala per tutti i set), il numero dentro ogni segmento. `points`:
    src.match_report.point_sources_by_set.
    """
    noi_casa = header["casa"] == nome_noi
    sets = [x for x in points["set"].unique() if x != TOTAL_LABEL]
    in_sets = points[points["set"] != TOTAL_LABEL]
    causes = [c for c in POINT_SOURCES if in_sets[in_sets["causa"] == c]["n"].sum()]
    scouted = {str(n): ok for n, _, _, ok in header["set_scout"]}
    per_panel = 0.5
    height = per_panel * len(sets) + 0.1
    fig, axes = plt.subplots(len(sets), 1, figsize=(CONTENT_W / MM_PER_INCH, height), sharex=True, squeeze=False)
    totals = in_sets.groupby(["set", "squadra"])["n"].sum()
    vmax = max(totals.max(), 1)
    for ax, label in zip(axes[:, 0], sets):
        sub = points[points["set"] == label]
        left = np.zeros(2)
        for causa in causes:
            vals = np.array([sub[(sub["squadra"] == k) & (sub["causa"] == causa)]["n"].sum() for k in ("noi", "loro")])
            ax.barh([0, 1], vals, 0.72, left=left, color=POINT_SOURCE_COLORS_LIGHT[causa], edgecolor="white",
                    linewidth=0.8)
            for yy, v, l0 in zip((0, 1), vals, left):
                if v:
                    ax.text(l0 + v / 2, yy, str(int(v)), ha="center", va="center", fontsize=6.6, color="white",
                            fontweight="bold")
            left += vals
        for yy, v in zip((0, 1), left):
            ax.text(v + vmax * 0.01, yy, str(int(v)), ha="left", va="center", fontsize=7, color=INK, fontweight="bold")
        i = int(label) - 1
        score = ""
        if i < len(header["parziali"]):
            a, b = header["parziali"][i]
            score = f"{a}-{b}" if noi_casa else f"{b}-{a}"
        partial = "  (scout parziale)" if not scouted.get(label, True) else ""
        ax.set_title(f"Set {label}   {score}{partial}", loc="left", fontsize=7.6, fontweight="bold", color=NAVY,
                     pad=2)
        ax.set_yticks([0, 1], [nome_noi, nome_loro])
        for tick, color in zip(ax.get_yticklabels(), (NOI_COLOR, LORO_COLOR)):
            tick.set_color(color)
            tick.set_fontweight("bold")
        ax.tick_params(axis="y", length=0, labelsize=7, pad=3)
        ax.invert_yaxis()
        ax.set_xlim(0, vmax * 1.08)
        ax.xaxis.set_visible(False)
        ax.spines["bottom"].set_visible(False)
    fig.subplots_adjust(hspace=0.85)
    return _fig_to_png(fig), height, causes


def _cause_legend(pdf, causes):
    """I colori delle cause dei punti, come badge in fila (a capo se serve)."""
    x, y = MARGIN, pdf.get_y()
    for causa in causes:
        pdf.set_font("helvetica", "B", 6.8)
        w = pdf.get_string_width(_t(causa)) + 5
        if x + w > MARGIN + CONTENT_W:
            x, y = MARGIN, y + 5.6
        _chip(pdf, x, y, causa, POINT_SOURCE_COLORS_LIGHT[causa], size=6.8, h=4.4)
        x += w + 1.8
    pdf.set_xy(MARGIN, y + 6)


# ---------------------------------------------------------------------------
# Noi e loro, KPI per KPI
# ---------------------------------------------------------------------------
def _metric_chart(comp, metric, nome_noi, nome_loro, incomplete_sets=()):
    """
    Un KPI a tutta larghezza: set per set (S1..S5 su ogni grafico) per le due
    squadre. Il totale della partita è una linea orizzontale tratteggiata nel
    colore della squadra, etichettata "Tot <squadra> <valore>" in una fascia
    libera a destra (così l'etichetta non copre mai i punti dei set). Per i
    conteggi (errori, muri punto) il totale è una somma, non sulla scala dei
    set: solo l'etichetta, senza linea, e la scala segue i valori dei set.
    Il valore di ogni set è scritto vicino al suo punto, nel colore della
    squadra (richiesta dell'utente, 2026-10-30): sopra per la squadra più
    alta in quel set, sotto per l'altra, così le due etichette non si
    sovrappongono mai tra loro né ai punti.
    """
    labels = [l for l in comp["noi"].index if l != TOTAL_LABEL]
    x = np.arange(len(labels))
    ticks = [f"S{l}*" if int(l) in incomplete_sets else f"S{l}" for l in labels]
    height = 1.75
    fig, ax = plt.subplots(figsize=(CONTENT_W / MM_PER_INCH, height))
    is_count = metric in COUNT_METRICS
    unit = "" if is_count else "%"
    series = {}
    for key, color, name in (("noi", NOI_COLOR, nome_noi), ("loro", LORO_COLOR, nome_loro)):
        vals = comp[key][metric].reindex(labels).astype(float).to_numpy()
        series[key] = (vals, color, name, float(comp[key][metric].get(TOTAL_LABEL, np.nan)))

    scale_vals = [s[0] for s in series.values()] + ([] if is_count else [[s[3] for s in series.values()]])
    all_vals = np.concatenate(scale_vals)
    lo, hi = np.nanmin(all_vals), np.nanmax(all_vals)
    span = max(hi - lo, 4 if is_count else 10)
    lo, hi = lo - span * 0.3, hi + span * 0.3  # spazio per le etichette dei set
    if not is_count:
        lo = min(lo, -2)
    if is_count:
        lo = min(lo, 0)

    label_x = x[-1] + 0.55  # inizio della fascia delle etichette, a destra dell'ultimo set
    for vals, color, name, tot in series.values():
        ax.plot(x, vals, color=color, linewidth=2, solid_capstyle="round", zorder=2)
        ax.scatter(x, vals, color=color, s=34, zorder=3, edgecolor="white", linewidth=1.2)
        if not np.isnan(tot) and not is_count:
            ax.axhline(tot, color=color, linewidth=1.1, linestyle=(0, (4, 3)), alpha=0.9, zorder=1)

    # valore di ogni set: sopra il punto per la squadra più alta, sotto per l'altra
    (v_noi, c_noi, _, _), (v_loro, c_loro, _, _) = series["noi"], series["loro"]
    halo = [matplotlib.patheffects.withStroke(linewidth=2.2, foreground="white")]
    for i in range(len(x)):
        pair = [(v, c) for v, c in ((v_noi[i], c_noi), (v_loro[i], c_loro)) if not np.isnan(v)]
        for k, (v, c) in enumerate(sorted(pair, key=lambda t: -t[0])):
            above = k == 0 if len(pair) == 2 else True
            ax.annotate(f"{v:.0f}{unit}", (x[i], v), xytext=(0, 5.5 if above else -5.5), textcoords="offset points",
                        ha="center", va="bottom" if above else "top", fontsize=6.4, fontweight="bold", color=c,
                        path_effects=halo, zorder=4)

    # etichette dei totali nella fascia di destra, centrate sulla propria
    # linea e distanziate se le due linee sono vicine
    tots = sorted(((s[3], s[2], s[1]) for s in series.values() if not np.isnan(s[3])), key=lambda t: t[0])
    if is_count:
        positions = [lo + (hi - lo) * f for f in (0.3, 0.72)][:len(tots)]
    else:
        positions = [t[0] for t in tots]
        min_gap = (hi - lo) * 0.36
        if len(positions) == 2 and positions[1] - positions[0] < min_gap:
            mid = (positions[0] + positions[1]) / 2
            positions = [mid - min_gap / 2, mid + min_gap / 2]
    for (tot, name, color), ypos in zip(tots, positions):
        ax.text(label_x, ypos, f"Tot {name}\n{tot:.0f}{unit}", fontsize=6.6, color=INK, fontweight="bold",
                ha="left", va="center", linespacing=1.15,
                bbox=dict(boxstyle="round,pad=0.25", facecolor="white", edgecolor=color, linewidth=1))
    if positions:
        lo, hi = min(lo, min(positions) - (hi - lo) * 0.18), max(hi, max(positions) + (hi - lo) * 0.18)

    if not is_count:
        ax.axhline(0, color="#c9ced6", linewidth=0.8, zorder=0)
    ax.axvline(x[-1] + 0.4, color=GRID, linewidth=0.8)
    ax.set_ylim(lo, hi)
    ax.set_xlim(-0.35, x[-1] + 2.4)
    ax.set_xticks(x, ticks)
    ax.tick_params(axis="x", labelsize=8, labelcolor=INK, length=0, pad=3)
    ax.tick_params(axis="y", labelsize=7, length=0)
    for label in ax.get_xticklabels():
        label.set_fontweight("bold")
    ax.yaxis.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    if not is_count:
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    else:
        ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True, nbins=4))
    return _fig_to_png(fig), height


def _comparison_section(pdf, comparison, header, nome_noi, nome_loro):
    incomplete = {n for n, _, _, completo in header["set_scout"] if not completo}
    for i, metric in enumerate(SET_METRICS):
        png, h_in = _metric_chart(comparison, metric, nome_noi, nome_loro, incomplete)
        block_h = h_in * MM_PER_INCH + 8
        if i == 0:
            _section_title(pdf, "Noi e loro, fondamentale per fondamentale", keep_with=block_h + 16)
            _note(pdf, "Set per set (S1, S2, ...) le due squadre; a destra il totale della partita, che per le "
                       "efficienze è anche la linea tratteggiata."
                       + (" * set rilevato solo in parte nello scout." if incomplete else ""))
            pdf.ln(1)
            _team_legend(pdf, nome_noi, nome_loro)
        _ensure_space(pdf, block_h)
        title, info = METRIC_INFO[metric]
        pdf.set_font("helvetica", "B", 9.5)
        pdf.cell(pdf.get_string_width(_t(title)) + 2, 5, _t(title))
        pdf.set_font("helvetica", size=6.8)
        pdf.set_text_color(*_rgb(MUTED))
        pdf.cell(0, 5, _t(info), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*_rgb(INK))
        _image(pdf, png, h_in * MM_PER_INCH)
        pdf.ln(1)


# ---------------------------------------------------------------------------
# Tabelle
# ---------------------------------------------------------------------------
def _grouped_table(pdf, groups, header, data, raw, widths, fills=None, size=TABLE_SIZE,
                   left_cols=(0,), bold_cols=(0,), total_row=True, cell_fills=None):
    """
    groups: [(titolo, n_colonne)] sopra `header` (su sfondo scuro); data:
    righe di testo; raw: righe di valori grezzi; fills: {indice colonna:
    funzione valore -> colore di sfondo o None}; cell_fills: {(riga,
    colonna): colore} forzati. Con total_row l'ultima riga è il totale.
    Ogni cella ha uno sfondo esplicito (bianco se non colorata): altrimenti
    fpdf2 riusa l'ultimo colore di riempimento impostato.
    """
    fills = fills or {}
    cell_fills = cell_fills or {}
    _ensure_space(pdf, _table_height(len(data), 2 if groups else 1))
    y_top, x_left = pdf.get_y(), pdf.l_margin
    pdf.set_font("helvetica", size=size)
    pdf.set_draw_color(*_rgb(GRID))
    heading = FontFace(emphasis="BOLD", fill_color=HEADER_FILL, color=_rgb(INK))
    with pdf.table(
        col_widths=widths, text_align="CENTER", line_height=LINE_H, width=min(sum(widths), pdf.epw), align="LEFT",
        headings_style=heading, num_heading_rows=2 if groups else 1, borders_layout="HORIZONTAL_LINES",
        padding=PAD,
    ) as table:
        if groups:
            row = table.row()
            for title, span in groups:
                style = FontFace(emphasis="BOLD", fill_color=_rgb(NAVY) if title else WHITE, color=WHITE,
                                 size_pt=size - 0.6)
                row.cell(_t(title), colspan=span, style=style)
        row = table.row()
        for h in header:
            row.cell(_t(h))
        last = len(data) - 1
        for i, (cells, raws) in enumerate(zip(data, raw)):
            row = table.row()
            is_total = total_row and i == last
            for j, (text, value) in enumerate(zip(cells, raws)):
                fill = cell_fills.get((i, j)) or (fills[j](value) if j in fills else None)
                style = FontFace(
                    emphasis="BOLD" if is_total or j in bold_cols else None,
                    fill_color=fill or (TOTAL_FILL if is_total else WHITE),
                    color=_rgb(NAVY) if is_total else _rgb(INK),
                )
                row.cell(_t(text), style=style, align="LEFT" if j in left_cols else "CENTER")
    if groups:
        # linee verticali tra le sezioni (gruppi di colonne), dalla riga sotto
        # i titoli di gruppo: sopra sarebbero blu su blu e sporgerebbero
        y_bottom = pdf.get_y()
        y_top += LINE_H + 2 * PAD
        pdf.set_draw_color(*_rgb(NAVY))
        pdf.set_line_width(0.35)
        x, col = x_left, 0
        for _, span in groups[:-1]:
            col += span
            x = x_left + sum(widths[:col])
            pdf.line(x, y_top, x, y_bottom)
        pdf.set_line_width(0.2)
        pdf.set_draw_color(*_rgb(GRID))
    pdf.ln(2)


def _widths(cols):
    """Larghezze: percentuali più larghe dei conteggi (es. '-100%'), il nome
    del giocatore prende lo spazio che resta."""
    widths = [PCT_W if f is _fmt_pct else WIDE_COLS.get(c, INT_W) for c, f in cols]
    return [CONTENT_W - sum(widths)] + widths


PLAYER_TABLE_LEGEND = (
    "**pt** punti fatti: attacchi, muri e ace punto.  "
    "**err** errori: battute, attacchi e alzate sbagliate, errori di muro (gli attacchi murati sono in "
    "**mur**, gli ace subiti sono gli **err** di ricezione).  "
    "**p-e/m** il saldo, punti fatti meno errori meno attacchi murati: sopra zero hai dato alla squadra più di "
    "quanto le hai tolto.  "
    "**eff** efficienza, sul totale delle azioni del fondamentale (come nella dashboard): battuta "
    "((# + + + /) - =); ricezione ((# + +) - (/ + =)); attacco (# - (= + /)). Muro: muri punto.  "
    "**Side-out** attacco subito dopo la ricezione; **contrattacco** tutti gli altri; **free ball (FB)** attacco "
    "dopo una difesa con voto ! (palla facile), una parte del contrattacco."
)


KPI_TABLE_LEGEND = (
    "Per ogni giocatore, fondamentale per fondamentale: **tot** azioni, **pt** punti fatti, **err** errori (in "
    "attacco solo gli attacchi sbagliati), **mur** murate, **p-e/m** punti meno errori meno murate (per i "
    "fondamentali senza murate, punti meno errori), **eff** efficienza (come nella dashboard: ricezione "
    "((# + +) - (/ + =)); battuta ((# + + + /) - =); attacco (# - (= + /)), tutte sul totale).  **Ricezione**: "
    "err = ace subiti.  **Attacco SO** (side-out) attacco subito dopo la ricezione; **attacco BP** (break-point) "
    "tutti gli altri, **free ball** compresa (attacco dopo una difesa con voto !).  **Palla alta** e **palla "
    "veloce** (super, quick): gli stessi attacchi divisi per tipo di alzata.  **Totale**: battuta, attacco, muro e "
    "alzata.  **Colori**: confronto con la media del giocatore nelle partite precedenti, verde meglio del solito, "
    "rosso peggio, più intenso quanto più si discosta (bianco = in media o storia troppo breve)."
)
KPI_TABLE_COLS = ["tot", "pt", "err", "mur", "pe", "eff"]
KPI_TABLE_WIDTHS = [15, 5.5, 5.5, 5.5, 6, 8, 7]  # mm: fondamentale, tot, pt, err, mur, p-e/m, eff
DEVIATION_SCALE = 2.5  # deviazioni standard per il colore pieno


def _kpi_table_rows(table):
    data, raw = [], []
    for r in table.to_dict("records"):
        label = "  di cui FB" if r["kpi"] == "fb" else r["label"]
        data.append([label, _fmt_int(r["tot"]), _fmt_int(r["pt"]), _fmt_int(r["err"]), _fmt_int(r.get("mur")),
                     "." if _is_missing(r["pe"]) else f"{int(r['pe']):+d}", _fmt_pct(r["eff"])])
        raw.append([None] * 7)
    return data, raw


def _player_kpi_tables(pdf, tables, deviations=None):
    """
    Tabellino del report, una tabella per giocatore (src.set_analysis.
    player_kpi_table, richiesta dell'utente 2026-10-30): fondamentali in riga,
    tot/punti/errori/murate/p-e/m/efficienza in colonna; due tabelle
    affiancate per riga, la squadra per prima. `deviations`: {giocatore:
    {(riga, colonna): z}} (src.set_analysis.kpi_deviations), il colore di
    ogni cella rispetto alla media personale.
    """
    entries = list(tables.items())
    if not entries:
        return
    deviations = deviations or {}
    first_h = 5 + _table_height(len(entries[0][1]))
    _section_title(pdf, "Tabellino giocatori", keep_with=first_h)
    _note(pdf, KPI_TABLE_LEGEND, markdown=True)
    pdf.ln(1)
    col_w = (CONTENT_W - 3) / 2
    for i in range(0, len(entries), 2):
        pair = entries[i:i + 2]
        _ensure_space(pdf, max(5 + _table_height(len(t)) for _, t in pair))
        y0, y_end = pdf.get_y(), pdf.get_y()
        for j, (name, table) in enumerate(pair):
            x0 = MARGIN + j * (col_w + 3)
            pdf.set_xy(x0, y0)
            pdf.set_font("helvetica", "B", 8.5)
            pdf.set_text_color(*_rgb(NAVY))
            pdf.cell(col_w, 5, _t(_display_name(name)))
            pdf.set_text_color(*_rgb(INK))
            pdf.set_xy(x0, y0 + 5)
            data, raw = _kpi_table_rows(table)
            fills = {}
            for (row, col), z in deviations.get(name, {}).items():
                fill = _diverging_fill(z, scale=DEVIATION_SCALE)
                if fill:
                    fills[(row, 1 + KPI_TABLE_COLS.index(col))] = fill
            pdf.set_left_margin(x0)
            _grouped_table(pdf, None, ["", "tot", "pt", "err", "mur", "p-e/m", "eff"], data, raw, KPI_TABLE_WIDTHS,
                           size=6.3, left_cols=(0,), bold_cols=(0,), cell_fills=fills)
            pdf.set_left_margin(MARGIN)
            y_end = max(y_end, pdf.get_y())
        pdf.set_xy(MARGIN, y_end + 1)


def _player_tables(pdf, table, legend=PLAYER_TABLE_LEGEND):
    rows = table.to_dict("records")
    # 1) punti, battuta, ricezione
    cols = [("pt", _fmt_int), ("err", _fmt_int), ("p-e", _fmt_int),
            ("bat_tot", _fmt_int), ("bat_err", _fmt_int), ("bat_ace", _fmt_int),
            ("bat_eff", _fmt_pct),
            ("ric_tot", _fmt_int), ("ric_err", _fmt_int), ("ric_eff", _fmt_pct)]
    data = [[r["giocatore"]] + [f(r[c]) for c, f in cols] for r in rows]
    raw = [[None] + [r[c] for c, _ in cols] for r in rows]
    _grouped_table(
        pdf, [("", 1), ("Punti", 3), ("Battuta", 4), ("Ricezione", 3)],
        ["", "pt", "err", "p-e/m", "tot", "err", "ace", "eff", "tot", "err", "eff"],
        data, raw, _widths(cols),
        fills={3: lambda v: _diverging_fill(v, scale=12), 7: _diverging_fill, 10: _diverging_fill},
    )
    # 2) attacco e muro
    rows = [r for r in rows if r["giocatore"] != "Generici"]
    cols = [("att_tot", _fmt_int), ("att_err", _fmt_int), ("att_mur", _fmt_int), ("att_pt", _fmt_int),
            ("att_eff", _fmt_pct), ("muro_pt", _fmt_int)]
    data = [[r["giocatore"]] + [f(r[c]) for c, f in cols] for r in rows]
    raw = [[None] + [r[c] for c, _ in cols] for r in rows]
    _grouped_table(
        pdf, [("", 1), ("Attacco", 5), ("Muro", 1)],
        ["", "tot", "err", "mur", "pt", "eff", "pt"],
        data, raw, _widths(cols), fills={5: _diverging_fill},
    )
    # 3) attacco per fase: side-out, contrattacco, di cui free ball
    cols = [("so_eff", _fmt_pct), ("so_tot", _fmt_int), ("contr_eff", _fmt_pct), ("contr_tot", _fmt_int),
            ("fb_eff", _fmt_pct), ("fb_tot", _fmt_int)]
    data = [[r["giocatore"]] + [f(r[c]) for c, f in cols] for r in rows]
    raw = [[None] + [r[c] for c, _ in cols] for r in rows]
    _grouped_table(
        pdf, [("", 1), ("Side-out", 2), ("Contrattacco", 2), ("di cui FB", 2)],
        ["", "eff", "n", "eff", "n", "eff", "n"],
        data, raw, _widths(cols), fills={1: _diverging_fill, 3: _diverging_fill, 5: _diverging_fill},
    )
    _note(pdf, legend, markdown=True)


COURT_GAP = 4  # mm tra le tre griglie di un palleggiatore
COURT_W = (CONTENT_W - 5 - 2 * COURT_GAP) / 3  # mm, lato di ogni metà campo
NET_H = 2.4
COURT_TITLE_H = 7.5
COURT_BLOCK_H = COURT_TITLE_H + 1.8 + NET_H + 0.4 + COURT_W + 2.5


def _court(pdf, stats, x0, width):
    """
    Distribuzione del palleggiatore (src.setter_distribution.zone_stats),
    in piccolo (tre per palleggiatore, una per situazione): metà campo vista
    da chi attacca, rete in alto con le antenne, linea dei 3 metri, 9 zone
    colorate per quota delle alzate, con quota, attacchi ed efficienza e il
    giocatore più servito. Disegno vettoriale, come il campo della
    dashboard (build_court_chart). Parte dalla y corrente, in `x0`.
    """
    margin = pdf.c_margin
    pdf.c_margin = 0  # testo misurato e centrato a mano nelle celle
    try:
        with pdf.local_context():
            _draw_court(pdf, stats, x0, width)
    finally:
        pdf.c_margin = margin
    pdf.set_text_color(*_rgb(INK))


TROPHY_GOLD = (229, 184, 11)
TROPHY_DARK = (150, 115, 0)


def _fit_font(pdf, text, style, size, width):
    """Imposta il font alla dimensione `size`, ridotta quanto serve perché
    `text` stia in `width` mm."""
    pdf.set_font("helvetica", style, size)
    w = pdf.get_string_width(text)
    if w > width:
        pdf.set_font("helvetica", style, size * width / w)


def _trophy(pdf, x, y, h):
    """Coppa stilizzata alta `h` mm con l'angolo in alto a sinistra in (x, y)."""
    with pdf.local_context():
        pdf.set_fill_color(*TROPHY_GOLD)
        pdf.set_draw_color(*TROPHY_GOLD)
        pdf.set_line_width(h * 0.09)
        w = h * 0.8
        pdf.rect(x + w * 0.12, y, w * 0.76, h * 0.42, style="F", round_corners=("BOTTOM_LEFT", "BOTTOM_RIGHT"),
                 corner_radius=w * 0.3)
        pdf.ellipse(x - w * 0.02, y + h * 0.04, w * 0.28, h * 0.28, style="D")
        pdf.ellipse(x + w * 0.74, y + h * 0.04, w * 0.28, h * 0.28, style="D")
        pdf.rect(x + w * 0.43, y + h * 0.42, w * 0.14, h * 0.28, style="F")
        pdf.rect(x + w * 0.22, y + h * 0.7, w * 0.56, h * 0.18, style="F")


def _draw_court(pdf, stats, x0, width):
    cell = width / 3
    top = pdf.get_y() + 1.8  # spazio per le antenne sopra la rete
    y0 = top + NET_H + 0.4
    pdf.set_fill_color(*_rgb(FREE_ZONE))
    pdf.rect(x0 - 1.2, top + NET_H, width + 2.4, width + 0.4 + 1.2, style="F")
    # rete: banda scura con le maglie, nastro bianco, antenne a strisce
    pdf.set_fill_color(*_rgb(NET_COLOR))
    pdf.rect(x0 - 1.2, top, width + 2.4, NET_H, style="F")
    pdf.set_draw_color(*_rgb(NET_MESH))
    pdf.set_line_width(0.1)
    x = x0 - 0.9
    while x < x0 + width + 1.1:
        pdf.line(x, top + 0.5, x, top + NET_H - 0.1)
        x += 0.8
    pdf.set_fill_color(*WHITE)
    pdf.rect(x0 - 1.2, top, width + 2.4, 0.5, style="F")
    pdf.set_draw_color(*_rgb(ANTENNA_RED))
    pdf.set_line_width(0.08)
    for xa in (x0, x0 + width):
        for k in range(4):
            pdf.set_fill_color(*(_rgb(ANTENNA_RED) if k % 2 else WHITE))
            pdf.rect(xa - 0.25, top - 1.8 + 0.9 * k, 0.5, 0.9, style="FD")

    # pavimento e zone
    pdf.set_fill_color(*_rgb(COURT_FLOOR))
    pdf.rect(x0, y0, width, width, style="F")
    by_zone = stats.set_index("zona")
    for r, zone_row in enumerate(ZONE_GRID):
        for c, z in enumerate(zone_row):
            cx, cy = x0 + c * cell, y0 + r * cell
            s = by_zone.loc[z]
            ink = _rgb(CELL_INK)
            if s["n"]:
                fill, white = share_color(s["pct"])
                ink = WHITE if white else _rgb(CELL_INK)
                pdf.set_fill_color(*_rgb(fill))
                pdf.rect(cx + 0.6, cy + 0.6, cell - 1.2, cell - 1.2, style="F", round_corners=True, corner_radius=1)
                pdf.set_text_color(*ink)
                eff = "-" if s["eff"] is None else f"{s['eff']:.0f}%"
                avail = cell - 1.6  # larghezza utile dentro il quadratino
                lines = [("B", 8.5, f"{s['pct']:.0f}%", 3.3), ("", 4.4, f"{int(s['n'])} att \u00b7 eff {eff}", 1.9)]
                y = cy + 1.9
                for style, size, text, h in lines:
                    _fit_font(pdf, _t(text), style, size, avail)
                    pdf.set_xy(cx, y)
                    pdf.cell(cell, h, _t(text), align="C")
                    y += h + 0.25
                if s["best"]:
                    # coppa disegnata + miglior p-e della zona (i font standard
                    # del PDF non hanno l'emoji)
                    name, pe, _ = s["best"]
                    text = _t(f"{short_player(name)} p-e/m {pe:+d}")
                    icon = 1.4
                    _fit_font(pdf, text, "", 4.1, avail - icon)
                    tw = pdf.get_string_width(text)
                    tx = cx + (cell - tw - icon) / 2
                    _trophy(pdf, tx, y + 0.3, 1.15)
                    pdf.set_xy(tx + icon, y)
                    pdf.cell(tw, 1.8, text)
            else:
                pdf.set_text_color(*_rgb(MUTED))
                pdf.set_font("helvetica", "", 7)
                pdf.set_xy(cx, cy + cell / 2 - 1.5)
                pdf.cell(cell, 3, "-", align="C")
            pdf.set_text_color(*ink)
            pdf.set_font("helvetica", "B", 3.8)
            pdf.set_xy(cx + 0.9, cy + 0.7)
            pdf.cell(2.5, 1.6, str(z))
    pdf.set_text_color(*_rgb(INK))

    # righe: divisori delle zone tratteggiati (non sono linee vere), linea
    # dei 3 metri e perimetro
    pdf.set_draw_color(*WHITE)
    pdf.set_line_width(0.15)
    pdf.set_dash_pattern(dash=0.5, gap=0.5)
    for k in (1, 2):
        pdf.line(x0 + k * cell, y0, x0 + k * cell, y0 + width)
    pdf.line(x0, y0 + 2 * cell, x0 + width, y0 + 2 * cell)
    pdf.set_dash_pattern()
    pdf.set_line_width(0.5)
    pdf.line(x0, y0 + cell, x0 + width, y0 + cell)
    pdf.rect(x0, y0, width, width, style="D")


def _setter_tables(pdf, setters, distributions=None):
    if not setters:
        return
    distributions = distributions or {}
    first = next(iter(setters.values()))
    _section_title(pdf, "Palleggiatori", keep_with=12 + 6 + _table_height(len(first)))
    _note(pdf, "Efficienza d'attacco della squadra **con lui in campo**, set per set: da ricezione positiva "
               "(+ o #), da ricezione ! (niente primo tempo) e da free ball. Tra parentesi il numero di attacchi.",
          markdown=True)
    if distributions:
        _note(pdf, "Sotto, la **distribuzione** con lui in campo, da R+ / #, da R! e da free ball: da quale zona "
                   "attaccano i giocatori serviti (% delle alzate, attacchi ed efficienza; con la coppa chi ha il miglior p-e/m della zona, punti − errori − murate, se maggiore di 2), escluse le "
                   "sue seconde intenzioni (per questo i totali possono differire di poco dalla tabella). Centrali sempre da 3, schiacciatori da dietro da 8 (pipe), opposto da "
                   "dietro da 9.", markdown=True)
        if any(d["stimate"] for d in distributions.values()):
            _note(pdf, "Direzioni d'attacco non rilevate in questa partita: zone di prima linea di schiacciatori "
                       "e opposti stimate dal ruolo (schiacciatore 4, opposto 2).")
    pdf.ln(1)
    keys = ["R+#", "R!", "FB"]
    for name, df in setters.items():
        _ensure_space(pdf, 6 + _table_height(len(df)))
        pdf.set_font("helvetica", "B", 9)
        pdf.set_text_color(*_rgb(NAVY))
        pdf.cell(0, 5.5, _t(name), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*_rgb(INK))
        data, raw = [], []
        for r in df.to_dict("records"):
            data.append([f"Set {r['set']}" if r["set"] != TOTAL_LABEL else "Tot"]
                        + [f"{_fmt_pct(r[f'{k} eff'])} ({r[f'{k} n']})" if r[f"{k} n"] else "." for k in keys])
            raw.append([None] + [r[f"{k} eff"] for k in keys])
        w = (CONTENT_W - 16) / 3
        _grouped_table(pdf, None, ["", "Da R+ / #", "Da R!", "Da free ball"], data, raw, [16, w, w, w],
                       fills={j: _diverging_fill for j in (1, 2, 3)})
        dist = distributions.get(name)
        if dist and any(dist[k]["n"] for k in COURT_SITUATIONS):
            _setter_courts(pdf, dist)


COURT_SITUATIONS = {"R+#": "Da R+ / #", "R!": "Da R!", "FB": "Da free ball"}


def _setter_courts(pdf, dist, titles=None):
    """Le tre griglie di un palleggiatore affiancate (src.setter_distribution.
    distributions_by_setter), ciascuna col suo titolo e volume; `titles`
    {chiave di dist: titolo} per altre terne di griglie con la stessa forma."""
    _ensure_space(pdf, 1 + COURT_BLOCK_H)
    pdf.ln(1)
    y = pdf.get_y()
    x_start = MARGIN + (CONTENT_W - 3 * COURT_W - 2 * COURT_GAP) / 2
    for j, (key, title) in enumerate((titles or COURT_SITUATIONS).items()):
        d = dist[key]
        x0 = x_start + j * (COURT_W + COURT_GAP)
        pdf.set_xy(x0 - 1.2, y)
        pdf.set_font("helvetica", "B", 7)
        pdf.set_text_color(*_rgb(NAVY))
        pdf.cell(COURT_W + 2.4, 3.6, _t(title), align="C")
        pdf.set_xy(x0 - 1.2, y + 3.6)
        pdf.set_font("helvetica", "", 5.6)
        pdf.set_text_color(*_rgb(MUTED))
        eff = "" if d["eff"] is None else f" \u00b7 efficienza totale {d['eff']:.0f}%"
        pdf.cell(COURT_W + 2.4, 3, _t(f"{d['n']} attacchi{eff}" if d["n"] else "nessun attacco"), align="C")
        pdf.set_text_color(*_rgb(INK))
        if d["n"]:
            pdf.set_y(y + COURT_TITLE_H)
            _court(pdf, d["stats"], x0, COURT_W)
    pdf.set_xy(MARGIN, y + COURT_BLOCK_H)


RANKING_HEADERS = {"Pos": "", "Battute": "Batt.", "Attacchi": "Att.", "Ricezioni": "Ric."}


def _display_name(giocatore):
    """'D'arienzo 22' -> "D'Arienzo 22" (il resto della pipeline normalizza i
    cognomi con capitalize, qui li mostriamo come nello scout)."""
    return " ".join(part.title() if not part.isdigit() else part for part in str(giocatore).split(" "))


def _rankings(pdf, rankings, n_partite, mins=None):
    if not rankings:
        return
    _section_title(pdf, "Classifiche della stagione", keep_with=12 + 5 + _table_height(len(rankings[0][1])))
    min_att, min_ric = mins or (0, 0)
    soglie = (f"Spike Guarantee con almeno {min_att} attacchi, Miglior ricevitore con almeno {min_ric} ricezioni"
              if min_att or min_ric else "senza soglie minime di attacchi e ricezioni")
    _note(pdf, f"Sulle {n_partite} partite giocate finora: {soglie}; Ace Man chi ha fatto almeno un ace, Muro della "
               "vergogna chi ha sbagliato almeno una battuta; MVP della stagione = somma dei punti MVP partita per "
               "partita (MVP = volte in cui è stato il migliore)"
               f"{' (una partita sola: le percentuali su pochi tentativi pesano molto)' if n_partite == 1 else ''}.")
    pdf.ln(1)
    col_w = (CONTENT_W - 3) / 2
    for i in range(0, len(rankings), 2):
        pair = rankings[i:i + 2]
        _ensure_space(pdf, max(5 + _table_height(len(df)) for _, df in pair))
        y0 = pdf.get_y()
        y_end = y0
        for j, (title, df) in enumerate(pair):
            x0 = MARGIN + j * (col_w + 3)
            pdf.set_xy(x0, y0)
            pdf.set_font("helvetica", "B", 8.5)
            pdf.set_text_color(*_rgb(NAVY))
            pdf.cell(col_w, 5, _t(title.split(" (")[0]))  # titolo corto: la colonna è stretta
            pdf.set_text_color(*_rgb(INK))
            pdf.set_xy(x0, y0 + 5)
            cols = [RANKING_HEADERS.get(c, c) for c in df.columns]
            name_w = col_w * 0.45
            widths = [5, name_w] + [(col_w - 5 - name_w) / (len(cols) - 2)] * (len(cols) - 2)
            data = [[_display_name(v) if c == "Giocatore" else str(v) for c, v in zip(df.columns, r)]
                    for r in df.itertuples(index=False)]
            raw = [[None] * len(cols) for _ in data]
            medals = {(k, 0): MEDALS[k] for k in range(min(3, len(data)))}
            pdf.set_left_margin(x0)
            _grouped_table(pdf, None, cols, data, raw, widths, size=6.8, left_cols=(1,), bold_cols=(0, 1),
                           total_row=False, cell_fills=medals)
            pdf.set_left_margin(MARGIN)
            y_end = max(y_end, pdf.get_y())
        pdf.set_xy(MARGIN, y_end + 1)


# ---------------------------------------------------------------------------
# Appendice: come si calcola il punteggio MVP (dai valori di config/mvp.json)
# ---------------------------------------------------------------------------
PILL_POS = (26, 127, 55)
PILL_NEG = (207, 34, 46)
PILL_ZERO = (140, 149, 159)
RULE_ROW_H = 5.4


def _value_pill(pdf, x, y, value, w=15, h=4.4):
    color = PILL_POS if value > 0 else PILL_NEG if value < 0 else PILL_ZERO
    pdf.set_fill_color(*color)
    pdf.rect(x, y, w, h, style="F", round_corners=True, corner_radius=1.4)
    pdf.set_xy(x, y)
    pdf.set_font("helvetica", "B", 8)
    pdf.set_text_color(*WHITE)
    pdf.cell(w, h, "0" if value == 0 else f"{value:+.2f}".replace(".", ","), align="C")
    pdf.set_text_color(*_rgb(INK))


def _rule_block(pdf, title, rows):
    """Un fondamentale: titolo e una riga per azione, valore in una pillola
    verde (guadagna), rossa (perde) o grigia (non conta)."""
    pdf.set_font("helvetica", size=8.3)
    n_lines = [len(pdf.multi_cell(CONTENT_W - 18, 4.0, _t(d), dry_run=True, output="LINES")) for _, d in rows]
    _ensure_space(pdf, 8 + sum(RULE_ROW_H + 4.0 * (n - 1) for n in n_lines))
    pdf.ln(1.5)
    pdf.set_font("helvetica", "B", 9.5)
    pdf.set_text_color(*_rgb(NAVY))
    pdf.cell(0, 5.5, _t(title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*_rgb(GRID))
    pdf.line(MARGIN, pdf.get_y(), MARGIN + CONTENT_W, pdf.get_y())
    pdf.ln(0.8)
    for (value, desc), n in zip(rows, n_lines):
        y = pdf.get_y()
        _value_pill(pdf, MARGIN, y + 0.5, value)
        pdf.set_xy(MARGIN + 18, y + 0.7)
        pdf.set_font("helvetica", size=8.3)
        pdf.set_text_color(*_rgb(INK if value else MUTED))
        pdf.multi_cell(CONTENT_W - 18, 4.0, _t(desc))
        pdf.set_text_color(*_rgb(INK))
        pdf.set_xy(MARGIN, y + RULE_ROW_H + 4.0 * (n - 1))


def _rule_text(pdf, title, paragraphs):
    pdf.set_font("helvetica", size=8.3)
    h = 8 + sum(len(pdf.multi_cell(CONTENT_W, 4.2, _t(p), dry_run=True, output="LINES", markdown=True)) * 4.2 + 1
                for p in paragraphs)
    _ensure_space(pdf, h)
    pdf.ln(1.5)
    pdf.set_font("helvetica", "B", 9.5)
    pdf.set_text_color(*_rgb(NAVY))
    pdf.cell(0, 5.5, _t(title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*_rgb(GRID))
    pdf.line(MARGIN, pdf.get_y(), MARGIN + CONTENT_W, pdf.get_y())
    pdf.ln(1)
    pdf.set_text_color(*_rgb(INK))
    for p in paragraphs:
        pdf.set_font("helvetica", size=8.3)
        pdf.multi_cell(CONTENT_W, 4.2, _t(p), new_x="LMARGIN", new_y="NEXT", markdown=True)
        pdf.ln(1)


def _num(v, digits=2):
    return f"{v:.{digits}f}".replace(".", ",")


def _mvp_appendix(pdf, rules):
    pdf.add_page()
    _section_title(pdf, "Appendice: i punti MVP")
    _note(pdf, "Ogni azione vale quanto cambia la probabilità di vincere lo scambio (calcolata su tutte le partite "
               "rilevate). Il punteggio di un giocatore è la somma delle sue azioni.", size=7.6)
    pdf.ln(1)
    y = pdf.get_y()
    x = MARGIN
    for value, label in ((0.4, "guadagna"), (-0.6, "perde"), (0, "non conta")):
        _value_pill(pdf, x, y, value, w=12, h=4)
        pdf.set_xy(x + 13, y)
        pdf.set_font("helvetica", size=7.6)
        pdf.set_text_color(*_rgb(MUTED))
        pdf.cell(20, 4, label)
        x += 34
    pdf.set_text_color(*_rgb(INK))
    pdf.set_xy(MARGIN, y + 6)
    for title, rows in rules["per_fondamentale"]:
        _rule_block(pdf, title, rows)

    q, media = rules["quota"], rules["media"]
    n_ex = 40
    atteso = media * n_ex
    _rule_text(pdf, "Palleggiatore", [
        f"Oltre alle sue azioni, il palleggiatore guadagna **{_num(q)} per ogni punto** e perde **{_num(q)} per ogni "
        "errore o murata** dei suoi attaccanti, ma solo per la parte **sopra o sotto il solito**: in media un "
        f"attacco vale {_num(media)} di punti meno errori e murate.",
        f"Esempio: alza {n_ex} palloni, su cui di solito i suoi attaccanti farebbero +{_num(atteso, 1)} (punti meno "
        f"errori e murate). Se fanno +14 guadagna (14 - {_num(atteso, 1)}) x {_num(q)} = "
        f"**+{_num((14 - atteso) * q)}**; se fanno +4 perde ({_num(atteso, 1)} - 4) x {_num(q)} = "
        f"**-{_num((atteso - 4) * q)}**.",
        "Contano gli attacchi dopo una ricezione #, + o ! e dopo una free ball"
        + (", solo su alzata veloce" if rules["solo_veloci"] else "")
        + ", non i suoi attacchi. Gli errori di alzata li paga come tutti.",
    ])
    k = rules["set_fittizi"]
    ex_tot, ex_sets, ex_played = 3.0, 5, 2
    ex = ex_tot * (ex_sets + k) / (ex_played + k)
    _rule_text(pdf, "Chi gioca meno set", [
        "Un punteggio positivo si rapporta ai set giocati, così può vincere anche chi entra a partita in corso: "
        f"**totale x (set della partita + {k}) / (set giocati + {k})**. I punteggi negativi restano come sono.",
        f"Esempio: in una partita da {ex_sets} set chi gioca solo gli ultimi {ex_played} e fa +{_num(ex_tot)} arriva a "
        f"{_num(ex_tot)} x {ex_sets + k}/{ex_played + k} = **+{_num(ex)}**.",
    ])

# ---------------------------------------------------------------------------
# Documento
# ---------------------------------------------------------------------------
def build_match_report_pdf(header, comment, sources, points_by_set, comparison, players, setters, rankings,
                           nome_noi, nome_loro, n_partite_stagione, distributions=None, ranking_mins=None,
                           deviations=None, mvp=None):
    """Ritorna il PDF (bytes). `points_by_set`: src.match_report.
    point_sources_by_set; `players`: src.set_analysis.player_kpi_table;
    `distributions`: griglie della distribuzione per
    palleggiatore (src.setter_distribution.distributions_by_setter);
    `ranking_mins`: (min attacchi, min ricezioni) delle classifiche;
    `deviations`: colori del tabellino rispetto alle medie personali; `mvp`:
    (riga dell'MVP di src.mvp.match_mvp, src.mvp.mvp_highlights)."""
    pdf = _ReportPDF(format=(PAGE_W, PAGE_H))
    pdf.set_margins(MARGIN, MARGIN, MARGIN)
    pdf.set_auto_page_break(True, margin=BOTTOM)
    pdf.add_page()

    # prima pagina: risultato, MVP e da dove arrivano i punti (richiesta
    # dell'utente, 2026-10-31); il commento subito dopo
    _header(pdf, header, nome_noi, nome_loro)
    if mvp:
        _mvp(pdf, *mvp)

    png_points, h_points, causes = _points_by_set_chart(points_by_set, header, nome_noi, nome_loro)
    _section_title(pdf, "Da dove arrivano i punti", keep_with=30)
    _points_tiles(pdf, sources, nome_noi, nome_loro)
    _note(pdf, "Set per set, divisi per causa: l'azione vincente di chi ha fatto il punto, altrimenti l'errore di chi "
               "l'ha perso.", size=6.8)
    pdf.ln(0.5)
    _cause_legend(pdf, causes)
    _image(pdf, png_points, h_points * MM_PER_INCH, shrink_to_fit=0.75)
    _comment(pdf, comment)

    _comparison_section(pdf, comparison, header, nome_noi, nome_loro)
    _setter_tables(pdf, setters, distributions)

    _player_kpi_tables(pdf, players, deviations)

    _rankings(pdf, rankings, n_partite_stagione, ranking_mins)
    if mvp:
        _mvp_appendix(pdf, scoring_rules())
    return bytes(pdf.output())
