"""
Report di sintesi stagionale per giocatore — richiesto esplicitamente
dall'utente il 2026-08-14: non i "finding" locali di src/player_report.py
(streak/cambio livello, periodi puntuali nel corso della stagione), ma una
vista d'insieme sul rendimento stagionale con indicatori di massima per
ruolo, punteggio composito di partita, migliore/peggiore partita, punti di
forza/debolezza — tutti con numeri a supporto.

Ruoli e KPI trattati (curatela concordata con l'utente il 2026-08-14):
  - Libero (L): Ricezione% (unico indicatore).
  - Martello (M, e ibridi 'M/L'/'M/O'/'M/O/L' — profilo più inclusivo, i
    fondamentali non giocati restano senza dati per i filtri di volume già
    in uso): Ricezione%; Attacco% generale (poi SO/FB/Contrattacco separati
    se c'è qualcosa di notevole) con # punti/= errori// murati/+ mette in
    difficoltà/- regalato; Muro # e Muro = (mani-fuori subite); Battuta con
    = errori/# ace/{+,!,/} mette in difficoltà/- conservativa.
  - Centrale (C): Muro # e Muro + (mani-out che permettono il contrattacco)
    in primo piano, confrontati con i compagni centrali; Attacco% (aggregato
    stagionale), anch'esso confrontato con i compagni centrali — "tanti
    attacchi ma efficienza bassa" è il segnale di problema esplicito
    dell'utente.
  - Opposto (O): Attacco# (punti) in primo piano — enfasi sui punti fatti
    più che sugli errori (l'utente accetta un tasso di errore più alto per
    l'opposto), confrontato con Martelli+Opposto insieme (nella rosa 2025-2026
    c'è un solo opposto, il confronto solo-ruolo sarebbe vacuo). Muro
    comunque rilevante.
  - Palleggiatore (P): rimandato ("discorso a parte", non ancora definito).

Punteggio composito di partita ("efficienza totale, apporto positivo alla
squadra"): punti fatti (Battuta # ace + Muro # punti + Attacco # punti)
meno errori fatti (Attacco = errori + Battuta = errori + Muro / errore —
NOTA: l'errore di muro usa convenzionalmente il voto '/', non '=' — vedi
find_errors in src/efficiency.py; 'Muro = mani-fuori subite' è un esito
negativo distinto, non incluso qui su indicazione esplicita dell'utente).

Punti di forza/debolezza, soglie concordate esplicitamente con l'utente:
  - Attacco SO% (aggregato stagionale) < 20% -> da migliorare.
  - Contrattacco% (aggregato stagionale) < 0% -> da migliorare (il
    contrattacco può fisiologicamente stare più basso del SO, ma sotto
    zero è comunque un problema — nessuna soglia intermedia è stata data).
  - Media errori in battuta per partita > 2 -> da migliorare (l'efficienza
    di battuta resta sempre bassa in assoluto, gli ace sono rari: non è un
    buon indicatore da solo, si giudica sul conteggio errori).
  - Confronto con i compagni di ruolo (o Martelli+Opposto insieme per
    l'attacco degli attaccanti di banda) su: Muro # e Muro + per i
    centrali, Attacco% per i centrali, Attacco # (punti) per martelli/opposto.
    Sopra la media di riferimento -> punto di forza; sotto -> punto debole.

Best/worst match: per i 6 KPI che generano punti/errori nel punteggio
composito, la singola partita col valore più alto e quella col valore più
basso, riportate solo se lo scarto dalla mediana stagionale del giocatore
supera la stessa tolleranza di src.player_report (altrimenti non è
notevole, solo il min/max di una serie comunque piatta) — più la
migliore/peggiore partita per il punteggio composito stesso.
"""
import numpy as np
import pandas as pd

from src.efficiency import calcola_efficienza
from src.leg_comparison import (
    ATTACCO_FB_KPI_LABELS,
    ATTACCO_SO_KPI_LABELS,
    CONTRATTACCO_KPI_LABELS,
    DEFAULT_REC_VOTE,
    EFF_KPI_LABELS,
    TOT_KPI_LABELS,
    build_attacco_so_dataset,
    build_comparison_dataset,
    load_all_matches,
)
from src.player_report import (
    DEFAULT_MIN_POINTS,
    DEFAULT_TOL_REL,
    DEFAULT_TOL_STD,
    KPI_DIRECTION,
    SETTER_ROLE_CODES,
    _cognome_from_player_label,
    _compute_tolerance,
    _load_player_roles,
)

