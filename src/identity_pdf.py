# src/identity_pdf.py
"""
PDF "La nostra identità" da condividere in allenamento con i ragazzi
(richiesta dell'utente del 2026-11-11): chi siamo (la situazione che porta
punti su cui appoggiarci), le regole dentro quella situazione, cosa
recuperare e cosa costruire con il come lavorarci, come ci misuriamo
partita per partita, e per ogni giocatore su cosa ci appoggiamo e su cosa
lavora. Ogni indicazione ha il suo grado di certezza (pallini).

Stesso formato stretto del report post-partita (src/match_report_pdf.py),
per la lettura da telefono. Dati: src/identity.identity_analysis.
"""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.identity import (
    CERTAINTY_LEVELS,
    LINE_LABELS,
    LINE_NOTES,
    SITUATION_WITH,
    pillar_texts,
)
from src.match_report_pdf import (
    CONTENT_W,
    HEADER_FILL,
    INK,
    MARGIN,
    MUTED,
    NAVY,
    NOI_COLOR,
    PAGE_H,
    PAGE_W,
    BOTTOM,
    WHITE,
    _court,
    _ensure_space,
    _fig_to_png,
    _image,
    _note,
    _ReportPDF,
    _rgb,
    _section_title,
    _t,
)
from src.performance_model import SITUATIONS
from src.setter_distribution import zone_stats

GREEN, ORANGE, RED, GREY = "#1a7f37", "#d97706", "#cf222e", "#898781"
LEVEL_DOTS = {level: 3 - i for i, (_, level) in enumerate(CERTAINTY_LEVELS)}
RULE_ORDER = {"linee": 0, "scelta": 1, "evitare": 2, "costruire": 3, "rotazione": 4}
LINE_ORDER = {"dietro": 0, "davanti": 1}


def _cap(text):
    return text[:1].upper() + text[1:]


def _rule_key(r):
    """Prima il confronto tra le linee, poi le regole del palleggiatore dietro
    e davanti (scelte, da evitare, da costruire), infine la rotazione."""
    if r["tipo"] == "linee":
        return (0, 0, 0, -r["p"])
    if r["tipo"] == "rotazione":
        return (3, 0, 0, -r["p"])
    return (1, LINE_ORDER.get(r.get("linea"), 2), RULE_ORDER[r["tipo"]], -r["p"])


# ---------------------------------------------------------------------------
# Elementi
# ---------------------------------------------------------------------------
def _dots(pdf, x, y, level, color=NOI_COLOR):
    """Tre pallini: pieni quanti il livello di certezza (3 certo, 2
    probabile, 1 da verificare)."""
    filled = LEVEL_DOTS.get(level, 0)
    pdf.set_line_width(0.25)
    pdf.set_draw_color(*_rgb(color))
    for i in range(3):
        pdf.set_fill_color(*(_rgb(color) if i < filled else WHITE))
        pdf.circle(x + i * 2.3 + 0.9, y + 0.9, 0.85, style="FD")


def _item(pdf, text, level=None, size=8.0, color=NOI_COLOR, sub=None):
    """Una voce: pallini di certezza a sinistra e testo (markdown)."""
    indent = 8.5 if level else 3
    pdf.set_font("helvetica", size=size)
    lines = len(pdf.multi_cell(CONTENT_W - indent, 3.9, _t(text), dry_run=True, output="LINES", markdown=True))
    sub_lines = 0
    if sub:
        pdf.set_font("helvetica", "I", size - 1)
        sub_lines = len(pdf.multi_cell(CONTENT_W - indent, 3.4, _t(sub), dry_run=True, output="LINES"))
    _ensure_space(pdf, lines * 3.9 + sub_lines * 3.4 + 1.5)
    y = pdf.get_y()
    if level:
        _dots(pdf, MARGIN, y + 1.0, level, color)
    pdf.set_xy(MARGIN + indent, y)
    pdf.set_font("helvetica", size=size)
    pdf.set_text_color(*_rgb(INK))
    pdf.multi_cell(CONTENT_W - indent, 3.9, _t(text), new_x="LMARGIN", new_y="NEXT", markdown=True)
    if sub:
        pdf.set_x(MARGIN + indent)
        pdf.set_font("helvetica", "I", size - 1)
        pdf.set_text_color(*_rgb(MUTED))
        pdf.multi_cell(CONTENT_W - indent, 3.4, _t(sub), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*_rgb(INK))
    pdf.ln(1.2)


