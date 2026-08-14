"""
Base dati per la pagella giocatore: combina i KPI di rendimento
(src/leg_comparison.py) con le presenze (src/attendance.py) per produrre,
per ciascun giocatore, un insieme di "finding" (streak + trend
prima/seconda metà stagione) pronti per una futura narrazione — questo
modulo produce solo la struttura dati verificata, non il rendering finale
(testo/PDF/pagina Streamlit), da decidere in un secondo momento.

Approccio concordato con l'utente il 2026-08-12: mostrare tutti i 32 KPI
per ogni giocatore sarebbe illeggibile. Si calcola trend e streak solo su
un sottoinsieme curato di KPI (PAGELLA_KPIS, vedi sotto — per lo più
percentuali, con qualche dettaglio assoluto mirato), e si mostrano solo i
`top_n` finding più notevoli (per punteggio, vedi score) tra quelli con
dati sufficienti (min_points) — la selezione si adatta comunque da sola
alla situazione specifica del giocatore: es. un libero non genera mai un
finding sui KPI di attacco per semplice scarsità di dati (troppe partite
senza quell'azione), anche se in teoria potrebbe.

Curatela KPI (PAGELLA_KPIS), richiesta esplicita dell'utente il 2026-08-12:
  - Battuta%, Battuta # (ace), Battuta = (errori)
  - Ricezione%, Ricezione = (ace subiti)
  - Attacco, scomposto in Attacco SO% / Attacco FB% / Contrattacco%
    (side-out / free ball / tutto il resto — vedi separate_attack_types in
    src/efficiency.py) con i rispettivi punti/errori/murati, MAI la versione
    generica Attacco% non distinta per tipo
  - Muro # (punti) SOLO — Muro% e Muro = (mani-fuori subite) esclusi
    ("poco indicativi" secondo l'utente)
  - Errori (totale)
Esclusi sempre: tutti i KPI 'Tot' (volumi, non qualità — già esclusi
tramite KPI_DIRECTION), Attacco%/Attacco # (punti)/Attacco = (errori)/
Attacco / (murati) generici, Muro%, Muro = (mani-fuori subite).

Esclusione per ruolo — palleggiatori: un palleggiatore (es. Caranzetti)
attacca poche volte a partita; quei pochi dati generavano comunque finding
sull'attacco, non significativi per il suo ruolo. Su richiesta esplicita
dell'utente, l'intera famiglia attacco (SO/FB/Contrattacco) è esclusa a
priori per i giocatori con ruolo 'P' (palleggiatore) nel foglio Presenze D
(vedi _load_player_roles) — non lasciata alla sola scarsità di dati.
TODO roadmap: una metrica specifica per palleggiatori (non ancora
definita, richiesta esplicitamente rimandata dall'utente).

Due tipi di finding per (giocatore, KPI):
  - 'streak': una sequenza di >= min_length partite giocate consecutive
    (ignorando i buchi per partite non disputate) in cui la MEDIA MOBILE
    centrata di ampiezza min_length resta sopra o sotto una banda di
    tolleranza attorno alla mediana stagionale del giocatore per quel KPI
    (non partita per partita — richiesto esplicitamente dall'utente il
    2026-08-13 per intercettare anche una crescita/calo momentaneo, senza
    che una singola partita "nella norma" in mezzo interrompa il periodo).
  - 'cambio_livello': cerca IL punto di rottura (tra due partite giocate
    consecutive) che massimizza la differenza tra la media prima e la
    media dopo, con almeno min_length partite giocate su ciascun lato —
    riportato solo se quel delta supera la stessa banda di tolleranza.
    Porta con sé anche la pendenza di regressione lineare (slope) come
    dato di supporto. Prima del 2026-08-12 lo split era fisso a metà
    stagione (match_seq 14): corretto perché un cambiamento di livello che
    non comincia esattamente a metà veniva diluito e perso — bug segnalato
    dall'utente sul caso Pessei/Attacco SO%, che mostrava un aumento reale
    dalla partita 20 in poi (media dal 12% al 50%) mai segnalato dallo
    split fisso (delta diluito a +6.5, sotto la tolleranza). Con la ricerca
    del punto di rottura ottimale, lo stesso caso emerge esattamente al
    match_seq 20, delta +38.5, ben sopra tolleranza.

Parametri di default concordati con l'utente il 2026-08-12: min_length=3
partite consecutive (streak, e partite minime per lato di un cambio
livello), tolleranza = max(15% relativo alla mediana, 0.5 deviazioni
standard), top_n=5 finding per giocatore.

KPI 'Tot' per fondamentale (Battuta/Ricezione/Attacco/Muro/Attacco SO/
Contrattacco Tot) sono volumi, non indicatori di qualità: non hanno una
direzione "meglio se alto/basso" e sono esclusi da trend/streak (KPI_DIRECTION
non li contiene) — restano però nell'input di compute_player_findings, che
li usa per il filtro di significatività qui sotto.

Filtro di significatività sui KPI percentuali (PERCENT_KPI_VOLUME),
richiesto esplicitamente dall'utente il 2026-08-12 (soglia alzata da 3 a 5
il 2026-08-13, sempre su richiesta esplicita): una percentuale calcolata su
pochi tentativi non è statisticamente affidabile — rumore che, oltre a
essere di per sé non significativo, gonfia anche la varianza stagionale
usata per calibrare la tolleranza, rendendo più difficile rilevare
cambiamenti reali (esattamente il meccanismo dietro il caso Pessei sopra).
Prima di calcolare streak/cambio livello su un KPI percentuale (Battuta%/
Ricezione%/Attacco SO%/Attacco FB%/Contrattacco%), le partite con volume
sottostante (il "Tot" della stessa famiglia) sotto DEFAULT_MIN_VOLUME_PERCENT
(5 tentativi) vengono escluse, come se il giocatore non avesse dati per
quel KPI in quella partita — stesso trattamento del buco/NaN già usato
altrove nel progetto (soglia minima attacchi in dashboard, KPI assoluti
mancanti). La soglia (5), motivazione dell'utente: su circa 120 azioni
totali in una partita, un giocatore che ne ha fatte 3 di un certo tipo "è
come se non avesse giocato" quel fondamentale — non abbastanza per dire
come sia andato. Per i ruoli a basso volume (es. i centrali, mediana
Attacco FB Tot = 2 a partita) questo esclude una parte sostanziale dei
dati: conseguenza accettata consapevolmente, non un effetto collaterale
nascosto. I KPI di conteggio assoluto (Battuta # ace, Battuta/Ricezione/
Attacco */Muro = errori, Muro # punti, Errori) NON sono percentuali e non
soffrono di questo problema — restano un numero intero valido a qualunque
volume, nessun filtro applicato.
"""
import re

