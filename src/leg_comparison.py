# src/leg_comparison.py
"""
Confronto KPI andata vs ritorno (ed eventuale playout) tra le partite della
stagione, sia a livello squadra che per giocatore.

Riusa calcola_efficienza/find_errors già estratte in src/efficiency.py.
Non tocca notebooks/tabellino.ipynb né la sua sezione "altro" (fuori scope).

Concetti chiave:
- Ogni partita viene caricata singolarmente (non concatenata come in
  classifiche.ipynb), taggata con:
  - `leg`: 'A' andata, 'R' ritorno, 'POA'/'POR' playout (sempre e solo JVC);
  - `giornata`: posizione dell'avversario nell'ordine del girone di andata
    (0-based) — usata per allineare andata/ritorno dello stesso avversario;
  - `x_label`: etichetta per l'asse X del grafico "per avversario" (grafico 1):
    il nome avversario per A/R, `PLAYOUT_LABEL` per POA/POR — il playout ha
    una posizione propria in coda (dopo l'ultimo avversario del girone), non
    è più raggruppato con la partita di andata/ritorno contro JVC;
  - `match_seq`: indice cronologico assoluto 0..27 su tutta la stagione,
    usato dal grafico "confronto giocatori" (grafico 2): andata (0..12),
    ritorno (13..25), POA (26 = 27ª partita), POR (27 = 28ª partita).
- Il filtro giocatori riusa config/player_identities.csv (stessa logica della
  cella FILTRO di classifiche.ipynb): esclude terne non riconosciute o marcate
  IGNORED. Non usa player_id per canonicalizzare il nome mostrato — identico
  al comportamento di rankings.py, l'identificativo resta "Cognome Numero".
- Ogni file Excel dichiara nella riga 0 (saltata da skiprows=1 nel resto del
  caricamento) chi è "Locali" e chi "Ospiti" per quella specifica partita
  (es. "Locali: decimo" o "Ospiti: Decimo D") — è l'unica fonte affidabile per
  sapere se le colonne 'Punti Locali'/'Punti Ospiti' del resto del file si
  riferiscono al Decimo o all'avversario: il nome della cartella/file NON è
  attendibile (verificato: discorda dalla riga 0 in alcuni casi, es. andata
  vs Roman). Vedi `_parse_decimo_locali`.
"""
import re

import pandas as pd

from config.paths import ROOT, build_base_path, load_matches
from src.efficiency import (
    calcola_efficienza,
    find_errors,
    separate_attack_types,
    eff_scalar,
    SRV_POS,
    SRV_NEG,
)

TEAM = "Decimo"
IDENTITIES_CSV = ROOT / "config" / "player_identities.csv"

# Etichetta per la posizione "playout" nel grafico per avversario (grafico 1):
# una 14ª posizione a sé stante, dopo l'ultimo avversario del girone di andata.
PLAYOUT_LABEL = "JVC (PO)"

# Per ciascun fondamentale, i voti positivi/negativi usati per il KPI di efficienza
# percentuale (stile tabellino) — anche fonte dei conteggi voto grezzi riusati dai
# KPI assoluti qui sotto (calcola_efficienza(total_efficiency=False) espone sempre
# tutte le colonne voto occorse, non solo quelle in pos/neg).
TIPO_POS_NEG = {
    "battuta": (SRV_POS, SRV_NEG),
    "ricezione": (["#", "+"], ["=", "/"]),
    "attacco": (["#"], ["=", "/"]),
    "muro": (["#", "+"], ["=", "/"]),
}
EFF_KPI_LABELS = {"battuta": "Battuta%", "ricezione": "Ricezione%", "attacco": "Attacco%", "muro": "Muro%"}

# KPI "Tot": numero totale di azioni di quel fondamentale (qualunque voto),
# cioè la colonna 'Tot' già calcolata da calcola_efficienza.
TOT_KPI_LABELS = {"battuta": "Battuta Tot", "ricezione": "Ricezione Tot", "attacco": "Attacco Tot", "muro": "Muro Tot"}

# KPI assoluti (conteggi per voto specifico): label -> (tipo, voto). Descrizione
# volley tra parentesi nel label stesso per leggibilità in dashboard.
ABS_KPI_DEFS = {
    "Attacco # (punti)": ("attacco", "#"),
    "Muro # (punti)": ("muro", "#"),
    "Battuta # (ace)": ("battuta", "#"),
    "Ricezione = (ace subiti)": ("ricezione", "="),
    "Battuta = (errori)": ("battuta", "="),
    "Attacco = (errori)": ("attacco", "="),
    "Attacco / (murati)": ("attacco", "/"),
    "Muro = (mani-fuori subite)": ("muro", "="),
}

ERRORI_KPI = "Errori"

