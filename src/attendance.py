"""
Presenze agli allenamenti/partite del gruppo Serie D (foglio "Presenze D").

Fonte: workbook 'Serie D_u19.xlsx' su Google Drive (nome file configurabile
via PRESENCE_FILENAME), foglio 'Presenze D'. Il file è mantenuto come .xlsx
reale (non il vecchio Google Sheet nativo ".gsheet", che non sincronizza
un binario locale) — si apre/edita direttamente quel file (da Sheets in
modalità compatibilità Excel, o da Excel/Numbers) così Google Drive lo
sincronizza come file reale, leggibile qui senza export manuale ricorrente.

Struttura del foglio (0-indexed dopo pd.read_excel(header=None)):
  - riga con la cella 'Nome' (header_row, auto-rilevata, non hardcodata:
    verificata stagione 2025-2026 = riga Excel 6) — nella stessa riga anche
    'gruppo' e 'Ruolo' se presenti.
  - riga immediatamente sopra (row_date): data dell'evento (datetime reale,
    non una stringa 'gg/mm' come inizialmente ipotizzato).
  - riga due sopra (row_sigla): sigla per colonna, vedi _classify_sigla.
  - righe sotto l'header: una riga per giocatore, fino a fine foglio.
    L'ultima riga del foglio (stagione 2025-2026) è un totale numerico per
    colonna, esclusa automaticamente perché ha 'Nome' vuoto.

Sigle di riga 'row_sigla' (verificate sui dati reali stagione 2025-2026,
confermate con l'utente il 2026-08-12):
  - vuoto -> allenamento normale
  - 'NO ALL' -> allenamento non tenuto (escluso dall'analisi: l'evento non
    è avvenuto, nessun giudizio di presenza/assenza è possibile)
  - 'PESI' -> sessione pesi (esclusa dall'analisi, non è allenamento di
    gruppo)
  - '<COD>-C' / '<COD>-T' -> partita di campionato (C=casa, T=trasferta)
  - sigle contenenti 'playout' (es. 'D-playout/a - jvc', 'D-playout/r - jvc')
    -> partita di playout, ATTENZIONE: playout (salvezza), non playoff
    (promozione) — vedi memoria 'decimo-playout-not-playoff'
  - sigle che iniziano con 'AMI' (AMI, ami, AMI-SG, case-insensitive)
    -> amichevole
  - sigle che iniziano con 'u19-4i' (case-insensitive, es. 'u19-4i/a',
    'u19-4i/r') -> evento U19 a parte (torneo/incontro specifico U19)
  - qualsiasi altra sigla non vuota (es. 'ALL U19', 'cong.2div', 'D', '2d',
    'D/2d') -> resta 'allenamento': indica solo quali sottogruppi si sono
    allenati insieme quel giorno, non cambia il tipo di evento. La sigla
    grezza è comunque conservata in 'sigla_raw' per riferimento.

Stati cella (colonna evento per riga giocatore), verificati sui dati reali
e confermati con l'utente il 2026-08-12:
  - P, Pi/PI, P?, P_spar -> presente (varianti tutte trattate come presenza;
    P_spar = presente con ruolo di sparring)
  - A -> assente, giustificata (ha avvisato)
  - I -> assente per infortunio
  - N -> assente, NON giustificata (non ha avvisato) — trattata come le
    altre assenze nel conteggio aggregato, ma la motivazione resta
    distinguibile (vedi motivo_assenza/n_assente_non_giustificata)
  - NC, '-' -> non convocato (escluso da numeratore e denominatore). '-' è
    quasi assente (2-3 occorrenze) per i giocatori tracciati tutta la
    stagione, dominante per i giocatori part-time/IGNORED — conferma
    empirica del significato "non convocato/non pertinente"
  - U19 -> quel giorno era con la squadra U19, non pertinente per il
    gruppo D (escluso da numeratore e denominatore)
  - R -> riposo concesso (escluso da numeratore e denominatore)
  - '?' -> dato mancante nel periodo tracciato (escluso dal denominatore,
    ma tracciato a parte in n_ignoto per segnalare buchi nei dati)
  - cella vuota (NaN) -> fuori dal periodo tracciato per quel giocatore
    (es. prima che entrasse nel gruppo, o dopo che l'ha lasciato) —
    escluso da numeratore e denominatore, distinto da '?' perché non è un
    dato mancante ma un evento non pertinente per definizione
  - qualsiasi altro valore non riconosciuto -> 'ignoto'/'non_riconosciuto',
    MAI silenziosamente ricondotto a una delle categorie note: usare
    list_unrecognized_statuses() per accorgersene prima di fidarsi del
    report (utile soprattutto per stagioni future, se compaiono sigle
    nuove non ancora viste).

Ambito giocatori: l'inclusione nell'analisi NON si basa sulla colonna
'gruppo' del foglio (che non corrisponde sempre 1:1 a "gioca in Serie D":
es. nella stagione 2025-2026 'Liberatori' è gruppo='2d' nel foglio presenze
ma è un giocatore Decimo riconosciuto in player_identities.csv) — si basa
invece sul cognome confrontato con config/player_identities.csv (team
Decimo, non IGNORED), stessa fonte di verità usata da src/leg_comparison.py.
Giocatori presenti nel foglio ma assenti dal registro (es. 'Valvona' nella
stagione 2025-2026, 92 eventi tracciati ma mai aggiunto a
player_identities.csv, presumibilmente perché non ha mai giocato una
partita di Serie D) vengono segnalati da filter_known_players(), non
scartati silenziosamente.
"""
import re