import numpy as np
import pandas as pd

from src.attendance import (
    DEFAULT_EVENT_TYPES,
    compute_attendance_summary,
    compute_attendance_summary_by_type,
    filter_known_players,
    load_presence,
)
from src.leg_comparison import (
    ATTACCO_FB_KPI_LABELS,
    ATTACCO_SO_KPI_LABELS,
    CONTRATTACCO_KPI_LABELS,
    DEFAULT_REC_VOTE,
    EFF_KPI_LABELS,
    ERRORI_KPI,
    TOT_KPI_LABELS,
    build_attacco_so_dataset,
    build_comparison_dataset,
    load_all_matches,
)

SEASON_LENGTH = 28  # partite totali stagione 2025-2026 (informativo, non più usato per uno split fisso)

# Parametri di default per streak/cambio livello, concordati con l'utente il 2026-08-12.
DEFAULT_MIN_LENGTH = 3
DEFAULT_TOL_REL = 0.15
DEFAULT_TOL_STD = 0.5
DEFAULT_MIN_POINTS = 6
DEFAULT_TOP_N = 5

# Soglia minima di tentativi sottostanti per fidarsi di un KPI percentuale in
# una partita — vedi docstring di modulo. Alzata da 3 a 5 su richiesta
# esplicita dell'utente (2026-08-13): "se in un'intera partita un giocatore
# ha attaccato 3 palloni, non possiamo dire com'è andato in attacco, è come
# se non avesse giocato" — su ~120 azioni totali a partita, 5 è ancora una
# soglia bassa in assoluto ma sufficiente a scartare i casi degeneri.
DEFAULT_MIN_VOLUME_PERCENT = 5

