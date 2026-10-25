# src/match_report.py
"""
Dati del report post-partita da mandare ai giocatori (PDF per telefono, vedi
src/match_report_pdf.py), calcolati da un file .dvw: richiede le azioni di
entrambe le squadre e gli scambi con il loro vincitore, che solo il .dvw ha.

Struttura del report (concordata con l'utente il 2026-10-23):
1. la partita (risultato, parziali, data) + commento dell'allenatore;
2. da dove arrivano i punti, nostri e loro;
3. noi e loro per fondamentale, sulla partita e set per set;
4. tabellino giocatori (sul modello dell'app di rilevazione);
5. palleggiatori: attacco alzato da R+/#, da R! e da free ball, con lui in campo;
6. classifiche stagionali aggiornate a questa partita, senza soglie.

Il report usa tutte le azioni del file (anche di giocatori non registrati o
IGNORED in config/player_identities.csv): racconta la partita così com'è
stata giocata. Le classifiche stagionali invece arrivano dalla dashboard, con
gli stessi filtri delle altre classifiche.
"""
import pandas as pd

from src.dvw import decimo_side, parse_dvw, side_actions, team_side
from src.efficiency import compute_set_metrics, eff_from_calcola, error_mask, separate_attack_types
from src.leg_comparison import TIPO_POS_NEG

POINT_SOURCES = ["Attacco", "Muro", "Ace", "Errori avversari", "Punti generici", "Non rilevato"]
SET_METRICS = ["Battuta%", "Ricezione%", "Attacco SO%", "Contrattacco%", "Muro%", "Errori"]
TOTAL_LABEL = "Tot"

_WINNING_SKILL = {"attacco": "Attacco", "muro": "Muro", "battuta": "Ace"}


def _lost_point(df):
    """Azioni che danno il punto all'avversario, come nell'app di rilevazione:
    Voto '=' su qualunque fondamentale (errori, ace subiti, mani-out, difese
    non recuperate), attacco murato ('/') e — a differenza dell'app — anche
    l'errore di muro ('/'), che va sempre contato (indicazione dell'utente)."""
    voto = df["Voto"].astype(str)
    return (voto == "=") | ((df["Tipo"].isin(["attacco", "muro"])) & (voto == "/"))


def load_report_source(file_path, team=None):
    """
    Legge il .dvw della partita per il report. Ritorna un dict con:
    parsed (src.dvw.parse_dvw), noi/loro (azioni di ciascuna squadra, formato
    degli Excel), nomi delle squadre, decimo_casa. "noi" è il Decimo, o la
    squadra di nome `team` (studio dell'avversario, src.dvw.team_side).
    """
    if not str(file_path).lower().endswith(".dvw"):
        raise ValueError("Il report partita richiede il file .dvw (azioni di entrambe le squadre).")
    parsed = parse_dvw(file_path)
    side = decimo_side(parsed) if team is None else team_side(parsed, team)
    other = "a" if side == "*" else "*"
    return {
        "parsed": parsed, "noi": side_actions(parsed, side), "loro": side_actions(parsed, other),
        "decimo_casa": side == "*", "side": side, "other": other,
        "nome_noi": parsed["teams"][side], "nome_loro": parsed["teams"][other],
    }