# KPI "Attacco SO" (side-out: attacco immediatamente successivo a una ricezione,
# al più con un'alzata di mezzo), "Attacco FB" (free ball: attacco immediatamente
# successivo a una difesa Voto '!', palla facile rimandata nel nostro campo — vedi
# separate_free_ball) e "Contrattacco" (tutti gli altri attacchi) — vedi
# separate_attack_types, che li produce come tre gruppi disgiunti ed esaustivi
# (Attacco SO Tot + Attacco FB Tot + Contrattacco Tot == Attacco Tot, sempre).
# Il voto di ricezione che qualifica l'attacco come "SO" è parametrico
# (`rec_vote`), non fisso: la dashboard lo espone come filtro, quindi questi KPI
# non fanno parte di ALL_KPIS/build_comparison_dataset (calcolati una volta per
# stagione), ma di build_attacco_so_dataset (ricalcolato quando il filtro
# cambia). Attacco FB e Contrattacco non dipendono da `rec_vote` di per sé, ma
# sono complementari ad Attacco SO rispetto allo stesso split, quindi cambiano
# comunque insieme ad esso (i confini SO/FB/Contrattacco si spostano insieme).
#
# Nota storica: prima del 2026-08-12 "Contrattacco" includeva anche gli attacchi
# dopo free ball (separate_attacks_counterattacks, due vie). Corretto perché una
# free ball è una situazione favorevole distinta da un vero contrattacco/rimessa
# in gioco confusa — l'efficienza attesa è diversa, va tenuta separata.
ATTACCO_SO_KPI_LABELS = {
    "eff": "Attacco SO%",
    "tot": "Attacco SO Tot",
    "punti": "Attacco SO # (punti)",
    "errori": "Attacco SO = (errori)",
    "murati": "Attacco SO / (murati)",
}
ATTACCO_FB_KPI_LABELS = {
    "eff": "Attacco FB%",
    "tot": "Attacco FB Tot",
    "punti": "Attacco FB # (punti)",
    "errori": "Attacco FB = (errori)",
    "murati": "Attacco FB / (murati)",
}
CONTRATTACCO_KPI_LABELS = {
    "eff": "Contrattacco%",
    "tot": "Contrattacco Tot",
    "punti": "Contrattacco # (punti)",
    "errori": "Contrattacco = (errori)",
    "murati": "Contrattacco / (murati)",
}
DEFAULT_REC_VOTE = ("#", "+", "!", "-")

# Soglia minima sul numero di attacchi (vedi apply_min_attacks_threshold): per
# ciascun KPI della famiglia "attacco", il "Tot" della stessa famiglia da
# confrontare con la soglia, per lo stesso giocatore/partita.
ATTACK_FAMILY_TOT_KPI = {}
for _kpi in ("Attacco%", "Attacco Tot", "Attacco # (punti)", "Attacco = (errori)", "Attacco / (murati)"):
    ATTACK_FAMILY_TOT_KPI[_kpi] = "Attacco Tot"
for _key, _label in ATTACCO_SO_KPI_LABELS.items():
    ATTACK_FAMILY_TOT_KPI[_label] = ATTACCO_SO_KPI_LABELS["tot"]
for _key, _label in ATTACCO_FB_KPI_LABELS.items():
    ATTACK_FAMILY_TOT_KPI[_label] = ATTACCO_FB_KPI_LABELS["tot"]
for _key, _label in CONTRATTACCO_KPI_LABELS.items():
    ATTACK_FAMILY_TOT_KPI[_label] = CONTRATTACCO_KPI_LABELS["tot"]
del _kpi, _key, _label

PERCENT_KPIS = set(EFF_KPI_LABELS.values()) | {
    ATTACCO_SO_KPI_LABELS["eff"], ATTACCO_FB_KPI_LABELS["eff"], CONTRATTACCO_KPI_LABELS["eff"],
}
ALL_KPIS = list(EFF_KPI_LABELS.values()) + list(TOT_KPI_LABELS.values()) + list(ABS_KPI_DEFS) + [ERRORI_KPI]
ERROR_COLS = ["battuta", "attacco", "muro", "alzata"]


OFFICIAL_RESULTS_FILENAME = "risultati_decimo_{season}.txt"


# Intestazioni di blocco per i 2 playout, oltre a "Giornata N" (regular season)
# — mappate sulle stesse posizioni 27/28 usate da match_seq + 1 (POA -> giornata
# 27, match_seq 26; POR -> giornata 28, match_seq 27).
_PLAYOUT_BLOCK_GIORNATA = {"Playout Andata": 27, "Playout Ritorno": 28}


def _parse_set_score(token):
    """
    'pa/pb' -> (pa, pb). Tollera prefissi non numerici sul token (es.
    '#11/15', visto nel file risultati per il playout ritorno 2025-2026 —
    probabile nota procedurale della federazione, non chiara nel significato
    ma non altera il punteggio) estraendo solo le cifre, invece di fallire
    silenziosamente o di lasciare un ValueError poco leggibile su int('#11').
    """
    nums = re.findall(r"\d+", token)
    if len(nums) != 2:
        raise ValueError(f"Punteggio set non riconosciuto: {token!r}")
    return (int(nums[0]), int(nums[1]))


