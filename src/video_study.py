"""
Studio video (richiesta dell'utente del 2026-11-27): dagli scout fatti con lo scout a tocchi (scout/index.html), che
scrivono sul .dvw il minuto del video di ogni azione, una riga per azione con quello che serve per filtrarle
(squadra, giocatore, fondamentale, voto, situazione dell'attacco, linea del palleggiatore, rotazione, tipo di palla,
finale di set, scambio vinto) e il secondo del video. La pagina video/index.html (src.video_server) le riproduce in fila.

Gli scout del programma vecchio (Volleyball Scout) hanno nella colonna del tempo l'ora della rilevazione, non il
minuto del video: non si usano (`is_video_scout`).

Link dei video: `video_scout.json` nella cartella della stagione su Drive, chiave = nome del file dello scout,
{"url": link YouTube o percorso di un file video, "offset": secondi da aggiungere ai tempi dello scout}.
"""
import json
import re
from pathlib import Path

import pandas as pd

from src.dvw import normalize_team_name, parse_dvw, role_codes, side_actions

VIDEO_LINKS_FILE = "video_scout.json"
SCOUT_APP = "Scout a tocchi"
SKIP_TYPES = {"cambio configurazione", "punto generico avversario"}
FAST = {"T", "U", "Q", "N", "F"}          # super, quick, "7", fast (come src.set_analysis.BALL_FAST)
HIGH = {"H"}
# tipo di battuta, 5° carattere del codice di battuta e di ricezione (come lo scrive lo scout a tocchi)
SERVE_TYPES = {"M": "salto float", "Q": "salto spin", "H": "float a terra"}
SITUATIONS = ["R#/R+", "R!", "R-", "Free ball", "Contrattacco"]
SKILL_ORDER = ["battuta", "ricezione", "alzata", "attacco", "muro", "rigore",
               "difesa", "free ball"]


def is_video_scout(path):
    """True se lo scout è dello scout a tocchi (tempi = minuto del video)."""
    with open(path, encoding="cp1252", errors="replace") as f:
        for _, line in zip(range(12), f):
            if line.startswith("GENERATOR-PRG:") and SCOUT_APP in line:
                return True
    return False


def _seconds(v):
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", str(v or "").strip())
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) if m else None


def _situation(prev):
    """Situazione dell'attacco dall'ultima azione della squadra prima dell'attacco (alzate escluse)."""
    if prev is None:
        return "Contrattacco"
    t, v = prev
    if t == "ricezione":
        return {"#": "R#/R+", "+": "R#/R+", "!": "R!", "-": "R-"}.get(v, "Contrattacco")
    if t == "free ball" or (t == "difesa" and v == "!"):
        return "Free ball"
    return "Contrattacco"