def classify_rallies(parsed):
    """
    Una causa per ogni punto assegnato, così la somma torna col punteggio
    (l'app di rilevazione invece somma tutti gli errori avversari, contando
    due volte ad es. i mani-out e le difese sbagliate sui nostri attacchi punto):
    1. azione vincente della squadra che fa il punto (attacco/muro/ace '#');
    2. altrimenti un'azione persa dall'altra squadra (vedi _lost_point) →
       "Errori avversari";
    3. altrimenti un punto/errore generico ('$$&') → "Punti generici";
    4. altrimenti (scambio senza azioni rilevate) → "Non rilevato".
    Ritorna il DataFrame degli scambi con le colonne aggiunte 'causa'.
    """
    actions = parsed["actions"]
    rallies = parsed["rallies"].copy()
    causes = []
    for rally, winner in zip(rallies["Rally"], rallies["vincitore"]):
        rows = actions[(actions["Rally"] == rally) & (actions["Tipo"] != "cambio configurazione")]
        won = rows[(rows["squadra"] == winner) & (rows["Voto"] == "#") & rows["Tipo"].isin(_WINNING_SKILL)]
        lost = rows[(rows["squadra"] != winner) & (rows["Tipo"] != "generico") & _lost_point(rows)]
        if not won.empty:
            causes.append(_WINNING_SKILL[won.iloc[-1]["Tipo"]])
        elif not lost.empty:
            causes.append("Errori avversari")
        elif (rows["Tipo"] == "generico").any():
            causes.append("Punti generici")
        else:
            causes.append("Non rilevato")
    rallies["causa"] = causes
    return rallies


def point_sources(source):
    """{'noi': {causa: n}, 'loro': {causa: n}} sugli scambi rilevati."""
    rallies = classify_rallies(source["parsed"])
    out = {}
    for key, side in (("noi", source["side"]), ("loro", source["other"])):
        counts = rallies[rallies["vincitore"] == side]["causa"].value_counts()
        out[key] = {c: int(counts.get(c, 0)) for c in POINT_SOURCES}
    return out


def _metrics_with_total(df):
    per_set = compute_set_metrics(df)
    total = compute_set_metrics(df.assign(**{"Numero Set": TOTAL_LABEL}))
    per_set.index = [str(int(s)) for s in per_set.index]
    return pd.concat([per_set, total])


def team_comparison(source):
    """{'noi': DataFrame, 'loro': DataFrame}: righe = set ('1'..'5') + 'Tot',
    colonne = SET_METRICS (efficienze come nel resto del progetto; contrattacco
    con le free ball; errori con l'errore di muro)."""
    return {"noi": _metrics_with_total(source["noi"]), "loro": _metrics_with_total(source["loro"])}


def _eff(part, tipo):
    """Efficienza (%) con le definizioni dei KPI della dashboard (TIPO_POS_NEG
    di src.leg_comparison); None se il fondamentale non è stato giocato."""
    part = part[part["Tipo"] == tipo]
    if part.empty:
        return None
    pos, neg = TIPO_POS_NEG[tipo]
    return eff_from_calcola(part, tipo, pos, neg)


def player_table(source):
    """
    Tabellino giocatori del Decimo, una riga per giocatore che ha toccato
    palla (ordinati per numero) + 'Generici' + 'Tot', con le STESSE
    definizioni dei KPI della dashboard (richiesta dell'utente, 2026-10-23:
    anche dove non coincidono con il tabellino dell'app di rilevazione) —
    efficienze da TIPO_POS_NEG di src.leg_comparison, errori da error_mask:
    - Punti: pt (attacco/muro/ace '#'), err (= KPI "Errori": battuta,
      attacco, alzata '=' e muro '/'), p-e;
    - Battuta: tot, err, ace, eff; Ricezione: tot, err (ace subiti), eff
      ((# + '+') - ('/' + '=')) / tot;
    - Attacco: tot, err, mur, pt, eff; SO eff e tot (dopo ricezione);
      Contrattacco eff e tot (free ball comprese); di cui free ball eff e tot
      (attacco dopo una difesa con voto '!'); Muro pt, eff.
    Valori None = fondamentale non giocato (mostrato come '.').
    """
    sources = point_sources(source)
    return player_table_from_df(source["noi"], sources["noi"]["Punti generici"], sources["loro"]["Punti generici"])