ROLE_LIBERO, ROLE_CENTRALE, ROLE_OPPOSTO, ROLE_MARTELLO, ROLE_PALLEGGIATORE = "L", "C", "O", "M", "P"

# Etichette per i conteggi grezzi non già coperti da ABS_KPI_DEFS in
# leg_comparison.py (quella lista copre solo i voti usati dall'efficienza
# standard) — vedi docstring di modulo.
MURO_ERRORE_KPI = "Muro / (errore)"
MURO_PIU_KPI = "Muro + (mani-out che permettono il contrattacco)"
ATTACCO_PLUS_KPI = "Attacco + (mette in difficoltà)"
ATTACCO_MINUS_KPI = "Attacco - (regalato)"
BATTUTA_DIFFICOLTA_KPI = "Battuta +/!// (mette in difficoltà)"
BATTUTA_CONSERVATIVA_KPI = "Battuta - (conservativa)"

# (tipo, [voti da sommare], etichetta) per _build_raw_voto_dataset.
EXTRA_VOTO_SPECS = [
    ("muro", ["/"], MURO_ERRORE_KPI),
    ("muro", ["+"], MURO_PIU_KPI),
    ("attacco", ["+"], ATTACCO_PLUS_KPI),
    ("attacco", ["-"], ATTACCO_MINUS_KPI),
    ("battuta", ["+", "!", "/"], BATTUTA_DIFFICOLTA_KPI),
    ("battuta", ["-"], BATTUTA_CONSERVATIVA_KPI),
]

# Punteggio composito di partita — vedi docstring di modulo.
COMPOSITE_POINT_KPIS = ["Battuta # (ace)", "Muro # (punti)", "Attacco # (punti)"]
COMPOSITE_ERROR_KPIS = ["Attacco = (errori)", "Battuta = (errori)", MURO_ERRORE_KPI]
NOTABLE_MATCH_KPIS = COMPOSITE_POINT_KPIS + COMPOSITE_ERROR_KPIS

# KPI_DIRECTION di src.player_report non contiene i nuovi conteggi extra né
# i conteggi assoluti generici di attacco (esclusi da PAGELLA_KPIS lì, ma
# usati qui in ROLE_COUNT_KPIS) — completato qui. Serve a sapere se un
# valore alto è un bene o un male (es. errori: alto = peggiore), usato sia
# da find_notable_matches sia da _compute_median_targets (quest'ultimo:
# bug corretto il 2026-08-14 — "sotto la mediana" veniva segnalato come
# obiettivo anche per i KPI dove stare sotto è un bene, es. errori,
# segnalando erroneamente un giocatore con MENO errori della mediana).
EXTRA_KPI_DIRECTION = {
    MURO_ERRORE_KPI: -1,
    MURO_PIU_KPI: +1,
    "Muro = (mani-fuori subite)": -1,
    "Attacco # (punti)": +1,
    "Attacco = (errori)": -1,
    "Attacco / (murati)": -1,
    ATTACCO_PLUS_KPI: +1,
    ATTACCO_MINUS_KPI: -1,
    BATTUTA_DIFFICOLTA_KPI: +1,
    BATTUTA_CONSERVATIVA_KPI: 0,  # né bene né male di per sé: informativo (stile di battuta), escluso dagli obiettivi
}


def _count_kpi_direction(kpi):
    """+1/-1/0 per un KPI di conteggio — unisce KPI_DIRECTION (src.player_report,
    KPI già coperti da PAGELLA_KPIS) e EXTRA_KPI_DIRECTION (i conteggi
    aggiuntivi di questo modulo)."""
    if kpi in KPI_DIRECTION:
        return KPI_DIRECTION[kpi]
    return EXTRA_KPI_DIRECTION.get(kpi, +1)


def _role_bucket(ruolo):
    """Risolve il codice ruolo grezzo (colonna Ruolo del foglio Presenze D:
    P/O/L/C/M, o ibridi 'M/L'/'M/O'/'M/O/L') nel bucket usato dal report.
    Gli ibridi ricadono sul profilo 'martello' (il più inclusivo)."""
    r = str(ruolo).strip().upper()
    if r in SETTER_ROLE_CODES:
        return ROLE_PALLEGGIATORE
    if r == "L":
        return ROLE_LIBERO
    if r == "C":
        return ROLE_CENTRALE
    if r == "O":
        return ROLE_OPPOSTO
    return ROLE_MARTELLO


