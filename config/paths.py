# config/paths.py
import os
import pandas as pd
from pathlib import Path

# ---------------------------------------------------------------------------
# Rilevamento ambiente
# ---------------------------------------------------------------------------

def is_colab() -> bool:
    return "COLAB_RELEASE_TAG" in os.environ or os.path.exists("/content")


def get_base_path() -> Path:
    """
    Ritorna il path base alla cartella dei dati.
    - Su Colab: usa il path Drive hardcodato in seasons.csv
    - In locale: legge VOLLEY_DATA_PATH da .env
    """
    if is_colab():
        return None  # il path drive viene costruito per stagione
    
    # carica .env se presente
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    
    local_path = os.environ.get("VOLLEY_DATA_PATH")
    if not local_path:
        raise EnvironmentError(
            "Variabile VOLLEY_DATA_PATH non trovata.\n"
            "Copia .env.example in .env e imposta il percorso locale."
        )
    return Path(local_path)


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
    
    drive_path = season_row["base_path_drive"].iloc[0]

    # costruisce il base path in base all'ambiente
    if is_colab():
        base = Path("/content/drive/MyDrive") / drive_path
    else:
        base = get_base_path()

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

    drive_path = season_row["base_path_drive"].iloc[0]

    if is_colab():
        return Path("/content/drive/MyDrive") / drive_path
    else:
        return get_base_path()


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