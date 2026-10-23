# src/scout_files.py
"""
Lettura del file di scout di una partita, unico punto di accesso per
dashboard, moduli src/ e notebook:
- .dvw (DataVolley, dalla stagione 2026-2027): src/dvw.py;
- .xlsx nel formato 2025-2026 (colonne Tipo/Voto/Cognome/Numero/Posizione
  Giocatore/Posizione Palleggiatore/Numero Set/Punti Locali/Punti Ospiti,
  riga 0 con "Locali: .../Ospiti: ...").

L'Excel esportato dal 2026-2027 ha un formato diverso (voto '#' diventato
'++', colonne rinominate, Tipo maiuscolo, le due squadre affiancate) e NON è
supportato: per quelle partite si registra il .dvw, che ha le stesse azioni
(verificato su G1 San Paolo) e in più formazioni, punteggi esatti e
minutaggio video. Un Excel in quel formato fa fallire la lettura con un
errore esplicito, invece di produrre KPI vuoti in silenzio.
"""
import re

import pandas as pd

from src.dvw import load_team_actions

TEAM = "Decimo"


def parse_decimo_locali(file_path):
    """
    Legge la riga 0 del file Excel (dichiarazione "Locali: <squadra>" /
    "Ospiti: <squadra>", saltata da skiprows=1 nel resto del caricamento) e
    determina se il Decimo è "Locali" o "Ospiti" in questa specifica partita.
    Ritorna True se Decimo è Locali, False se è Ospiti.

    Solleva ValueError se l'etichetta non è nel formato atteso o non menziona
    il Decimo — meglio fallire in modo rumoroso che dedurre l'informazione da
    fonti meno affidabili (es. il nome della cartella/file, che in alcuni casi
    reali diverge da questa dichiarazione).
    """
    label_row = pd.read_excel(file_path, header=None, nrows=1)
    label = str(label_row.iloc[0, 0])
    match = re.match(r"\s*(Locali|Ospiti)\s*:\s*(.+)", label, re.IGNORECASE)
    if not match:
        raise ValueError(f"Etichetta Locali/Ospiti non trovata in riga 0 di {file_path}: {label!r}")
    ruolo, squadra = match.group(1).lower(), match.group(2)
    if TEAM.lower() not in squadra.lower():
        raise ValueError(f"L'etichetta '{label}' in {file_path} non menziona '{TEAM}'")
    return ruolo == "locali"


def read_match_file(file_path, team="decimo"):
    """
    Azioni di una squadra in una partita (team='decimo' o 'avversario').
    Ritorna (df, decimo_locali, dvw_parsed):
    - df nel formato degli Excel 2025-2026 (righe con Numero mancante incluse:
      punti/errori generici, cambi);
    - decimo_locali: True se il Decimo era la squadra di casa (None per un
      Excel avversario, che nel 2025-2026 era un file a parte);
    - dvw_parsed: output completo di src.dvw.parse_dvw per un .dvw, None per
      un Excel.
    """
    if str(file_path).lower().endswith(".dvw"):
        return load_team_actions(file_path, team)
    df = pd.read_excel(file_path, skiprows=1)
    if "Numero Set" not in df.columns:
        raise ValueError(
            f"{file_path}: formato Excel non riconosciuto (atteso quello 2025-2026). "
            "Dalla stagione 2026-2027 registra in config/matches.csv il file .dvw della partita."
        )
    return df, (parse_decimo_locali(file_path) if team == "decimo" else None), None