def video_actions(path, match_label=None):
    """Una riga per azione (entrambe le squadre) di uno scout dello scout a tocchi."""
    P = parse_dvw(path)
    winner = P["rallies"].set_index("Rally")["vincitore"].to_dict() if len(P["rallies"]) else {}
    label = match_label or Path(path).stem
    out = []
    for side in ("*", "a"):
        team = P["teams"].get(side) or ("casa" if side == "*" else "ospite")
        sig = role_codes(P["players"], side)
        A = side_actions(P, side)
        own_pts, opp_pts = ("Punti Locali", "Punti Ospiti") if side == "*" else ("Punti Ospiti", "Punti Locali")
        last = {}                                   # rally -> ultima azione della squadra (alzate escluse)
        nth = {}                                    # rally -> azioni della squadra già viste (per la chiave)
        for x in A.to_dict("records"):
            k_rally = nth[x["Rally"]] = nth.get(x["Rally"], -1) + 1
            tipo = x["Tipo"]
            if tipo in SKIP_TYPES:
                continue
            rally = x["Rally"]
            sec = _seconds(x.get("Video"))
            cogn = str(x.get("Cognome") or "").strip().replace("’", "'")
            num, sigla = sig.get(cogn, (x.get("Numero"), None))
            try:
                num = int(num)
            except (TypeError, ValueError):
                num = None
            who = f"{cogn} ({num}, {sigla})" if sigla else (f"{cogn} ({num})" if num is not None else cogn)
            zp = str(x.get("Posizione Palleggiatore") or "")
            rot = f"P{zp[-1]}" if zp[-1:].isdigit() else None
            tp = x.get("Tipo palla stimato") if isinstance(x.get("Tipo palla stimato"), str) else x.get("Tipo palla")
            is_att = tipo.startswith("attacco")
            sett = int(x["Numero Set"]) if pd.notna(x.get("Numero Set")) else None
            a_, b_ = int(x.get(own_pts) or 0), int(x.get(opp_pts) or 0)
            final = (max(a_, b_) >= 20 and abs(a_ - b_) <= 3) or (sett == 5 and max(a_, b_) >= 10 and abs(a_ - b_) <= 3)
            out.append({
                # chiave stabile dell'azione per l'allineamento a mano: squadra, scambio, ordine nello scambio, fondamentale
                "chiave": f"{side}{rally}.{k_rally}{tipo[:1]}{num if num is not None else ''}",
                "alzata_negativa": tipo == "attacco da alzata negativa",
                "partita": label, "file": Path(path).name, "squadra": team, "squadra_key": normalize_team_name(team),
                "lato": side, "set": sett, "punteggio": f"{a_}-{b_}", "sec": sec,
                "fondamentale": "attacco" if is_att else tipo, "voto": x.get("Voto"), "giocatore": who, "cognome": cogn, "numero": num,
                "sigla": sigla,
                "situazione": _situation(last.get(rally)) if is_att else None,
                "rotazione": rot,
                "linea": None if rot is None else ("dietro" if rot in ("P1", "P6", "P5") else "davanti"),
                "palla": ("veloce" if tp in FAST else "alta" if tp in HIGH else None) if is_att else None,
                "colpo": ("pallonetto" if x.get("Tipo colpo") == "T" else "forte") if is_att else None,
                "tipo_battuta": SERVE_TYPES.get(str(x.get("Codice") or "")[4:5]) if tipo in ("battuta", "ricezione") else None,
                "finale": bool(final),
                "scambio_vinto": None if rally not in winner else winner[rally] == side,
            })
            if tipo != "alzata":
                last[rally] = (tipo, x.get("Voto"))
    df = pd.DataFrame(out)
    return df[df["sec"].notna()].reset_index(drop=True) if len(df) else df


# ---------------------------------------------------------------------------- link dei video
def youtube_id(url):
    """Id di un video YouTube da un link (watch, youtu.be, embed, shorts, live) o None."""
    m = re.search(r"(?:v=|youtu\.be/|embed/|shorts/|live/)([A-Za-z0-9_-]{11})", str(url or ""))
    return m.group(1) if m else None