import numpy as np
import pandas as pd

from config.paths import build_competition_root_path
from src.leg_comparison import IDENTITIES_CSV, TEAM

PRESENCE_SHEET_NAME = "Presenze D"
PRESENCE_FILENAME = "Serie D_u19.xlsx"

SIGLA_TRAINING_SKIPPED = "NO ALL"
SIGLA_WEIGHTS = "PESI"

# Eventi che richiedono un giudizio di presenza/assenza; esclusi per
# costruzione (non passati come event_type qui): 'allenamento_non_tenuto'
# (l'evento non è avvenuto) e 'pesi' (non è una sessione di gruppo).
DEFAULT_EVENT_TYPES = ("allenamento", "partita", "partita_playout", "amichevole", "evento_u19")

_MATCH_RE = re.compile(r"^(.+)-([TC])$")

_PRESENT = {"P", "PI", "P?", "P_SPAR"}
_ABSENT_GIUSTIFICATA = {"A"}
_ABSENT_INFORTUNIO = {"I"}
_ABSENT_NON_GIUSTIFICATA = {"N"}
_NON_CONVOCATO = {"-", "NC"}
_UNKNOWN_MARKER = {"?"}

# Token riconosciuti: usato da list_unrecognized_statuses per segnalare
# qualsiasi valore che non rientri in nessuna delle categorie note.
_KNOWN_STATUS_TOKENS = (
    _PRESENT | _ABSENT_GIUSTIFICATA | _ABSENT_INFORTUNIO | _ABSENT_NON_GIUSTIFICATA
    | _NON_CONVOCATO | _UNKNOWN_MARKER | {"U19", "R"}
)


def _presence_file_path(season):
    return build_competition_root_path(season) / PRESENCE_FILENAME


def _find_header_row_and_col(raw, label, search_rows=10):
    """Cerca la cella con testo `label` (case-insensitive) nelle prime
    `search_rows` righe del foglio. Fallisce rumorosamente se non trovata:
    meglio un errore chiaro che una posizione indovinata."""
    for r in range(min(search_rows, len(raw))):
        row = raw.iloc[r]
        for c, val in enumerate(row):
            if isinstance(val, str) and val.strip().lower() == label.lower():
                return r, c
    raise ValueError(
        f"Colonna '{label}' non trovata nelle prime {search_rows} righe "
        f"del foglio '{PRESENCE_SHEET_NAME}' — struttura del file cambiata?"
    )


def _classify_sigla(sigla):
    """Ritorna (event_type, opponent, sede) a partire dalla sigla di riga
    'row_sigla'. Vedi il docstring del modulo per la casistica completa."""
    if pd.isna(sigla) or str(sigla).strip() == "":
        return {"event_type": "allenamento", "opponent": None, "sede": None}
    s = str(sigla).strip()
    s_low = s.lower()
    s_up = s.upper()

    if s_up == SIGLA_TRAINING_SKIPPED:
        return {"event_type": "allenamento_non_tenuto", "opponent": None, "sede": None}
    if s_up == SIGLA_WEIGHTS:
        return {"event_type": "pesi", "opponent": None, "sede": None}
    if "playout" in s_low:
        leg = "andata" if "/a" in s_low else ("ritorno" if "/r" in s_low else None)
        opponent = s.split("-")[-1].strip().upper() if "-" in s else None
        return {"event_type": "partita_playout", "opponent": opponent, "sede": leg}
    if s_low.startswith("ami"):
        return {"event_type": "amichevole", "opponent": None, "sede": None}
    if s_low.startswith("u19-4i"):
        return {"event_type": "evento_u19", "opponent": None, "sede": None}
    m = _MATCH_RE.match(s_up)
    if m:
        opponent_code, sede_code = m.group(1), m.group(2)
        sede = "trasferta" if sede_code == "T" else "casa"
        return {"event_type": "partita", "opponent": opponent_code, "sede": sede}
    # sottogruppi di allenamento (es. 'ALL U19', 'cong.2div', 'D', '2d', 'D/2d'):
    # non cambiano il tipo di evento, resta 'allenamento'.
    return {"event_type": "allenamento", "opponent": None, "sede": None}