def _card(pdf, title, text, color, how=None):
    """Riquadro con barra colorata a sinistra: titolo, testo e, se c'è,
    "Come lavoriamo"."""
    pdf.set_font("helvetica", size=8.4)
    lines = len(pdf.multi_cell(CONTENT_W - 7, 4.1, _t(text), dry_run=True, output="LINES", markdown=True))
    how_lines = 0
    if how:
        pdf.set_font("helvetica", size=7.6)
        how_lines = len(pdf.multi_cell(CONTENT_W - 7, 3.6, _t("Come lavoriamo: " + how), dry_run=True,
                                       output="LINES", markdown=True))
    h = (6 if title else 3.5) + lines * 4.1 + (how_lines * 3.6 + 1.5 if how else 0) + 2
    _ensure_space(pdf, h + 2)
    y = pdf.get_y()
    pdf.set_fill_color(*HEADER_FILL)
    pdf.rect(MARGIN, y, CONTENT_W, h, style="F", round_corners=True, corner_radius=1.5)
    pdf.set_fill_color(*_rgb(color))
    pdf.rect(MARGIN, y, 1.4, h, style="F")
    pdf.set_xy(MARGIN + 4, y + 1.6)
    if title:
        pdf.set_font("helvetica", "B", 9)
        pdf.set_text_color(*_rgb(color))
        pdf.cell(0, 4.4, _t(title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_rgb(INK))
    pdf.set_x(MARGIN + 4)
    pdf.set_font("helvetica", size=8.4)
    pdf.multi_cell(CONTENT_W - 7, 4.1, _t(text), new_x="LMARGIN", new_y="NEXT", markdown=True)
    if how:
        pdf.ln(1)
        pdf.set_x(MARGIN + 4)
        pdf.set_font("helvetica", size=7.6)
        pdf.multi_cell(CONTENT_W - 7, 3.6, _t("**Come lavoriamo:** " + how), new_x="LMARGIN", new_y="NEXT",
                       markdown=True)
    pdf.set_y(y + h + 2)


def _header(pdf, I, generated):
    band_h = 34
    pdf.set_fill_color(*_rgb(NAVY))
    pdf.rect(0, 0, PAGE_W, band_h, style="F")
    pdf.set_xy(MARGIN, 5)
    pdf.set_font("helvetica", "B", 7.5)
    pdf.set_text_color(185, 196, 214)
    pdf.cell(CONTENT_W, 4, _t(f"IDENTITA' DI GIOCO  -  {generated}"), align="C")
    pdf.set_xy(MARGIN, 11)
    pdf.set_font("helvetica", "B", 17)
    pdf.set_text_color(*WHITE)
    pdf.cell(CONTENT_W, 9, _t("La nostra identità"), align="C")
    pdf.set_xy(MARGIN, 22)
    pdf.set_font("helvetica", "", 8.5)
    n = I["n_partite"]
    pdf.cell(CONTENT_W, 5, _t(f"Decimo Roma  -  stagione {I['stagione']}  -  {n} {'partita' if n == 1 else 'partite'} "
                              "con lo scout"), align="C")
    pdf.set_text_color(*_rgb(INK))
    pdf.set_xy(MARGIN, band_h + 3)


def _coach_notes(pdf, text):
    if text and text.strip():
        _section_title(pdf, "Dall'allenatore")
        _card(pdf, None, text.strip(), NAVY)


def _legend(pdf):
    _ensure_space(pdf, 18)
    pdf.set_font("helvetica", "B", 7.4)
    pdf.cell(0, 4, _t("Quanto siamo sicuri"), new_x="LMARGIN", new_y="NEXT")
    for _, level in CERTAINTY_LEVELS:
        desc = {"certo": "certo: lo dicono i numeri e si vede quasi in ogni partita",
                "probabile": "probabile: molto verosimile, da confermare nelle prossime partite",
                "da verificare": "da verificare: un indizio, lo teniamo d'occhio"}[level]
        _item(pdf, desc, level, size=7.2)


def _situations_chart(I):
    """Scambi vinti per situazione: noi quest'anno (pallino blu), l'anno
    scorso (cerchio vuoto), squadre playoff (rombo grigio)."""
    t = I["situazioni"].set_index("situazione").loc[list(SITUATIONS)]
    t = t[t["noi_n"] > 0]
    fig, ax = plt.subplots(figsize=(4.6, 0.42 * len(t) + 0.9))
    y = np.arange(len(t))[::-1]
    for yi, (_, x) in zip(y, t.iterrows()):
        vals = [v for v in (x["noi_vinti_pct"], x["playoff_vinti_pct"], x["prima_vinti_pct"]) if pd.notna(v)]
        ax.plot([100 * min(vals), 100 * max(vals)], [yi, yi], color="#e5e7eb", lw=2, zorder=1)
    ax.scatter(100 * t["playoff_vinti_pct"], y, marker="D", s=30, color=GREY, zorder=2, label="squadre playoff")
    if t["prima_vinti_pct"].notna().any():
        ax.scatter(100 * t["prima_vinti_pct"], y, s=34, facecolors="white", edgecolors=NOI_COLOR, linewidths=1.2,
                   zorder=3, label="noi l'anno scorso")
    ax.scatter(100 * t["noi_vinti_pct"], y, s=52, color=NOI_COLOR, edgecolors="white", linewidths=1, zorder=4,
               label="noi quest'anno")
    ax.set_yticks(y)
    ax.set_yticklabels([f"{x['breve']}\n{x['noi_per_set']:.0f} attacchi a set".replace(".", ",")
                        for _, x in t.iterrows()], fontsize=7)
    ax.set_xlabel("scambi vinti su 100 attacchi", fontsize=7)
    ax.tick_params(axis="x", labelsize=7)
    ax.tick_params(axis="y", length=0)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.grid(axis="x", color="#eef0f3", lw=0.6)
    ax.set_axisbelow(True)
    lo = 100 * np.nanmin(t[["noi_vinti_pct", "playoff_vinti_pct", "prima_vinti_pct"]].to_numpy()) - 4
    hi = 100 * np.nanmax(t[["noi_vinti_pct", "playoff_vinti_pct", "prima_vinti_pct"]].to_numpy()) + 4
    ax.set_xlim(lo, hi)
    ax.legend(loc="upper center", bbox_to_anchor=(0.45, -0.28 if len(t) > 3 else -0.45), ncol=3, fontsize=6.5,
              frameon=False, handletextpad=0.3, columnspacing=1.0)
    return _fig_to_png(fig)


def _line_courts(pdf, detail):
    """Due campetti della situazione: palleggiatore dietro e davanti."""
    gap = 5
    width = (CONTENT_W - gap) / 2 - 4
    block_h = 9 + width + 6
    _ensure_space(pdf, block_h)
    y = pdf.get_y()
    for j, line in enumerate(("dietro", "davanti")):
        d = detail["linee"][line]
        x0 = MARGIN + 2 + j * (width + gap + 4)
        pdf.set_xy(x0 - 2, y)
        pdf.set_font("helvetica", "B", 7)
        pdf.set_text_color(*_rgb(NAVY))
        pdf.cell(width + 4, 3.6, _t(_cap(LINE_LABELS[line])), align="C")
        pdf.set_xy(x0 - 2, y + 3.6)
        pdf.set_font("helvetica", "", 5.8)
        pdf.set_text_color(*_rgb(MUTED))
        won = f" - {d['vinti']} scambi vinti ({100 * d['vinti_pct']:.0f}%)" if d["n"] else ""
        pdf.cell(width + 4, 3, _t(f"{d['n']} attacchi{won}"), align="C")
        pdf.set_text_color(*_rgb(INK))
        rows = d["righe"]
        if len(rows):
            pdf.set_y(y + 8)
            _court(pdf, zone_stats(rows[["zona", "attaccante", "esito"]].rename(columns={"esito": "voto"})), x0, width)
    pdf.set_xy(MARGIN, y + block_h)
    _note(pdf, "In ogni zona: quota dei palloni (colore e numero grande), attacchi ed efficienza, chi la attacca.",
          size=6.4)


def _rules(pdf, detail, kinds=None, limit=None):
    rules = sorted(detail["regole"], key=_rule_key)
    if kinds:
        rules = [r for r in rules if r["tipo"] in kinds]
    for r in rules[:limit]:
        color = RED if r["tipo"] in ("evitare", "rotazione") else (ORANGE if r["tipo"] == "costruire" else NOI_COLOR)
        _item(pdf, r["testo"], r["certezza"], color=color)
    if not rules:
        _note(pdf, "Ancora nessuna regola chiara: servono più partite.")


def _monitor(pdf, I):
    sit = I["pilastri"]["forza"]
    if not sit:
        return
    d = I["dettagli"][sit]
    t = I["situazioni"].set_index("situazione").loc[sit]
    cmp_ = d.get("confronto_linee")
    _section_title(pdf, "Come ci misuriamo", keep_with=30)
    _item(pdf, f"Obiettivo di ogni partita: {SITUATION_WITH[sit].lower()} vincere almeno "
               f"**{100 * t['playoff_vinti_pct']:.0f} scambi su 100**, come le squadre in zona playoff, sia con il "
               "palleggiatore dietro sia davanti.")
    if cmp_ is not None and len(cmp_["partite"]):
        pdf.set_font("helvetica", "B", 7)
        widths = [CONTENT_W - 44, 22, 22]
        pdf.set_fill_color(*HEADER_FILL)
        for w, h in zip(widths, ["Partita", "Pall. dietro", "Pall. davanti"]):
            pdf.cell(w, 5, _t(h), border=0, fill=True, align="C" if h != "Partita" else "L")
        pdf.ln(5)
        pdf.set_font("helvetica", "", 7)
        target = t["playoff_vinti_pct"]
        for _, x in cmp_["partite"].iterrows():
            _ensure_space(pdf, 5)
            pdf.cell(widths[0], 4.6, _t(x["partita"][11:] if len(x["partita"]) > 11 else x["partita"]))
            for w, line in zip(widths[1:], ("dietro", "davanti")):
                val = x.get(line, "")
                ok = None
                if val:
                    k, n = (int(v) for v in val.split("/"))
                    ok = k / n >= target
                pdf.set_text_color(*_rgb(GREEN if ok else (RED if ok is not None else MUTED)))
                pdf.cell(w, 4.6, _t(val or "-"), align="C")
            pdf.set_text_color(*_rgb(INK))
            pdf.ln(4.6)
        _note(pdf, f"Scambi vinti su attacchi {SITUATION_WITH[sit].lower()}: in verde dove abbiamo raggiunto "
                   "l'obiettivo, in rosso dove no.", size=6.4)


def _players(pdf, I):
    players = I["giocatori"]
    _section_title(pdf, "Giocatore per giocatore", keep_with=30)
    _note(pdf, "Su cosa ci appoggiamo e su cosa lavori, rispetto ai compagni nella stessa situazione. Vale per "
               "quello che è successo finora: con più partite le indicazioni si confermano o cambiano.")
    for name, v in players.items():
        _ensure_space(pdf, 16)
        pdf.ln(1)
        pdf.set_font("helvetica", "B", 9.5)
        pdf.set_text_color(*_rgb(NAVY))
        pdf.cell(0, 5, _t(name), new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(*_rgb("#e5e7eb"))
        pdf.line(MARGIN, pdf.get_y(), MARGIN + CONTENT_W, pdf.get_y())
        pdf.ln(1)
        pdf.set_text_color(*_rgb(INK))
        if not v["appoggio"] and not v["lavoro"]:
            _note(pdf, "Per ora nessuna indicazione netta: rendi come i compagni nelle stesse situazioni, oppure "
                       "le azioni sono ancora poche.")
            continue
        if v["appoggio"]:
            pdf.set_font("helvetica", "B", 7.6)
            pdf.set_text_color(*_rgb(GREEN))
            pdf.cell(0, 4, _t("Ci appoggiamo su di te"), new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(*_rgb(INK))
            for it in v["appoggio"]:
                _item(pdf, it["testo"], it["certezza"], size=7.6, color=GREEN)
        if v["lavoro"]:
            pdf.set_font("helvetica", "B", 7.6)
            pdf.set_text_color(*_rgb(RED))
            pdf.cell(0, 4, _t("Ci lavoriamo"), new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(*_rgb(INK))
            for it in v["lavoro"]:
                _item(pdf, it["testo"], it["certezza"], size=7.6, color=RED,
                      sub=("Come: " + it["come"]) if it.get("come") else None)


def build_identity_pdf(I, notes="", generated=""):
    """PDF (bytes) da src.identity.identity_analysis."""
    pdf = _ReportPDF(format=(PAGE_W, PAGE_H))
    pdf.set_margins(MARGIN, MARGIN, MARGIN)
    pdf.set_auto_page_break(True, margin=BOTTOM)
    pdf.add_page()
    _header(pdf, I, generated)
    _coach_notes(pdf, notes)
    texts = pillar_texts(I)
    f = texts["forza"]

    _section_title(pdf, "Chi siamo")
    if f:
        _card(pdf, "Il nostro punto di forza", f["testo"], NOI_COLOR)
        _note(pdf, "È la situazione che porta punti in cui siamo già al livello delle squadre in zona playoff (prime "
                   "4 in classifica), e quella che ci capita più spesso: deve diventare la nostra comfort zone, quella "
                   "su cui appoggiarci sempre.")
    _image(pdf, _situations_chart(I), 52)
    _note(pdf, "Scambi vinti dopo un nostro attacco in ogni situazione: il pallino blu siamo noi quest'anno, il "
               "cerchio vuoto noi l'anno scorso, il rombo grigio le squadre in zona playoff.", size=6.4)

    if f:
        sit = f["situazione"]
        _section_title(pdf, f"Le nostre regole: {SITUATION_WITH[sit].lower()}", keep_with=40)
        _line_courts(pdf, I["dettagli"][sit])
        for line in ("dietro", "davanti"):
            _note(pdf, f"{_cap(LINE_LABELS[line])}: {LINE_NOTES[line]}.", size=6.4)
        pdf.ln(1)
        _rules(pdf, I["dettagli"][sit])
        if f["linea_debole"]:
            _card(pdf, f"Da costruire: {LINE_LABELS[f['linea_debole']]}",
                  "È la parte della nostra situazione migliore in cui perdiamo terreno: qui cerchiamo una soluzione "
                  "in più.", ORANGE, how=f["come"])

    if texts["recuperare"]:
        _section_title(pdf, "Da recuperare", keep_with=30)
        for x in texts["recuperare"]:
            _card(pdf, "Lo sapevamo fare, l'abbiamo perso", x["testo"], ORANGE, how=x["come"])
            _rules(pdf, I["dettagli"][x["situazione"]], limit=4)
    if texts["costruire"]:
        _section_title(pdf, "Da costruire", keep_with=30)
        for x in texts["costruire"]:
            _card(pdf, "Dove perdiamo di più", x["testo"], RED, how=x["come"])
            _rules(pdf, I["dettagli"][x["situazione"]], kinds=("linee", "scelta", "costruire"), limit=4)

    _monitor(pdf, I)
    _players(pdf, I)
    _section_title(pdf, "Come leggere questo documento", keep_with=20)
    _legend(pdf)
    _note(pdf, "Scambi vinti = scambi che vinciamo dopo l'attacco (anche se il punto arriva più avanti nello "
               "scambio): 1 scambio = 1 punto. Squadre in zona playoff: " + ", ".join(I["squadre_playoff"]) + ".",
          size=6.4)
    return bytes(pdf.output())
