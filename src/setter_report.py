# src/setter_report.py
"""
KPI di rendimento per il palleggiatore.

A differenza degli altri giocatori, un palleggiatore non si valuta sulla
propria efficienza di attacco (attacca poche volte a partita: dati non
significativi per il suo ruolo — vedi l'esclusione per ruolo 'P' in
src/player_report.py) ma sull'efficienza di attacco DEI SUOI ATTACCANTI,
nelle sole azioni in cui è ragionevole assumere che sia stato lui ad
alzare la palla. Richiesto esplicitamente dall'utente il 2026-08-16.

Riusa src/lineup.py (ricostruzione formazioni, validata su dati reali)
per determinare, istante per istante, quale dei palleggiatori è
effettivamente il palleggiatore IN CAMPO — necessario perché in questa
stagione due giocatori (Caranzetti, Licenziati) sono entrambi registrati
con ruolo 'P' nel foglio Presenze D: il ruolo da solo non basta a sapere
chi sta palleggiando in un dato momento di una data partita, perché:
  - a volte uno dei due gioca da libero (verificato: JVC playout
    ritorno, Licenziati fa solo ricezione/difesa — mai battuta, muro,
    attacco o alzata — mentre Caranzetti fa tutto il resto);
  - a volte entrano entrambi in campo, in ruoli diversi dal palleggiare
    (verificato: PQP andata, Licenziati serve/blocca/difende quanto
    Caranzetti nel Set 1, ma solo Caranzetti risulta "autoconfermato"
    come palleggiatore nei propri turni di battuta — fino a un cambio
    di titolarità rilevato a metà Set 2, con più sostituzioni ravvicinate
    che rendono l'esatto punto di cambio indeterminabile: quella
    finestra resta "incerta", non attribuita a nessuno dei due).

## Meccanismo — identify_active_setter

Le uniche righe dove "Posizione Giocatore" è affidabile come indicatore
di rotazione sono le battute (sempre "p1", per regola — vedi src/lineup.py).
Per queste, se il battitore È uno dei `setter_names` E "Posizione
Palleggiatore" == "p1" nella STESSA riga, è un evento di autoconferma
diretta: quel giocatore sta servendo proprio mentre occupa la posizione
che la colonna registra come "il palleggiatore" — quindi è lui il
palleggiatore in campo in quel momento (se fosse un altro giocatore a
palleggiare, "Posizione Palleggiatore" mostrerebbe una posizione diversa
da p1, dato che il vero palleggiatore non può essere lui stesso al
servizio nello stesso turno).

Tra due autoconferme consecutive con la STESSA identità, quell'identità
vale per tutto l'intervallo. Se differiscono, l'INTERO intervallo resta
"incerto" (None) — non si tenta di individuare il punto esatto del
cambio tramite le righe 'cambio configurazione': nella validazione (PQP
andata, Set 2) 4 sostituzioni ravvicinate cadevano nello stesso
intervallo, rendendo il punto esatto indeterminabile. Meglio un'ampia
zona onestamente "incerta" (esclusa dal calcolo) che un'attribuzione
azzardata.

In aggiunta, l'inizio di ogni set viene "seminato" incrociando
`reconstruct_starting_lineup` (src/lineup.py) con la "Posizione
Palleggiatore" della primissima riga del set: se lo slot risultante non
è tra gli `incerti` di quella ricostruzione, diventa un'autoconferma
aggiuntiva — copre i casi in cui il palleggiatore titolare non serve mai
per primo nel set.

**Sostituzione incrociata palleggiatore/opposto** (segnalata
esplicitamente dall'utente il 2026-08-16 e confermata sui dati reali):
quando il secondo palleggiatore entra al posto dell'opposto in campo e
contemporaneamente il secondo opposto entra al posto del palleggiatore,
"Posizione Palleggiatore" compie un salto NON di -1 (es. da p4 a p1),
perché il nuovo palleggiatore fisicamente entra nello slot che era
dell'opposto. Verificato sui dati 2025-2026: 19 transizioni anomale
(diverse da 0 o -1 mod 6) in tutta la stagione, 17 delle quali
esattamente p4→p1, e IN TUTTI E 19 I CASI coincidenti con una riga
'cambio configurazione' nelle vicinanze — pattern sistematico, non
rumore. Il meccanismo qui sopra gestisce questo caso correttamente senza
bisogno di adattamenti: non assume continuità della rotazione, si basa
solo sull'autoconferma diretta, quindi un salto discontinuo non lo
confonde (verificato concretamente: Decimo-Appio andata, Set 1, la
finestra attorno al salto resta "incerta" finché il nuovo palleggiatore
non si autoconferma al proprio turno di battuta successivo).

Nota dati: il Cognome va normalizzato (strip) prima del confronto — sui
dati reali, "Licenziati" compare in almeno un file con uno spazio finale
("Licenziati "), che romperebbe silenziosamente il confronto con
`setter_names` se non normalizzato.

## Tre famiglie separate, non sommate

Richiesto esplicitamente dall'utente il 2026-08-16: "Attacco Alzato" non
è un unico KPI ma tre famiglie DISTINTE, mai sommate insieme:
  - **R+#** (`SETTER_ATTACK_REC_VOTE_POS`, Side-Out dopo ricezione
    ottima/perfetta): `ATTACCO_ALZATO_POS_KPI_LABELS`.
  - **R!** (`SETTER_ATTACK_REC_VOTE_ESCL`, Side-Out dopo ricezione che
    non permette 1° tempo, ma la palla arriva comunque al
    palleggiatore): `ATTACCO_ALZATO_ESCL_KPI_LABELS`.
  - **FB** (dopo free ball, cioè una difesa Voto '!' — vedi
    separate_free_ball in src/efficiency.py): `ATTACCO_ALZATO_FB_KPI_LABELS`.
    Meno dati rispetto alle altre due famiglie: la codifica della free
    ball (difesa Voto '!') è stata introdotta dall'utente solo a partire
    da un certo punto della stagione 2025-2026 (data esatta non nota) —
    le partite precedenti a quell'introduzione risultano semplicemente
    senza dati per questa famiglia (buco, non uno zero), stesso
    trattamento del resto del progetto per i dati mancanti.
Esclude sempre ricezione '-' (voto incluso invece nel default SO usato
altrove nel progetto, es. src/leg_comparison.py) e il contrattacco
generico: in quei casi non è affidabile assumere che sia stato il
palleggiatore titolare ad alzare la palla (spesso è un'azione di
emergenza, alzata da chi capita — coerente con le righe Tipo=='alzata'
osservate sui dati reali: più frequenti nei liberi/difensori che
coprono un'emergenza che nel palleggiatore titolare stesso).
"""
import pandas as pd

