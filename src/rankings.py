# src/rankings.py
"""
Funzioni core per il calcolo delle classifiche giocatori:
Ace Man, Spike Leader, Spike Guarantee, Block Monster, Rigoristi,
Miglior Ricevitore, Muro della vergogna (battute sbagliate).

Estratto da notebooks/classifiche.ipynb per riuso (cross-stagione, test, altri notebook).
"""
import pandas as pd

# Voti standard usati nei file di match analysis.
# '#': punto/perfetto; '+': positivo; '!': medio; '/': negativo; '-': impreciso; '=': errore.
VOTI = ['#', '+', '!', '/', '-', '=']


def calculate_player_scores_by_type(df, action_type, ignore_list=None):
    """
    Conta i voti per giocatore su uno specifico fondamentale.

    Ritorna una tabella indicizzata per 'Giocatore' con le colonne:
    '#', '+', '!', '/', '-', '=', 'Totale'.
    """
    if ignore_list is None:
        ignore_list = []

    # Filtra il DataFrame per fondamentale.
    df_filtered = df[df['Tipo'] == action_type].copy()

    # Esclude eventuali giocatori da non considerare in quella classifica.
    df_filtered = df_filtered[~df_filtered['Cognome'].isin(ignore_list)]

    # Identificativo giocatore: Cognome + Numero.
    df_filtered['Giocatore'] = df_filtered['Cognome'] + ' ' + df_filtered['Numero'].astype(str)

    # Conta i voti per giocatore.
    player_voto_counts = pd.crosstab(df_filtered['Giocatore'], df_filtered['Voto'])

    # Garantisce sempre la presenza di tutte le colonne voto.
    for voto in VOTI:
        if voto not in player_voto_counts.columns:
            player_voto_counts[voto] = 0

    # Riordina le colonne e aggiunge il totale azioni.
    player_voto_counts = player_voto_counts[VOTI]
    player_voto_counts['Totale'] = player_voto_counts.sum(axis=1)

    return player_voto_counts


def format_percent(value):
    """Formatta un numero percentuale gia' espresso su scala 0-100, arrotondato all'intero."""
    return f"{value:.0f}%"


def build_ace_man(srv_scores):
    """
    ACE MAN
    - Aggiunge %# = ace / battute totali
    - Entra chi ha fatto almeno un ace, qualunque sia il numero di battute
      (richiesta dell'utente, 2026-10-30: prima serviva una soglia minima di
      battute, e chi aveva 0 ace entrava comunque in coda)
    - Ordina per ace assoluti, poi per %#
    """
    work = srv_scores[srv_scores['#'] >= 1].copy()
    work['%#_sort'] = (work['#'] / work['Totale']) * 100

    result = work.sort_values(by=['#', '%#_sort'], ascending=[False, False]).reset_index()
    result['Pos'] = result.index + 1
    result['Battute'] = result['Totale']
    result['%#'] = result['%#_sort'].apply(format_percent)
    return result[['Pos', 'Giocatore', '#', 'Battute', '%#']]


def build_best_attack(attacco_scores):
    """
    SPIKE LEADER — classifica per numero assoluto di attacchi punto '#'.
    """
    result = attacco_scores.sort_values(by='#', ascending=False).reset_index()
    result['Pos'] = result.index + 1
    return result[['Pos', 'Giocatore', '#']]


def build_spike_guarantee(attacco_scores, min_attacchi=50):
    """
    SPIKE GUARANTEE
    - Eff% = (# - (/ + =)) / totale attacchi
    - Entra solo chi ha almeno `min_attacchi` attacchi
    - Ordina per Eff%
    """
    work = attacco_scores[attacco_scores['Totale'] >= min_attacchi].copy()
    work['Eff%_sort'] = ((work['#'] - (work['/'] + work['='])) / work['Totale']) * 100

    result = work.sort_values(by=['Eff%_sort', 'Totale'], ascending=[False, False]).reset_index()
    result['Pos'] = result.index + 1
    result['Attacchi'] = result['Totale']
    result['Eff%'] = result['Eff%_sort'].apply(format_percent)
    return result[['Pos', 'Giocatore', 'Eff%', 'Attacchi']]


