# Roadmap e sviluppi futuri

## In corso

- Migrazione notebook da Google Colab a struttura repo locale

- Config layer per gestione path multi-ambiente (Colab / locale)

- Sistema di player identity con risoluzione terne (team, cognome, numero)

## Prossimi passi

### Priorità alta

- [ ] config/[paths.py](http://paths.py) — rilevamento automatico ambiente e caricamento path

- [ ] Cella di check terne nei notebook con input interattivo

- [ ] Popolamento players.csv e player_identities.csv dalla stagione 2025-2026

- [ ] Pulizia e migrazione notebook in notebooks/

### Priorità media

- [ ] Estrazione funzioni core in src/ ([efficiency.py](http://efficiency.py), [attacks.py](http://attacks.py), [rankings.py](http://rankings.py))

- [ ] Confronto KPI cross-stagione

- [ ] requirements.txt completo e testato

### Priorità bassa / idee future

- [ ] Dashboard interattiva (es. Streamlit) per visualizzare le classifiche

- [ ] Automazione caricamento nuovo Excel via script settimanale

- [ ] Esportazione PDF classifiche automatizzata