from config.paths import ROOT, build_base_path, load_matches
from src.efficiency import calcola_efficienza, eff_from_calcola, separate_attack_types, SRV_POS, SRV_NEG
from src.leg_comparison import PLAYOUT_LABEL, get_opponent_order
from src.lineup import reconstruct_starting_lineup

DEFAULT_SETTER_NAMES = ("Caranzetti", "Licenziati")

# Stesso schema di ATTACCO_SO_KPI_LABELS/ATTACCO_FB_KPI_LABELS/CONTRATTACCO_KPI_LABELS
# in src/leg_comparison.py, qui per l'efficienza di attacco DEGLI ATTACCANTI
# alzati dal palleggiatore in campo (vedi compute_setter_attack_kpis) — non un
# KPI di attacco del palleggiatore stesso. Tre famiglie separate (mai sommate,
# vedi docstring di modulo). Dicitura "Eff. attacco alzato da R+#/R!/FB"
# (non "Attacco Alzato +#..."), richiesta esplicitamente dall'utente il
# 2026-08-16 per il solo KPI di efficienza (%) — i conteggi assoluti restano
# senza il prefisso "Eff.", non essendo percentuali.
ATTACCO_ALZATO_POS_KPI_LABELS = {
    "eff": "Eff. attacco alzato da R+#",
    "tot": "Attacco alzato da R+# Tot",
    "punti": "Attacco alzato da R+# # (punti)",
    "errori": "Attacco alzato da R+# = (errori)",
    "murati": "Attacco alzato da R+# / (murati)",
}
ATTACCO_ALZATO_ESCL_KPI_LABELS = {
    "eff": "Eff. attacco alzato da R!",
    "tot": "Attacco alzato da R! Tot",
    "punti": "Attacco alzato da R! # (punti)",
    "errori": "Attacco alzato da R! = (errori)",
    "murati": "Attacco alzato da R! / (murati)",
}
ATTACCO_ALZATO_FB_KPI_LABELS = {
    "eff": "Eff. attacco alzato da FB",
    "tot": "Attacco alzato da FB Tot",
    "punti": "Attacco alzato da FB # (punti)",
    "errori": "Attacco alzato da FB = (errori)",
    "murati": "Attacco alzato da FB / (murati)",
}

