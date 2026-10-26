# src/opponent_summary.py
"""
Riassunto dello studio dell'avversario (dashboard, tab "Studio avversario"),
con i criteri indicati dall'utente il 2026-10-25:
- peggior e miglior ricevitore: efficienza di ricezione più bassa/più alta
  (definizione dei KPI della dashboard, ((# + +) - (/ + =)) / ricezioni)
  tra chi ha almeno MIN_RECEPTIONS ricezioni;
- giocatore più servito nei finali di set: più attacchi (esclusi quelli del
  palleggiatore) quando una delle due squadre ha almeno CLUTCH_FROM punti
  (CLUTCH_FROM_TIEBREAK al quinto set) e lo scarto è al più CLUTCH_MAX_DIFF;
- battitore più pericoloso: almeno MIN_SERVES battute, di cui almeno
  DANGEROUS_SERVE_SHARE % #, + o / (ace, ricezione avversaria in
  difficoltà, palla rimandata di là), e tra questi chi fa fare più punti
  alla sua squadra quando batte (scambi vinti sulla sua battuta). La prima
  richiesta dell'utente ("più della metà #, + o /", il più alto per quota)
  è stata rivista sui dati il 2026-10-25: su tutti i .dvw 2025-2026 e
  2026-2027 ci arrivavano 9 battitori su 137 (con almeno 10 battute), e
  proprio loro facevano punto sulla propria battuta MENO della media della
  squadra (rischiano e sbagliano di più). La quota di #, + o / dice poco
  sul set (correlazione 0.23 con gli scambi vinti in battuta; dopo una
  battuta + la squadra vince lo scambio il 50% delle volte, dopo una ! o
  una - il 45%), ace ed errori molto di più: per questo la quota resta un
  filtro (40% = circa il quarto migliore dei battitori) e si sceglie sul
  risultato, gli scambi vinti in battuta;
- punti per causa nei set vinti e nei set persi (media per set), per capire
  per cosa vince e per cosa perde.
"""
import pandas as pd

from src.match_report import POINT_SOURCES, _eff

MIN_RECEPTIONS = 15
MIN_SERVES = 10
DANGEROUS_SERVE_GRADES = ("#", "+", "/")
DANGEROUS_SERVE_SHARE = 40
CLUTCH_FROM, CLUTCH_FROM_TIEBREAK, CLUTCH_MAX_DIFF = 20, 10, 3
# Cause dei punti che descrivono il gioco (per il testo di swing_summary).
SWING_CAUSES = {"Attacco": "attacco", "Muro": "muro", "Ace": "battuta (ace)",
                "Errori avversari": "errori"}


def _player(r):
    return f"{int(r['Numero'])} {r['Cognome']}"


def _receivers(df, min_n):
    ric = df[(df["Tipo"] == "ricezione") & df["Numero"].notna()]
    out = []
    for _, sub in ric.groupby(["Numero", "Cognome"]):
        if len(sub) >= min_n:
            out.append({"giocatore": _player(sub.iloc[0]), "n": len(sub), "eff": _eff(sub, "ricezione"),
                        "perfette_pos": 100 * sub["Voto"].isin(["#", "+"]).mean(),
                        "errori": int((sub["Voto"] == "=").sum())})
    return out


def worst_receiver(df, min_n=MIN_RECEPTIONS):
    """{giocatore, n, eff, perfette_pos (% # e +), errori (=)} o None."""
    out = _receivers(df, min_n)
    return min(out, key=lambda r: r["eff"]) if out else None


def best_receiver(df, min_n=MIN_RECEPTIONS):
    """Come worst_receiver, l'efficienza più alta."""
    out = _receivers(df, min_n)
    return max(out, key=lambda r: r["eff"]) if out else None


def clutch_rows(rows):
    """Attacchi (src.setter_distribution.attack_rows) nei finali di set."""
    if rows.empty:
        return rows
    soglia = rows["set"].astype(int).mod(100).eq(5).map({True: CLUTCH_FROM_TIEBREAK, False: CLUTCH_FROM})
    return rows[(rows[["punti_noi", "punti_loro"]].max(axis=1) >= soglia)
                & (rows["diff_punteggio"].abs() <= CLUTCH_MAX_DIFF)]