def rigore_mask(df):
    """True sui "rigori" (muro punto dopo una ricezione o difesa '/' avversaria,
    colonna 'Rigore' di src.dvw, dove il Tipo diventa 'rigore'); tutto False se
    la colonna manca (Excel 2025-2026)."""
    if 'Rigore' not in df.columns:
        return pd.Series(False, index=df.index)
    return df['Rigore'].eq(True)


def without_rigori(df):
    """Le azioni senza i rigori: per Block Monster contano solo i muri veri
    (richiesta dell'utente, 2026-11-02). Dal 2026-11-09 i rigori hanno già
    Tipo 'rigore' e non sono tra i muri: il filtro resta per sicurezza."""
    return df[~rigore_mask(df)]


def build_best_block(blk_scores):
    """
    BLOCK MONSTER — classifica per muri punto '#'. Calcolare `blk_scores` su
    without_rigori(df): i rigori hanno la loro classifica (build_rigoristi).
    """
    result = blk_scores.sort_values(by='#', ascending=False).reset_index()
    result['Pos'] = result.index + 1
    return result[['Pos', 'Giocatore', '#']]


def build_best_rec(pos_rec, min_ricezioni=40):
    """
    MIGLIOR RICEVITORE
    - Eff% = ((# + +) - (/ + =)) / ricezioni totali: la stessa efficienza di
      ricezione dei KPI della dashboard e del tabellino del report partita
      (richiesta dell'utente, 2026-10-23; prima la classifica ordinava per
      Pos% = (# + +) / ricezioni, senza togliere errori e palle rimandate)
    - Prf% = # / ricezioni totali
    - Entra solo chi ha almeno `min_ricezioni` ricezioni
    - Ordina per Eff%
    """
    work = pos_rec[pos_rec['Totale'] >= min_ricezioni].copy()
    work['Eff%_sort'] = ((work['#'] + work['+'] - work['/'] - work['=']) / work['Totale']) * 100
    work['Prf%_sort'] = (work['#'] / work['Totale']) * 100

    result = work.sort_values(
        by=['Eff%_sort', 'Totale', 'Prf%_sort'],
        ascending=[False, False, False]
    ).reset_index()
    result['Pos'] = result.index + 1
    result['Ricezioni'] = result['Totale']
    result['Eff%'] = result['Eff%_sort'].apply(format_percent)
    result['Prf%'] = result['Prf%_sort'].apply(format_percent)
    return result[['Pos', 'Giocatore', 'Eff%', 'Ricezioni', 'Prf%']]


def build_srv_shame(srv_scores):
    """
    MURO DELLA VERGOGNA / BATTUTE SBAGLIATE
    - Aggiunge Err% = errori '=' / battute totali
    - Entra solo chi ha sbagliato almeno una battuta (richiesta dell'utente,
      2026-10-30)
    - Ordina principalmente per numero assoluto di errori '='
    """
    work = srv_scores[srv_scores['='] >= 1].copy()
    work['Err%_sort'] = (work['='] / work['Totale']) * 100

    result = work.sort_values(by=['=', 'Err%_sort'], ascending=[False, False]).reset_index()
    result['Pos'] = result.index + 1
    result['Battute'] = result['Totale']
    result['Err%'] = result['Err%_sort'].apply(format_percent)
    return result[['Pos', 'Giocatore', '=', 'Battute', 'Err%']]


def build_rigoristi(df, ignore_list=None):
    """
    RIGORISTI — classifica per "rigori" (muro punto subito dopo una ricezione
    o difesa '/' avversaria, vedi rigore_mask), richiesta dell'utente del 2026-11-02.
    Entra chi ne ha fatto almeno uno. Rigori segnati dalla G1 2026-2027 (prima
    l'utente li segnava come attacco punto).
    """
    work = df[rigore_mask(df) & ~df['Cognome'].isin(ignore_list or [])]
    work = work.assign(Giocatore=work['Cognome'] + ' ' + work['Numero'].astype(str))
    counts = work.groupby('Giocatore').size().rename('#').sort_values(ascending=False, kind='stable')
    result = counts.reset_index()
    result['Pos'] = result.index + 1
    return result[['Pos', 'Giocatore', '#']]
