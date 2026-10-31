# src/opponent_report_pdf.py
"""
PDF del report sull'avversario da usare prima della partita (richiesto
dall'utente il 2026-10-25), con i dati della tab "Studio avversario" della
dashboard sulle partite scelte. Stesso formato e stessi elementi del report
post-partita (src/match_report_pdf.py): pagine strette che si leggono sul
telefono e si stampano su A4 adattate alla pagina.

La squadra studiata è in arancio (il colore dell'avversario nel report
post-partita), le squadre che ha affrontato in grigio.

Sezioni: intestazione, note dell'allenatore (facoltative), partite
considerate, in breve (src/opponent_summary.py), cosa sposta l'ago della
bilancia (punti per causa nei set vinti e persi), la squadra e i suoi
avversari per fondamentale, palleggiatori (con le tre griglie della
distribuzione e le tendenze significative), da R+ / R# chi riceve, tabellino
dei giocatori sulle partite considerate.
"""
import matplotlib.pyplot as plt
import numpy as np

from src.match_report_pdf import (
    CONTENT_W,
    HEADER_FILL,
    INK,
    LORO_COLOR,
    MARGIN,
    MM_PER_INCH,
    MUTED,
    NAVY,
    PAGE_H,
    PAGE_W,
    TOTAL_FILL,
    WHITE,
    BOTTOM,
    _chip,
    _diverging_fill,
    _ensure_space,
    _fig_to_png,
    _fmt_pct,
    _grouped_table,
    _image,
    _note,
    _player_tables,
    _ReportPDF,
    _rgb,
    _section_title,
    _setter_courts,
    _t,
    _table_height,
)
from src.opponent_summary import (
    CLUTCH_FROM,
    CLUTCH_FROM_TIEBREAK,
    CLUTCH_MAX_DIFF,
    DANGEROUS_SERVE_SHARE,
    MIN_RECEPTIONS,
    MIN_SERVES,
    SWING_CAUSES,
    SWING_INTRO,
    situation_text,
)
from src.setter_distribution import ALPHA, MIN_PER_CATEGORY, RECEIVER_TYPES, SITUATIONS

WIN_COLOR, LOSS_COLOR = "#0ca30c", "#d03b3b"
WIN_TINT, LOSS_TINT = (206, 237, 206), (246, 210, 210)  # sfondo della cella dei set: vinta / persa
RECEIVER_TITLES = ["Riceve il libero", "Martello davanti", "Martello dietro"]
# Palleggiatori con meno attacchi alzati (R+/#, R! e free ball insieme) fuori
# dalla sezione: giocatori segnati come palleggiatori in una sola partita o
# entrati per pochi scambi, solo nominati in una nota.
MIN_SETTER_ATTACKS = 20
SETTER_COLUMNS = {"R+#": "Da R+ / #", "R!": "Da R!", "FB": "Da free ball"}

LEGEND = (
    "**pt** punti fatti: attacchi, muri e ace punto.  **err** errori: battute, attacchi e alzate sbagliate, errori "
    "di muro (attacchi murati in **mur**, ace subiti negli **err** di ricezione).  **p-e/m** punti fatti meno errori meno attacchi murati.  "
    "**eff** efficienza (come nella dashboard): battuta ((# + + + /) - =); ricezione ((# + +) - (/ + =)); attacco "
    "(# - (= + /)). Muro: muri punto.  **Side-out** attacco subito dopo la ricezione; **contrattacco** tutti "
    "gli altri; **free ball (FB)** attacco dopo una difesa con voto !, una parte del contrattacco."
)


