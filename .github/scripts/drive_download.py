"""
Scarica da Google Drive la cartella "Match Analysis" della stagione, per il workflow dei referti
(.github/workflows/referti.yml): i PDF dei referti di "scambio video/referti" e i .dvw delle altre
sottocartelle (da cui src/referti.py ricava i palleggiatori noti). Il resto (video, Excel...) no.

Accesso con un account di servizio Google con cui la cartella è condivisa in sola lettura:
- GDRIVE_SA_KEY: il file JSON della chiave dell'account di servizio (segreto di GitHub);
- GDRIVE_FOLDER_ID: l'id della cartella "Match Analysis" (dall'indirizzo della cartella su Drive).

Uso: python .github/scripts/drive_download.py <cartella di destinazione>
"""
import json
import os
import sys
from pathlib import Path

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

API = "https://www.googleapis.com/drive/v3/files"
FOLDER = "application/vnd.google-apps.folder"


def children(session, folder_id):
    params = {"q": f"'{folder_id}' in parents and trashed = false", "pageSize": 1000,
              "fields": "nextPageToken, files(id, name, mimeType)",
              "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
    while True:
        r = session.get(API, params=params)
        r.raise_for_status()
        data = r.json()
        yield from data.get("files", [])
        if not data.get("nextPageToken"):
            return
        params["pageToken"] = data["nextPageToken"]


def wanted(rel):
    """I PDF dei referti e i .dvw fuori da "scambio video"."""
    parts, name = rel.parts, rel.name.lower()
    if "scambio video" in parts:
        return name.endswith(".pdf") and "referti" in parts
    return name.endswith(".dvw")


def walk(session, folder_id, rel, dest, counts):
    for f in children(session, folder_id):
        path = rel / f["name"]
        if f["mimeType"] == FOLDER:
            if f["name"].lower() != "video":       # i video non servono e sono enormi
                walk(session, f["id"], path, dest, counts)
        elif wanted(path):
            r = session.get(f"{API}/{f['id']}", params={"alt": "media", "supportsAllDrives": "true"})
            r.raise_for_status()
            out = dest / path
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(r.content)
            counts[path.suffix.lower()] = counts.get(path.suffix.lower(), 0) + 1


def fail(msg):
    """Errore come annotazione dell'esecuzione (si legge anche senza aprire il registro) e uscita."""
    print(f"::error title=Referti da Drive::{msg}")
    sys.exit(1)


def main():
    dest = Path(sys.argv[1])
    raw, folder = os.environ.get("GDRIVE_SA_KEY", ""), os.environ.get("GDRIVE_FOLDER_ID", "").strip()
    if not raw.strip():
        fail("manca il segreto GDRIVE_SA_KEY (Settings → Secrets and variables → Actions)")
    if not folder:
        fail("manca il segreto GDRIVE_FOLDER_ID")
    if "/" in folder:                 # incollato l'indirizzo intero invece dell'id
        folder = folder.rstrip("/").split("/")[-1].split("?")[0]
    try:
        key = json.loads(raw)
    except json.JSONDecodeError as e:
        fail(f"GDRIVE_SA_KEY non è un JSON valido ({e.msg}, riga {e.lineno}): va incollato tutto il contenuto del file .json della chiave")
    if key.get("type") != "service_account":
        fail(f"GDRIVE_SA_KEY non è la chiave di un account di servizio (type = {key.get('type')!r})")
    print("account di servizio:", key.get("client_email"), "| cartella:", folder)
    creds = service_account.Credentials.from_service_account_info(
        key, scopes=["https://www.googleapis.com/auth/drive.readonly"])
    session = AuthorizedSession(creds)
    r = session.get(f"{API}/{folder}", params={"fields": "id, name, mimeType", "supportsAllDrives": "true"})
    if not r.ok:
        try:
            err = r.json().get("error", {})
            reason = (err.get("errors") or [{}])[0].get("reason", "")
            text = err.get("message", "")
        except ValueError:
            reason, text = "", r.text[:200]
        hint = {403: "se il messaggio parla di API non abilitata: Google Cloud → API e servizi → Libreria → Google Drive API → Abilita (nel progetto dell'account di servizio)",
                404: f"cartella non trovata: controlla GDRIVE_FOLDER_ID e che la cartella sia condivisa con {key.get('client_email')}"}.get(r.status_code, "")
        fail(f"Drive ha risposto {r.status_code} {reason}: {text} {('— ' + hint) if hint else ''}")
    meta = r.json()
    if meta.get("mimeType") != FOLDER:
        fail(f"GDRIVE_FOLDER_ID indica \"{meta.get('name')}\", che non è una cartella")
    print("cartella:", meta.get("name"))
    counts = {}
    walk(session, folder, Path(), dest, counts)
    print("scaricati:", ", ".join(f"{n} {ext}" for ext, n in sorted(counts.items())) or "niente")
    if not counts.get(".pdf"):
        fail(f"nella cartella \"{meta.get('name')}\" non ci sono referti in \"scambio video/referti\": è la cartella \"Match Analysis\" della stagione?")
    print(f"::notice title=Referti da Drive::scaricati {counts.get('.pdf', 0)} referti e {counts.get('.dvw', 0)} .dvw dalla cartella \"{meta.get('name')}\"")


if __name__ == "__main__":
    main()