def parse_official_results(file_path):
    """
    Parsa il file testuale ufficiale dei risultati (a blocchi "Giornata N"
    per il campionato regolare, "Playout Andata"/"Playout Ritorno" per i 2
    playout — righe tab-separated N./Data/Località/Squadra A/Squadra B/
    RIS/PARZIALI, esportato dal sito federale/aggiunto a mano per il playout).
    Fonte autorevole per i punteggi finali dei set: i dati Excel di match
    analysis non riportano mai esplicitamente il punteggio finale di un set
    (vedi compute_set_outcomes).

    Ritorna un dict {giornata: {squadra_a, squadra_b, sets_a, sets_b, parziali}},
    con `parziali` = lista di tuple (punti_a, punti_b) una per set. `giornata`
    per i playout è 27/28 (vedi _PLAYOUT_BLOCK_GIORNATA), corrispondente a
    match_seq + 1 come per il campionato regolare.
    """
    results = {}
    giornata = None
    with open(file_path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            stripped = line.strip()
            m = re.match(r"Giornata\s+(\d+)", stripped)
            if m:
                giornata = int(m.group(1))
                continue
            if stripped in _PLAYOUT_BLOCK_GIORNATA:
                giornata = _PLAYOUT_BLOCK_GIORNATA[stripped]
                continue
            parts = line.split("\t")
            if len(parts) < 7 or parts[0].strip() in ("", "N."):
                continue
            squadra_a, squadra_b, ris, parziali_str = parts[3], parts[4], parts[5], parts[6]
            sets_a, sets_b = (int(x) for x in ris.split("-"))
            parziali = [_parse_set_score(p) for p in parziali_str.split()]
            results[giornata] = {
                "squadra_a": squadra_a.strip(), "squadra_b": squadra_b.strip(),
                "sets_a": sets_a, "sets_b": sets_b, "parziali": parziali,
            }
    return results


def _load_official_results(season):
    """
    Carica i risultati ufficiali per la stagione, se il file esiste (vedi
    OFFICIAL_RESULTS_FILENAME) — copre il campionato regolare (andata +
    ritorno, blocchi "Giornata N") e, se presenti nel file, i 2 playout
    (blocchi "Playout Andata"/"Playout Ritorno", aggiunti a mano dal sito
    federale non avendo un export automatico dedicato). Ritorna {} se il
    file non è presente: le partite coperte usano i punteggi esatti, le
    altre (stagioni senza questo file, o senza i blocchi playout) ricadono
    sulla ricostruzione approssimata da compute_set_outcomes.
    """
    path = build_base_path(season=season) / OFFICIAL_RESULTS_FILENAME.format(season=season)
    if not path.exists():
        return {}
    try:
        return parse_official_results(str(path))
    except Exception as e:
        print(f"Errore leggendo i risultati ufficiali {path}: {e}")
        return {}


def _decimo_side(official_result):
    """'a' o 'b': quale squadra è il Decimo in questo risultato ufficiale."""
    if TEAM.lower() in official_result["squadra_a"].lower():
        return "a"
    if TEAM.lower() in official_result["squadra_b"].lower():
        return "b"
    raise ValueError(f"'{TEAM}' non trovato nel risultato ufficiale: {official_result}")


def compute_set_outcomes_from_official(official_result):
    """Come compute_set_outcomes, ma da un risultato ufficiale (punteggi finali esatti, sempre completi)."""
    side = _decimo_side(official_result)
    rows = []
    for i, (pa, pb) in enumerate(official_result["parziali"], start=1):
        punti_decimo, punti_avversario = (pa, pb) if side == "a" else (pb, pa)
        rows.append({
            "numero_set": i,
            "punti_decimo": punti_decimo,
            "punti_avversario": punti_avversario,
            "punti_diff": punti_decimo - punti_avversario,
            "set_completo": True,
            "set_vinto": punti_decimo > punti_avversario,
        })
    return pd.DataFrame(rows)


def compute_match_outcome_from_official(official_result):
    """Come compute_match_outcome, ma da un risultato ufficiale (esatto, nessuna approssimazione)."""
    side = _decimo_side(official_result)
    set_vinti, set_persi = (
        (official_result["sets_a"], official_result["sets_b"]) if side == "a"
        else (official_result["sets_b"], official_result["sets_a"])
    )
    sets = compute_set_outcomes_from_official(official_result)
    return {
        "set_vinti": set_vinti,
        "set_persi": set_persi,
        "partita_vinta": set_vinti > set_persi,
        "punti_diff_totale": int(sets["punti_diff"].sum()),
        "n_set_totali": len(sets),
        "n_set_completi": len(sets),
    }


def _parse_decimo_locali(file_path):
    """
    Legge la riga 0 del file Excel (dichiarazione "Locali: <squadra>" /
    "Ospiti: <squadra>", saltata da skiprows=1 nel resto del caricamento) e
    determina se il Decimo è "Locali" o "Ospiti" in questa specifica partita.
    Ritorna True se Decimo è Locali, False se è Ospiti.

    Solleva ValueError se l'etichetta non è nel formato atteso o non menziona
    il Decimo — meglio fallire in modo rumoroso che dedurre l'informazione da
    fonti meno affidabili (es. il nome della cartella/file, che in alcuni casi
    reali diverge da questa dichiarazione).
    """
    label_row = pd.read_excel(file_path, header=None, nrows=1)
    label = str(label_row.iloc[0, 0])
    match = re.match(r"\s*(Locali|Ospiti)\s*:\s*(.+)", label, re.IGNORECASE)
    if not match:
        raise ValueError(f"Etichetta Locali/Ospiti non trovata in riga 0 di {file_path}: {label!r}")
    ruolo, squadra = match.group(1).lower(), match.group(2)
    if TEAM.lower() not in squadra.lower():
        raise ValueError(f"L'etichetta '{label}' in {file_path} non menziona '{TEAM}'")
    return ruolo == "locali"


def _load_known_identity_keys():
    """Terne (team, cognome, numero) riconosciute e non marcate IGNORED."""
    identities = pd.read_csv(IDENTITIES_CSV)
    known = identities[identities["player_id"] != "IGNORED"]
    return set(zip(known["team"], known["cognome"], known["numero"].astype(str)))


def _normalize_and_filter_players(df, known_keys):
    """Normalizza Cognome, crea 'Giocatore' ed esclude le terne non riconosciute/IGNORED."""
    df = df.copy()
    df["Cognome"] = df["Cognome"].astype(str).apply(lambda x: x.replace("’", "'").strip().capitalize())
    df["Giocatore"] = df["Cognome"] + " " + df["Numero"].astype(str)
    mask = df.apply(lambda r: (TEAM, r["Cognome"], str(int(r["Numero"]))) in known_keys, axis=1)
    return df[mask]


def get_opponent_order(season="2025-2026"):
    """Ordine avversari del girone di andata — definisce le posizioni sull'asse X."""
    matches = load_matches()
    season_matches = matches[(matches["season"] == season) & (matches["active"] == 1)]
    return list(season_matches[season_matches["leg"] == "A"]["opponent"])


def get_x_axis_order(season="2025-2026"):
    """
    Le posizioni (in ordine) dell'asse X del grafico "per avversario" (grafico 1):
    i 13 avversari del girone di andata, seguiti dalla posizione playout a sé
    stante (PLAYOUT_LABEL).
    """
    return get_opponent_order(season) + [PLAYOUT_LABEL]


def load_all_matches(season="2025-2026"):
    """
    Carica tutte le partite attive della stagione (regular season + playout),
    una per una, filtrate sui giocatori riconosciuti.

    Ritorna una lista di dict: {leg, opponent, giornata, x_label, match_seq,
    decimo_locali, official_result, df}. `official_result` è il risultato
    ufficiale (vedi parse_official_results) se disponibile per questa partita
    — copre le 26 di andata/ritorno se il file dei risultati esiste per la
    stagione, None per i playout (o se il file manca): in quel caso
    compute_set_outcomes/compute_match_outcome ricostruiscono un'approssimazione
    dal file Excel di match analysis.
    """
    matches = load_matches()
    season_matches = matches[(matches["season"] == season) & (matches["active"] == 1)]
    base = build_base_path(season=season)
    known_keys = _load_known_identity_keys()
    opponent_order = get_opponent_order(season)
    n_opponents = len(opponent_order)
    official_results = _load_official_results(season)

    out = []
    for _, row in season_matches.iterrows():
        file_path = str(base / row["path"])
        try:
            df = pd.read_excel(file_path, skiprows=1)
            decimo_locali = _parse_decimo_locali(file_path)
        except Exception as e:
            print(f"Errore caricando {file_path}: {e}")
            continue

        df = df.dropna(subset=["Numero"]).copy()
        df["Numero"] = df["Numero"].astype(int)
        df = _normalize_and_filter_players(df, known_keys)

        leg = row["leg"]
        try:
            giornata = opponent_order.index(row["opponent"])
        except ValueError:
            giornata = None  # avversario non presente nel girone di andata (caso anomalo)

        if leg == "A":
            x_label = row["opponent"]
            match_seq = giornata
        elif leg == "R":
            x_label = row["opponent"]
            match_seq = n_opponents + giornata
        elif leg == "POA":
            x_label = PLAYOUT_LABEL
            match_seq = 2 * n_opponents        # 27ª partita (0-based: n_opponents*2)
        elif leg == "POR":
            x_label = PLAYOUT_LABEL
            match_seq = 2 * n_opponents + 1    # 28ª partita
        else:
            x_label = row["opponent"]
            match_seq = None

        # giornata ufficiale = match_seq + 1 (match_seq è 0-based, 0..25 per andata+ritorno,
        # 26/27 per i playout — mai presenti nel file dei risultati ufficiali).
        official_result = official_results.get(match_seq + 1) if match_seq is not None else None

        out.append({
            "leg": leg,
            "opponent": row["opponent"],
            "giornata": giornata,
            "x_label": x_label,
            "match_seq": match_seq,
            "decimo_locali": decimo_locali,
            "official_result": official_result,
            "df": df,
        })
    return out


def _set_win_threshold(numero_set):
    """Punti per chiudere il set: 15 per il tie-break (sempre e solo il 5° set), 25 per gli altri."""
    return 15 if numero_set == 5 else 25


def compute_set_outcomes(df_match, decimo_locali, official_result=None):
    """
    Una riga per set giocato in questa partita: punti Decimo, punti avversario,
    differenza punti con segno dal punto di vista Decimo, ed esito.

    Se `official_result` è fornito (vedi parse_official_results), i punteggi
    finali sono quelli ufficiali — sempre esatti e completi. Altrimenti si
    ricostruisce un'approssimazione dal file Excel di match analysis: quei
    dati non riportano mai esplicitamente il punteggio finale di un set (ogni
    riga mostra il punteggio prima dell'esito dell'azione di quella riga; per
    l'ultima azione del set l'incremento non compare in nessuna riga, dato che
    il set successivo riparte da 0-0), quindi in questo caso `punti_decimo`/
    `punti_avversario` sono solo un'approssimazione per difetto (tipicamente
    di 1 punto sul lato vincente, a volte di più se un'azione tutta
    dell'avversario non genera una riga propria nel file, centrato sul Decimo).

    `set_completo` è False se il punteggio finale ricostruito non è un esito
    valido di volley (nessuna delle due squadre ha raggiunto la soglia con
    almeno 2 punti di margine) — capita quando la registrazione si interrompe
    a metà set (verificato su dati reali: R-Civitavecchia, set 2, fermo a
    24-24). In quel caso `set_vinto` è `None`: non sappiamo chi avrebbe vinto,
    non va trattato né come vittoria né come sconfitta. Con `official_result`
    fornito, `set_completo` è sempre True (il punteggio è quello reale).
    """
    if official_result is not None:
        return compute_set_outcomes_from_official(official_result)

    d = df_match.dropna(subset=["Numero Set"])
    sets_summary = (
        d.groupby("Numero Set")
        .agg(punti_locali=("Punti Locali", "max"), punti_ospiti=("Punti Ospiti", "max"))
        .reset_index()
        .rename(columns={"Numero Set": "numero_set"})
        .sort_values("numero_set")
    )

    if decimo_locali:
        sets_summary["punti_decimo"] = sets_summary["punti_locali"]
        sets_summary["punti_avversario"] = sets_summary["punti_ospiti"]
    else:
        sets_summary["punti_decimo"] = sets_summary["punti_ospiti"]
        sets_summary["punti_avversario"] = sets_summary["punti_locali"]

    sets_summary["punti_diff"] = sets_summary["punti_decimo"] - sets_summary["punti_avversario"]

    def _is_completo(row):
        soglia = _set_win_threshold(row["numero_set"])
        return max(row["punti_decimo"], row["punti_avversario"]) >= soglia and abs(row["punti_diff"]) >= 2

    sets_summary["set_completo"] = sets_summary.apply(_is_completo, axis=1)
    sets_summary["set_vinto"] = sets_summary.apply(
        lambda r: (r["punti_diff"] > 0) if r["set_completo"] else None, axis=1
    )

    return sets_summary[
        ["numero_set", "punti_decimo", "punti_avversario", "punti_diff", "set_completo", "set_vinto"]
    ].reset_index(drop=True)


def compute_match_outcome(df_match, decimo_locali, official_result=None):
    """
    Aggregato partita: set vinti/persi (solo tra i set completi — vedi
    compute_set_outcomes), esito finale, differenza punti totale (su tutti i
    set, anche incompleti: i punti realmente segnati restano un'informazione
    valida anche se il set non è arrivato a un esito), numero di set totali
    registrati e completi (utile per segnalare partite con dati mancanti,
    es. A-Lazio: un solo set registrato su una partita finita presumibilmente
    3-0 secondo il nome cartella).

    Se `official_result` è fornito, tutto è esatto (vedi
    compute_match_outcome_from_official); altrimenti è la ricostruzione
    approssimata dal file Excel (vedi compute_set_outcomes).
    """
    if official_result is not None:
        return compute_match_outcome_from_official(official_result)

    sets = compute_set_outcomes(df_match, decimo_locali)
    completi = sets[sets["set_completo"]]
    set_vinti = int((completi["set_vinto"] == True).sum())  # noqa: E712 (confronto esplicito, set_vinto può essere None)
    set_persi = int((completi["set_vinto"] == False).sum())  # noqa: E712

    return {
        "set_vinti": set_vinti,
        "set_persi": set_persi,
        "partita_vinta": (set_vinti > set_persi) if (set_vinti != set_persi) else None,
        "punti_diff_totale": int(sets["punti_diff"].sum()),
        "n_set_totali": len(sets),
        "n_set_completi": len(completi),
    }


def compute_team_kpis(df_match):
    """
    KPI squadra per una singola partita. Ritorna {kpi_label: valore}.
    Include le percentuali di efficienza (Battuta%/Ricezione%/Attacco%/Muro%), i
    conteggi assoluti per voto (ABS_KPI_DEFS) e il totale errori (Errori).
    A livello squadra non ci sono "buchi": ogni fondamentale viene sempre giocato,
    quindi un conteggio a zero è un valore legittimo, non un dato mancante.
    """
    out = {}
    for tipo, (pos, neg) in TIPO_POS_NEG.items():
        raw = calcola_efficienza(df_match, tipo, pos=pos, neg=neg, total_efficiency=False)
        tot = raw[raw["Giocatore"] == "Totale"]
        tot_row = tot.iloc[0] if not tot.empty else None

        out[EFF_KPI_LABELS[tipo]] = eff_scalar(float(tot_row["Eff"])) if tot_row is not None else float("nan")
        out[TOT_KPI_LABELS[tipo]] = int(tot_row["Tot"]) if tot_row is not None else 0

        for label, (t, voto) in ABS_KPI_DEFS.items():
            if t != tipo:
                continue
            out[label] = int(tot_row[voto]) if tot_row is not None and voto in tot_row.index and pd.notna(tot_row[voto]) else 0

    errors = find_errors(df_match)
    tot_row = errors[errors["Giocatore"] == "Totale"]
    out[ERRORI_KPI] = int(tot_row[ERROR_COLS].sum(axis=1).iloc[0]) if not tot_row.empty else 0
    return out


def compute_player_kpis(df_match):
    """
    KPI per giocatore per una singola partita: percentuali di efficienza, totale
    azioni per fondamentale (TOT_KPI_LABELS), conteggi assoluti per voto specifico
    (ABS_KPI_DEFS) e totale errori.
    Ritorna un DataFrame indicizzato per 'Giocatore' con una colonna per KPI.
    Un KPI è assente (NaN) per un giocatore/fondamentale se quel giocatore non ha
    fatto NESSUNA azione di quel fondamentale in quella partita (es. un libero che
    non attacca mai) — un conteggio a zero (es. 0 murati su N attacchi) resta
    invece un valore esplicito, distinto dal "non ha giocato quel fondamentale".
    """
    per_player = {}

    for tipo, (pos, neg) in TIPO_POS_NEG.items():
        raw = calcola_efficienza(df_match, tipo, pos=pos, neg=neg, total_efficiency=False)
        raw = raw[raw["Giocatore"] != "Totale"]
        eff_label = EFF_KPI_LABELS[tipo]
        for _, row in raw.iterrows():
            entry = per_player.setdefault(row["Giocatore"], {})
            entry[eff_label] = eff_scalar(float(row["Eff"]))
            entry[TOT_KPI_LABELS[tipo]] = int(row["Tot"])
            for label, (t, voto) in ABS_KPI_DEFS.items():
                if t != tipo:
                    continue
                entry[label] = int(row[voto]) if voto in row.index and pd.notna(row[voto]) else 0

    errors = find_errors(df_match)
    errors = errors[errors["Giocatore"] != "Totale"]
    for _, row in errors.iterrows():
        per_player.setdefault(row["Giocatore"], {})[ERRORI_KPI] = int(row[ERROR_COLS].sum())

    return pd.DataFrame.from_dict(per_player, orient="index").reindex(columns=ALL_KPIS)


def _attack_split_totals(sub_df):
    """
    Da un sotto-dataframe di attacchi (SO o contrattacco, già filtrato da
    separate_attacks_counterattacks), calcola {eff, tot, punti, errori, murati}
    aggregati (riga 'Totale' di calcola_efficienza).
    """
    raw = calcola_efficienza(sub_df, "attacco", pos=["#"], neg=["=", "/"], total_efficiency=False)
    tot = raw[raw["Giocatore"] == "Totale"]
    tot_row = tot.iloc[0] if not tot.empty else None

    out = {
        "eff": eff_scalar(float(tot_row["Eff"])) if tot_row is not None else float("nan"),
        "tot": int(tot_row["Tot"]) if tot_row is not None else 0,
    }
    for key, voto in (("punti", "#"), ("errori", "="), ("murati", "/")):
        out[key] = int(tot_row[voto]) if tot_row is not None and voto in tot_row.index and pd.notna(tot_row[voto]) else 0
    return out


def _attack_split_player_totals(sub_df):
    """Come _attack_split_totals, ma una riga per giocatore (dict {giocatore: {eff, tot, punti, errori, murati}})."""
    raw = calcola_efficienza(sub_df, "attacco", pos=["#"], neg=["=", "/"], total_efficiency=False)
    raw = raw[raw["Giocatore"] != "Totale"]

    per_player = {}
    for _, row in raw.iterrows():
        stats = {"eff": eff_scalar(float(row["Eff"])), "tot": int(row["Tot"])}
        for key, voto in (("punti", "#"), ("errori", "="), ("murati", "/")):
            stats[key] = int(row[voto]) if voto in row.index and pd.notna(row[voto]) else 0
        per_player[row["Giocatore"]] = stats
    return per_player


def compute_team_attacco_so_kpis(df_match, rec_vote=DEFAULT_REC_VOTE):
    """
    KPI squadra "Attacco SO", "Attacco FB" e "Contrattacco" per una singola
    partita: efficienza e conteggi assoluti (totale/punti/errori/murati) sui
    tre sottoinsiemi disgiunti di attacchi determinati da separate_attack_types
    (SO = dopo una ricezione con voto in `rec_vote`, al più con un'alzata di
    mezzo; FB = dopo una free ball; contrattacco = tutti gli altri). Ritorna
    {kpi_label: valore}.
    """
    so_df, fb_df, ctr_df = separate_attack_types(df_match, rec_vote=rec_vote)
    so_stats = _attack_split_totals(so_df)
    fb_stats = _attack_split_totals(fb_df)
    ctr_stats = _attack_split_totals(ctr_df)

    out = {}
    for key, label in ATTACCO_SO_KPI_LABELS.items():
        out[label] = so_stats[key]
    for key, label in ATTACCO_FB_KPI_LABELS.items():
        out[label] = fb_stats[key]
    for key, label in CONTRATTACCO_KPI_LABELS.items():
        out[label] = ctr_stats[key]
    return out


def compute_player_attacco_so_kpis(df_match, rec_vote=DEFAULT_REC_VOTE):
    """
    KPI per giocatore "Attacco SO", "Attacco FB" e "Contrattacco" per una
    singola partita (vedi compute_team_attacco_so_kpis). Ritorna un DataFrame
    indicizzato per 'Giocatore'. Un giocatore assente da una delle tre
    famiglie non ha fatto nessun attacco di quel tipo in questa partita
    (buco, non uno zero esplicito).
    """
    so_df, fb_df, ctr_df = separate_attack_types(df_match, rec_vote=rec_vote)
    so_player = _attack_split_player_totals(so_df)
    fb_player = _attack_split_player_totals(fb_df)
    ctr_player = _attack_split_player_totals(ctr_df)

    per_player = {}
    for player, stats in so_player.items():
        entry = per_player.setdefault(player, {})
        for key, label in ATTACCO_SO_KPI_LABELS.items():
            entry[label] = stats[key]
    for player, stats in fb_player.items():
        entry = per_player.setdefault(player, {})
        for key, label in ATTACCO_FB_KPI_LABELS.items():
            entry[label] = stats[key]
    for player, stats in ctr_player.items():
        entry = per_player.setdefault(player, {})
        for key, label in CONTRATTACCO_KPI_LABELS.items():
            entry[label] = stats[key]

    all_cols = (
        list(ATTACCO_SO_KPI_LABELS.values())
        + list(ATTACCO_FB_KPI_LABELS.values())
        + list(CONTRATTACCO_KPI_LABELS.values())
    )
    return pd.DataFrame.from_dict(per_player, orient="index").reindex(columns=all_cols)


def apply_min_attacks_threshold(player_df, threshold):
    """
    Soglia minima sul numero di attacchi, solo per giocatore (non a livello
    squadra). Per ogni KPI della famiglia "attacco" (Attacco*, Attacco SO*,
    Contrattacco* — vedi ATTACK_FAMILY_TOT_KPI), la riga (opponent, leg,
    player) viene scartata se il KPI "Tot" della stessa famiglia, per lo
    stesso giocatore nella stessa partita, non raggiunge `threshold`: il
    giocatore risulta "senza dati" per quel KPI in quella partita (stesso
    trattamento del buco/NaN già usato altrove), non un valore a zero.
    KPI fuori dalla famiglia attacco (Battuta*, Ricezione*, Muro*, Errori)
    non sono soggetti a questa soglia. threshold <= 0 non filtra nulla.
    """
    if threshold <= 0 or player_df.empty:
        return player_df

    tot_kpis = set(ATTACK_FAMILY_TOT_KPI.values())
    tot_lookup = (
        player_df[player_df["kpi"].isin(tot_kpis)]
        .set_index(["opponent", "leg", "player", "kpi"])["value"]
    )

    def _keep(row):
        family_tot_kpi = ATTACK_FAMILY_TOT_KPI.get(row["kpi"])
        if family_tot_kpi is None:
            return True
        tot = tot_lookup.get((row["opponent"], row["leg"], row["player"], family_tot_kpi))
        return tot is not None and tot >= threshold

    return player_df[player_df.apply(_keep, axis=1)].reset_index(drop=True)


def build_comparison_dataset(season="2025-2026", matches=None):
    """
    Assembla le tabelle "tidy" per il confronto andata/ritorno/playout (i KPI
    "fissi" di ALL_KPIS — non i KPI "Attacco SO", parametrici: vedi
    build_attacco_so_dataset).

    `matches` (opzionale) evita di ricaricare gli Excel se già disponibili da
    una chiamata a load_all_matches — utile perché entrambi i dataset partono
    dalle stesse partite.

    Ritorna (team_df, player_df):
    - team_df:   colonne [opponent, leg, giornata, x_label, match_seq, kpi, value]
    - player_df: colonne [opponent, leg, giornata, x_label, match_seq, player, kpi, value]
    """
    if matches is None:
        matches = load_all_matches(season)
    team_rows = []
    player_rows = []

    for m in matches:
        base_cols = {
            "opponent": m["opponent"], "leg": m["leg"], "giornata": m["giornata"],
            "x_label": m["x_label"], "match_seq": m["match_seq"],
        }

        for kpi, value in compute_team_kpis(m["df"]).items():
            team_rows.append({**base_cols, "kpi": kpi, "value": value})

        player_kpis = compute_player_kpis(m["df"])
        for player, row in player_kpis.iterrows():
            for kpi in ALL_KPIS:
                value = row.get(kpi)
                if pd.notna(value):
                    player_rows.append({**base_cols, "player": player, "kpi": kpi, "value": value})

    team_df = pd.DataFrame(team_rows)
    player_df = pd.DataFrame(player_rows)
    return team_df, player_df


def build_attacco_so_dataset(season="2025-2026", rec_vote=DEFAULT_REC_VOTE, matches=None):
    """
    Come build_comparison_dataset, ma per i KPI "Attacco SO", "Attacco FB" e
    "Contrattacco" (vedi ATTACCO_SO_KPI_LABELS/ATTACCO_FB_KPI_LABELS/
    CONTRATTACCO_KPI_LABELS), parametrizzati sul voto di ricezione che
    qualifica l'attacco come "dopo ricezione" (Attacco FB e Contrattacco non
    dipendono direttamente da `rec_vote`, ma i loro confini si spostano
    insieme ad Attacco SO perché sono complementari sullo stesso split).
    """
    if matches is None:
        matches = load_all_matches(season)
    kpi_cols = (
        list(ATTACCO_SO_KPI_LABELS.values())
        + list(ATTACCO_FB_KPI_LABELS.values())
        + list(CONTRATTACCO_KPI_LABELS.values())
    )
    team_rows = []
    player_rows = []

    for m in matches:
        base_cols = {
            "opponent": m["opponent"], "leg": m["leg"], "giornata": m["giornata"],
            "x_label": m["x_label"], "match_seq": m["match_seq"],
        }

        for kpi, value in compute_team_attacco_so_kpis(m["df"], rec_vote).items():
            team_rows.append({**base_cols, "kpi": kpi, "value": value})

        player_kpis = compute_player_attacco_so_kpis(m["df"], rec_vote)
        for player, row in player_kpis.iterrows():
            for kpi in kpi_cols:
                value = row.get(kpi)
                if pd.notna(value):
                    player_rows.append({**base_cols, "player": player, "kpi": kpi, "value": value})

    team_df = pd.DataFrame(team_rows)
    player_df = pd.DataFrame(player_rows)
    return team_df, player_df


def build_match_outcomes_dataset(season="2025-2026", matches=None):
    """
    Una riga per partita: esito (vedi compute_match_outcome) con le stesse
    colonne di contesto (opponent, leg, giornata, x_label, match_seq) delle
    altre tabelle tidy — pronta per essere incrociata con i KPI, e base per il
    futuro modello (random forest) su set vinti / differenza punti / partita
    vinta.
    """
    if matches is None:
        matches = load_all_matches(season)
    rows = []
    for m in matches:
        outcome = compute_match_outcome(m["df"], m["decimo_locali"], official_result=m.get("official_result"))
        rows.append({
            "opponent": m["opponent"], "leg": m["leg"], "giornata": m["giornata"],
            "x_label": m["x_label"], "match_seq": m["match_seq"],
            "esatto": m.get("official_result") is not None,
            **outcome,
        })
    return pd.DataFrame(rows)


def build_set_outcomes_dataset(season="2025-2026", matches=None):
    """
    Una riga per (partita, set): granularità più fine di
    build_match_outcomes_dataset, pensata per il futuro modello sul singolo
    set (vedi compute_set_outcomes).
    """
    if matches is None:
        matches = load_all_matches(season)
    rows = []
    for m in matches:
        sets = compute_set_outcomes(m["df"], m["decimo_locali"], official_result=m.get("official_result"))
        for _, s in sets.iterrows():
            rows.append({
                "opponent": m["opponent"], "leg": m["leg"], "giornata": m["giornata"],
                "x_label": m["x_label"], "match_seq": m["match_seq"],
                "esatto": m.get("official_result") is not None,
                **s.to_dict(),
            })
    return pd.DataFrame(rows)
