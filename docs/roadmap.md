# Roadmap e sviluppi futuri

## In corso

- Migrazione notebook da Google Colab a struttura repo locale

- Config layer per gestione path multi-ambiente (Colab / locale)

- Sistema di player identity con risoluzione terne (team, cognome, numero)

## Prossimi passi

### Priorità alta

- [x] config/[paths.py](http://paths.py) — rilevamento automatico ambiente e caricamento path

- [x] Cella di check terne nei notebook con input interattivo (con opzione di ignora permanente via `player_id = IGNORED`)

- [x] Popolamento players.csv e player_identities.csv dalla stagione 2025-2026

- [x] Pulizia e migrazione notebook in notebooks/ (classifiche.ipynb verificato end-to-end)

### Priorità media

- [x] Estrazione classifiche in src/[rankings.py](http://rankings.py) (da classifiche.ipynb: calculate_player_scores_by_type + 6 builder di classifica)

- [ ] Estrazione funzioni core da tabellino.ipynb in src/ ([efficiency.py](http://efficiency.py), [attacks.py](http://attacks.py) — notebook più corposo, richiede pulizia celle sperimentali prima dello split)

- [ ] Confronto KPI cross-stagione

- [ ] requirements.txt completo e testato

### Priorità bassa / idee future

- [ ] Dashboard interattiva (es. Streamlit) per visualizzare le classifiche

- [ ] Automazione caricamento nuovo Excel via script settimanale

- [ ] Esportazione PDF classifiche automatizzata