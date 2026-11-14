# Diario degli allenamenti — Decimo Roma

Un file per allenamento (`AAAA-MM-GG.md`), più gli allegati con lo stesso prefisso (schemi, PDF da stampare).
L'obiettivo è costruire nel tempo un archivio di esercizi, regole di punteggio e risultati, da confrontare con le partite.

## Formato di un allenamento

In cima al file c'è un blocco di dati strutturati (frontmatter YAML), così un domani si possono leggere tutti gli
allenamenti da codice (per esempio una tab della dashboard). Sotto ci sono il racconto, gli esercizi e le osservazioni.

```yaml
---
data: 2026-11-13
partita_dopo: 2026-11-15 Decimo - Fenice    # la partita che si preparava
focus: [side-out palleggiatore davanti, seconda linea, free ball, palla alta]
presenti: [Racheli, Caranzetti, ...]
assenti_o_limitati: {Chimenton: "caviglia, solo da libero"}
esercizi:
  - nome: Sintetico ricezione-attacco senza posto 4
    tipo: sintetico          # analitico | sintetico | globale
    durata_min: 35
    allegato: 2026-11-13_sintetico.pdf
  - nome: Globale a punteggio (punto diretto, free ball di conferma, palla alta)
    tipo: globale
    risultato: "B 25-23"
---
```

Per ogni esercizio, nel testo: **perché** (da quale dato o osservazione nasce), **regole**, **squadre e rotazioni**,
**riferimenti** (cosa vorrebbe dire "livello nostro" e "livello playoff" con le stesse regole, quando si possono
calcolare), **come è andata** e **cosa ne teniamo**. Se qualcosa era pianificato ma non è stato fatto così, scriverlo.

## Allenamenti

| Data | Focus | Esercizi principali | Esito |
|---|---|---|---|
| [2026-11-11](2026-11-11.md) | Break-point e side-out per rotazione | Esercizio a punteggio a blocchi da 6 palle per rotazione | A 7 su 33 in break-point, B 17 su 35; differenza tutta col palleggiatore davanti |
| [2026-11-13](2026-11-13.md) | Palleggiatore davanti, seconda linea, transizione | Sintetico P4/P2 senza posto 4; globale con free ball di conferma e palla alta | Globale B 25-23 (A avanti 15-8, poi crollo in P2 e P1) |