def _build_raw_voto_dataset(matches, specs=EXTRA_VOTO_SPECS):
    """
    Righe tidy [match_seq, player, kpi, value]: per ciascuna (tipo, voti,
    etichetta) in `specs`, il conteggio per giocatore/partita di quanti voti
    in `voti` occorrono su quel `tipo` — permette di sommare più voti grezzi
    sotto un'unica etichetta (es. Battuta +/!// "mette in difficoltà").
    Questi KPI non fanno parte di ABS_KPI_DEFS/ALL_KPIS (non sono tra i 32
    KPI della dashboard esistente): calcolati appositamente per questo report.
    """
    rows = []
    for m in matches:
        df = m["df"]
        for tipo, voti, label in specs:
            sub = df[df["Tipo"] == tipo]
            if sub.empty:
                continue
            for player, player_df in sub.groupby("Giocatore"):
                voto_counts = player_df["Voto"].value_counts()
                value = int(sum(voto_counts.get(v, 0) for v in voti))
                rows.append({"match_seq": m["match_seq"], "player": player, "kpi": label, "value": value})
    return pd.DataFrame(rows, columns=["match_seq", "player", "kpi", "value"])


def compute_composite_scores(kpi_df):
    """
    Punteggio composito di partita per giocatore — vedi docstring di modulo.
    kpi_df deve includere anche i KPI di COMPOSITE_ERROR_KPIS (incluso
    MURO_ERRORE_KPI, da _build_raw_voto_dataset).

    Ritorna un DataFrame [player, match_seq, punti, errori, netto]: una riga
    per ogni (player, match_seq) in cui il giocatore ha almeno una riga in
    kpi_df (ha giocato quella partita) — netto=0 se non ha prodotto nessuna
    delle azioni del punteggio composito, non un buco: ha comunque giocato.
    """
    played = kpi_df[["player", "match_seq"]].drop_duplicates()
    sub = kpi_df[kpi_df["kpi"].isin(COMPOSITE_POINT_KPIS + COMPOSITE_ERROR_KPIS)]
    pivot = sub.pivot_table(index=["player", "match_seq"], columns="kpi", values="value", aggfunc="sum", fill_value=0)
    for col in COMPOSITE_POINT_KPIS + COMPOSITE_ERROR_KPIS:
        if col not in pivot.columns:
            pivot[col] = 0
    pivot["punti"] = pivot[COMPOSITE_POINT_KPIS].sum(axis=1)
    pivot["errori"] = pivot[COMPOSITE_ERROR_KPIS].sum(axis=1)
    pivot["netto"] = pivot["punti"] - pivot["errori"]
    pivot = pivot.reset_index()[["player", "match_seq", "punti", "errori", "netto"]]

    out = played.merge(pivot, on=["player", "match_seq"], how="left")
    out[["punti", "errori", "netto"]] = out[["punti", "errori", "netto"]].fillna(0).astype(int)
    return out


def find_best_worst_composite_match(composite_df, player_label):
    """
    (migliore, peggiore) per il punteggio composito di un giocatore — dict
    con match_seq/punti/errori/netto. (None, None) se il giocatore non ha
    partite in composite_df, o se 'netto' non varia mai (es. un libero, che
    non contribuisce al punteggio composito: sarebbe sempre 0 per tutte le
    partite — migliore e peggiore coinciderebbero sulla prima riga per puro
    artefatto di idxmax/idxmin su valori tutti uguali, non un vero
    migliore/peggiore).
    """
    sub = composite_df[composite_df["player"] == player_label]
    if sub.empty or sub["netto"].nunique() <= 1:
        return None, None
    best = sub.loc[sub["netto"].idxmax()].to_dict()
    worst = sub.loc[sub["netto"].idxmin()].to_dict()
    return best, worst