def clutch_target(rows):
    """Il più servito nei finali di set: {giocatore, n, quota (% degli
    attacchi nei finali), totale, punti, eff, quota_partita (% su tutti
    gli attacchi)} o None."""
    clutch = clutch_rows(rows)
    if clutch.empty:
        return None
    counts = clutch["attaccante"].value_counts()
    name = counts.index[0]
    sub = clutch[clutch["attaccante"] == name]
    eff = 100 * ((sub["voto"] == "#").sum() - sub["voto"].isin(["=", "/"]).sum()) / len(sub)
    return {"giocatore": name, "n": int(counts.iloc[0]), "quota": 100 * counts.iloc[0] / len(clutch),
            "totale": len(clutch), "punti": int((sub["voto"] == "#").sum()), "eff": eff,
            "quota_partita": 100 * (rows["attaccante"] == name).mean()}


def serve_rows(source):
    """Battute della squadra di una partita (src.match_report.load_report_source)
    con 'vinto' = la squadra ha vinto lo scambio; solo scambi assegnati."""
    winners = source["parsed"]["rallies"].set_index("Rally")["vincitore"]
    bat = source["noi"][(source["noi"]["Tipo"] == "battuta") & source["noi"]["Numero"].notna()]
    bat = bat[bat["Rally"].isin(winners.index)]
    return bat.assign(vinto=bat["Rally"].map(winners) == source["side"])[["Numero", "Cognome", "Voto", "vinto"]]


def dangerous_server(serves, min_n=MIN_SERVES, min_share=DANGEROUS_SERVE_SHARE):
    """
    Il battitore più pericoloso (vedi docstring di modulo) da `serves`
    (serve_rows di una o più partite): {giocatore, n, quota (% #, + o /),
    break (% scambi vinti sulla sua battuta), break_squadra (% della
    squadra), ace, errori} o None.
    """
    out = []
    team_break = 100 * serves["vinto"].mean() if len(serves) else None
    for _, sub in serves.groupby(["Numero", "Cognome"]):
        quota = 100 * sub["Voto"].isin(DANGEROUS_SERVE_GRADES).mean()
        if len(sub) >= min_n and quota >= min_share:
            out.append({"giocatore": _player(sub.iloc[0]), "n": len(sub), "quota": quota,
                        "break": 100 * sub["vinto"].mean(), "break_squadra": team_break,
                        "ace": int((sub["Voto"] == "#").sum()), "errori": int((sub["Voto"] == "=").sum())})
    return max(out, key=lambda r: (r["break"], r["quota"])) if out else None


def set_results(parsed, side):
    """{numero_set: vinto (True/False)} per i set conclusi nello scout."""
    out = {}
    for s in parsed["sets"]:
        if s["completo"]:
            own, opp = (s["punti_casa"], s["punti_ospiti"]) if side == "*" else (s["punti_ospiti"], s["punti_casa"])
            out[s["numero_set"]] = own > opp
    return out


def points_by_set_outcome(matches):
    """
    Punti per causa in media per set, nei set vinti e nei set persi dalla
    squadra. `matches`: [(points, results)] con points = DataFrame di
    src.match_report.point_sources_by_set e results = set_results. Ritorna
    (DataFrame [set ('vinti'/'persi'), squadra ('noi'/'loro'), causa, n
    (media per set)], {'vinti': numero di set, 'persi': numero di set}).
    Solo i set conclusi nello scout.
    """
    parts = []
    for k, (points, results) in enumerate(matches):
        for numero_set, won in results.items():
            sub = points[points["set"] == str(numero_set)]
            parts.append(sub.assign(esito="vinti" if won else "persi", chiave=f"{k}-{numero_set}"))
    n_sets = {"vinti": 0, "persi": 0}
    if not parts:
        return pd.DataFrame(columns=["set", "squadra", "causa", "n"]), n_sets
    allp = pd.concat(parts, ignore_index=True)
    rows = []
    for esito in ("vinti", "persi"):
        sub = allp[allp["esito"] == esito]
        n = sub["chiave"].nunique()
        n_sets[esito] = n
        for squadra in ("noi", "loro"):
            for causa in POINT_SOURCES:
                tot = sub[(sub["squadra"] == squadra) & (sub["causa"] == causa)]["n"].sum()
                rows.append({"set": esito, "squadra": squadra, "causa": causa, "n": tot / n if n else 0.0})
    return pd.DataFrame(rows), n_sets