def player_table_from_df(df, generici_pt, generici_err):
    """Il tabellino di player_table dalle azioni di una squadra (anche più
    partite insieme, vedi combine_matches) e dai punti generici fatti/
    regalati."""
    actions = df[df["Numero"].notna()]
    so_df, fb_df, rest_df = separate_attack_types(df)
    contr_df = pd.concat([fb_df, rest_df])

    eff = _eff

    def row_for(sub, so, contr, fb):
        bat, ric, att, muro = (sub[sub["Tipo"] == t] for t in ("battuta", "ricezione", "attacco", "muro"))
        pt = int(((sub["Voto"] == "#") & sub["Tipo"].isin(_WINNING_SKILL)).sum())
        err = int(error_mask(sub).sum())
        so_att, contr_att, fb_att = (x[x["Tipo"] == "attacco"] for x in (so, contr, fb))
        return {
            "pt": pt, "err": err, "p-e": pt - err,
            "bat_tot": len(bat) or None, "bat_err": int((bat["Voto"] == "=").sum()) if len(bat) else None,
            "bat_ace": int((bat["Voto"] == "#").sum()) if len(bat) else None,
            "bat_eff": eff(bat, "battuta"),
            "ric_tot": len(ric) or None, "ric_err": int((ric["Voto"] == "=").sum()) if len(ric) else None,
            "ric_eff": eff(ric, "ricezione"),
            "att_tot": len(att) or None, "att_err": int((att["Voto"] == "=").sum()) if len(att) else None,
            "att_mur": int((att["Voto"] == "/").sum()) if len(att) else None,
            "att_pt": int((att["Voto"] == "#").sum()) if len(att) else None,
            "att_eff": eff(att, "attacco"),
            "so_eff": eff(so_att, "attacco"), "so_tot": len(so_att) or None,
            "contr_eff": eff(contr_att, "attacco"), "contr_tot": len(contr_att) or None,
            "fb_eff": eff(fb_att, "attacco"), "fb_tot": len(fb_att) or None,
            "muro_pt": int((muro["Voto"] == "#").sum()) if len(muro) else None,
            "muro_eff": eff(muro, "muro"),
        }

    rows = []
    for numero in sorted(actions["Numero"].unique()):
        sub = actions[actions["Numero"] == numero]
        idx = sub.index
        rows.append({
            "giocatore": f"{int(numero)} {sub['Cognome'].iloc[0]}",
            **row_for(sub, so_df[so_df.index.isin(idx)], contr_df[contr_df.index.isin(idx)], fb_df[fb_df.index.isin(idx)]),
        })

    generici = {k: None for k in rows[0] if k != "giocatore"} if rows else {}
    generici.update({"pt": generici_pt, "err": generici_err})
    generici["p-e"] = generici["pt"] - generici["err"]
    rows.append({"giocatore": "Generici", **generici})

    total = row_for(actions, so_df, contr_df, fb_df)
    total["pt"] += generici["pt"]
    total["err"] += generici["err"]
    total["p-e"] = total["pt"] - total["err"]
    rows.append({"giocatore": TOTAL_LABEL, **total})
    return pd.DataFrame(rows)


def point_sources_by_set(source):
    """
    Distribuzione dei punti per causa, set per set e sulla partita (vedi
    classify_rallies): DataFrame [set ('1'..'5', 'Tot'), squadra ('noi'/
    'loro'), causa, n], con tutte le cause (anche a 0) per ogni set e squadra.
    """
    rallies = classify_rallies(source["parsed"])
    rows = []
    sets = [str(int(x)) for x in sorted(rallies["Numero Set"].dropna().unique())]
    for label in sets + [TOTAL_LABEL]:
        sub = rallies if label == TOTAL_LABEL else rallies[rallies["Numero Set"] == int(label)]
        for key, side in (("noi", source["side"]), ("loro", source["other"])):
            counts = sub[sub["vincitore"] == side]["causa"].value_counts()
            rows += [{"set": label, "squadra": key, "causa": c, "n": int(counts.get(c, 0))} for c in POINT_SOURCES]
    return pd.DataFrame(rows)