def find_notable_matches(kpi_df, player_label, kpis=None, min_points=DEFAULT_MIN_POINTS,
                          tol_rel=DEFAULT_TOL_REL, tol_std=DEFAULT_TOL_STD):
    """
    Per ciascun KPI in `kpis` (default NOTABLE_MATCH_KPIS, i 6 che generano
    punti/errori nel punteggio composito), trova la singola partita col
    valore più notevole in senso migliorativo e quella in senso peggiorativo
    — riportate solo se lo scarto dalla mediana stagionale del giocatore per
    quel KPI supera la tolleranza (altrimenti è solo il min/max di una serie
    comunque piatta, non un vero notable). La direzione (alto=meglio o
    basso=meglio) segue KPI_DIRECTION/EXTRA_KPI_DIRECTION.
    """
    kpis = kpis if kpis is not None else NOTABLE_MATCH_KPIS
    notable = []
    for kpi in kpis:
        direction = KPI_DIRECTION.get(kpi, EXTRA_KPI_DIRECTION.get(kpi))
        if not direction:
            continue
        g = kpi_df[(kpi_df["player"] == player_label) & (kpi_df["kpi"] == kpi)].sort_values("match_seq")
        values = g["value"].to_numpy(dtype=float)
        match_seq = g["match_seq"].to_numpy()
        if len(values) < min_points:
            continue

        baseline = float(np.median(values))
        tol = _compute_tolerance(values, baseline, tol_rel, tol_std)
        i_max, i_min = int(np.argmax(values)), int(np.argmin(values))
        i_good, i_bad = (i_max, i_min) if direction > 0 else (i_min, i_max)

        if abs(values[i_good] - baseline) > tol:
            notable.append({
                "kpi": kpi, "tipo": "migliore", "match_seq": int(match_seq[i_good]),
                "value": float(values[i_good]), "baseline": baseline,
            })
        if abs(values[i_bad] - baseline) > tol:
            notable.append({
                "kpi": kpi, "tipo": "peggiore", "match_seq": int(match_seq[i_bad]),
                "value": float(values[i_bad]), "baseline": baseline,
            })
    return notable


# ----------------------------------------------------------------------------
# Aggregati stagionali — curatela per ruolo (vedi docstring di modulo).
# ----------------------------------------------------------------------------

# ("chiave interna", kpi efficienza, kpi Tot corrispondente).
ROLE_EFFICIENCY_FAMILIES = {
    ROLE_LIBERO: [
        ("ricezione", EFF_KPI_LABELS["ricezione"], TOT_KPI_LABELS["ricezione"]),
    ],
    ROLE_MARTELLO: [
        ("ricezione", EFF_KPI_LABELS["ricezione"], TOT_KPI_LABELS["ricezione"]),
        ("attacco", EFF_KPI_LABELS["attacco"], TOT_KPI_LABELS["attacco"]),
        ("attacco_so", ATTACCO_SO_KPI_LABELS["eff"], ATTACCO_SO_KPI_LABELS["tot"]),
        ("attacco_fb", ATTACCO_FB_KPI_LABELS["eff"], ATTACCO_FB_KPI_LABELS["tot"]),
        ("contrattacco", CONTRATTACCO_KPI_LABELS["eff"], CONTRATTACCO_KPI_LABELS["tot"]),
        ("battuta", EFF_KPI_LABELS["battuta"], TOT_KPI_LABELS["battuta"]),
    ],
    ROLE_CENTRALE: [
        ("attacco", EFF_KPI_LABELS["attacco"], TOT_KPI_LABELS["attacco"]),
        ("attacco_so", ATTACCO_SO_KPI_LABELS["eff"], ATTACCO_SO_KPI_LABELS["tot"]),
        ("attacco_fb", ATTACCO_FB_KPI_LABELS["eff"], ATTACCO_FB_KPI_LABELS["tot"]),
        ("contrattacco", CONTRATTACCO_KPI_LABELS["eff"], CONTRATTACCO_KPI_LABELS["tot"]),
    ],
    ROLE_OPPOSTO: [
        ("attacco", EFF_KPI_LABELS["attacco"], TOT_KPI_LABELS["attacco"]),
        ("attacco_so", ATTACCO_SO_KPI_LABELS["eff"], ATTACCO_SO_KPI_LABELS["tot"]),
        ("attacco_fb", ATTACCO_FB_KPI_LABELS["eff"], ATTACCO_FB_KPI_LABELS["tot"]),
        ("contrattacco", CONTRATTACCO_KPI_LABELS["eff"], CONTRATTACCO_KPI_LABELS["tot"]),
    ],
    ROLE_PALLEGGIATORE: [],
}