def swing_summary(by_outcome, n_sets, min_gap=0.3):
    """
    Cosa sposta l'ago della bilancia (output di points_by_set_outcome), per
    le cause di SWING_CAUSES, con il saldo = punti della squadra meno punti
    degli avversari per quella causa, in media per set:
    {'persi': [(causa, saldo)] dove va sotto nei set persi (saldo <=
    -min_gap, dal peggiore), 'vinti': [(causa, saldo)] dove guadagna nei set
    vinti (saldo >= min_gap, dal migliore), 'differenza': [(causa, saldo
    vinti, saldo persi, differenza)] dalla causa che cambia di più tra set
    vinti e persi}. Liste vuote dove mancano set vinti o persi.
    """
    v = by_outcome.pivot_table(index="causa", columns=["set", "squadra"], values="n", aggfunc="sum")
    saldo = {}
    for esito in ("vinti", "persi"):
        if n_sets.get(esito):
            saldo[esito] = {c: v.loc[c, (esito, "noi")] - v.loc[c, (esito, "loro")] for c in SWING_CAUSES if c in v.index}
    out = {"persi": [], "vinti": [], "differenza": []}
    if "persi" in saldo:
        out["persi"] = sorted(((c, x) for c, x in saldo["persi"].items() if x <= -min_gap), key=lambda t: t[1])
    if "vinti" in saldo:
        out["vinti"] = sorted(((c, x) for c, x in saldo["vinti"].items() if x >= min_gap), key=lambda t: -t[1])
    if len(saldo) == 2:
        out["differenza"] = sorted(((c, saldo["vinti"][c], saldo["persi"][c], saldo["vinti"][c] - saldo["persi"][c])
                                    for c in saldo["vinti"]), key=lambda t: -t[3])
    return out


SWING_INTRO = ("saldo per set rispetto agli avversari (punti fatti meno punti concessi per quella causa; per gli "
               "errori: punti regalati dagli avversari meno punti regalati da lei)")


def swing_sentences(by_outcome, n_sets):
    """Le frasi (markdown con **grassetto**) del testo "cosa sposta l'ago
    della bilancia", da swing_summary: per la dashboard e per il PDF."""
    sw = swing_summary(by_outcome, n_sets)

    def items(pairs):
        return ", ".join(f"**{SWING_CAUSES[c]}** ({x:+.1f})" for c, x in pairs)

    lines = []
    if n_sets["persi"]:
        lines.append(f"**Nei set persi** ({n_sets['persi']}) va sotto soprattutto in {items(sw['persi'])}."
                     if sw["persi"] else f"**Nei set persi** ({n_sets['persi']}) non va sotto in modo netto in "
                                         "nessuna voce: perde di poco un po' ovunque.")
    if n_sets["vinti"]:
        lines.append(f"**Nei set vinti** ({n_sets['vinti']}) guadagna soprattutto in {items(sw['vinti'])}."
                     if sw["vinti"] else f"**Nei set vinti** ({n_sets['vinti']}) non guadagna in modo netto in "
                                         "nessuna voce: vince di poco un po' ovunque.")
    top = [t for t in sw["differenza"] if t[3] >= 0.3][:2]
    if top:
        parts = [f"**{SWING_CAUSES[c]}** (da {p:+.1f} nei set persi a {v:+.1f} in quelli vinti, {d:.1f} punti a set "
                 "di differenza)" for c, v, p, d in top]
        lines.append("**Cosa sposta l'ago della bilancia**: " + " e ".join(parts) + ".")
    return lines


def balance_rows(by_outcome, n_sets):
    """Per causa (POINT_SOURCES con almeno un punto): {causa, vinti: (noi,
    loro, saldo) o None, persi: idem}, medie per set."""
    v = by_outcome.pivot_table(index="causa", columns=["set", "squadra"], values="n", aggfunc="sum")
    out = []
    for causa in [c for c in POINT_SOURCES if c in v.index]:
        if all(v.loc[causa].fillna(0) == 0):
            continue
        row = {"causa": causa}
        for esito in ("vinti", "persi"):
            if n_sets[esito]:
                noi, loro = v.loc[causa, (esito, "noi")], v.loc[causa, (esito, "loro")]
                row[esito] = (noi, loro, noi - loro)
            else:
                row[esito] = None
        out.append(row)
    return out


def situation_text(setter, key, d):
    """Frase (markdown) di uno scostamento di test_setter_situations."""
    verso = "più" if d["residuo"] > 0 else "meno"
    where = {"R+#": "da R+ / R#", "R!": "da R!", "FB": "da free ball"}[key]
    return (f"**{setter}** {where}: **{verso}** palloni in **zona {d['zona']}**, "
            f"{d['pct']:.0f}% contro il {d['pct_totale']:.0f}% del resto dei suoi palloni")
