# src/rankings.py
"""
Funzioni core per il calcolo delle classifiche giocatori:
Ace Man, Spike Leader, Spike Guarantee, Block Monster,
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


def build_ace_man(srv_scores, min_battute=10):
    """
    ACE MAN
    - Aggiunge %# = ace / battute totali
    - Entra solo chi ha almeno `min_battute` battute
    - Ordina per ace assoluti, poi per %#
    """
    work = srv_scores[srv_scores['Totale'] >= min_battute].copy()
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


def build_best_block(blk_scores):
    """
    BLOCK MONSTER — classifica per muri punto '#'.
    """
    result = blk_scores.sort_values(by='#', ascending=False).reset_index()
    result['Pos'] = result.index + 1
    return result[['Pos', 'Giocatore', '#']]


def build_best_rec(pos_rec, min_ricezioni=40):
    """
    MIGLIOR RICEVITORE
    - Pos% = (# + +) / ricezioni totali
    - Prf% = # / ricezioni totali
    - Entra solo chi ha almeno `min_ricezioni` ricezioni
    - Ordina per Pos%
    """
    work = pos_rec[pos_rec['Totale'] >= min_ricezioni].copy()
    work['Pos%_sort'] = ((work['#'] + work['+']) / work['Totale']) * 100
    work['Prf%_sort'] = (work['#'] / work['Totale']) * 100

    result = work.sort_values(
        by=['Pos%_sort', 'Totale', 'Prf%_sort'],
        ascending=[False, False, False]
    ).reset_index()
    result['Pos'] = result.index + 1
    result['Ricezioni'] = result['Totale']
    result['Pos%'] = result['Pos%_sort'].apply(format_percent)
    result['Prf%'] = result['Prf%_sort'].apply(format_percent)
    return result[['Pos', 'Giocatore', 'Pos%', 'Ricezioni', 'Prf%']]


def build_srv_shame(srv_scores):
    """
    MURO DELLA VERGOGNA / BATTUTE SBAGLIATE
    - Aggiunge Err% = errori '=' / battute totali
    - Ordina principalmente per numero assoluto di errori '='
    """
    work = srv_scores.copy()
    work['Err%_sort'] = (work['='] / work['Totale']) * 100

    result = work.sort_values(by=['=', 'Err%_sort'], ascending=[False, False]).reset_index()
    result['Pos'] = result.index + 1
    result['Battute'] = result['Totale']
    result['Err%'] = result['Err%_sort'].apply(format_percent)
    return result[['Pos', 'Giocatore', '=', 'Battute', 'Err%']]
