# src/fipav.py
"""
Risultati ufficiali dal sito federale FIPAV Online, al posto del dump
manuale `risultati_decimo_{season}.txt` (dal 2026-2027).

La pagina pubblica del girone (https://public.fipavonline.it/gironi/<id>) è
un'applicazione JavaScript che legge i dati da un'API pubblica in JSON, senza
autenticazione: `https://app.fipavonline.it/api/v2/calendar/0/<id>` ritorna
tutto il calendario del girone (verificato il 2026-10-23 sul girone 63671,
Serie D Regionale Maschile girone A 2026-2027: 182 partite, 26 giornate).
Per partita: `day` ("Giornata N"), `date` ("gg/mm/aaaa"), `team1`/`team2`
(casa/ospite, `title`), `played`, `ris_ufficiale` ("1" = omologato),
`team1-setwin`/`team2-setwin`, `pt_a`/`pt_b` (punti per set, casa/ospite).

L'id del girone di ogni stagione è in config/seasons.csv (colonna
`fipav_girone`, vuota se la stagione non lo usa).
"""
import json
import urllib.request

API_URL = "https://app.fipavonline.it/api/v2/calendar/0/{girone}"
TEAM = "Decimo"
TIMEOUT_S = 15


def fetch_calendar(girone):
    """Calendario del girone (lista delle partite, formato dell'API)."""
    request = urllib.request.Request(API_URL.format(girone=girone), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        return json.load(response)["data"]["matches"]


def decimo_results_by_date(matches):
    """
    {data 'gg/mm/aaaa': risultato} per le partite del Decimo già giocate,
    nello stesso formato di src.leg_comparison.parse_official_results
    ({squadra_a, squadra_b, sets_a, sets_b, parziali}, a = casa) più
    `giornata` (int) e `ufficiale` (omologato dalla federazione o no).
    """
    out = {}
    for m in matches:
        home, away = m["team1"]["title"].strip(), m["team2"]["title"].strip()
        if TEAM.lower() not in (home + away).lower() or not m.get("played") or not m.get("pt_a"):
            continue
        out[m["date"]] = {
            "squadra_a": home, "squadra_b": away,
            "sets_a": int(m["team1-setwin"]), "sets_b": int(m["team2-setwin"]),
            "parziali": [(int(a), int(b)) for a, b in zip(m["pt_a"], m["pt_b"])],
            "giornata": int(m["day"].split()[-1]),
            "ufficiale": str(m.get("ris_ufficiale")) == "1",
        }
    return out


def load_decimo_results(girone):
    """Come decimo_results_by_date, scaricando il calendario. {} se il sito
    non risponde: le partite ricadono sul file risultati manuale o sul .dvw."""
    try:
        return decimo_results_by_date(fetch_calendar(girone))
    except Exception as e:
        print(f"Risultati FIPAV non disponibili (girone {girone}): {e}")
        return {}