ROLE_COUNT_KPIS = {
    ROLE_LIBERO: [],
    ROLE_MARTELLO: [
        "Battuta # (ace)", "Battuta = (errori)", BATTUTA_DIFFICOLTA_KPI, BATTUTA_CONSERVATIVA_KPI,
        "Muro # (punti)", "Muro = (mani-fuori subite)",
        "Attacco # (punti)", "Attacco = (errori)", "Attacco / (murati)", ATTACCO_PLUS_KPI, ATTACCO_MINUS_KPI,
    ],
    ROLE_CENTRALE: [
        "Muro # (punti)", MURO_PIU_KPI, "Muro = (mani-fuori subite)",
        "Attacco = (errori)", "Attacco / (murati)",
    ],
    ROLE_OPPOSTO: [
        "Attacco # (punti)", "Attacco = (errori)", "Attacco / (murati)", ATTACCO_PLUS_KPI, ATTACCO_MINUS_KPI,
        "Muro # (punti)", MURO_PIU_KPI,
    ],
    ROLE_PALLEGGIATORE: [],
}


def compute_season_aggregate_efficiency(kpi_df, player_label, eff_kpi, tot_kpi):
    """
    Efficienza aggregata di STAGIONE per (player, eff_kpi): non la media
    delle percentuali per partita (peserebbe ugualmente partite con volumi
    molto diversi), ma (somma pos-neg)/(somma tot) su tutta la stagione.
    Ricavata da valore_partita/100*tot_partita (= pos-neg di quella
    partita), sommata su tutte le partite — non servono i conteggi pos/neg
    grezzi, bastano le colonne '%'/'Tot' già in kpi_df.
    Ritorna (eff_aggregata_%, tot_stagione) — (None, 0) se il giocatore non
    ha mai giocato quel fondamentale.
    """
    eff_g = kpi_df[(kpi_df["player"] == player_label) & (kpi_df["kpi"] == eff_kpi)].set_index("match_seq")["value"]
    tot_g = kpi_df[(kpi_df["player"] == player_label) & (kpi_df["kpi"] == tot_kpi)].set_index("match_seq")["value"]
    common = eff_g.index.intersection(tot_g.index)
    if len(common) == 0:
        return None, 0
    season_tot = int(tot_g.loc[common].sum())
    if season_tot == 0:
        return None, 0
    pos_minus_neg = (eff_g.loc[common] / 100 * tot_g.loc[common]).sum()
    return float(pos_minus_neg / season_tot * 100), season_tot


def compute_per_match_average(kpi_df, player_label, kpi):
    """Media/somma/n-partite per un KPI di conteggio — (None, 0, 0) se il
    giocatore non ha mai avuto quel KPI (buco, coerente col resto del
    progetto: non uno zero implicito)."""
    g = kpi_df[(kpi_df["player"] == player_label) & (kpi_df["kpi"] == kpi)]["value"]
    if g.empty:
        return None, 0, 0
    return float(g.mean()), int(g.sum()), int(len(g))


# ----------------------------------------------------------------------------
# Punti di forza/debolezza — soglie assolute e confronto tra compagni di ruolo
# (vedi docstring di modulo per le motivazioni esplicite dell'utente).
# ----------------------------------------------------------------------------

WEAKNESS_ATTACCO_SO_THRESHOLD = 20.0  # %, sotto = da migliorare
WEAKNESS_CONTRATTACCO_THRESHOLD = 0.0  # %, sotto (negativo) = da migliorare
WEAKNESS_BATTUTA_ERRORI_THRESHOLD = 2.0  # errori/partita in media, sopra = da migliorare

# Scarto minimo (relativo alla media dei compagni) perché una differenza sia
# "notevole" e non rumore — stessa banda (15%) usata altrove nel progetto
# per coerenza, non una nuova soglia inventata ad hoc.
PEER_COMPARISON_TOL_REL = 0.15

# (chiave interna univoca, kpi count o "eff:<chiave famiglia>", bucket/i di
# ruolo su cui fare il confronto — "MO" = Martelli+Opposto insieme).
PEER_COMPARISON_SPECS = [
    ("Muro # (punti)", "count", (ROLE_CENTRALE,)),
    (MURO_PIU_KPI, "count", (ROLE_CENTRALE,)),
    ("attacco", "eff", (ROLE_CENTRALE,)),
    ("Attacco # (punti)", "count", (ROLE_MARTELLO, ROLE_OPPOSTO)),
]