# KPI per set (squadra e singolo giocatore), con le definizioni dei KPI della
# dashboard: efficienze (%) e conteggi.
SET_KPI_PERCENT = ["Battuta%", "Ricezione%", "Attacco%", "Attacco SO%", "Contrattacco%", "Attacco FB%", "Muro%"]
SET_KPI_COUNTS = ["Punti", "Errori", "p-e"]
SET_KPI_VOLUME = {
    "Battuta%": "Battute", "Ricezione%": "Ricezioni", "Attacco%": "Attacchi",
    "Attacco SO%": "Attacchi SO", "Contrattacco%": "Contrattacchi", "Attacco FB%": "Attacchi FB", "Muro%": "Muri",
}


def set_kpis(team_df):
    """
    KPI per set e sulla partita per la squadra ('Squadra') e per ciascun
    giocatore ('<numero> <cognome>') di `team_df` (azioni di UNA squadra, vedi
    load_report_source). DataFrame tidy [entita, set ('1'..'5', 'Tot'), kpi,
    value]: SET_KPI_PERCENT (None = fondamentale non giocato), SET_KPI_COUNTS
    e i volumi SET_KPI_VOLUME (numero di azioni dietro ogni efficienza).

    La divisione side-out/contrattacco va fatta sulla sequenza di TUTTA la
    squadra (l'attacco segue una ricezione di un compagno): si divide sul
    set intero e poi si assegna ogni attacco al suo giocatore.
    """
    actions = team_df[team_df["Numero"].notna()]
    players = [
        (f"{int(n)} {actions[actions['Numero'] == n]['Cognome'].iloc[0]}", n)
        for n in sorted(actions["Numero"].unique())
    ]
    sets = [str(int(x)) for x in sorted(team_df["Numero Set"].dropna().unique())]
    rows = []
    for label in sets + [TOTAL_LABEL]:
        d = team_df if label == TOTAL_LABEL else team_df[team_df["Numero Set"] == int(label)]
        so, fb, rest = separate_attack_types(d)
        contr = pd.concat([fb, rest])
        for entity, numero in [("Squadra", None)] + players:
            mask = d["Numero"].notna() if numero is None else d["Numero"] == numero
            sub = d[mask]
            sub_so, sub_contr, sub_fb = (x[x.index.isin(sub.index)] for x in (so, contr, fb))
            pt = int(((sub["Voto"] == "#") & sub["Tipo"].isin(_WINNING_SKILL)).sum())
            err = int(error_mask(sub).sum())
            values = {
                "Battuta%": _eff(sub, "battuta"), "Ricezione%": _eff(sub, "ricezione"),
                "Attacco%": _eff(sub, "attacco"), "Attacco SO%": _eff(sub_so, "attacco"),
                "Contrattacco%": _eff(sub_contr, "attacco"), "Attacco FB%": _eff(sub_fb, "attacco"),
                "Muro%": _eff(sub, "muro"),
                "Punti": pt, "Errori": err, "p-e": pt - err,
                "Battute": int((sub["Tipo"] == "battuta").sum()), "Ricezioni": int((sub["Tipo"] == "ricezione").sum()),
                "Attacchi": int((sub["Tipo"] == "attacco").sum()),
                "Attacchi SO": int((sub_so["Tipo"] == "attacco").sum()),
                "Contrattacchi": int((sub_contr["Tipo"] == "attacco").sum()),
                "Attacchi FB": int((sub_fb["Tipo"] == "attacco").sum()),
                "Muri": int((sub["Tipo"] == "muro").sum()),
            }
            rows += [{"entita": entity, "set": label, "kpi": k, "value": v} for k, v in values.items()]
    return pd.DataFrame(rows)


# Esiti di ricezione selezionabili per il primo attacco dopo ricezione (dopo
# una ricezione '=' non c'è attacco: è l'ace subito).
RECEPTION_GRADES = {"#": "perfetta (#)", "+": "positiva (+)", "!": "esclamativa (!)", "-": "negativa (-)",
                    "/": "rigiocata di là (/)"}