def load_video_links(base_path):
    try:
        with open(Path(base_path) / VIDEO_LINKS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def save_video_link(base_path, scout_file, url, offset=0):
    links = load_video_links(base_path)
    if str(url or "").strip():
        links[scout_file] = {**links.get(scout_file, {}), "url": str(url).strip(), "offset": float(offset or 0)}
    else:
        links.pop(scout_file, None)
    with open(Path(base_path) / VIDEO_LINKS_FILE, "w", encoding="utf-8") as f:
        json.dump(links, f, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------------------- allineamento a mano
# Il minuto segnato nello scout è spesso un po' in ritardo, ma non di una quantità fissa (indicazione dell'utente del
# 2026-11-27). Per ogni azione si può fissare a mano l'istante vero (video_scout.json, "fix": {chiave: secondo}); le
# altre azioni dello stesso video prendono lo scarto delle azioni fissate vicine (interpolato nel tempo). Stessa regola
# nella pagina dello studio video (video/index.html).
def save_fix(base_path, scout_file, key, t):
    links = load_video_links(base_path)
    entry = links.setdefault(scout_file, {})
    fix = entry.setdefault("fix", {})
    if t is None:
        fix.pop(key, None)
    else:
        fix[key] = round(float(t), 2)
    with open(Path(base_path) / VIDEO_LINKS_FILE, "w", encoding="utf-8") as f:
        json.dump(links, f, ensure_ascii=False, indent=1)


def aligned_time(raw, key, fixes, raws):
    """Istante dell'azione nel video: quello fissato a mano, altrimenti `raw` più lo scarto interpolato tra le azioni
    fissate più vicine (`raws`: chiave -> tempo grezzo, per le azioni fissate). (tempo, fissata?)"""
    if key in fixes:
        return float(fixes[key]), True
    pts = sorted((raws[k], fixes[k] - raws[k]) for k in fixes if k in raws)
    if not pts:
        return float(raw), False
    if raw <= pts[0][0]:
        return raw + pts[0][1], False
    if raw >= pts[-1][0]:
        return raw + pts[-1][1], False
    for (x0, o0), (x1, o1) in zip(pts, pts[1:]):
        if x0 <= raw <= x1:
            return raw + (o0 + (o1 - o0) * (raw - x0) / (x1 - x0) if x1 > x0 else o0), False
    return float(raw), False


# ---------------------------------------------------------------------------- dati per lo studio video (video/index.html)
# Lo studio video è una pagina a sé (richiesta dell'utente del 2026-11-27: nella dashboard ogni filtro rifaceva tutta la
# pagina): il server locale (src.video_server) manda in una volta tutte le azioni della stagione e la pagina filtra nel
# browser. Aprire gli scout su Drive è lento (circa 3 s l'uno), quindi quali hanno il minuto del video e le loro azioni
# si tengono in una cache su disco, rifatta solo per i file cambiati.
CACHE_FILE = Path(__file__).resolve().parent.parent / "data" / "video_study_cache.pkl"
ACTION_FIELDS = ["chiave", "alzata_negativa", "partita", "file", "squadra", "lato", "set", "punteggio", "sec",
                 "fondamentale", "voto", "giocatore", "situazione", "rotazione", "linea", "palla", "colpo", "finale",
                 "scambio_vinto", "tipo_battuta"]
CACHE_VERSION = 2               # da aumentare quando cambiano i campi delle azioni: la cache si rifà


def _load_cache():
    import pickle
    try:
        with open(CACHE_FILE, "rb") as f:
            c = pickle.load(f)
        return c if c.get("_versione") == CACHE_VERSION else {"_versione": CACHE_VERSION}
    except Exception:  # noqa: BLE001 - cache assente o vecchia: si rifà
        return {"_versione": CACHE_VERSION}


def _save_cache(cache):
    import pickle
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        pickle.dump(cache, f)
    tmp.replace(CACHE_FILE)


def _clean(v):
    if v is None or (isinstance(v, float) and v != v):
        return None
    if hasattr(v, "item"):
        return v.item()
    return v


def season_scouts(base_path):
    """[(percorso, azioni come lista di dizionari)] degli scout con il minuto del video nella cartella della stagione."""
    import glob
    import os
    cache, changed, out = _load_cache(), False, []
    for f in sorted(glob.glob(str(Path(base_path) / "**" / "*.dvw"), recursive=True)):
        mt = os.path.getmtime(f)
        hit = cache.get(f)
        if not hit or hit[0] != mt:
            ok = is_video_scout(f)
            rows = []
            if ok:
                df = video_actions(f)
                rows = [{k: _clean(r.get(k)) for k in ACTION_FIELDS + ["squadra_key"]} for r in df.to_dict("records")]
            hit = cache[f] = (mt, ok, rows)
            changed = True
        if hit[1]:
            out.append((f, hit[2]))
    for f in [f for f in cache if f != "_versione" and not Path(f).exists() and str(f).startswith(str(base_path))]:
        cache.pop(f)
        changed = True
    if changed:
        _save_cache(cache)
    return out


def season_data(base_path, file_src):
    """Tutto quello che serve alla pagina dello studio video. file_src(link, base) -> sorgente del video o None."""
    scouts = season_scouts(base_path)
    links = load_video_links(base_path)
    actions = [dict(a) for _, rows in scouts for a in rows]
    # stessa squadra scritta in due modi negli scout ("Valsugana" e "Valsugana Panizzo"): vale il nome più lungo
    keys = sorted({a["squadra_key"] for a in actions}, key=len, reverse=True)
    unify = {k: next(o for o in keys if o == k or o.startswith(k + " ")) for k in keys}
    longest = {}
    for a in actions:
        u = unify[a["squadra_key"]]
        if len(a["squadra"]) > len(longest.get(u, "")):
            longest[u] = a["squadra"]
    for a in actions:
        a["squadra"] = longest[unify[a.pop("squadra_key")]]
    files = []
    for f, _ in scouts:
        name = Path(f).name
        link = links.get(name, {})
        src = file_src(link, Path(base_path)) if link else None
        files.append({"file": name, "label": Path(f).stem, "url": link.get("url", ""),
                      "offset": float(link.get("offset", 0) or 0), "fix": link.get("fix", {}),
                      "src": src, "bad_link": bool(link.get("url")) and src is None})
    return {"base": str(base_path), "files": files, "actions": actions}
