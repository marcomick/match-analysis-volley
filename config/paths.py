# config/paths.py
import os
import pandas as pd
from pathlib import Path

# ---------------------------------------------------------------------------
# Rilevamento ambiente
# ---------------------------------------------------------------------------

def is_colab() -> bool:
    return "COLAB_RELEASE_TAG" in os.environ or os.path.exists("/content")


def _load_dotenv():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass


def get_base_path() -> Path:
    """
    Path legacy alla cartella dati di UNA stagione (VOLLEY_DATA_PATH in .env).
    Usato solo se VOLLEY_DRIVE_ROOT non è impostata — vedi season_base_path.
    """
    if is_colab():
        return None  # il path drive viene costruito per stagione

    _load_dotenv()
    local_path = os.environ.get("VOLLEY_DATA_PATH")
    if not local_path:
        raise EnvironmentError(
            "Variabili VOLLEY_DRIVE_ROOT / VOLLEY_DATA_PATH non trovate.\n"
            "Copia .env.example in .env e imposta il percorso locale."
        )
    return Path(local_path)


def season_base_path(drive_path: str) -> Path:
    """
    Cartella dati di una stagione, dato il suo base_path_drive (seasons.csv,
    relativo alla radice di Google Drive).
    - Su Colab: /content/drive/MyDrive / base_path_drive
    - In locale con VOLLEY_DRIVE_ROOT (radice "Il mio Drive"): stessa logica
      di Colab, quindi ogni stagione punta alla propria cartella
    - In locale con solo VOLLEY_DATA_PATH (legacy): quel path così com'è —
      valido per una sola stagione, quella a cui punta
    """
    if is_colab():
        return Path("/content/drive/MyDrive") / drive_path

    _load_dotenv()
    drive_root = os.environ.get("VOLLEY_DRIVE_ROOT")
    if drive_root:
        return Path(drive_root) / drive_path
    return get_base_path()


# ---------------------------------------------------------------------------
# Caricamento config stagioni e partite
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parent.parent  # root del repo


def load_seasons() -> pd.DataFrame:
    return pd.read_csv(ROOT / "config" / "seasons.csv")


def load_matches() -> pd.DataFrame:
    return pd.read_csv(ROOT / "config" / "matches.csv")


def build_file_list(season: str) -> tuple[list, list]:
    """
    Ritorna (FILES, avversari) per la stagione richiesta,
    con path assoluti corretti per l'ambiente corrente.
    """
    seasons = load_seasons()
    matches = load_matches()

    # recupera il path Drive per questa stagione
    season_row = seasons[seasons["season"] == season]
    if season_row.empty:
        raise ValueError(f"Stagione '{season}' non trovata in seasons.csv")
    
    base = season_base_path(season_row["base_path_drive"].iloc[0])

    # filtra le partite attive per la stagione
    season_matches = matches[
        (matches["season"] == season) & (matches["active"] == 1)
    ]

    FILES = [str(base / row["path"]) for _, row in season_matches.iterrows()]
    avversari = [f"{row['leg']}-{row['opponent']}" for _, row in season_matches.iterrows()]

    return FILES, avversari

def build_base_path(season: str) -> Path:
    """
    Ritorna il path base per una stagione specifica.
    Usato da tabellino.ipynb per costruire i path dei singoli file.
    """
    seasons = load_seasons()
    season_row = seasons[seasons["season"] == season]
    if season_row.empty:
        raise ValueError(f"Stagione '{season}' non trovata in seasons.csv")

    return season_base_path(season_row["base_path_drive"].iloc[0])


def build_competition_root_path(season: str) -> Path:
    """
    Cartella della competizione per la stagione, un livello sopra
    'Match analysis' (dove punta base_path_drive) — usata per file
    team-wide non specifici di una singola partita, es. il foglio
    presenze U19 (vedi src/attendance.py). Assume che base_path_drive
    termini con 'Match analysis', convenzione attuale in seasons.csv;
    se in futuro cambia, questa funzione va aggiornata di conseguenza.
    """
    return build_base_path(season).parent