# Rec_vote delle due famiglie Side-Out — vedi docstring di modulo. Esclude
# sempre '-' (voto incluso invece nel default SO usato altrove nel progetto,
# es. src/leg_comparison.py). Richiesto esplicitamente dall'utente, 2026-08-16.
# La famiglia FB non ha un rec_vote proprio: separate_attack_types la
# classifica in base alla riga precedente (difesa Voto '!'), indipendente
# dal rec_vote passato — vedi compute_setter_attack_kpis.
SETTER_ATTACK_REC_VOTE_POS = ("+", "#")
SETTER_ATTACK_REC_VOTE_ESCL = ("!",)


def _normalize_cognome(series):
    return series.astype(str).str.strip()


def identify_active_setter(df_match, setter_names=DEFAULT_SETTER_NAMES):
    """
    Per ogni riga di `df_match` (dataframe grezzo di UNA partita, non
    filtrato — vedi src.lineup.load_match_for_lineup), stabilisce quale
    tra `setter_names` è il palleggiatore in campo in quel momento.

    Ritorna una pd.Series allineata all'indice originale di `df_match`:
    il cognome del palleggiatore in campo, o None se non determinabile
    con sufficiente sicurezza (vedi meccanismo nel docstring del modulo).
    """
    original_index = df_match.index
    d = df_match.reset_index(drop=True).copy()
    d["Cognome"] = _normalize_cognome(d["Cognome"])
    setter_names = set(setter_names)

    conferme = []
    for idx, row in d.iterrows():
        if row["Tipo"] == "battuta" and row["Cognome"] in setter_names and row["Posizione Palleggiatore"] == "p1":
            conferme.append((idx, row["Cognome"]))

    for numero_set in sorted(d["Numero Set"].dropna().unique()):
        try:
            ricostruito = reconstruct_starting_lineup(d, numero_set)
        except ValueError:
            continue
        set_rows = d[d["Numero Set"] == numero_set]
        prima_riga = set_rows.iloc[0]
        slot_iniziale = prima_riga["Posizione Palleggiatore"]
        cognome_iniziale = ricostruito["lineup"].get(slot_iniziale)
        if cognome_iniziale in setter_names and slot_iniziale not in ricostruito["incerti"]:
            conferme.append((set_rows.index[0], cognome_iniziale))

    conferme = sorted(set(conferme), key=lambda t: t[0])

    assegnazione = pd.Series([None] * len(d), index=d.index, dtype=object)
    for i, (idx_a, cog_a) in enumerate(conferme):
        idx_b, cog_b = conferme[i + 1] if i + 1 < len(conferme) else (len(d), None)
        if cog_b is None or cog_a == cog_b:
            assegnazione.loc[idx_a:idx_b - 1] = cog_a

    assegnazione.index = original_index
    return assegnazione


def _attack_kpis_from_so_df(so_df, setter_in_campo, setter_names):
    """Helper: dato un so_df già filtrato su UN SOLO rec_vote (una delle due
    famiglie) e la Series setter_in_campo allineata al dataframe completo,
    calcola {cognome: {"efficienza", "tot", "punti", "errori", "murati"}}."""
    in_campo = setter_in_campo.reindex(so_df.index)
    risultati = {}
    for cognome in setter_names:
        sub = so_df[in_campo == cognome]
        tot = len(sub)
        risultati[cognome] = {
            "efficienza": eff_from_calcola(sub, tipo_val="attacco", pos=["#"], neg=["=", "/"]) if tot > 0 else None,
            "tot": tot,
            "punti": int((sub["Voto"] == "#").sum()),
            "errori": int((sub["Voto"] == "=").sum()),
            "murati": int((sub["Voto"] == "/").sum()),
        }
    return risultati