def side_out_by_reception(team_df, rec_vote):
    """
    Efficienza del primo attacco dopo una ricezione con voto in `rec_vote`,
    set per set e sulla partita: DataFrame [set ('1'..'5', 'Tot'), eff, n].
    Stessa divisione del KPI "Attacco SO" (separate_attack_types: attacco
    subito dopo la ricezione, al più con un'alzata di mezzo).
    """
    sets = [str(int(x)) for x in sorted(team_df["Numero Set"].dropna().unique())]
    rows = []
    for label in sets + [TOTAL_LABEL]:
        d = team_df if label == TOTAL_LABEL else team_df[team_df["Numero Set"] == int(label)]
        so, _, _ = separate_attack_types(d, rec_vote=tuple(rec_vote))
        att = so[so["Tipo"] == "attacco"]
        rows.append({"set": label, "eff": _eff(att, "attacco"), "n": len(att)})
    return pd.DataFrame(rows)


def setter_table(source):
    """
    {palleggiatore: DataFrame}: righe = set + 'Tot', colonne = efficienza e
    numero degli attacchi della squadra con lui in campo, da ricezione
    positiva ('+' o '#', il '++' dell'Excel), da ricezione '!' e da free ball.
    """
    df = source["noi"]
    setters = sorted(df["Palleggiatore in campo"].dropna().unique())
    sets = [s for s in sorted(df["Numero Set"].dropna().unique())]

    def family_stats(sub):
        so_pos, fb, _ = separate_attack_types(sub, rec_vote=("#", "+"))
        so_escl, _, _ = separate_attack_types(sub, rec_vote=("!",))
        out = {}
        for key, part in (("R+#", so_pos), ("R!", so_escl), ("FB", fb)):
            out[key] = part[part["Tipo"] == "attacco"]
        return out

    per_set = {s: family_stats(df[df["Numero Set"] == s]) for s in sets}
    total = family_stats(df)
    out = {}
    for setter in setters:
        rows = []
        for label, fams in [(str(int(s)), per_set[s]) for s in sets] + [(TOTAL_LABEL, total)]:
            row = {"set": label}
            for key, att in fams.items():
                mine = att[att["Palleggiatore in campo"] == setter]
                row[f"{key} eff"] = _eff(mine, "attacco")
                row[f"{key} n"] = len(mine)
            rows.append(row)
        out[setter] = pd.DataFrame(rows)
    return out


def combine_matches(dfs):
    """
    Azioni di una squadra in più partite (DataFrame di load_report_source)
    in un solo DataFrame, per i totali su più partite (studio
    dell'avversario): set e scambi rinumerati per partita (set 101, 102...
    nella seconda, ecc.), così le funzioni che lavorano per set e per
    sequenza di azioni non legano mai un attacco a una ricezione di
    un'altra partita. Dei risultati per set conta solo la riga 'Tot'.
    """
    parts = []
    for k, df in enumerate(dfs):
        d = df.copy()
        d["Numero Set"] = d["Numero Set"] + 100 * k
        d["Rally"] = d["Rally"] + 100000 * k
        parts.append(d)
    return pd.concat(parts, ignore_index=True)


def match_header(source, official_result):
    """Titolo, risultato e parziali dal punto di vista casa-ospite del file.
    `official_result` (formato di parse_official_results, a = casa) se
    disponibile, altrimenti il riepilogo set del .dvw."""
    parsed = source["parsed"]
    if official_result is not None:
        parziali = list(official_result["parziali"])
        sets_casa, sets_ospite = official_result["sets_a"], official_result["sets_b"]
        completo = True
    else:
        parziali = [(s["punti_casa"], s["punti_ospiti"]) for s in parsed["sets"]]
        sets_casa = sum(a > b for a, b in parziali)
        sets_ospite = sum(b > a for a, b in parziali)
        completo = all(s["completo"] for s in parsed["sets"])
    return {
        "casa": parsed["teams"]["*"], "ospite": parsed["teams"]["a"],
        "sets_casa": sets_casa, "sets_ospite": sets_ospite,
        "parziali": parziali, "data": parsed["data"], "risultato_completo": completo,
        "set_scout": [(s["numero_set"], s["punti_casa"], s["punti_ospiti"], s["completo"]) for s in parsed["sets"]],
    }
