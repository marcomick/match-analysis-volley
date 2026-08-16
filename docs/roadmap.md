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

- [x] requirements.txt completo e testato — versioni pinnate a quelle verificate funzionanti (Python 3.9), aggiunto ipykernel (mancava: necessario per eseguire i notebook in Cursor/VS Code, prima installato manualmente senza essere dichiarato). Testato installando in un venv pulito ed eseguendo end-to-end sia classifiche.ipynb che tabellino.ipynb (write_files=False): nessun errore.

- [ ] Confronto KPI cross-stagione — in standby: richiede più stagioni popolate, non ancora disponibili

- [x] Confronto KPI andata vs ritorno (stagione corrente) — sostituisce per ora il cross-stagione. `src/leg_comparison.py` (dati: KPI per partita, squadra e per giocatore, riusando calcola_efficienza/find_errors di efficiency.py) + `dashboard/[app.py](http://app.py)` (Streamlit+Plotly), tre sezioni: (1) per ogni avversario, barre affiancate andata/ritorno (playout JVC come 14ª posizione a sé, 2 barre) + 2 linee di trend lisciate con kernel gaussiano, spezzate dove mancano dati, toggle barre/trend; (2) confronto multi-giocatore: una linea continua per entità su tutte le 28 partite (playout = 27ª/28ª), con tabella Min/Mediana/Max; (3) confronto multi-KPI per una singola entità (speculare alla 2). Multiselect KPI ed entità su tutte le sezioni. Verificato: KPI incrociati con calcola_efficienza/find_errors diretti, smoothing verificato a preservare i buchi nei dati mancanti, app testata headless via `streamlit.testing.v1.AppTest` (nessuna eccezione su selezione multipla completa) e con screenshot reali (Playwright, solo per verifica).
  - **Bug corretto**: l'ordine dell'asse X non era forzato esplicitamente — con dati mancanti per alcuni giocatori, Plotly deduceva un ordine incoerente tra le tracce. Fix: `categoryorder='array', categoryarray=x_axis_order` esplicito nel layout.
  - **Estensione KPI assoluti**: oltre alle 4 percentuali (Battuta/Ricezione/Attacco/Muro%) e Errori, aggiunti 4 totali per fondamentale (Battuta/Ricezione/Attacco/Muro Tot) e 8 conteggi per voto specifico (punti attacco/muro/ace, ace subiti, errori battuta/attacco, attacchi murati, mani-fuori subite) — 17 KPI totali, tutti disponibili in tutte e 3 le sezioni.
  - **Bug corretto (dark mode)**: le etichette della legenda restavano nere e illeggibili su sfondo nero in dark mode, perché `st.plotly_chart` applicava il tema Streamlit sopra colori di sfondo fissi con font hardcoded scuro. Fix: rilevamento tema via `st.context.theme.type`, palette/superficie/font dipendenti dal tema, `theme=None` esplicito su `st.plotly_chart`.
  - **Fix hover**: le linee di trend sono smussate solo per la resa visiva; l'hover ora mostra sempre il valore reale (pre-smoothing), non quello smussato — prima i conteggi assoluti apparivano come numeri decimali fuorvianti.
  - **KPI "Attacco SO" (side-out) e "Contrattacco"**: 10 nuovi KPI (5+5: Eff%, Tot, # punti, = errori, / murati) sui due sottoinsiemi esaustivi degli attacchi (dopo ricezione / tutto il resto), con l'esito della ricezione che qualifica il "SO" selezionabile da un multiselect in sidebar (default `#,+,!,-`) — 27 KPI totali.
  - **Bug corretto (`separate_attacks_counterattacks`)**: la classificazione "attacco dopo ricezione" era solo posizionale sul dataframe già filtrato sui giocatori riconosciuti — un'azione di un giocatore IGNORED tra ricezione e attacco reale veniva rimossa dal filtro, agganciando per errore un attacco di un rally diverso. Verificato empiricamente: 5 falsi positivi su 1640 nella stagione 2025-2026. Fix: aggiunto controllo di consistenza (stesso `Numero Set` e stesso punteggio tra ricezione e attacco); ri-verificato, zero violazioni residue su 1635 attacchi.
  - **Soglia minima attacchi**: nuovo controllo in sidebar — per i KPI della famiglia "attacco" (Attacco*, Attacco SO*, Contrattacco*), se un giocatore in una partita non raggiunge la soglia sul numero totale di attacchi di quella famiglia, il KPI per quella partita/giocatore risulta senza dati (non zero). Non si applica a Battuta/Ricezione/Muro/Errori né al livello squadra.
  - **Esiti partite/set (base dati per un futuro modello random forest su cosa determina vittorie/set/differenza punti)**: risolta l'ambiguità Locali/Ospiti leggendo la dicitura autoritativa in riga 0 di ogni Excel (`_parse_decimo_locali`, invece dell'euristica sul nome cartella che aveva 5 mismatch su 28); risolto il problema che il punteggio esatto di fine set non è ricavabile dal solo log Excel (ogni riga mostra il punteggio *prima* del proprio esito) integrando come fonte primaria un file di risultati ufficiali federali (`risultati_decimo_{season}.txt` su Drive, parsato da `parse_official_results`), cross-validato 0 mismatch su 26 partite contro `decimo_locali`. `compute_set_outcomes`/`compute_match_outcome` usano i parziali ufficiali quando disponibili (26/28 partite, flag `esatto=True`) e ricadono sulla stima approssimata dal log solo per i 2 playout (`esatto=False`, non disponibile un file ufficiale equivalente). `build_match_outcomes_dataset`/`build_set_outcomes_dataset` producono le tabelle tidy corrispondenti (risultato stagione 2025-2026: 11 vittorie, 15 sconfitte su 26 partite esatte). In dashboard, sezioni 2 e 3: striscia di quadratini verde/rosso/grigio sotto ogni grafico (vittoria/sconfitta/indeterminato), allineata 1:1 alle 28 partite (`add_result_strip`, subplot condiviso con l'asse X del grafico principale).
  - **Attacco scomposto in tre vie (SO/Free Ball/Contrattacco)**: nuova funzione `separate_attack_types` (`src/efficiency.py`) che distingue anche gli attacchi dopo free ball (difesa Voto `!`) dal contrattacco generico, prima lumped insieme — 15 KPI (5+5+5) invece di 10, 32 KPI totali in dashboard. Vedi CLAUDE.md per i dettagli del fix e della verifica dell'invariante.

- [x] **Pagella giocatore**: `src/attendance.py` (parsing presenze/assenze da foglio Presenze D), `src/player_report.py` (finding di rendimento: streak + cambio di livello, su KPI curati con filtro di significatività sui volumi) e `src/player_season_report.py` (report di sintesi stagionale per ruolo: efficienze aggregate, punteggio composito di partita/bilanci per fondamentale, partite notevoli, punti di forza/debolezza, obiettivi per la prossima stagione basati sulla mediana di riferimento) pronti e verificati sui dati reali 2025-2026. Sezione "Pagella giocatore" in `dashboard/app.py`: una tab per giocatore, con "Sintesi stagionale" + i finding puntuali (grafico di evidenza per ciascuno) + pulsante "Genera report" che produce un file Word scaricabile con tutte le sezioni della tab, apertura con un giudizio sintetico testuale generato da regole. Vedi CLAUDE.md per i dettagli, inclusi diversi giri di correzione (algoritmo streak/cambio livello nato da un caso reale segnalato dall'utente — Pessei/Attacco SO%; direzione dei KPI nel confronto con la mediana; bilanci per fondamentale al posto dei singoli KPI assoluti nelle partite notevoli; grassetto Markdown non renderizzato nel documento Word).
  - [ ] Rendering narrativo più ricco/PDF eventuale — per ora il Word copre le stesse informazioni della tab, non è ancora una vera "pagella" discorsiva oltre al giudizio sintetico in apertura.
  - [x] **Metrica specifica per palleggiatori**: `src/setter_report.py` (2026-08-16) — "Attacco Alzato", l'efficienza di attacco DEI SUOI ATTACCANTI nelle azioni Side-Out mentre il palleggiatore in questione risultava in campo (`identify_active_setter`, riusa `src/lineup.py`). Sostituisce l'esclusione totale precedente sia in `player_report.py` (finding) sia in `player_season_report.py` (sintesi stagionale, nuovo bucket ruolo 'P'). Validato su due scenari reali (uno dei due palleggiatori da libero; entrambi in campo nello stesso set con cambio di titolarità) — copertura 91,9% degli attacchi SO qualificanti della stagione. Vedi CLAUDE.md per i dettagli.
  - [ ] Rendering finale della pagella (narrazione testuale, PDF, o pagina Streamlit dedicata) — formato non ancora deciso.
  - [ ] Report generale di singola partita (andamento partita + giocatori nel corso della partita, riusando i grafici già esistenti in `src/attacks.py`) — esplicitamente rimandato dall'utente ("per la prossima stagione"), non ancora iniziato.

- [x] **Ricostruzione formazioni di partenza (P1..P6) senza referto**: `src/lineup.py` (`reconstruct_starting_lineup`/`reconstruct_match_lineups`) — dalla sola sequenza dei battitori Decimo nel log Excel, senza bisogno del referto federale (che resta comunque la fonte più attendibile per un confronto puntuale, quando disponibile). Validato su 23 set/7 partite reali contro i referti PDF: 21/23 set esatti (91%), 135/138 slot corretti (98%); i 2 scarti spiegati da un limite noto e già segnalato (sostituzione di un titolare prima che il suo slot arrivi mai a servire). Vedi CLAUDE.md per il meccanismo completo.
  - [ ] Controllo incrociato con la regola strutturale di rotazione (posizione palleggiatore → pattern di ruolo atteso) — non ancora implementato, non risultato necessario nella validazione.
  - [ ] Db ruoli esplicito per giocatori ibridi (Carrer/Sardella, anche Opposto) — rimandato.
  - [ ] Esposizione di "rotazione" (= posizione del palleggiatore in una data azione) come dimensione riutilizzabile per analisi future — non ancora implementato.
  - [ ] Integrazione in dashboard/notebook — per ora solo modulo `src/`, nessuna UI.

### Priorità bassa / idee future

- [x] Dashboard interattiva (Streamlit) — realizzata per il confronto andata/ritorno (vedi sopra); da estendere eventualmente anche alle classifiche stagionali

- [ ] Automazione caricamento nuovo Excel via script settimanale

- [ ] Esportazione PDF classifiche automatizzata