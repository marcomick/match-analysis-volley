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
- Nel refactoring di `tabellino.ipynb` verso `src/`, tutto da "# altro" in poi (celle ad-hoc per singola partita, sezione "tabellino" standard con `create_player_summary_df`, "SO per rotazioni") resta intenzionalmente **fuori scope**, non refactorato: quelle celle restano invariate nel notebook, non spostate in `src/`
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
- `src/efficiency.py` creato: `calcola_efficienza`, `find_errors`, `separate_attacks_counterattacks`, `separate_free_ball`, `calcola_efficienza_free_ball`, `compute_set_metrics`, `export_tabellino_to_xlsx` estratti da `tabellino.ipynb` (celle 7, 19, che ora importano da `src.efficiency`). Il notebook aveva due copie divergenti di `find_errors`: una (usata da `export_tabellino_to_xlsx`, export "`[tabellino F]`") contava gli errori di muro, l'altra (usata dal tabellino standard, `create_player_summary_df`, sezione "altro"/"tabellino" — **fuori scope**, non toccata) no. Solo la copia usata da `export_tabellino_to_xlsx` è stata unificata per includere sempre il muro; `create_player_summary_df` resta nel notebook con la sua `find_errors` locale invariata (divergenza tra i due export ancora presente, per ora). Rimossa dead code mai chiamata nel notebook (`_attack_tipo_value`, `create_player_summary_df_with_free_ball`). Verificato eseguendo notebook baseline (pre-refactor) e post-refactor sulla stessa partita reale (JVC, con `write_files=False` per non toccare Google Drive): output identico.
- `src/attacks.py` creato: tutte le funzioni di grafico di `tabellino.ipynb` (`compute_points_table`, `plot_points_grouped`, `compute_attack_eff_breakdown`, `plot_attack_eff_breakdown_bars`, `create_attack_eff_plots`, `plot_set_efficiency_groups`, `create_metrics_plot`, `plot_set_radar`), estratte dalle celle 13/16/19/23 (tutte precedenti alla sezione "altro"). Le firme ora accettano `write_files`/`save_dir` come parametri espliciti al posto delle variabili globali del notebook (`write_files`, `file_path1`) — un modulo importato non vede i globali del notebook chiamante, quindi le celle 14/17/21/24 ora passano questi valori esplicitamente ad ogni chiamata. Verificato: eseguendo baseline pre-refactor e post-refactor sulla stessa partita reale (`write_files=False`), tutti gli output testuali e tutti i grafici PNG generati sono risultati byte-identici (hash SHA256 uguali).
- Estrazione `src/` da `tabellino.ipynb` completata per tutto ciò che precede la sezione "altro" (config, caricamento dati, tabellino con Free Ball, punti/efficienza/trend/radar). Da "altro" in poi (celle ad-hoc per singola partita, tabellino standard, "SO per rotazioni") il notebook resta byte-identico all'originale, per scelta.
- `requirements.txt` completato e pinnato alle versioni verificate funzionanti (Python 3.9): pandas 2.3.3, numpy 2.0.2, matplotlib 3.9.4, openpyxl 3.1.5, xlsxwriter 3.2.9, fpdf2 2.8.4, python-dotenv 1.2.1. Aggiunto `ipykernel` (mancava: necessario per eseguire i notebook come kernel Jupyter in Cursor/VS Code, prima installato manualmente senza essere dichiarato). Testato installando in un venv pulito (non il `.venv` del progetto) ed eseguendo end-to-end sia `classifiche.ipynb` che `tabellino.ipynb` (`write_files=False`): nessun errore.

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