def _header(pdf, data):
    band_h = 38
    pdf.set_fill_color(*_rgb(NAVY))
    pdf.rect(0, 0, PAGE_W, band_h, style="F")
    pdf.set_xy(MARGIN, 5)
    pdf.set_font("helvetica", "B", 7.5)
    pdf.set_text_color(185, 196, 214)
    pdf.cell(CONTENT_W, 4, _t(f"REPORT AVVERSARIO  -  {data['generato']}"), align="C")
    pdf.set_font("helvetica", "B", 15)
    w = pdf.get_string_width(_t(data["squadra"])) + 5
    _chip(pdf, MARGIN + (CONTENT_W - w) / 2, 12.5, data["squadra"], LORO_COLOR, size=15, h=10)
    partite = data["partite"]
    vinte = sum(p["vinta"] for p in partite)
    pdf.set_xy(MARGIN, 26)
    pdf.set_font("helvetica", "", 8.5)
    pdf.set_text_color(*WHITE)
    perse = len(partite) - vinte
    pdf.cell(CONTENT_W, 5, _t(f"{len(partite)} {'partita analizzata' if len(partite) == 1 else 'partite analizzate'}: "
                              f"{vinte} {'vinta' if vinte == 1 else 'vinte'}, {perse} {'persa' if perse == 1 else 'perse'}"),
             align="C")
    pdf.set_text_color(*_rgb(INK))
    pdf.set_xy(MARGIN, band_h + 3)