def _absolute_threshold_flags(efficienze, conteggi):
    """Punti deboli da soglie assolute fisse — vedi docstring di modulo."""
    flags = []
    so_eff, so_tot = efficienze.get("attacco_so", (None, 0))
    if so_eff is not None and so_eff < WEAKNESS_ATTACCO_SO_THRESHOLD:
        flags.append({
            "tipo": "punto_debole", "area": "Attacco SO",
            "motivo": f"efficienza aggregata {so_eff:.1f}% su {so_tot} attacchi (soglia {WEAKNESS_ATTACCO_SO_THRESHOLD:.0f}%)",
        })
    ctr_eff, ctr_tot = efficienze.get("contrattacco", (None, 0))
    if ctr_eff is not None and ctr_eff < WEAKNESS_CONTRATTACCO_THRESHOLD:
        flags.append({
            "tipo": "punto_debole", "area": "Contrattacco",
            "motivo": f"efficienza aggregata negativa ({ctr_eff:.1f}% su {ctr_tot} attacchi)",
        })
    batt_errori = conteggi.get("Battuta = (errori)", (None, 0, 0))
    if batt_errori[0] is not None and batt_errori[0] > WEAKNESS_BATTUTA_ERRORI_THRESHOLD:
        flags.append({
            "tipo": "punto_debole", "area": "Battuta",
            "motivo": f"media {batt_errori[0]:.1f} errori/partita su {batt_errori[2]} partite (soglia {WEAKNESS_BATTUTA_ERRORI_THRESHOLD:.0f})",
        })
    return flags


def _compute_peer_averages(kpi_df, roles, efficienze_by_player):
    """
    Per ogni voce di PEER_COMPARISON_SPECS, la media del valore (aggregato
    stagionale) tra i giocatori dei bucket di ruolo indicati — usata da
    _peer_comparison_flags per confrontare ciascun giocatore con la media
    DEI COMPAGNI (esclude se stesso al momento del confronto, non qui).
    Ritorna {(kpi_o_chiave, tipo): {player_label: valore}}.
    """
    values_by_spec = {}
    for kpi_or_key, tipo, buckets in PEER_COMPARISON_SPECS:
        per_player = {}
        for player_label, ruolo in roles.items():
            if _role_bucket(ruolo) not in buckets:
                continue
            if tipo == "count":
                media, _, n = compute_per_match_average(kpi_df, player_label, kpi_or_key)
                if media is not None and n > 0:
                    per_player[player_label] = media
            else:  # "eff"
                eff_specs = {k: (e, t) for k, e, t in ROLE_EFFICIENCY_FAMILIES[_role_bucket(ruolo)]}
                if kpi_or_key not in eff_specs:
                    continue
                eff_kpi, tot_kpi = eff_specs[kpi_or_key]
                eff, tot = efficienze_by_player.get(player_label, {}).get(kpi_or_key, (None, 0))
                if eff is not None:
                    per_player[player_label] = eff
        values_by_spec[(kpi_or_key, tipo)] = per_player
    return values_by_spec


def _peer_comparison_flags(player_label, peer_values):
    """Punti di forza/debolezza dal confronto con la media dei compagni
    (esclude se stesso dalla media di riferimento) — vedi docstring di modulo."""
    flags = []
    for (kpi_or_key, tipo), per_player in peer_values.items():
        if player_label not in per_player:
            continue
        others = {p: v for p, v in per_player.items() if p != player_label}
        if len(others) < 1:
            continue  # nessun compagno di ruolo con cui confrontarsi
        peer_avg = sum(others.values()) / len(others)
        value = per_player[player_label]
        tol = max(abs(peer_avg) * PEER_COMPARISON_TOL_REL, 1e-9)
        label = kpi_or_key if tipo == "count" else f"Efficienza {kpi_or_key}"
        if value - peer_avg > tol:
            flags.append({
                "tipo": "punto_forza", "area": label,
                "motivo": f"{value:.1f} contro una media compagni di {peer_avg:.1f}",
            })
        elif peer_avg - value > tol:
            flags.append({
                "tipo": "punto_debole", "area": label,
                "motivo": f"{value:.1f} contro una media compagni di {peer_avg:.1f}",
            })
    return flags