def _status_category(raw_status):
    """
    Ritorna (categoria, motivo). categoria è una tra:
    'presente', 'assente', 'escluso', 'ignoto'.
    """
    if pd.isna(raw_status):
        return "escluso", "fuori_periodo"
    s = str(raw_status).strip()
    if s == "":
        return "escluso", "fuori_periodo"
    s_up = s.upper()

    if s_up in _PRESENT:
        return "presente", None
    if s_up in _ABSENT_GIUSTIFICATA:
        return "assente", "giustificata"
    if s_up in _ABSENT_INFORTUNIO:
        return "assente", "infortunio"
    if s_up in _ABSENT_NON_GIUSTIFICATA:
        return "assente", "non_giustificata"
    if s_up in _NON_CONVOCATO:
        return "escluso", "non_convocato"
    if s_up == "U19":
        return "escluso", "altrove_u19"
    if s_up == "R":
        return "escluso", "riposo"
    if s_up in _UNKNOWN_MARKER:
        return "ignoto", "dato_mancante"
    return "ignoto", "non_riconosciuto"


def _normalize_cognome(raw):
    """Rimuove l'iniziale disambiguante (es. 'Ferrazzi A.' -> 'Ferrazzi')
    e normalizza come altrove nel progetto (replace apostrofo tipografico
    + .capitalize(), stessa convenzione di _normalize_and_filter_players
    in src/leg_comparison.py)."""
    s = str(raw).replace("’", "'").strip()
    s = re.sub(r"\s+[A-Za-z]\.$", "", s)
    return s.capitalize()


def parse_presence_sheet(file_path, season):
    """Legge il foglio 'Presenze D' e ritorna un DataFrame tidy: una riga
    per (giocatore, evento), con tipo evento, sigla grezza, stato grezzo e
    categoria/motivo già classificati. Non filtra sul registro giocatori
    (vedi filter_known_players) né su event_type (vedi compute_attendance_summary)."""
    raw = pd.read_excel(file_path, sheet_name=PRESENCE_SHEET_NAME, header=None)
    header_row, nome_col = _find_header_row_and_col(raw, "Nome")
    row_date = header_row - 1
    row_sigla = header_row - 2
    first_player_row = header_row + 1

    header_vals = raw.iloc[header_row]
    gruppo_col = next(
        (c for c, v in enumerate(header_vals) if isinstance(v, str) and v.strip().lower() == "gruppo"), None
    )
    ruolo_col = next(
        (c for c, v in enumerate(header_vals) if isinstance(v, str) and v.strip().lower() == "ruolo"), None
    )

    event_cols = [c for c in range(nome_col + 1, raw.shape[1]) if pd.notna(raw.iat[row_date, c])]
    if not event_cols:
        raise ValueError(
            f"Nessuna colonna evento trovata (riga data, indice {row_date}, tutta vuota) "
            f"nel foglio '{PRESENCE_SHEET_NAME}'."
        )

    events = []
    for c in event_cols:
        sigla = raw.iat[row_sigla, c]
        info = _classify_sigla(sigla)
        event_date = raw.iat[row_date, c]
        events.append(
            {
                "col": c,
                "sigla_raw": None if pd.isna(sigla) else str(sigla).strip(),
                "event_date": pd.Timestamp(event_date) if pd.notna(event_date) else pd.NaT,
                **info,
            }
        )

    rows = []
    for r in range(first_player_row, raw.shape[0]):
        cognome_raw = raw.iat[r, nome_col]
        if pd.isna(cognome_raw) or str(cognome_raw).strip() == "":
            continue
        cognome_raw = str(cognome_raw).strip()
        gruppo = raw.iat[r, gruppo_col] if gruppo_col is not None else None
        ruolo = raw.iat[r, ruolo_col] if ruolo_col is not None else None
        for ev in events:
            status_raw = raw.iat[r, ev["col"]]
            categoria, motivo = _status_category(status_raw)
            rows.append(
                {
                    "cognome_raw": cognome_raw,
                    "cognome": _normalize_cognome(cognome_raw),
                    "gruppo": gruppo,
                    "ruolo": ruolo,
                    "event_date": ev["event_date"],
                    "event_type": ev["event_type"],
                    "sigla_raw": ev["sigla_raw"],
                    "opponent": ev.get("opponent"),
                    "sede": ev.get("sede"),
                    "status_raw": None if pd.isna(status_raw) else str(status_raw).strip(),
                    "categoria": categoria,
                    "motivo": motivo,
                }
            )

    return pd.DataFrame(rows)


