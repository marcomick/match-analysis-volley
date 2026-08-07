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