# KPI percentuale -> il suo "Tot" (volume) di famiglia, per il filtro di
# significatività (vedi docstring di modulo e DEFAULT_MIN_VOLUME_PERCENT).
PERCENT_KPI_VOLUME = {
    EFF_KPI_LABELS["battuta"]: TOT_KPI_LABELS["battuta"],
    EFF_KPI_LABELS["ricezione"]: TOT_KPI_LABELS["ricezione"],
    ATTACCO_SO_KPI_LABELS["eff"]: ATTACCO_SO_KPI_LABELS["tot"],
    ATTACCO_FB_KPI_LABELS["eff"]: ATTACCO_FB_KPI_LABELS["tot"],
    CONTRATTACCO_KPI_LABELS["eff"]: CONTRATTACCO_KPI_LABELS["tot"],
}

# Ruoli (colonna 'Ruolo' del foglio Presenze D, vedi src/attendance.py) per i
# quali l'intera famiglia attacco è esclusa dalla pagella — vedi docstring
# di modulo. 'P' = palleggiatore.
SETTER_ROLE_CODES = {"P"}

# KPI "altri" (non attacco) curati per la pagella — vedi docstring di modulo.
PAGELLA_OTHER_KPIS = [
    EFF_KPI_LABELS["battuta"],
    "Battuta # (ace)",
    "Battuta = (errori)",
    EFF_KPI_LABELS["ricezione"],
    "Ricezione = (ace subiti)",
    "Muro # (punti)",
    ERRORI_KPI,
]

# KPI di attacco curati per la pagella: solo la scomposizione SO/FB/Contrattacco
# (mai Attacco% generico) — vedi docstring di modulo.
PAGELLA_ATTACK_KPIS = (
    list(ATTACCO_SO_KPI_LABELS.values())
    + list(ATTACCO_FB_KPI_LABELS.values())
    + list(CONTRATTACCO_KPI_LABELS.values())
)

# Tutti i KPI 'Tot' delle tre famiglie di attacco restano fuori (volumi, non
# qualità) anche se PAGELLA_ATTACK_KPIS li elenca: KPI_DIRECTION non li
# contiene, quindi compute_player_findings li salta comunque.
PAGELLA_KPIS = PAGELLA_OTHER_KPIS + PAGELLA_ATTACK_KPIS


def _build_kpi_direction():
    """label -> +1 (meglio se alto) / -1 (meglio se basso), solo per i KPI
    in PAGELLA_KPIS (gli altri KPI del progetto — Attacco% generico, Muro%,
    Muro = mani-fuori subite, tutti i 'Tot' — sono fuori scope per la
    pagella, vedi docstring di modulo, e non hanno una direzione qui)."""
    d = {
        EFF_KPI_LABELS["battuta"]: +1,
        "Battuta # (ace)": +1,
        "Battuta = (errori)": -1,
        EFF_KPI_LABELS["ricezione"]: +1,
        "Ricezione = (ace subiti)": -1,
        "Muro # (punti)": +1,
        ERRORI_KPI: -1,
    }
    for family in (ATTACCO_SO_KPI_LABELS, ATTACCO_FB_KPI_LABELS, CONTRATTACCO_KPI_LABELS):
        d[family["eff"]] = +1
        d[family["punti"]] = +1
        d[family["errori"]] = -1
        d[family["murati"]] = -1
    return d


KPI_DIRECTION = _build_kpi_direction()


def _compute_tolerance(values, baseline, tol_rel, tol_std):
    std = float(np.std(values, ddof=0))
    return max(abs(baseline) * tol_rel, std * tol_std)


