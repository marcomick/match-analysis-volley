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

from src.dvw import decimo_side, load_team_actions
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


def load_report_source(file_path):
    """
    Legge il .dvw della partita per il report. Ritorna un dict con:
    parsed (src.dvw.parse_dvw), noi/loro (azioni di ciascuna squadra, formato
    degli Excel), nomi delle squadre, decimo_casa.
    """
    if not str(file_path).lower().endswith(".dvw"):
        raise ValueError("Il report partita richiede il file .dvw (azioni di entrambe le squadre).")
    noi, decimo_casa, parsed = load_team_actions(file_path, "decimo")
    loro, _, _ = load_team_actions(file_path, "avversario")
    side = decimo_side(parsed)
    other = "a" if side == "*" else "*"
    return {
        "parsed": parsed, "noi": noi, "loro": loro, "decimo_casa": decimo_casa,
        "side": side, "other": other,
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
      Contrattacco eff e tot (free ball comprese); Muro pt, eff.
    Valori None = fondamentale non giocato (mostrato come '.').
    """
    df = source["noi"]
    actions = df[df["Numero"].notna()]
    so_df, fb_df, rest_df = separate_attack_types(df)
    contr_df = pd.concat([fb_df, rest_df])

    eff = _eff

    def row_for(sub, so, contr):
        bat, ric, att, muro = (sub[sub["Tipo"] == t] for t in ("battuta", "ricezione", "attacco", "muro"))
        pt = int(((sub["Voto"] == "#") & sub["Tipo"].isin(_WINNING_SKILL)).sum())
        err = int(error_mask(sub).sum())
        so_att, contr_att = so[so["Tipo"] == "attacco"], contr[contr["Tipo"] == "attacco"]
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
            "muro_pt": int((muro["Voto"] == "#").sum()) if len(muro) else None,
            "muro_eff": eff(muro, "muro"),
        }

    rows = []
    for numero in sorted(actions["Numero"].unique()):
        sub = actions[actions["Numero"] == numero]
        idx = sub.index
        rows.append({
            "giocatore": f"{int(numero)} {sub['Cognome'].iloc[0]}",
            **row_for(sub, so_df[so_df.index.isin(idx)], contr_df[contr_df.index.isin(idx)]),
        })

    sources = point_sources(source)
    generici = {k: None for k in rows[0] if k != "giocatore"} if rows else {}
    generici.update({"pt": sources["noi"]["Punti generici"], "err": sources["loro"]["Punti generici"]})
    generici["p-e"] = generici["pt"] - generici["err"]
    rows.append({"giocatore": "Generici", **generici})

    total = row_for(actions, so_df, contr_df)
    total["pt"] += generici["pt"]
    total["err"] += generici["err"]
    total["p-e"] = total["pt"] - total["err"]
    rows.append({"giocatore": TOTAL_LABEL, **total})
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