def compute_setter_attack_kpis(df_match, setter_names=DEFAULT_SETTER_NAMES):
    """
    Per ciascun palleggiatore in `setter_names`, TRE famiglie separate
    (mai sommate, vedi docstring di modulo) di efficienza di attacco DEI
    SUOI ATTACCANTI (non la sua): "pos" (Side-Out dopo ricezione +/#),
    "escl" (Side-Out dopo ricezione !) e "fb" (dopo free ball) — tutte
    solo sulle azioni avvenute mentre risultava lui il palleggiatore in
    campo (identify_active_setter). Esclude sempre contrattacco generico,
    ricezione '-' e le azioni con "palleggiatore in campo" non determinabile.

    separate_attack_types classifica la free ball in base alla riga
    precedente (difesa Voto '!'), indipendentemente dal rec_vote passato:
    basta quindi prendere il suo secondo valore di ritorno da UNA delle
    due chiamate già necessarie per "pos"/"escl" (freeball_df è identico
    in entrambe), non serve una terza chiamata a separate_attack_types.

    Ritorna {cognome: {"pos": {...}, "escl": {...}, "fb": {...}}},
    ciascuna con le chiavi {"efficienza": float|None, "tot": int, "punti":
    int, "errori": int, "murati": int} — efficienza None se tot == 0
    (nessun dato, non uno zero).
    """
    d = df_match.copy()
    d["Cognome"] = _normalize_cognome(d["Cognome"])
    assegnazione = identify_active_setter(d, setter_names)

    so_pos_df, freeball_df, _ = separate_attack_types(d, rec_vote=SETTER_ATTACK_REC_VOTE_POS)
    so_escl_df, _, _ = separate_attack_types(d, rec_vote=SETTER_ATTACK_REC_VOTE_ESCL)

    pos_kpis = _attack_kpis_from_so_df(so_pos_df, assegnazione, setter_names)
    escl_kpis = _attack_kpis_from_so_df(so_escl_df, assegnazione, setter_names)
    fb_kpis = _attack_kpis_from_so_df(freeball_df, assegnazione, setter_names)

    return {
        cognome: {"pos": pos_kpis[cognome], "escl": escl_kpis[cognome], "fb": fb_kpis[cognome]}
        for cognome in setter_names
    }


def compute_setter_battuta_kpis(df_match, setter_names=DEFAULT_SETTER_NAMES):
    """
    Battuta%, # (ace), = (errori) per ciascun palleggiatore in
    `setter_names` — come per qualunque altro giocatore non libero: la
    battuta è sempre attribuibile direttamente al Cognome della riga, non
    serve identify_active_setter.
    """
    d = df_match.copy()
    d["Cognome"] = _normalize_cognome(d["Cognome"])

    risultati = {}
    for cognome in setter_names:
        sub = d[(d["Tipo"] == "battuta") & (d["Cognome"] == cognome)]
        tot = len(sub)
        risultati[cognome] = {
            "efficienza": eff_from_calcola(sub, tipo_val="battuta", pos=SRV_POS, neg=SRV_NEG) if tot > 0 else None,
            "tot": tot,
            "ace": int((sub["Voto"] == "#").sum()),
            "errori": int((sub["Voto"] == "=").sum()),
        }
    return risultati


def _load_setter_player_labels(setter_names):
    """cognome -> 'Cognome Numero' (numero ABITUALE da player_identities.csv,
    stesso formato 'Giocatore' usato in tutta la pipeline esistente — vedi
    src.leg_comparison._normalize_and_filter_players) — necessario per far
    combaciare 'player' con le righe già prodotte da build_comparison_dataset
    per Battuta/Ricezione/ecc. degli stessi due giocatori."""
    identities = pd.read_csv(ROOT / "config" / "player_identities.csv")
    decimo = identities[identities["team"] == "Decimo"]
    out = {}
    for cognome in setter_names:
        match = decimo[decimo["cognome"] == cognome]
        if not match.empty:
            out[cognome] = f"{cognome} {int(match.iloc[0]['numero'])}"
    return out


