# Match Analysis Volley — Decimo Roma

Strumenti di analisi statistica per la pallavolo, sviluppati per

Decimo Roma (Serie D, Roma).

## Cosa fa questo progetto

`notebooks/tabellino.ipynb`* — analisi di una singola partita:

efficienza per fondamentale (battuta, ricezione, attacco, muro),

separazione attacchi/contrattacchi/free ball, report formattato.

`notebooks/classifiche.ipynb`* — classifiche stagionali aggregate

su tutte le partite: Ace Man, Spike Leader, Spike Guarantee,

Block Monster, Miglior Ricevitore, output in PDF.

## Struttura repo

match-analysis-volley/  
├── notebooks/ # notebook Jupyter  
├── src/ # moduli Python (sviluppo futuro)  
├── config/ # configurazione stagioni, partite, giocatori  
 │ ├── seasons.csv  
 │ ├── matches.csv  
 │ ├── players.csv  
 │ └── player_identities.csv  
├── data/ # esclusa dal repo — vedi data/[README.md](http://README.md)  
└── docs/ # documentazione e roadmap

## Setup ambiente locale

### Requisiti

- Python 3.10+

- pip

### Installazione

```bash

git clone [git@github.com](mailto:git@github.com):marcomick/match-analysis-volley.git

cd match-analysis-volley

pip install -r requirements.txt

cp .env.example .env

```

Imposta `VOLLEY_DATA_PATH` nel file `.env` con il percorso locale

alla cartella Drive sincronizzata (vedi `data/README.md`).

### Su Google Colab

Il rilevamento dell'ambiente è automatico — monta Drive normalmente

e i path vengono impostati da `config/paths.py` senza modifiche.

## Aggiungere una nuova partita

1. Aggiungi una riga in `config/matches.csv`

2. Apri `notebooks/classifiche.ipynb`

3. Esegui la cella di check — il notebook rileva eventuali nuovi

   giocatori e chiede come associarli

## Stagioni

| Stagione  | Competizione | Partite |

|-----------|-------------|---------|

| 2025-2026 | Serie D     | 28      |

