# Match Analysis Volley — Decimo Roma

## Contesto del progetto
Strumenti di analisi statistica per la pallavolo, sviluppati per Decimo Roma (Serie D, Roma).
Il progetto è stato migrato da Google Colab a una repo strutturata con supporto multi-ambiente.

## Struttura repo
- `notebooks/` — notebook Jupyter (classifiche.ipynb, tabellino.ipynb)
- `config/` — CSV di configurazione (seasons, matches, players, player_identities)
- `src/` — moduli Python (sviluppo futuro)
- `data/` — esclusa dal repo, dati reali su Google Drive
- `docs/roadmap.md` — sviluppi futuri

## Ambiente
- Python 3.9 nel virtual environment `.venv`
- Attiva sempre con `source .venv/bin/activate`
- Dipendenze in `requirements.txt`
- Dati su Google Drive, accessibili in locale via:
  `/Users/Marco.Miccheli/Library/CloudStorage/GoogleDrive-marco86sim@gmail.com/Il mio Drive/Pallavolo/Decimo Roma/`
- Path configurato in `.env` tramite `VOLLEY_DATA_PATH`

## Decisioni architetturali prese
- I dati (Excel partite) restano su Google Drive, mai nel repo
- `config/paths.py` rileva automaticamente l'ambiente (Colab vs locale)
- `config/matches.csv` — una riga per partita, con colonne: season, leg, opponent, path, active
- `config/seasons.csv` — una riga per stagione
- `config/players.csv` — registro anagrafico giocatori con player_id stabile
- `config/player_identities.csv` — terne (team, cognome, numero) associate a player_id
- La cella di check terne in classifiche.ipynb usa input() interattivo — funziona sia in Colab che in Cursor (il prompt appare in cima alla finestra)
- I giocatori saltati nel check terne vengono esclusi dalle classifiche tramite filtro su player_identities.csv
- Terne da ignorare in modo permanente (es. giocatori occasionali non più di interesse) si marcano con `player_id = IGNORED` in `player_identities.csv`: il check terne le riconosce come già gestite (nessun nuovo prompt) e la cella FILTRO le esclude esplicitamente dalle classifiche
- `save_identity`/`save_player` nel notebook usano `csv.writer(f, lineterminator='\n')` per evitare terminazioni di riga miste (`\r\n`) nei CSV — da mantenere in eventuali refactor

## Stato attuale (aggiornato 2026-08-08)
- Stagione 2025-2026 completa in `config/matches.csv`: 28 partite (andata + ritorno + playoff), tutte `active=1`
- `config/players.csv` / `config/player_identities.csv` popolati con 11 giocatori Decimo + 4 terne marcate `IGNORED` (Martignoni, Amore, Ferrazzi, Principato)
- `notebooks/classifiche.ipynb` verificato end-to-end senza errori (caricamento, check terne, filtro, classifiche, export PDF)
- `src/rankings.py` creato: `calculate_player_scores_by_type` + i 6 builder di classifica (Ace Man, Spike Leader, Spike Guarantee, Block Monster, Miglior Ricevitore, Muro della vergogna) estratti da `classifiche.ipynb`, che ora li importa. Le `ignore_list` per fondamentale restano nel notebook (scelta editoriale specifica della stagione, non logica riutilizzabile). Verificato: output identico pre/post refactor.
- Prossimo passo lato `src/`: estrazione da `tabellino.ipynb` (`efficiency.py`, `attacks.py`) — notebook molto più corposo (2041 righe) con celle sperimentali/commentate da valutare prima dello split

## Workflow settimanale (nuova partita)
1. Aggiungi riga in `config/matches.csv`
2. Apri `notebooks/classifiche.ipynb` in Cursor o Colab
3. Esegui cella per cella — il check terne rileva nuovi giocatori
4. Committa `config/players.csv` e `config/player_identities.csv` dopo il check
5. Push su GitHub

## Prossimi sviluppi (vedi docs/roadmap.md)
- Estrazione funzioni core in src/ (efficiency.py, attacks.py, rankings.py)
- Confronto KPI cross-stagione
- Aggiunta stagione 2026-2027 a settembre