def _coach_notes(pdf, text):
    if not text or not text.strip():
        return
    _section_title(pdf, "Note")
    pdf.set_font("helvetica", size=9)
    text = _t(text.strip())
    lines = len(pdf.multi_cell(CONTENT_W - 7, 4.6, text, dry_run=True, output="LINES"))
    h = lines * 4.6 + 5
    _ensure_space(pdf, h)
    y = pdf.get_y()
    pdf.set_fill_color(*TOTAL_FILL)
    pdf.rect(MARGIN, y, CONTENT_W, h, style="F", round_corners=True, corner_radius=1.5)
    pdf.set_fill_color(*_rgb(LORO_COLOR))
    pdf.rect(MARGIN, y, 1.4, h, style="F")
    pdf.set_xy(MARGIN + 4, y + 2.5)
    pdf.multi_cell(CONTENT_W - 7, 4.6, text, new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(y + h + 1)


def _matches(pdf, partite):
    _section_title(pdf, "Partite considerate", keep_with=_table_height(len(partite)))
    data = [[p["data"] or "", p["avversario"], p["set"], p["parziali"]] for p in partite]
    raw = [[None] * 4 for _ in partite]
    fills = {(i, 2): WIN_TINT if p["vinta"] else LOSS_TINT for i, p in enumerate(partite)}
    _grouped_table(pdf, None, ["Data", "Avversario", "Set", "Parziali"], data, raw,
                   [17, 33, 10, CONTENT_W - 60], size=6.8, left_cols=(1, 3), bold_cols=(1,), total_row=False,
                   cell_fills=fills)
    if any(p["parziale"] for p in partite):
        _note(pdf, "* Scout parziale: risultato e parziali fin dove arriva la rilevazione.")


def _card(pdf, title, text, caption=None):
    """Una voce del riassunto: titolo in grassetto, testo (markdown), nota."""
    pdf.set_font("helvetica", size=8.4)
    lines = len(pdf.multi_cell(CONTENT_W - 6, 4.1, _t(text), dry_run=True, output="LINES", markdown=True))
    pdf.set_font("helvetica", size=6.6)
    cap_lines = len(pdf.multi_cell(CONTENT_W - 6, 3.3, _t(caption), dry_run=True, output="LINES")) if caption else 0
    h = 5.5 + lines * 4.1 + cap_lines * 3.3 + 2.5
    _ensure_space(pdf, h + 1.5)
    y = pdf.get_y()
    pdf.set_fill_color(*HEADER_FILL)
    pdf.rect(MARGIN, y, CONTENT_W, h, style="F", round_corners=True, corner_radius=1.5)
    pdf.set_fill_color(*_rgb(LORO_COLOR))
    pdf.rect(MARGIN, y, 1.2, h, style="F")
    pdf.set_xy(MARGIN + 3.5, y + 1.3)
    pdf.set_font("helvetica", "B", 8.6)
    pdf.set_text_color(*_rgb(NAVY))
    pdf.cell(0, 4.2, _t(title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_rgb(INK))
    pdf.set_x(MARGIN + 3.5)
    pdf.set_font("helvetica", size=8.4)
    pdf.multi_cell(CONTENT_W - 6, 4.1, _t(text), new_x="LMARGIN", new_y="NEXT", markdown=True)
    if caption:
        pdf.set_x(MARGIN + 3.5)
        pdf.set_font("helvetica", size=6.6)
        pdf.set_text_color(*_rgb(MUTED))
        pdf.multi_cell(CONTENT_W - 6, 3.3, _t(caption), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*_rgb(INK))
    pdf.set_y(y + h + 1.5)


def _receiver_text(rec):
    return (f"**{rec['giocatore']}**: efficienza {rec['eff']:.0f}% su {rec['n']} ricezioni "
            f"({rec['perfette_pos']:.0f}% perfette o positive, {rec['errori']} ace subiti)")


def _summary(pdf, r):
    _section_title(pdf, "In breve", keep_with=20)
    none_rec = f"Nessuno con almeno {MIN_RECEPTIONS} ricezioni."
    _card(pdf, "Peggior ricevitore", _receiver_text(r["peggior"]) if r["peggior"] else none_rec)
    _card(pdf, "Miglior ricevitore", _receiver_text(r["miglior"]) if r["miglior"] and r["miglior"] != r["peggior"]
          else none_rec)
    srv = r["battitore"]
    errori = srv and f"{srv['errori']} {'errore' if srv['errori'] == 1 else 'errori'}"
    _card(pdf, "Battitore più pericoloso",
          f"**{srv['giocatore']}**: la squadra vince il {srv['break']:.0f}% degli scambi sulla sua battuta (media "
          f"della squadra {srv['break_squadra']:.0f}%), {srv['quota']:.0f}% di battute #, + o / su {srv['n']} "
          f"({srv['ace']} ace, {errori})" if srv
          else f"Nessuno con almeno {MIN_SERVES} battute e almeno il {DANGEROUS_SERVE_SHARE}% #, + o /.",
          f"Tra chi ha almeno {MIN_SERVES} battute e almeno il {DANGEROUS_SERVE_SHARE}% #, + o /, quello con cui la "
          "squadra fa più punti in battuta.")
    c = r["finali"]
    _card(pdf, "Più servito nei finali di set",
          f"**{c['giocatore']}**: {c['n']} attacchi su {c['totale']} ({c['quota']:.0f}%, contro il "
          f"{c['quota_partita']:.0f}% sul totale degli attacchi della squadra), {c['punti']} punti, efficienza "
          f"{c['eff']:.0f}%" if c else "Nessun attacco nei finali di set.",
          f"Da {CLUTCH_FROM} punti di una delle due squadre ({CLUTCH_FROM_TIEBREAK} al quinto set), con scarto di "
          f"al più {CLUTCH_MAX_DIFF} punti.")
    sig = r["palleggiatore"]
    _card(pdf, "Tendenze del palleggiatore",
          "\n".join(f"- {situation_text(s, k, d)}" for s, k, d in sig) if sig
          else "Nessuna tendenza significativa da R+ / R#, da R! o da free ball.")


def _balance_chart(rows, n_sets):
    """Saldo per causa (squadra - avversari, media per set): set vinti in
    verde, set persi in rosso, barre affiancate con il valore."""
    rows = [r for r in rows if r["causa"] in SWING_CAUSES]
    cats = [r["causa"] for r in rows]
    y = np.arange(len(cats)) * 1.3
    h = 0.5
    height = 0.5 * len(cats) + 0.35
    fig, ax = plt.subplots(figsize=(CONTENT_W / MM_PER_INCH, height))
    vals_all = [r[e][2] for r in rows for e in ("vinti", "persi") if r[e]]
    vmax = max([abs(v) for v in vals_all] + [1])
    for offset, esito, color in ((-h / 2 - 0.03, "vinti", WIN_COLOR), (h / 2 + 0.03, "persi", LOSS_COLOR)):
        if not n_sets[esito]:
            continue
        vals = [r[esito][2] for r in rows]
        ax.barh(y + offset, vals, h, color=color)
        for yy, v in zip(y + offset, vals):
            ax.text(v + (vmax * 0.03 if v >= 0 else -vmax * 0.03), yy, f"{v:+.1f}", va="center",
                    ha="left" if v >= 0 else "right", fontsize=7.5, color=INK, fontweight="bold")
    ax.axvline(0, color="#9aa1aa", linewidth=0.9)
    ax.set_yticks(y, [SWING_CAUSES[c].capitalize() for c in cats])
    ax.invert_yaxis()
    ax.set_xlim(-vmax * 1.35, vmax * 1.35)
    ax.xaxis.set_visible(False)
    ax.spines["bottom"].set_visible(False)
    ax.tick_params(axis="y", length=0, labelsize=8, labelcolor=INK, pad=4)
    for label in ax.get_yticklabels():
        label.set_fontweight("bold")
    return _fig_to_png(fig), height


def _set_legend(pdf, n_sets):
    y = pdf.get_y()
    x = MARGIN
    for esito, color in (("vinti", WIN_COLOR), ("persi", LOSS_COLOR)):
        if n_sets[esito]:
            x += _chip(pdf, x, y, f"Set {esito} ({n_sets[esito]})", color, size=7.5, h=4.8) + 2.5
    pdf.set_xy(MARGIN, y + 6.5)


def _balance(pdf, b, squadra):
    if not (b["n_sets"]["vinti"] or b["n_sets"]["persi"]):
        return
    _section_title(pdf, "Cosa sposta l'ago della bilancia", keep_with=40)
    _note(pdf, f"{squadra}, {SWING_INTRO}; in media per set, solo i set conclusi nello scout.")
    pdf.ln(0.5)
    pdf.set_font("helvetica", size=8.4)
    for line in b["frasi"]:
        _ensure_space(pdf, 9)
        pdf.set_x(MARGIN)
        pdf.multi_cell(CONTENT_W, 4.1, _t(f"- {line}"), new_x="LMARGIN", new_y="NEXT", markdown=True)
        pdf.ln(0.6)
    png, h_in = _balance_chart(b["righe"], b["n_sets"])
    _ensure_space(pdf, h_in * MM_PER_INCH + 10)
    pdf.ln(1)
    _note(pdf, "Saldo per causa (punti fatti meno punti concessi), in media per set:")
    _set_legend(pdf, b["n_sets"])
    _image(pdf, png, h_in * MM_PER_INCH)

    # tabella delle medie
    esiti = [e for e in ("vinti", "persi") if b["n_sets"][e]]
    data, raw = [], []
    for r in b["righe"]:
        cells, raws = [r["causa"]], [None]
        for e in esiti:
            noi, loro, saldo = r[e]
            cells += [f"{noi:.1f}", f"{loro:.1f}", f"{saldo:+.1f}"]
            raws += [None, None, saldo]
        data.append(cells)
        raw.append(raws)
    groups = [("", 1)] + [(f"Set {e}", 3) for e in esiti]
    header = [""] + ["sq.", "avv.", "saldo"] * len(esiti)
    w = 11
    fills = {1 + 3 * k + 2: (lambda v: _diverging_fill(v, scale=4)) for k in range(len(esiti))}
    _grouped_table(pdf, groups, header, data, raw, [CONTENT_W - w * 3 * len(esiti)] + [w] * 3 * len(esiti),
                   fills=fills, total_row=False)
    _note(pdf, "sq. = punti della squadra, avv. = punti dei suoi avversari per quella causa. \"Errori avversari\" "
               "= punti regalati dall'altra squadra.")


def _kpis(pdf, rows, squadra):
    _section_title(pdf, f"{squadra} e i suoi avversari", keep_with=_table_height(len(rows)))
    data = [[label, a, b] for label, a, b, _ in rows]
    raw = [[None, va, vb] for _, _, _, (va, vb) in rows]
    _grouped_table(pdf, None, ["", squadra, "Avversari affrontati"], data, raw, [CONTENT_W - 64, 32, 32],
                   fills={1: _diverging_fill, 2: _diverging_fill}, total_row=False)
    _note(pdf, "Sulle partite considerate, con le definizioni dei KPI della dashboard; side-out, contrattacco e free "
               "ball come nel tabellino.")


def _setters(pdf, setters, distributions, tests):
    def volume(name):
        _, total = setters[name][-1]
        return sum(int(total[f"{k} n"]) for k in SETTER_COLUMNS)

    minor = [n for n in setters if volume(n) < MIN_SETTER_ATTACKS]
    setters = {n: rows for n, rows in setters.items() if n not in minor}
    if not setters:
        return
    _section_title(pdf, "Palleggiatori", keep_with=50)
    _note(pdf, "Efficienza d'attacco della squadra **con lui in campo** (tra parentesi il numero di attacchi), poi la "
               "**distribuzione**: da quale zona attaccano i giocatori serviti (% delle alzate, attacchi ed "
               "efficienza; con la coppa il miglior p-e/m della zona, se maggiore di 2), escluse le sue seconde intenzioni. Centrali sempre da 3, schiacciatori da "
               "dietro da 8 (pipe), opposto da dietro da 9.", markdown=True)
    pdf.ln(1)
    keys = list(SETTER_COLUMNS)
    for name, rows in setters.items():
        _ensure_space(pdf, 6 + _table_height(len(rows)))
        pdf.set_font("helvetica", "B", 9)
        pdf.set_text_color(*_rgb(NAVY))
        pdf.cell(0, 5.5, _t(name), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*_rgb(INK))
        data, raw = [], []
        for label, r in rows:
            data.append([label] + [f"{_fmt_pct(r[f'{k} eff'])} ({r[f'{k} n']})" if r[f"{k} n"] else "." for k in keys])
            raw.append([None] + [r[f"{k} eff"] for k in keys])
        w = 21
        _grouped_table(pdf, None, [""] + list(SETTER_COLUMNS.values()), data, raw, [CONTENT_W - 3 * w, w, w, w],
                       size=6.8, fills={j: _diverging_fill for j in (1, 2, 3)})
        dist = distributions.get(name)
        if dist and any(dist[k]["n"] for k in SITUATIONS):
            _setter_courts(pdf, dist)
        lines = []
        for k in SITUATIONS:
            res = tests.get((name, k))
            if res and res["significativo"] and res["scostamenti"]:
                lines.append(situation_text(name, k, res["scostamenti"][0]))
        pdf.ln(1)
        pdf.set_font("helvetica", size=7.8)
        text = ("Tendenze significative: " + "; ".join(lines) + ".") if lines else \
            "Nessuna tendenza significativa da R+ / R#, da R! o da free ball rispetto al resto dei suoi palloni."
        _ensure_space(pdf, 8)
        pdf.multi_cell(CONTENT_W, 3.8, _t(text), new_x="LMARGIN", new_y="NEXT", markdown=True)
        pdf.ln(2)
    if minor:
        _note(pdf, f"Non mostrati, con meno di {MIN_SETTER_ATTACKS} attacchi alzati in tutto: {', '.join(minor)}.")
    if any(distributions[n]["stimate"] for n in setters if n in distributions):
        _note(pdf, "In alcune partite le direzioni d'attacco non sono rilevate: zone di prima linea di schiacciatori "
                   "e opposti stimate dal ruolo (schiacciatore 4, opposto 2).")


def _receivers(pdf, res):
    if not any(d["n"] for d in res["per_tipo"].values()):
        return
    _section_title(pdf, "Da R+ / R#: chi riceve", keep_with=50)
    _note(pdf, "Primo attacco dopo una ricezione + o #, a seconda di chi ha ricevuto: libero, martello in prima "
               "linea, martello in seconda linea (opposti e centrali in ricezione esclusi).")
    _setter_courts(pdf, res["per_tipo"], titles=dict(zip(RECEIVER_TYPES, RECEIVER_TITLES)))
    pdf.ln(1)
    test = res["test"]
    if test is None:
        text = "Dati insufficienti per dire se la distribuzione cambia a seconda di chi riceve."
    elif test["p"] < ALPHA:
        devs = "; ".join(f"quando riceve il {str(d['categoria']).lower()} **{'più' if d['residuo'] > 0 else 'meno'}** "
                         f"palloni in zona {d['zona']} ({d['pct']:.0f}% contro il {d['pct_totale']:.0f}% sul totale)"
                         for d in test["scostamenti"][:3])
        text = f"**La distribuzione cambia a seconda di chi riceve** (p = {test['p']:.3f}): {devs}."
    else:
        text = f"**La distribuzione non cambia in modo significativo a seconda di chi riceve** (p = {test['p']:.3f})."
    front = res["martello_davanti"]
    if front:
        (a_self, n_self), (a_other, n_other) = front["riceve_lui"], front["riceve_altro"]
        verdict = "differenza significativa" if front["p"] < ALPHA else "differenza compatibile con il caso"
        text += (f" Il **martello di prima linea** attacca il {100 * a_self / n_self:.0f}% dei palloni quando riceve "
                 f"lui ({a_self} su {n_self}) e il {100 * a_other / n_other:.0f}% quando riceve un altro ({a_other} "
                 f"su {n_other}): {verdict} (p = {front['p']:.3f}).")
    else:
        text += (f" Martello di prima linea: dati insufficienti (servono almeno {MIN_PER_CATEGORY} attacchi sia "
                 "quando riceve lui sia quando riceve un altro).")
    pdf.set_font("helvetica", size=7.8)
    _ensure_space(pdf, 12)
    pdf.multi_cell(CONTENT_W, 3.8, _t(text), new_x="LMARGIN", new_y="NEXT", markdown=True)


def build_opponent_report_pdf(data):
    """
    Ritorna il PDF (bytes). `data` (assemblato dalla dashboard):
    squadra, generato (data), note, partite [{data, avversario, set,
    parziali, vinta, parziale}], riassunto {peggior, miglior, battitore,
    finali, palleggiatore [(palleggiatore, situazione, scostamento)]},
    bilancia {frasi, righe (balance_rows), n_sets}, kpi [(etichetta, testo
    squadra, testo avversari, (valore squadra, valore avversari))],
    palleggiatori {nome: [(etichetta riga, riga di setter_table)]},
    distribuzioni (distributions_by_setter), test_palleggiatori
    (test_setter_situations), ricevitori (positive_reception_by_receiver),
    giocatori (player_table_from_df).
    """
    pdf = _ReportPDF(format=(PAGE_W, PAGE_H))
    pdf.set_margins(MARGIN, MARGIN, MARGIN)
    pdf.set_auto_page_break(True, margin=BOTTOM)
    pdf.add_page()
    _header(pdf, data)
    _coach_notes(pdf, data.get("note"))
    _summary(pdf, data["riassunto"])
    _balance(pdf, data["bilancia"], data["squadra"])
    _setters(pdf, data["palleggiatori"], data["distribuzioni"], data["test_palleggiatori"])
    _receivers(pdf, data["ricevitori"])
    _kpis(pdf, data["kpi"], data["squadra"])
    _matches(pdf, data["partite"])
    _section_title(pdf, "Giocatori", keep_with=_table_height(min(len(data["giocatori"]), 8), 2))
    _note(pdf, "Sulle partite considerate.")
    _player_tables(pdf, data["giocatori"], legend=LEGEND)
    return bytes(pdf.output())