def load_presence(season="2025-2026"):
    """Carica e parsa il foglio presenze per la stagione indicata."""
    return parse_presence_sheet(_presence_file_path(season), season)


def _load_known_decimo_cognomi():
    identities = pd.read_csv(IDENTITIES_CSV)
    known = identities[(identities["team"] == TEAM) & (identities["player_id"] != "IGNORED")]
    return set(known["cognome"].unique())


def filter_known_players(presence_df):
    """
    Mantiene solo i giocatori riconosciuti in player_identities.csv (team
    Decimo, non IGNORED) — stessa fonte di verità di src/leg_comparison.py.
    Il confronto è solo sul cognome (il foglio presenze non riporta il
    numero di maglia): non è un problema nella stagione 2025-2026
    (verificato, nessun cognome Decimo duplicato tra i giocatori
    riconosciuti), da riverificare se in futuro capitasse un omonimo.

    Ritorna (df_filtrato, cognomi_non_in_registro): il secondo elenco
    segnala giocatori presenti nel foglio ma assenti dal registro (es. non
    hanno mai giocato una partita di Serie D) — da mostrare, non scartare
    silenziosamente.
    """
    known = _load_known_decimo_cognomi()
    all_cognomi = set(presence_df["cognome"].unique())
    not_in_registry = sorted(c for c in all_cognomi if c not in known)
    mask = presence_df["cognome"].isin(known)
    return presence_df[mask].copy(), not_in_registry


def list_unrecognized_statuses(presence_df):
    """Valori di status_raw non riconosciuti da nessuna delle categorie
    note (esclude '?' e cella vuota, che sono categorie note per 'ignoto').
    Da controllare a inizio stagione o dopo modifiche al foglio, prima di
    fidarsi del report: una sigla nuova non vista finora finirebbe
    altrimenti silenziosamente in 'ignoto'/'non_riconosciuto'."""
    raw_vals = presence_df["status_raw"].dropna().unique()
    return sorted(v for v in raw_vals if v.strip().upper() not in _KNOWN_STATUS_TOKENS)


def _aggregate_group(g):
    n_presente = int((g["categoria"] == "presente").sum())
    n_assente = int((g["categoria"] == "assente").sum())
    n_escluso = int((g["categoria"] == "escluso").sum())
    n_ignoto = int((g["categoria"] == "ignoto").sum())
    n_convocato = n_presente + n_assente
    motivo_assenza = g.loc[g["categoria"] == "assente", "motivo"].value_counts()
    return {
        "n_eventi_totali": len(g),
        "n_convocato": n_convocato,
        "n_presente": n_presente,
        "n_assente": n_assente,
        "n_assente_giustificata": int(motivo_assenza.get("giustificata", 0)),
        "n_assente_infortunio": int(motivo_assenza.get("infortunio", 0)),
        "n_assente_non_giustificata": int(motivo_assenza.get("non_giustificata", 0)),
        "n_escluso": n_escluso,
        "n_ignoto": n_ignoto,
        "tasso_assenza": (n_assente / n_convocato) if n_convocato else np.nan,
    }


def compute_attendance_summary(presence_df, event_types=DEFAULT_EVENT_TYPES):
    """
    Aggrega per giocatore: assenze (A + I + N) sul totale dei convocati
    (n_presente + n_assente — esclude NC/'-'/U19/R/fuori-periodo dal
    denominatore, ed esclude '?'/non riconosciuti come dato mancante).
    Filtra su event_types (default: tutti tranne 'allenamento_non_tenuto'
    e 'pesi', esclusi per costruzione — vedi DEFAULT_EVENT_TYPES).
    """
    df = presence_df[presence_df["event_type"].isin(event_types)]
    rows = []
    for cognome, g in df.groupby("cognome"):
        rows.append({"cognome": cognome, **_aggregate_group(g)})
    return pd.DataFrame(rows).sort_values("tasso_assenza", ascending=False).reset_index(drop=True)


def compute_attendance_summary_by_type(presence_df, event_types=DEFAULT_EVENT_TYPES):
    """Come compute_attendance_summary, ma con una riga per (giocatore,
    event_type) — utile per distinguere es. assenze agli allenamenti da
    assenze alle partite nella pagella."""
    df = presence_df[presence_df["event_type"].isin(event_types)]
    rows = []
    for (cognome, event_type), g in df.groupby(["cognome", "event_type"]):
        rows.append({"cognome": cognome, "event_type": event_type, **_aggregate_group(g)})
    return pd.DataFrame(rows).sort_values(["cognome", "event_type"]).reset_index(drop=True)
