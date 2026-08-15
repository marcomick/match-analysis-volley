# src/lineup.py
"""
Ricostruzione delle formazioni di partenza (P1..P6) del Decimo, set per
set, dai soli dati del log Excel di match analysis — senza bisogno del
referto ufficiale. Se un referto è disponibile resta la fonte più
attendibile per un confronto puntuale, ma l'algoritmo qui prescinde da
esso (richiesta esplicita dell'utente, 2026-08-15: "dobbiamo poter
prescindere dal referto... se c'è lo usiamo come fonte più attendibile,
altrimenti utilizziamo l'algoritmo").

Validato (2026-08-16) su 23 set attraverso 7 partite reali della stagione
2025-2026, incrociando con i referti ufficiali PDF (cartella Referti/ su
Google Drive): 21/23 set combacianti esattamente con la formazione da
referto (91%), 135/138 slot singoli corretti (98%). I soli 2 scarti sono
spiegati dallo stesso, identico limite noto (sostituzione pre-turno, vedi
sotto) — non da errori sparsi: la logica centrale (fuori da quel limite)
è risultata 100% corretta su tutto il campione.

IMPORTANTE — dataframe grezzo, non filtrato: a differenza degli altri
moduli (leg_comparison.py, player_report.py, ...), che escludono i
giocatori non riconosciuti/marcati IGNORED in config/player_identities.csv,
qui bisogna lavorare sul dataframe GREZZO (pd.read_excel(path,
skiprows=1), senza alcun filtro): un titolare può benissimo essere un
giocatore occasionale marcato IGNORED altrove (es. Martignoni, Amore —
verificato su dati reali: Martignoni titolare in Decimo-Appio andata Set1).
Filtrare i giocatori qui rimuoverebbe le sue righe di battuta e
corromperebbe la sequenza osservata per tutti gli slot successivi.

## Il meccanismo

- Il campo "Posizione Giocatore" per le righe Tipo=='battuta' è sempre
  "p1" (per regola: si serve solo da p1) — non porta informazione
  posizionale utile per la battuta. L'informazione utile è la SEQUENZA di
  CHI serve (colonna Cognome), non il valore di posizione.
- Il campo "Posizione Palleggiatore" traccia la posizione attuale del
  vero palleggiatore (in questa stagione: Caranzetti, ruolo 'P' — NON
  Moscetta, 'C', nonostante occupi spesso p1: pura coincidenza
  posizionale), e DECRESCE di 1 (mod 6, wraparound 6->1) a ogni cambio
  palla vinto dal Decimo — convenzione standard pallavolo (chi era in p2
  passa a servire in p1, chi era in p1 passa in p6, ...). Non usato
  direttamente da questo modulo (la sequenza dei battitori distinti basta
  da sola), ma è l'autoverifica concettuale dietro la validazione: quando
  Caranzetti stessa serve, "Posizione Palleggiatore" vale sempre "p1".
- Ordine ciclico fisso del sestetto in un set: la sequenza CRONOLOGICA
  delle righe 'battuta' di quel set, collassando i turni consecutivi con
  lo stesso Cognome in un solo elemento — ogni transizione tra elementi
  consecutivi rappresenta ESATTAMENTE una rotazione, indipendentemente da
  quanti punti "invisibili" l'avversario abbia segnato nel mezzo servendo
  (la rotazione del Decimo avanza solo quando il Decimo conquista un
  nuovo cambio palla, non ad ogni punto giocato).
- Sfasamento (offset) battuta/ricezione: chi ha vinto il primissimo punto
  del set determina se il 1° battitore osservato corrisponde a P1
  (Decimo ha servito per primo, offset=0) o a P2 (Decimo ha ricevuto per
  primo: un cambio palla = una rotazione è già avvenuta prima del primo
  turno di battuta Decimo, offset=1). In generale il k-esimo battitore
  osservato (1-indexed) corrisponde a P_{((k-1+offset) mod 6) + 1}.
- Determinare l'offset guardando solo il Tipo della primissima riga del
  set NON è robusto: se l'avversario sbaglia la propria battuta senza che
  il Decimo tocchi palla, non c'è nessuna riga 'ricezione' per quel punto,
  e la primissima riga loggata del set può già essere una 'battuta' del
  Decimo pur avendo l'avversario servito per primo. Per questo si guarda
  anche il punteggio (Punti Locali/Punti Ospiti, che rappresentano il
  punteggio PRIMA dell'esito di quella riga stessa): il Decimo ha servito
  il vero primo punto del set solo se la primissima riga è 'battuta' E il
  punteggio è 0-0 esatto; in ogni altro caso l'avversario ha servito per
  primo (offset=1).
- L'ordine ciclico va derivato DA CAPO per ogni set, mai assumendo
  continuità col set precedente: è normale/legale schierare una
  formazione di partenza diversa (anche stesso sestetto, ordine diverso)
  a ogni set — verificato: Set 1 e Set 2 di uno stesso match reale hanno
  ordini di rotazione diversi.

## Limite noto: sostituzione prima del primo turno di battuta dello slot

Se un titolare viene sostituito PRIMA che il suo slot arrivi mai a
servire nel set, l'algoritmo (basato solo su chi serve) assegna quello
slot al sostituto, non al vero titolare: non c'è un pattern consolidato
da rompere, perché lo slot non ha ancora mai servito. Confermato 2 volte
indipendenti nella validazione. Contrasto utile: una sostituzione
avvenuta DOPO che lo slot ha già servito almeno una volta non corrompe
nulla (il dizionario tiene la prima occorrenza per slot).

Rilevabile solo in modo euristico: la riga Tipo=='cambio configurazione'
marca il momento di una sostituzione ma non dice chi sostituisce chi.
`reconstruct_starting_lineup` segnala come "incerto" ogni slot il cui
giocatore compare per la prima volta nel set (qualunque azione, non solo
battuta) solo DOPO la prima riga 'cambio configurazione' del set — un
segnale, non una certezza: un titolare genuino ma poco coinvolto nelle
prime fasi del set potrebbe in teoria essere segnalato per errore: meglio
un falso "incerto" che un'assegnazione sbagliata data per buona.
"""
import pandas as pd

