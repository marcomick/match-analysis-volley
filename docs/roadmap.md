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

- [x] Estrazione core tabulare da tabellino.ipynb in src/[efficiency.py](http://efficiency.py) (calcola_efficienza, find_errors, separate_attacks_counterattacks, separate_free_ball, calcola_efficienza_free_ball, compute_set_metrics, export_tabellino_to_xlsx). Deduplicata la copia di find_errors usata da export_tabellino_to_xlsx (una non contava gli errori di muro, l'altra sì) — unificata includendo sempre il muro. Rimossa dead code mai chiamata (_attack_tipo_value, create_player_summary_df_with_free_ball). **Nota**: tutto da "# altro" in poi (incluso create_player_summary_df / tabellino standard / "SO per rotazioni") è fuori scope, non refactorato — resta con la propria find_errors locale, non unificata.

- [x] Estrazione grafici da tabellino.ipynb in src/[attacks.py](http://attacks.py) (compute_points_table, plot_points_grouped, compute_attack_eff_breakdown, plot_attack_eff_breakdown_bars, create_attack_eff_plots, plot_set_efficiency_groups, create_metrics_plot, plot_set_radar). `write_files`/percorso di salvataggio ora parametri espliciti (`write_files`, `save_dir`) invece di variabili globali del notebook — necessario perché un modulo importato non vede i globali del notebook chiamante.

- [ ] Eventuale estrazione della sezione "altro" in poi di tabellino.ipynb (create_player_summary_df, export licenziati/caranzetti, "SO per rotazioni") — volutamente esclusa dai due giri di refactoring precedenti

- [ ] Confronto KPI cross-stagione

- [ ] requirements.txt completo e testato

### Priorità bassa / idee future

- [ ] Dashboard interattiva (es. Streamlit) per visualizzare le classifiche

- [ ] Automazione caricamento nuovo Excel via script settimanale

- [ ] Esportazione PDF classifiche automatizzata