def _median_peer_buckets(kind, kpi_or_key, own_bucket):
    """
    Gruppo di ruoli su cui calcolare la mediana di riferimento per un KPI,
    dato il ruolo del giocatore stesso — richiesto esplicitamente
    dall'utente il 2026-08-14 ("valori da migliorare quest'anno... tenersi
    sopra la mediana del kpi in esame"). Quando il ruolo del giocatore ha un
    solo titolare in rosa (Opposto, Libero nella stagione 2025-2026) si
    aggrega al ruolo più affine che condivide lo stesso fondamentale
    (Libero+Martello per la ricezione, entrambi ricevono; Martello+Opposto
    per attacco/muro, entrambi attaccano principalmente da banda) —
    altrimenti la mediana sarebbe calcolabile su un solo valore (se stesso),
    non un vero confronto. I centrali restano un gruppo a sé per
    attacco/muro (richiesto esplicitamente: la loro efficienza attesa non è
    confrontabile con quella dei martelli/opposto, ruolo diverso).
    """
    if kind == "eff":
        if kpi_or_key == "ricezione":
            return (ROLE_LIBERO, ROLE_MARTELLO)
        if kpi_or_key == "battuta":
            return (ROLE_MARTELLO,)  # unico ruolo con la battuta tracciata, per ora
        return (ROLE_CENTRALE,) if own_bucket == ROLE_CENTRALE else (ROLE_MARTELLO, ROLE_OPPOSTO)
    # kind == "count"
    if kpi_or_key.startswith("Muro") or kpi_or_key.startswith("Attacco"):
        return (ROLE_CENTRALE,) if own_bucket == ROLE_CENTRALE else (ROLE_MARTELLO, ROLE_OPPOSTO)
    if kpi_or_key.startswith("Battuta"):
        return (ROLE_MARTELLO,)  # unico ruolo con la battuta tracciata, per ora
    return (own_bucket,)


def _compute_median_targets(roles, efficienze_by_player, conteggi_by_player):
    """
    Per ogni giocatore e ciascun KPI del proprio ruolo (efficienza
    aggregata o conteggio medio/partita), calcola la mediana del gruppo di
    riferimento (vedi _median_peer_buckets, mediana calcolata INCLUDENDO il
    giocatore stesso — è un riferimento di squadra fisso, non "gli altri")
    e segnala come "valore da migliorare quest'anno" quelli dalla parte
    sbagliata della mediana (sotto, per i KPI dove alto=meglio; sopra, per
    quelli dove basso=meglio, es. gli errori). A differenza di
    punti_forza/punti_deboli (soglie assolute + confronto con la MEDIA dei
    compagni, con una banda di tolleranza), qui non c'è banda: qualunque
    scarto dalla mediana nel verso sbagliato è un obiettivo, per costruzione
    (in un gruppo di N, circa metà sarà sempre dalla parte "sbagliata" su un
    dato KPI — è la definizione stessa di un obiettivo di miglioramento, non
    un giudizio di "problema" come i punti deboli).

    Ritorna {player_label: [{"kpi", "tipo" ("eff"/"count"), "valore",
    "mediana", "direzione"}, ...]}.
    """
    targets_by_player = {label: [] for label in roles}
    for kind, source in (("eff", efficienze_by_player), ("count", conteggi_by_player)):
        # tutte le chiavi KPI viste in questo dizionario (efficienze o conteggi)
        all_keys = {k for values in source.values() for k in values}
        for kpi_or_key in all_keys:
            # i KPI 'eff' hanno sempre direzione +1 (efficienza più alta = meglio,
            # per ogni famiglia); i 'count' seguono _count_kpi_direction — i KPI
            # a direzione neutra (0, es. Battuta - conservativa) sono esclusi:
            # nessun verso "da migliorare" ha senso per uno stile, non una qualità.
            direction = +1 if kind == "eff" else _count_kpi_direction(kpi_or_key)
            if not direction:
                continue
            for player_label, ruolo in roles.items():
                own_bucket = _role_bucket(ruolo)
                own_value_entry = source.get(player_label, {}).get(kpi_or_key)
                own_value = own_value_entry[0] if own_value_entry else None
                if own_value is None:
                    continue
                peer_buckets = _median_peer_buckets(kind, kpi_or_key, own_bucket)
                group_values = [
                    source.get(label, {}).get(kpi_or_key, (None,))[0]
                    for label, r in roles.items() if _role_bucket(r) in peer_buckets
                ]
                group_values = [v for v in group_values if v is not None]
                if len(group_values) < 2:
                    continue  # nessun gruppo di riferimento significativo
                mediana = float(np.median(group_values))
                # "da migliorare" = dalla parte sbagliata della mediana nel verso
                # dato da 'direction' (per direction=-1, es. errori, vuol dire
                # SOPRA la mediana, non sotto — bug corretto il 2026-08-14: prima
                # veniva sempre confrontato come se un valore basso fosse sempre
                # da migliorare, segnalando erroneamente chi aveva MENO errori
                # della mediana come "da migliorare").
                if (mediana - own_value) * direction > 0:
                    targets_by_player[player_label].append({
                        "kpi": kpi_or_key, "tipo": kind, "valore": own_value,
                        "mediana": mediana, "direzione": direction,
                    })
    return targets_by_player