def _rolling_mean(values, window):
    """
    Media mobile CENTRATA di ampiezza `window` sulla serie (ridotta ai
    margini, mai un buco: il punto i usa la finestra [i-window//2, i+window//2]
    ritagliata sui limiti della serie).
    """
    n = len(values)
    half = window // 2
    out = np.empty(n)
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        out[i] = values[lo:hi].mean()
    return out


def detect_streaks(match_seq, values, direction, min_length=DEFAULT_MIN_LENGTH,
                    tol_rel=DEFAULT_TOL_REL, tol_std=DEFAULT_TOL_STD):
    """
    match_seq, values: array-like allineati, SOLO partite giocate (senza
    NaN) in ordine cronologico. direction: +1 se 'meglio alto' per questo
    KPI, -1 se 'meglio basso'. Ritorna la lista delle sequenze di almeno
    `min_length` partite consecutive sopra/sotto la banda di tolleranza
    attorno alla mediana stagionale del giocatore.

    La classificazione sopra/sotto tolleranza è calcolata sulla MEDIA MOBILE
    centrata di ampiezza `min_length` (non partita per partita) — richiesto
    esplicitamente dall'utente (2026-08-13) per intercettare anche una
    crescita/calo momentaneo: prima, una singola partita "nella norma" in
    mezzo a un periodo altrimenti fuori norma interrompeva lo streak anche
    se il periodo nel complesso era comunque anomalo. Il valore medio
    riportato nel finding (mean_value) resta però quello GREZZO delle
    partite del periodo, non la media mobile — per restare un numero
    onesto/leggibile nella frase discorsiva.
    """
    values = np.asarray(values, dtype=float)
    match_seq = np.asarray(match_seq)
    n = len(values)
    if n < min_length:
        return []

    baseline = float(np.median(values))
    tol = _compute_tolerance(values, baseline, tol_rel, tol_std)
    smoothed = _rolling_mean(values, min_length)
    delta = (smoothed - baseline) * direction
    label = np.where(delta > tol, 1, np.where(delta < -tol, -1, 0))

    streaks = []
    i = 0
    while i < n:
        if label[i] == 0:
            i += 1
            continue
        j = i
        while j < n and label[j] == label[i]:
            j += 1
        length = j - i
        if length >= min_length:
            streaks.append({
                "start_match_seq": int(match_seq[i]),
                "end_match_seq": int(match_seq[j - 1]),
                "length": length,
                "mean_value": float(values[i:j].mean()),
                "baseline": baseline,
                "tipo": "positivo" if label[i] == 1 else "negativo",
            })
        i = j
    return streaks


def detect_level_shift(match_seq, values, min_length=DEFAULT_MIN_LENGTH):
    """
    Cerca IL punto di rottura (tra due partite giocate consecutive) che
    massimizza |media(dopo) - media(prima)|, con almeno `min_length`
    partite giocate su ciascun lato — vedi docstring di modulo per il
    perché di una ricerca sul punto ottimale invece di uno split fisso a
    metà stagione. match_seq, values: array-like allineati, SOLO partite
    giocate (senza NaN) in ordine cronologico.

    Ritorna None se non c'è spazio per almeno `min_length` partite su
    entrambi i lati (serie troppo corta), altrimenti il migliore split
    trovato (non ancora confrontato con una soglia di tolleranza — quel
    confronto è a carico del chiamante, coerente con detect_streaks).
    """
    match_seq = np.asarray(match_seq)
    values = np.asarray(values, dtype=float)
    n = len(values)
    if n < 2 * min_length:
        return None

    best = None
    for k in range(min_length, n - min_length + 1):
        before, after = values[:k], values[k:]
        delta = float(after.mean() - before.mean())
        if best is None or abs(delta) > abs(best["delta"]):
            best = {
                "split_match_seq": int(match_seq[k]),
                "prima_media": float(before.mean()),
                "dopo_media": float(after.mean()),
                "delta": delta,
                "n_prima": int(len(before)),
                "n_dopo": int(len(after)),
            }
    return best