from config.paths import build_base_path

BATTUTA = "battuta"
CAMBIO_CONFIGURAZIONE = "cambio configurazione"


def load_match_for_lineup(season, path):
    """
    Carica il file Excel di una partita SENZA filtrare i giocatori (a
    differenza di leg_comparison.load_all_matches): la ricostruzione delle
    formazioni ha bisogno di vedere anche i titolari occasionali marcati
    IGNORED in config/player_identities.csv (es. Martignoni, Amore), che
    altrove vengono esclusi da classifiche/KPI.
    """
    file_path = build_base_path(season=season) / path
    return pd.read_excel(file_path, skiprows=1)


def _determine_offset(sub):
    """
    0 se il Decimo ha servito il primissimo punto vero del set, 1 se lo
    ha ricevuto. Vedi il meccanismo descritto nel docstring del modulo:
    guarda Tipo E punteggio della primissima riga, non solo il Tipo.
    """
    prima = sub.iloc[0]
    decimo_serve_primo = (
        prima["Tipo"] == BATTUTA
        and prima["Punti Locali"] == 0
        and prima["Punti Ospiti"] == 0
    )
    return 0 if decimo_serve_primo else 1


def _server_sequence(sub):
    """
    Sequenza (cognome, indice di riga in `sub`) dei turni di battuta
    DISTINTI e consecutivi del Decimo in questo set: un nuovo elemento
    solo quando il battitore cambia rispetto al turno precedente.
    """
    battute = sub[sub["Tipo"] == BATTUTA]
    sequenza = []
    prev = None
    for idx, row in battute.iterrows():
        if row["Cognome"] != prev:
            sequenza.append((row["Cognome"], idx))
            prev = row["Cognome"]
    return sequenza


def reconstruct_starting_lineup(df_match, numero_set):
    """
    Ricostruisce la formazione di partenza (P1..P6) del Decimo per un set,
    dalla sola sequenza dei battitori osservati (colonna 'Cognome' delle
    righe Tipo=='battuta'). `df_match` deve essere il dataframe GREZZO,
    non filtrato sui giocatori riconosciuti (vedi load_match_for_lineup).

    Ritorna un dict:
      {
        "numero_set": ...,
        "decimo_serve_primo": bool,
        "lineup": {"p1": cognome, ..., "pN": cognome},  # N = min(6, servitori distinti)
        "n_servitori_distinti": int,
        "sestetto_incompleto": bool,  # True se sono stati osservati < 6 battitori distinti
        "incerti": {"p5": "motivo...", ...},  # solo gli slot con sostituzione pre-turno sospetta
      }

    Solleva ValueError se il set non ha nessuna riga Tipo=='battuta'
    (impossibile ricostruire qualunque cosa).
    """
    sub = df_match[df_match["Numero Set"] == numero_set].reset_index(drop=True)
    if sub.empty:
        raise ValueError(f"Nessuna riga per il set {numero_set!r}")

    offset = _determine_offset(sub)
    sequenza = _server_sequence(sub)
    if not sequenza:
        raise ValueError(
            f"Nessuna battuta Decimo registrata nel set {numero_set!r}: "
            "impossibile ricostruire la formazione"
        )

    n = len(sequenza)
    lineup = {}
    for k, (cognome, _idx) in enumerate(sequenza[:6], start=1):
        p_slot = ((k - 1 + offset) % 6) + 1
        lineup[f"p{p_slot}"] = cognome

    cambio_idx = sub.index[sub["Tipo"] == CAMBIO_CONFIGURAZIONE]
    primo_cambio = cambio_idx.min() if len(cambio_idx) else None

    incerti = {}
    if primo_cambio is not None:
        for slot_key, cognome in lineup.items():
            prima_apparizione = sub.index[sub["Cognome"] == cognome]
            if len(prima_apparizione) and prima_apparizione.min() > primo_cambio:
                incerti[slot_key] = (
                    f"'{cognome}' compare per la prima volta nel set solo dopo una riga "
                    f"'cambio configurazione' (riga {primo_cambio}): potrebbe essere un "
                    "subentrato al posto del vero titolare, mai arrivato a servire prima "
                    "della sostituzione."
                )

    return {
        "numero_set": numero_set,
        "decimo_serve_primo": offset == 0,
        "lineup": lineup,
        "n_servitori_distinti": n,
        "sestetto_incompleto": n < 6,
        "incerti": incerti,
    }


def reconstruct_match_lineups(df_match):
    """
    Applica reconstruct_starting_lineup a tutti i set presenti nella
    partita. Ritorna {"sets": {numero_set: risultato}, "falliti":
    {numero_set: motivo}} — i set senza alcuna battuta Decimo registrata
    (es. log incompleto) vengono segnalati in "falliti", non omessi in
    silenzio.
    """
    sets = {}
    falliti = {}
    for numero_set in sorted(df_match["Numero Set"].dropna().unique()):
        try:
            sets[int(numero_set)] = reconstruct_starting_lineup(df_match, numero_set)
        except ValueError as e:
            falliti[int(numero_set)] = str(e)
    return {"sets": sets, "falliti": falliti}
