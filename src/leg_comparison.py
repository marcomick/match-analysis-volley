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
    il nome avversario per A/R, `playout_label(avversario)` per POA/POR — il
    playout ha una posizione propria in coda (dopo l'ultimo avversario del
    girone), non è raggruppato con la partita di andata/ritorno contro lo
    stesso avversario;
  - `match_seq`: indice cronologico assoluto su tutta la stagione, usato dai
    grafici lungo la stagione: con N avversari registrati nel girone di
    andata, andata 0..N-1, ritorno N..2N-1, POA 2N, POR 2N+1 (2025-2026:
    N=13, 28 partite). N è il numero di partite di andata registrate in
    config/matches.csv — a stagione in corso cresce man mano.
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
  vs Roman). Vedi `src.scout_files.parse_decimo_locali`. Per i .dvw (dal
  2026-2027) lo dice l'intestazione del file (squadra di casa/ospite).
"""
import re

import pandas as pd

from config.paths import ROOT, build_base_path, load_matches, load_seasons
from src.dvw import dvw_official_result
from src.fipav import load_decimo_results
from src.scout_files import read_match_file
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



def playout_label(opponent):
    """Etichetta per la posizione "playout" nel grafico per avversario
    (grafico 1): una posizione a sé stante, dopo l'ultimo avversario del
    girone di andata (2025-2026: "JVC (PO)")."""
    return f"{opponent} (PO)"

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
# al più con un'alzata di mezzo), "Contrattacco" (tutti gli altri attacchi,
# free ball COMPRESE) e "Attacco FB" (il sottoinsieme del contrattacco
# immediatamente successivo a una difesa Voto '!', palla facile rimandata nel
# nostro campo — vedi separate_free_ball). La free ball fa parte del
# contrattacco (indicazione esplicita dell'utente, 2026-10-23: dal 2026-08-12
# al 2026-10-23 il contrattacco la escludeva, in disaccordo col tabellino):
# Attacco SO Tot + Contrattacco Tot == Attacco Tot, Attacco FB Tot <=
# Contrattacco Tot. separate_attack_types produce SO/FB/resto disgiunti, il
# contrattacco è FB + resto (vedi _split_so_fb_contrattacco).
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
# — indicizzate per leg (non per un numero di giornata fisso: il numero di
# giornate dipende da quante squadre ha il girone, diverso stagione per stagione).
_PLAYOUT_BLOCK_LEG = {"Playout Andata": "POA", "Playout Ritorno": "POR"}


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
    è il numero intero N per i blocchi "Giornata N" (= match_seq + 1), e la
    stringa "POA"/"POR" per i playout (vedi _PLAYOUT_BLOCK_LEG).
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
            if stripped in _PLAYOUT_BLOCK_LEG:
                giornata = _PLAYOUT_BLOCK_LEG[stripped]
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


def _load_fipav_results(season):
    """Risultati del Decimo dal sito federale ({data: risultato}, vedi
    src.fipav), se in config/seasons.csv la stagione ha un `fipav_girone`."""
    seasons = load_seasons()
    row = seasons[seasons["season"] == season]
    if row.empty or "fipav_girone" not in row.columns or pd.isna(row["fipav_girone"].iloc[0]):
        return {}
    return load_decimo_results(int(row["fipav_girone"].iloc[0]))


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


def _load_known_identity_keys(include_ignored=False):
    """Terne (team, cognome, numero) riconosciute e non marcate IGNORED
    (con include_ignored=True: tutte le terne già gestite nel check terne,
    IGNORED comprese)."""
    identities = pd.read_csv(IDENTITIES_CSV)
    known = identities if include_ignored else identities[identities["player_id"] != "IGNORED"]
    return set(zip(known["team"], known["cognome"], known["numero"].astype(str)))


def _normalize_cognome(cognome):
    return str(cognome).replace("’", "'").strip().capitalize()


def _normalize_and_filter_players(df, known_keys, cognome_to_numero=None):
    """
    Normalizza Cognome, crea 'Giocatore' ed esclude le terne non
    riconosciute/IGNORED. Con `cognome_to_numero` (vedi
    _season_cognome_to_numero) rinumera prima ogni giocatore sul suo numero
    di riferimento per la stagione, come la cella di caricamento di
    classifiche.ipynb.
    """
    df = df.copy()
    df["Cognome"] = df["Cognome"].apply(_normalize_cognome)
    if cognome_to_numero is not None:
        df["Numero"] = df["Cognome"].map(cognome_to_numero).fillna(df["Numero"]).astype(int)
    df["Giocatore"] = df["Cognome"] + " " + df["Numero"].astype(str)
    mask = df.apply(lambda r: (TEAM, r["Cognome"], str(int(r["Numero"]))) in known_keys, axis=1)
    return df[mask]


def _season_cognome_to_numero(dfs):
    """
    Cognome -> ultimo numero di maglia con cui compare nella stagione (partite
    nell'ordine di config/matches.csv) — stessa regola della cella di
    caricamento di classifiche.ipynb, che produce le classifiche PDF: un
    giocatore con maglie diverse nel corso della stagione (es. Caranzetti con
    la 17 "in prestito" a inizio 2025-2026, poi la 14) resta un unico
    giocatore, sotto l'ultimo numero. Limite (lo stesso del notebook): due
    giocatori Decimo con lo stesso cognome nella stessa stagione verrebbero
    fusi in uno.
    """
    mapping = {}
    for df in dfs:
        for cognome, numero in zip(df["Cognome"].apply(_normalize_cognome), df["Numero"]):
            mapping[cognome] = int(numero)
    return mapping


def active_season_matches(season):
    matches = load_matches()
    return matches[(matches["season"] == season) & (matches["active"] == 1)]


def get_opponent_order(season="2025-2026"):
    """Ordine avversari del girone di andata — definisce le posizioni sull'asse X."""
    season_matches = active_season_matches(season)
    return list(season_matches[season_matches["leg"] == "A"]["opponent"])


def get_x_axis_order(season="2025-2026"):
    """
    Le posizioni (in ordine) dell'asse X del grafico "per avversario" (grafico 1):
    gli avversari del girone di andata, seguiti dalla posizione playout a sé
    stante (playout_label), se in stagione è registrato un playout.
    """
    season_matches = active_season_matches(season)
    playout_opponents = season_matches[season_matches["leg"].isin(["POA", "POR"])]["opponent"]
    return get_opponent_order(season) + [playout_label(o) for o in dict.fromkeys(playout_opponents)]


def match_position(leg, opponent, opponent_order):
    """
    (giornata, x_label, match_seq) di una partita — vedi docstring di modulo.
    Condivisa con src.setter_report, che ricarica gli Excel per conto proprio.
    """
    n_opponents = len(opponent_order)
    try:
        giornata = opponent_order.index(opponent)
    except ValueError:
        giornata = None  # avversario non presente nel girone di andata (caso anomalo)

    if leg == "A":
        return giornata, opponent, giornata
    if leg == "R":
        return giornata, opponent, None if giornata is None else n_opponents + giornata
    if leg == "POA":
        return giornata, playout_label(opponent), 2 * n_opponents
    if leg == "POR":
        return giornata, playout_label(opponent), 2 * n_opponents + 1
    return giornata, opponent, None


def load_all_matches(season="2025-2026"):
    """
    Carica tutte le partite attive della stagione (regular season + playout),
    una per una, filtrate sui giocatori riconosciuti.

    Ritorna una lista di dict: {leg, opponent, giornata, x_label, match_seq,
    decimo_locali, official_result, unregistered, file_path, df}. I giocatori sono
    rinumerati per cognome sull'ultimo numero della stagione (vedi
    _season_cognome_to_numero) prima del filtro, come in classifiche.ipynb.
    `unregistered`: terne Decimo ("Cognome Numero", dopo la rinumerazione)
    presenti nel file ma mai gestite nel check terne di classifiche.ipynb (né
    riconosciute né IGNORED) — escluse dal `df` come le altre non
    riconosciute, ma segnalate: a inizio stagione (nuovi giocatori, numeri di
    maglia cambiati) altrimenti sparirebbero in silenzio. `official_result` è il risultato
    ufficiale (vedi parse_official_results) se disponibile per questa partita;
    in mancanza, per un .dvw, il risultato dello scout se tutti i set sono
    conclusi (src.dvw.dvw_official_result); altrimenti None, e
    compute_set_outcomes/compute_match_outcome ricostruiscono un'approssimazione
    dal file di scout.
    """
    season_matches = active_season_matches(season)
    base = build_base_path(season=season)
    known_keys = _load_known_identity_keys()
    handled_keys = _load_known_identity_keys(include_ignored=True)
    opponent_order = get_opponent_order(season)
    official_results = _load_official_results(season)
    fipav_results = _load_fipav_results(season)

    # Prima passata: lettura di tutti i file, serve la stagione intera per la
    # rinumerazione per cognome (vedi _season_cognome_to_numero).
    loaded = []
    for _, row in season_matches.iterrows():
        file_path = str(base / row["path"])
        try:
            df, decimo_locali, dvw_parsed = read_match_file(file_path)
        except Exception as e:
            print(f"Errore caricando {file_path}: {e}")
            continue
        df = df.dropna(subset=["Numero"]).copy()
        df["Numero"] = df["Numero"].astype(int)
        dvw_result = dvw_official_result(dvw_parsed) if dvw_parsed is not None else None
        data = dvw_parsed["data"] if dvw_parsed is not None else None
        loaded.append((row, decimo_locali, dvw_result, data, df))
    cognome_to_numero = _season_cognome_to_numero([df for *_, df in loaded])

    out = []
    for row, decimo_locali, dvw_result, data, df in loaded:
        terne = {(c, str(cognome_to_numero[c])) for c in df["Cognome"].apply(_normalize_cognome)}
        unregistered = sorted(f"{c} {n}" for c, n in terne if (TEAM, c, n) not in handled_keys)
        df = _normalize_and_filter_players(df, known_keys, cognome_to_numero)

        leg = row["leg"]
        giornata, x_label, match_seq = match_position(leg, row["opponent"], opponent_order)

        # Risultato, in ordine di priorità:
        # 1. sito federale, abbinato per DATA (dal .dvw): robusto anche se una
        #    partita non viene scoutata/registrata, a differenza della giornata;
        # 2. file risultati manuale: giornata ufficiale = match_seq + 1 per
        #    andata/ritorno (match_seq è 0-based, quindi presuppone tutte le
        #    partite registrate in ordine), playout indicizzati per leg (vedi
        #    _PLAYOUT_BLOCK_LEG);
        # 3. il .dvw stesso, se tutti i set sono conclusi nello scout (vedi
        #    src.dvw.dvw_official_result).
        official_result = fipav_results.get(data) if data else None
        if official_result is None:
            if leg in ("POA", "POR"):
                official_result = official_results.get(leg)
            else:
                official_result = official_results.get(match_seq + 1) if match_seq is not None else None
        if official_result is None:
            official_result = dvw_result

        out.append({
            "leg": leg,
            "opponent": row["opponent"],
            "giornata": giornata,
            "x_label": x_label,
            "match_seq": match_seq,
            "decimo_locali": decimo_locali,
            "official_result": official_result,
            "unregistered": unregistered,
            "file_path": str(base / row["path"]),
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


def _split_so_fb_contrattacco(df_match, rec_vote):
    """(so_df, fb_df, contrattacco_df): il contrattacco comprende le free ball
    (vedi commento su ATTACCO_SO_KPI_LABELS), FB ne è un sottoinsieme."""
    so_df, fb_df, rest_df = separate_attack_types(df_match, rec_vote=rec_vote)
    return so_df, fb_df, pd.concat([fb_df, rest_df]).sort_index()


def compute_team_attacco_so_kpis(df_match, rec_vote=DEFAULT_REC_VOTE):
    """
    KPI squadra "Attacco SO", "Attacco FB" e "Contrattacco" per una singola
    partita: efficienza e conteggi assoluti (totale/punti/errori/murati) su
    SO (dopo una ricezione con voto in `rec_vote`, al più con un'alzata di
    mezzo), contrattacco (tutti gli altri, free ball comprese) e FB (dopo una
    free ball, sottoinsieme del contrattacco). Ritorna {kpi_label: valore}.
    """
    so_df, fb_df, ctr_df = _split_so_fb_contrattacco(df_match, rec_vote)
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
    so_df, fb_df, ctr_df = _split_so_fb_contrattacco(df_match, rec_vote)
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