def compute_linear_trend(match_seq, values):
    """Pendenza (slope) di una regressione lineare valore ~ match_seq —
    dato di supporto per il trend prima/seconda metà, non un finding a
    parte (vedi docstring di modulo)."""
    match_seq = np.asarray(match_seq, dtype=float)
    values = np.asarray(values, dtype=float)
    if len(values) < 2 or np.ptp(match_seq) == 0:
        return None
    slope, intercept = np.polyfit(match_seq, values, 1)
    return {"slope": float(slope), "intercept": float(intercept)}


def _apply_volume_filter(kpi, match_seq, values, player_kpi_df, min_volume_percent):
    """
    Per un KPI percentuale con una voce in PERCENT_KPI_VOLUME, rimuove le
    partite dove il volume sottostante (il 'Tot' della stessa famiglia, letto
    da player_kpi_df) è sotto min_volume_percent — vedi docstring di modulo.
    Per gli altri KPI (conteggi assoluti) ritorna (match_seq, values) invariati.
    """
    vol_kpi = PERCENT_KPI_VOLUME.get(kpi)
    if vol_kpi is None or len(match_seq) == 0:
        return match_seq, values
    vol_rows = player_kpi_df[player_kpi_df["kpi"] == vol_kpi]
    vol_by_match = dict(zip(vol_rows["match_seq"], vol_rows["value"]))
    # dtype=bool esplicito: un array vuoto (kpi senza righe per questo
    # giocatore) darebbe altrimenti dtype float64 di default, non indicizzabile
    # come booleano — causava un IndexError a runtime (bug riscontrato in verifica).
    keep = np.array([vol_by_match.get(ms, 0) >= min_volume_percent for ms in match_seq], dtype=bool)
    return match_seq[keep], values[keep]


def compute_player_findings(player_kpi_df, kpis=None, min_length=DEFAULT_MIN_LENGTH, tol_rel=DEFAULT_TOL_REL,
                             tol_std=DEFAULT_TOL_STD, min_points=DEFAULT_MIN_POINTS,
                             top_n=DEFAULT_TOP_N, min_volume_percent=DEFAULT_MIN_VOLUME_PERCENT):
    """
    player_kpi_df: righe [match_seq, kpi, value] per UN giocatore (formato
    lungo di build_comparison_dataset/build_attacco_so_dataset concatenati) —
    deve includere anche i KPI 'Tot' delle famiglie percentuali (servono al
    filtro di significatività, vedi _apply_volume_filter), anche se `kpis`
    (default PAGELLA_KPIS) li esclude dai finding generati.

    Streak e cambio_livello sono raccolti e ordinati per score SEPARATAMENTE,
    poi combinati a metà (vedi _select_balanced): i due score non sono sulla
    stessa scala (lo streak moltiplica per 'length' perché OGNI partita del
    periodo supera individualmente la tolleranza — una prova più forte; il
    cambio di livello richiede solo che la MEDIA del segmento la superi, una
    prova più debole ma che copre casi che uno streak stretto non cattura),
    quindi un ranking unico finirebbe sempre per far vincere un tipo (prima
    versione: sempre gli streak; con un moltiplicatore di lunghezza aggiunto
    al cambio di livello per compensare: sempre il cambio di livello, ma
    spesso quello con delta minuscolo su un segmento enorme, non quello più
    interessante — bug riscontrato in verifica sul caso Pessei/Attacco SO%,
    dove il cambio di livello reale, delta=+38.5 punti percentuali dalla
    partita 20, restava fuori dalla top-5 in entrambe le versioni). Ritorna i
    `top_n` finding più notevoli, con una rappresentazione equilibrata tra i
    due tipi quando entrambi sono disponibili.
    """
    kpis = kpis if kpis is not None else PAGELLA_KPIS
    streak_findings, shift_findings = [], []
    for kpi in kpis:
        direction = KPI_DIRECTION.get(kpi)
        if direction is None:
            continue  # KPI 'Tot' o non mappato: nessuna direzione di qualità
        g = player_kpi_df[player_kpi_df["kpi"] == kpi].sort_values("match_seq")
        values = g["value"].to_numpy(dtype=float)
        match_seq = g["match_seq"].to_numpy()
        match_seq, values = _apply_volume_filter(kpi, match_seq, values, player_kpi_df, min_volume_percent)
        if len(values) < min_points:
            continue

        baseline = float(np.median(values))
        tol = _compute_tolerance(values, baseline, tol_rel, tol_std)

        for s in detect_streaks(match_seq, values, direction, min_length, tol_rel, tol_std):
            # normalizzato sulla tolleranza (non sulla baseline): la baseline di
            # molti KPI di conteggio è spesso 0 (es. Muro # punti in una partita
            # senza muri vincenti) e dividere per essa fa esplodere lo score.
            # Il denominatore è sempre > 0 quando arriviamo qui: uno streak esiste
            # solo se un delta ha superato tol, quindi tol non può essere zero
            # (tol=0 richiederebbe std=0, cioè valori tutti uguali, che non
            # produce mai un delta > 0).
            rel_magnitude = abs(s["mean_value"] - s["baseline"]) / tol
            streak_findings.append({
                "kpi": kpi,
                "tipo_finding": "streak",
                "direzione": s["tipo"],
                "start_match_seq": s["start_match_seq"],
                "end_match_seq": s["end_match_seq"],
                "length": s["length"],
                "mean_value": s["mean_value"],
                "baseline": s["baseline"],
                "score": s["length"] * rel_magnitude,
            })

        shift = detect_level_shift(match_seq, values, min_length)
        if shift is not None and abs(shift["delta"]) > tol:
            # stesso motivo di normalizzazione su tol invece che su prima_media
            # (che può essere 0) spiegato sopra per gli streak — senza
            # moltiplicatore di ampiezza: vedi il docstring qui sopra sul
            # perché non risolve il problema di scala, risolto invece
            # separando i due ranking (_select_balanced).
            rel_delta = abs(shift["delta"]) / tol
            lin = compute_linear_trend(match_seq, values)
            shift_findings.append({
                "kpi": kpi,
                "tipo_finding": "cambio_livello",
                "direzione": "positivo" if shift["delta"] * direction > 0 else "negativo",
                **shift,
                "slope": lin["slope"] if lin else None,
                "score": rel_delta,
            })

    return _select_balanced(streak_findings, shift_findings, top_n)