def build_setter_kpi_dataset(season="2025-2026", setter_names=DEFAULT_SETTER_NAMES, matches=None):
    """
    Tabella tidy (stesso formato di build_comparison_dataset: colonne
    [opponent, leg, giornata, x_label, match_seq, player, kpi, value]) per le
    TRE famiglie di "Attacco Alzato" (ATTACCO_ALZATO_POS_KPI_LABELS/
    ATTACCO_ALZATO_ESCL_KPI_LABELS/ATTACCO_ALZATO_FB_KPI_LABELS, mai sommate
    — vedi docstring di modulo) di ciascun palleggiatore in `setter_names` —
    l'efficienza di attacco DEI SUOI ATTACCANTI mentre lui risultava il
    palleggiatore in campo (vedi identify_active_setter/compute_setter_attack_kpis).

    A differenza degli altri build_*_dataset del progetto, qui bisogna
    ricaricare ogni Excel GREZZO (non filtrato/normalizzato) — il `matches`
    di src.leg_comparison.load_all_matches non è riusabile qui: il suo `df`
    è già filtrato sui giocatori riconosciuti (vedi src.lineup e docstring
    di modulo), e userlo romperebbe la ricostruzione formazioni/
    identificazione palleggiatore. `matches` qui (opzionale) accetta invece
    una lista di dict {opponent, leg, path} già risolti da config/matches.csv
    (evita solo di rileggere quel CSV, non gli Excel).
    """
    if matches is None:
        all_matches = load_matches()
        season_matches = all_matches[(all_matches["season"] == season) & (all_matches["active"] == 1)]
        matches = season_matches[["opponent", "leg", "path"]].to_dict(orient="records")

    base = build_base_path(season=season)
    opponent_order = get_opponent_order(season)
    n_opponents = len(opponent_order)
    player_labels = _load_setter_player_labels(setter_names)

    rows = []
    for match in matches:
        df = pd.read_excel(base / match["path"], skiprows=1)

        leg = match["leg"]
        try:
            giornata = opponent_order.index(match["opponent"])
        except ValueError:
            giornata = None
        if leg == "A":
            x_label, match_seq = match["opponent"], giornata
        elif leg == "R":
            x_label, match_seq = match["opponent"], n_opponents + giornata
        elif leg == "POA":
            x_label, match_seq = PLAYOUT_LABEL, 2 * n_opponents
        elif leg == "POR":
            x_label, match_seq = PLAYOUT_LABEL, 2 * n_opponents + 1
        else:
            x_label, match_seq = match["opponent"], None
        base_cols = {
            "opponent": match["opponent"], "leg": leg, "giornata": giornata,
            "x_label": x_label, "match_seq": match_seq,
        }

        attacco_kpis = compute_setter_attack_kpis(df, setter_names)
        for cognome in setter_names:
            player_label = player_labels.get(cognome)
            if player_label is None:
                continue
            famiglie = (
                ("pos", ATTACCO_ALZATO_POS_KPI_LABELS),
                ("escl", ATTACCO_ALZATO_ESCL_KPI_LABELS),
                ("fb", ATTACCO_ALZATO_FB_KPI_LABELS),
            )
            for famiglia, labels in famiglie:
                kpis = attacco_kpis[cognome][famiglia]
                if kpis["tot"] == 0:
                    continue  # nessun dato: buco, non uno zero (stesso trattamento del resto del progetto)
                values = {
                    labels["eff"]: kpis["efficienza"],
                    labels["tot"]: kpis["tot"],
                    labels["punti"]: kpis["punti"],
                    labels["errori"]: kpis["errori"],
                    labels["murati"]: kpis["murati"],
                }
                for kpi, value in values.items():
                    rows.append({**base_cols, "player": player_label, "kpi": kpi, "value": value})

    return pd.DataFrame(rows)