def build_player_season_report(season="2025-2026", rec_vote=DEFAULT_REC_VOTE, matches=None):
    """
    Costruisce, per ogni giocatore Decimo riconosciuto, il report di
    sintesi stagionale: aggregati per ruolo, punteggio composito di
    partita, migliore/peggiore partita (composito e per KPI notevoli),
    punti di forza/debolezza (soglie assolute + confronto con i compagni).

    Ritorna {cognome: {...}} — vedi le singole chiavi nel dict costruito
    in fondo alla funzione.
    """
    if matches is None:
        matches = load_all_matches(season)
    _, player_df = build_comparison_dataset(season, matches=matches)
    _, player_so_df = build_attacco_so_dataset(season, rec_vote=rec_vote, matches=matches)
    extra_df = _build_raw_voto_dataset(matches)
    kpi_df = pd.concat([player_df, player_so_df, extra_df], ignore_index=True)

    roles_by_cognome = _load_player_roles(season)
    player_labels = sorted(kpi_df["player"].unique())
    roles = {label: roles_by_cognome.get(_cognome_from_player_label(label)) for label in player_labels}

    composite_df = compute_composite_scores(kpi_df)

    efficienze_by_player = {}
    conteggi_by_player = {}
    for player_label in player_labels:
        bucket = _role_bucket(roles.get(player_label))
        efficienze_by_player[player_label] = {
            key: compute_season_aggregate_efficiency(kpi_df, player_label, eff_kpi, tot_kpi)
            for key, eff_kpi, tot_kpi in ROLE_EFFICIENCY_FAMILIES[bucket]
        }
        conteggi_by_player[player_label] = {
            kpi: compute_per_match_average(kpi_df, player_label, kpi)
            for kpi in ROLE_COUNT_KPIS[bucket]
        }

    peer_values = _compute_peer_averages(kpi_df, roles, efficienze_by_player)
    targets_by_player = _compute_median_targets(roles, efficienze_by_player, conteggi_by_player)

    report = {}
    for player_label in player_labels:
        cognome = _cognome_from_player_label(player_label)
        ruolo = roles.get(player_label)
        bucket = _role_bucket(ruolo)

        conteggi = conteggi_by_player[player_label]
        efficienze = efficienze_by_player[player_label]
        n_partite = int(kpi_df[kpi_df["player"] == player_label]["match_seq"].nunique())

        best_composite, worst_composite = find_best_worst_composite_match(composite_df, player_label)
        notevoli = find_notable_matches(kpi_df, player_label)

        punti_forza = []
        punti_deboli = _absolute_threshold_flags(efficienze, conteggi)
        for flag in _peer_comparison_flags(player_label, peer_values):
            (punti_forza if flag["tipo"] == "punto_forza" else punti_deboli).append(flag)

        report[cognome] = {
            "player_label": player_label,
            "ruolo": ruolo,
            "ruolo_bucket": bucket,
            "n_partite_giocate": n_partite,
            "efficienze": efficienze,
            "conteggi_medi_partita": conteggi,
            "composito_migliore": best_composite,
            "composito_peggiore": worst_composite,
            "partite_notevoli": notevoli,
            "punti_forza": punti_forza,
            "punti_deboli": punti_deboli,
            "obiettivi_prossima_stagione": targets_by_player.get(player_label, []),
        }

    return report