def _select_balanced(streak_findings, shift_findings, top_n):
    """
    Combina due pool di finding (già con uno 'score' proprio, non
    confrontabile direttamente tra pool — vedi compute_player_findings) in
    una lista di massimo `top_n`, riservando metà dei posti a ciascun pool
    quando entrambi hanno abbastanza candidati; se un pool ne ha meno,
    l'altro riempie gli slot residui. Il risultato finale è ordinato per
    score solo all'interno del proprio tipo (non tra i due tipi, essendo
    scale diverse) — prima tutti gli streak scelti (per score), poi tutti
    i cambio_livello scelti (per score).
    """
    streak_findings = sorted(streak_findings, key=lambda f: f["score"], reverse=True)
    shift_findings = sorted(shift_findings, key=lambda f: f["score"], reverse=True)

    half = (top_n + 1) // 2
    n_streak = min(half, len(streak_findings))
    n_shift = min(top_n - n_streak, len(shift_findings))
    # se i cambio_livello disponibili non bastano a riempire gli slot
    # residui, gli streak avanzati (oltre 'half') recuperano lo spazio.
    n_streak = min(top_n - n_shift, len(streak_findings))
    return streak_findings[:n_streak] + shift_findings[:n_shift]


def _cognome_from_player_label(player_label):
    """'Cepparano 11' -> 'Cepparano' (formato 'Giocatore' di leg_comparison,
    Cognome + ' ' + Numero)."""
    return re.sub(r"\s+\d+$", "", str(player_label)).strip()


def _load_player_roles(season):
    """cognome -> ruolo più frequente nel foglio Presenze D (es. 'P', 'O',
    'M', 'L', 'C', ...). Ritorna {} se il foglio presenze non è disponibile
    per la stagione (nessuna esclusione per ruolo verrà applicata)."""
    try:
        presence_df = load_presence(season)
    except Exception:
        return {}
    with_ruolo = presence_df.dropna(subset=["ruolo"])
    if with_ruolo.empty:
        return {}
    roles = with_ruolo.groupby("cognome")["ruolo"].agg(lambda s: s.value_counts().idxmax())
    return roles.to_dict()


