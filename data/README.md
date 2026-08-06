# Dati

Questa cartella è esclusa dal repository `.gitignore`).

I file Excel delle partite risiedono su **Google Drive** e non vengono

mai committati nella repo per due motivi:

- contengono dati personali dei giocatori
- vengono aggiornati ogni settimana con nuove partite

## Struttura attesa su Drive

Pallavolo/Decimo Roma/

└── 2025-2026/

```
└── Serie D/

    └── Match analysis/

        ├── (a1) Decimo-Lazio 3-0/

        │   └── Decimo-Lazio 3-0.xlsx

        ├── (a2) Pqp - Decimo 3-0/

        │   └── [decimo] Pqp - decimo.xlsx

        └── ...
```

## Come configurare l'ambiente locale

1. Sincronizza la cartella Drive sul tuo Mac tramite il client desktop di Google Drive
2. Copia .env.example in .env
3. Imposta il percorso locale in .env:
  VOLLEY_DATA_PATH=/percorso/locale/Match analysis/



## Aggiungere una nuova partita

Aggiungi una riga in config/matches.csv con il path relativo

alla cartella della stagione. Il notebook rileverà automaticamente

i nuovi giocatori al prossimo avvio.