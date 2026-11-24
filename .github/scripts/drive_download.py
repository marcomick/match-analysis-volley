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


def main():
    dest = Path(sys.argv[1])
    key = json.loads(os.environ["GDRIVE_SA_KEY"])
    creds = service_account.Credentials.from_service_account_info(
        key, scopes=["https://www.googleapis.com/auth/drive.readonly"])
    counts = {}
    walk(AuthorizedSession(creds), os.environ["GDRIVE_FOLDER_ID"].strip(), Path(), dest, counts)
    print("scaricati:", ", ".join(f"{n} {ext}" for ext, n in sorted(counts.items())) or "niente")
    if not counts.get(".pdf"):
        sys.exit("nessun referto trovato: la cartella è condivisa con l'account di servizio?")


if __name__ == "__main__":
    main()