def _is_setter(ruolo):
    return str(ruolo).strip().upper() in SETTER_ROLE_CODES


def build_player_report_base(season="2025-2026", rec_vote=DEFAULT_REC_VOTE,
                              min_length=DEFAULT_MIN_LENGTH, tol_rel=DEFAULT_TOL_REL,
                              tol_std=DEFAULT_TOL_STD, min_points=DEFAULT_MIN_POINTS,
                              top_n=DEFAULT_TOP_N, min_volume_percent=DEFAULT_MIN_VOLUME_PERCENT, matches=None):
    """
    Costruisce, per ogni giocatore Decimo riconosciuto (player_identities.csv,
    non IGNORED), la base dati della pagella: finding di rendimento sui KPI
    curati (PAGELLA_KPIS, esclusa la famiglia attacco per i palleggiatori —
    vedi docstring di modulo) e presenze (totali e per tipo evento).

    `matches` (opzionale) evita di ricaricare gli Excel se già disponibili
    (es. da una cache condivisa nella dashboard, come per gli altri builder
    di src.leg_comparison).

    Ritorna (report, cognomi_presenze_non_in_registro):
    - report: {cognome: {"player_label", "ruolo", "findings", "presenze", "presenze_by_type"}}
    - cognomi_presenze_non_in_registro: giocatori nel foglio presenze ma
      assenti da player_identities.csv (vedi src.attendance.filter_known_players)
      — segnalati, non scartati silenziosamente.
    """
    if matches is None:
        matches = load_all_matches(season)
    _, player_df = build_comparison_dataset(season, matches=matches)
    _, player_so_df = build_attacco_so_dataset(season, rec_vote=rec_vote, matches=matches)
    # kpi_df NON è filtrato su PAGELLA_KPIS qui: deve contenere anche i KPI
    # 'Tot' delle famiglie percentuali, usati da compute_player_findings per
    # il filtro di significatività (vedi PERCENT_KPI_VOLUME) — il parametro
    # `kpis` di compute_player_findings limita comunque i finding generati.
    kpi_df = pd.concat([player_df, player_so_df], ignore_index=True)

    roles = _load_player_roles(season)

    findings_by_cognome = {}
    for player_label, g in kpi_df.groupby("player"):
        cognome = _cognome_from_player_label(player_label)
        ruolo = roles.get(cognome)
        kpis = PAGELLA_OTHER_KPIS if _is_setter(ruolo) else PAGELLA_KPIS
        findings_by_cognome[cognome] = {
            "player_label": player_label,
            "ruolo": ruolo,
            "findings": compute_player_findings(
                g, kpis=kpis, min_length=min_length, tol_rel=tol_rel, tol_std=tol_std,
                min_points=min_points, top_n=top_n, min_volume_percent=min_volume_percent,
            ),
        }

    presence_df = load_presence(season)
    presence_known, not_in_registry = filter_known_players(presence_df)
    attendance = compute_attendance_summary(presence_known, event_types=DEFAULT_EVENT_TYPES)
    attendance_by_type = compute_attendance_summary_by_type(presence_known, event_types=DEFAULT_EVENT_TYPES)
    attendance_by_cognome = attendance.set_index("cognome").to_dict(orient="index")

    report = {}
    for cognome in set(findings_by_cognome) | set(attendance_by_cognome):
        by_type_rows = attendance_by_type[attendance_by_type["cognome"] == cognome].to_dict(orient="records")
        report[cognome] = {
            "player_label": findings_by_cognome.get(cognome, {}).get("player_label"),
            "ruolo": findings_by_cognome.get(cognome, {}).get("ruolo", roles.get(cognome)),
            "findings": findings_by_cognome.get(cognome, {}).get("findings", []),
            "presenze": attendance_by_cognome.get(cognome),
            "presenze_by_type": by_type_rows,
        }

    return report, not_in_registry
