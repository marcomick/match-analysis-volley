"""
Lettura dei referti ufficiali FIPAV (PDF del sito federale) delle partite del girone.

I referti stanno su Drive in `<stagione>/Match Analysis/scambio video/referti/`, uno per partita, con nome
`G<giornata>RE_<numero gara>.pdf` (numero gara = campo `ng` del calendario di `src/fipav.py`).

Il PDF è il modello stampato del referto con sopra i dati compilati in Helvetica: si leggono solo le parole in
Helvetica e le si colloca nella griglia in base alla posizione. Ogni set ha un riquadro (cinque riquadri per
pagina: set 1 e 2 in alto, 3 e 4 in mezzo, 5 in basso a sinistra), riconosciuto dalla scritta "INIZIO" del
modello; dentro ogni riquadro due blocchi (uno per squadra; nel quinto set un terzo blocco dopo il cambio campo)
con le stesse righe, a distanza fissa dall'alto del riquadro:

- formazione di partenza nell'ordine di servizio I-VI;
- riserve entrate in ogni posizione, punteggio del cambio e del rientro ("punti della squadra : punti avversari");
- la "X" nella prima casella dei turni di servizio = la squadra parte in ricezione (indicazione dell'utente);
- i turni di servizio: in ogni casella il punteggio della squadra quando ha perso la battuta;
- la colonna del libero: sequenze "centrale-libero-..." (il primo numero è il giocatore sostituito dal libero:
  i centrali, indicazione dell'utente del 2026-11-17).

Ruoli: tutte le squadre giocano con l'ordine P-M-C-O-M-C (indicazione dell'utente), quindi dai due centrali si
ricavano martelli e la coppia palleggiatore/opposto; quale dei due è il palleggiatore si decide con i palleggiatori
noti (argomento `setters`), con il doppio cambio (un palleggiatore noto che entra nel posto dell'opposto) e
propagando tra i set della stessa squadra.
"""
from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

COL_STEP = 28.4          # distanza tra le colonne I-VI di un blocco
ROW = {"lineup": 27, "subs": 39, "sub_score": 52, "back_score": 65, "turns": (79, 92, 104, 117)}
LIBERO_DX = 196          # colonna del libero rispetto alla prima colonna del blocco


def _helvetica_words(page):
    return [w for w in page.extract_words(extra_attrs=["fontname", "size"]) if w["fontname"] == "Helvetica"]


def _num(t):
    return int(t) if re.fullmatch(r"\d+", t) else None


def _blocks(box_x, box_y, words, boxes):
    """Blocchi-squadra di un riquadro: x della colonna I. Due blocchi; tre nel quinto set (il riquadro senza un
    altro riquadro alla sua destra, con il terzo blocco dopo il cambio campo)."""
    xs = [box_x + 2, box_x + 258]
    third = box_x + 541
    alone = not any(abs(by - box_y) < 3 and bx > box_x + 100 for bx, by in boxes)
    if alone and any(abs(w["top"] - (box_y + ROW["lineup"])) < 3 and abs(w["x0"] - third) < 6 for w in words):
        xs.append(third)
    return xs


def _col(x, x0):
    k = round((x - x0) / COL_STEP)
    return k if 0 <= k <= 5 and abs(x - (x0 + k * COL_STEP)) < 14 else None


def _row_words(words, y, x0, tol=3.5, size=None):
    out = [w for w in words if abs(w["top"] - y) < tol and x0 - 8 <= w["x0"] <= x0 + 5 * COL_STEP + 22]
    if size:
        out = [w for w in out if abs(w["size"] - size) < 0.6]
    return sorted(out, key=lambda w: w["x0"])


def _pairs(words, x0):
    """Punteggi "a : b" scritti come due numeri vicini sotto una colonna."""
    nums = [w for w in words if _num(w["text"]) is not None]
    out, i = {}, 0
    while i < len(nums) - 1:
        a, b = nums[i], nums[i + 1]
        k = _col(a["x0"] + 2, x0)
        if k is not None and b["x0"] - a["x0"] < 20:
            out[k] = (int(a["text"]), int(b["text"]))
            i += 2
        else:
            i += 1
    return out


def _team_name(words, box_y, x0):
    toks = [w["text"] for w in words if abs(w["top"] - (box_y - 2)) < 3 and x0 + 25 <= w["x0"] <= x0 + 175
            and not re.fullmatch(r"\d+:\d+|[AB]", w["text"])]
    return " ".join(toks)


def _side(short, team_a, team_b):
    """Il blocco riporta il nome abbreviato (es. "DECIMO R"): lo si abbina alla squadra A o B."""
    t = [x for x in short.upper().split() if len(x) > 1]
    score = lambda full: sum(1 for x in t if x in full.upper())
    return "A" if score(team_a) >= score(team_b) else "B"


def _block(words, box_y, x0):
    lineup = {}
    for w in _row_words(words, box_y + ROW["lineup"], x0, size=10):
        k = _col(w["x0"] + 3, x0)
        if k is not None and _num(w["text"]) is not None:
            lineup[k] = int(w["text"])
    subs = {}
    for w in _row_words(words, box_y + ROW["subs"], x0, size=10):
        k = _col(w["x0"] + 3, x0)
        if k is not None and _num(w["text"]) is not None:
            subs[k] = int(w["text"])
    sub_score = _pairs(_row_words(words, box_y + ROW["sub_score"], x0, size=10), x0)
    back_score = _pairs(_row_words(words, box_y + ROW["back_score"], x0, size=10), x0)
    receiving = any(w["text"] == "X" and abs(w["size"] - 14) < 1 and abs(w["top"] - (box_y + 76)) < 4
                    and abs(w["x0"] - x0) < 8 for w in words)
    turns = []
    for r, dy in enumerate(ROW["turns"]):
        for w in _row_words(words, box_y + dy, x0, size=8):
            k = _col(w["x0"] + 2, x0)
            if k is not None and _num(w["text"]) is not None:
                turns.append((r, k, int(w["text"])))
    turns.sort()
    lib = set()
    lx = x0 + LIBERO_DX
    for w in words:
        # con un solo libero a referto c'è solo il numero sbarrato (il libero è sottinteso)
        if abs(w["x0"] - lx) < 6 and box_y + 10 < w["top"] < box_y + 90 and re.fullmatch(r"\d+(-\d+)*", w["text"]):
            lib.add(w["text"])
    # tempi di riposo: caselle "T" sotto la colonna PUNTI, "punti della squadra : avversari" (la superiore è il primo)
    timeouts = [tuple(int(x) for x in w["text"].split(":")) for w in sorted(words, key=lambda w: w["top"])
                if re.fullmatch(r"\d+:\d+", w["text"]) and abs(w["x0"] - (x0 + 169)) < 9
                and box_y + 96 < w["top"] < box_y + 124]
    return {"lineup": [lineup.get(k) for k in range(6)], "subs": subs, "sub_score": sub_score, "timeouts": timeouts,
            "back_score": back_score, "receiving": receiving, "turn_ends": [(k, v) for _, k, v in turns],
            "libero_seq": sorted(lib, key=lambda s: (len(s), s))}


def _roster(words):
    """Rose: colonna della squadra A (x circa 913) e B (circa 1037); liberi marcati "- L1"/"- L2"."""
    out = {"A": {}, "B": {}}
    lines = {}
    for w in words:
        if w["top"] > 430 and w["x0"] > 900 and abs(w["size"] - 8) < 0.6:
            side = "A" if w["x0"] < 1030 else "B"
            lines.setdefault((side, round(w["top"])), []).append(w)
    for (side, _), ws in lines.items():
        ws.sort(key=lambda w: w["x0"])
        if _num(ws[0]["text"]) is None:
            continue
        name = " ".join(w["text"] for w in ws[1:])
        lib = re.search(r"-\s*L([12])", name)
        name = re.sub(r"\s*-\s*L[12]\s*$", "", name).strip()
        n = int(ws[0]["text"])
        prev = out[side].get(n, {})
        out[side][n] = {"nome": name or prev.get("nome", ""), "libero": bool(lib) or prev.get("libero", False)}
    return out


def parse_referto(path):
    path = Path(path)
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        all_words = page.extract_words(extra_attrs=["fontname", "size"])
        words = [w for w in all_words if w["fontname"] == "Helvetica"]
        boxes = sorted({(round(w["x0"]), round(w["top"])) for w in all_words if w["text"] == "INIZIO"},
                       key=lambda b: (b[1], b[0]))
    head = {w["text"]: w for w in words if abs(w["top"] - 110) < 3}
    team_a = " ".join(w["text"] for w in sorted(words, key=lambda w: w["x0"]) if abs(w["top"] - 110) < 3 and 55 < w["x0"] < 300)
    team_b = " ".join(w["text"] for w in sorted(words, key=lambda w: w["x0"]) if abs(w["top"] - 110) < 3 and 300 < w["x0"] < 580)
    gara = next((w["text"] for w in words if abs(w["top"] - 72) < 3 and 260 < w["x0"] < 300), None)
    data = next((w["text"] for w in words if re.fullmatch(r"\d\d/\d\d/\d{4}", w["text"])), None)
    sets = []
    for n, (bx, by) in enumerate(boxes, start=1):
        blocks = []
        for x0 in _blocks(bx, by, words, boxes):
            b = _block(words, by, x0)
            if not any(b["lineup"]):
                continue
            short = _team_name(words, by, x0)
            b["squadra"] = short
            b["lato"] = _side(short, team_a, team_b)
            b["squadra"] = team_a if b["lato"] == "A" else team_b
            blocks.append(b)
        if not blocks:
            continue
        if len(blocks) == 3:          # quinto set: il terzo blocco continua il primo dopo il cambio campo
            first, other, cont = blocks
            first["turn_ends"] += cont["turn_ends"]
            first["subs"].update(cont["subs"]); first["sub_score"].update(cont["sub_score"])
            first["back_score"].update(cont["back_score"])
            blocks = [first, other]
        sets.append({"set": n, "blocchi": blocks})
    return {"file": path.name, "gara": gara, "data": data, "squadra_A": team_a, "squadra_B": team_b,
            "rose": _roster(words), "set": sets}


# ---------------------------------------------------------------------------
# Ruoli, rotazione di partenza, punti in battuta
# ---------------------------------------------------------------------------
def centrali(block, liberi):
    firsts = {int(s.split("-")[0]) for s in block["libero_seq"]}
    return [i for i, n in enumerate(block["lineup"]) if n in firsts and n not in liberi]


def roles(block, liberi, setters):
    """Ruolo di ogni posizione I-VI: P, M1, C2, O, M2, C1 (ordine P-M-C-O-M-C); None se non si decide."""
    c = centrali(block, liberi)
    if len(c) == 1:                                       # un solo centrale nella colonna del libero: l'altro è opposto a lui
        c = sorted([c[0], (c[0] + 3) % 6])
    if len(c) != 2 or (c[1] - c[0]) % 6 != 3:
        return None
    cands = [(c[0] - 2) % 6, (c[1] - 2) % 6]           # le due posizioni possibili del palleggiatore
    lu = block["lineup"]
    p = next((k for k in cands if lu[k] in setters), None)
    if p is None:   # doppio cambio: un palleggiatore noto entra nel posto dell'opposto
        for k in cands:
            if block["subs"].get(k) in setters:
                p = next(x for x in cands if x != k)
    if p is None:
        return None
    order = ["P", "M1", "C2", "O", "M2", "C1"]
    return {(p + i) % 6: order[i] for i in range(6)}


def serve_turns(block, won_set=None):
    """Turni di servizio: (posizione del battitore 0-5, punti fatti in quel turno di battuta).

    Manuale del segnapunti FIPAV (2.2.9): se l'ultimo punto del set lo fa la squadra in ricezione, il punteggio
    finale si scrive nella casella del turno successivo senza che quel giocatore abbia servito. Per la squadra che
    ha vinto il set, un'ultima casella con un solo punto in più della precedente è quindi quella "fantasma" e si
    toglie (`won_set=True`)."""
    ends = list(block["turn_ends"])
    if won_set and len(ends) >= 2 and ends[-1][1] == ends[-2][1] + 1:
        ends = ends[:-1]
    out, prev_end = [], None
    for k, end in ends:
        start = (0 if not block["receiving"] else 1) if prev_end is None else prev_end + 1
        out.append((k, max(0, end - start)))
        prev_end = end
    return out


def set_winner_side(set_):
    """Lato ("A"/"B") che ha vinto il set: punteggio più alto nell'ultima casella dei turni di servizio."""
    finals = {b["lato"]: (b["turn_ends"][-1][1] if b["turn_ends"] else 0) for b in set_["blocchi"]}
    return max(finals, key=finals.get) if len(finals) == 2 else None


def team_matches(folder, team, setters=()):
    """Tutte le partite di una squadra nella cartella dei referti, con ruoli risolti propagando i palleggiatori."""
    team = team.upper()
    parsed = []
    for f in sorted(Path(folder).glob("G*RE_*.pdf")):
        r = parse_referto(f)
        if team in (r["squadra_A"] or "").upper() or team in (r["squadra_B"] or "").upper():
            parsed.append(r)
    known = set(setters)
    for _ in range(3):                                   # propagazione dei palleggiatori tra i set
        for r in parsed:
            side = "A" if team in r["squadra_A"].upper() else "B"
            liberi = {n for n, x in r["rose"][side].items() if x["libero"]}
            for s in r["set"]:
                for b in s["blocchi"]:
                    if team in b["squadra"].upper():
                        ro = roles(b, liberi, known)
                        if ro:
                            known.add(b["lineup"][next(k for k, v in ro.items() if v == "P")])
    return parsed, known


# ---------------------------------------------------------------------------
# Cambi per la battuta (indicazione dell'utente del 2026-11-17): il cambio è fatto sul giocatore che sta per
# battere (in zona 1), quindi all'inizio di un suo turno di servizio; il titolare rientra appena gli avversari
# fanno cambio palla (fine del turno) oppure, se è un centrale, dopo tre rotazioni (quando torna in prima linea).
# ---------------------------------------------------------------------------
def _turn_timeline(set_, side):
    """Turni di servizio della squadra `side` con punteggio (nostro, loro) all'inizio e alla fine."""
    me = next(b for b in set_["blocchi"] if b["lato"] == side)
    op = next(b for b in set_["blocchi"] if b["lato"] != side)
    mine, theirs = me["turn_ends"], op["turn_ends"]
    out = []
    for i, (k, end) in enumerate(mine):
        if me["receiving"]:                     # prima di ogni nostro turno c'è un loro turno
            opp = theirs[i][1] if i < len(theirs) else None
            start = 1 if i == 0 else mine[i - 1][1] + 1
        else:
            opp = 0 if i == 0 else (theirs[i - 1][1] if i - 1 < len(theirs) else None)
            start = 0 if i == 0 else mine[i - 1][1] + 1
        out.append({"pos": k, "start": (start, opp), "end": end, "idx": i})
    return out


def classify_subs(set_, side, centrali_pos=()):
    """Per ogni cambio della squadra: posizione, chi entra, chi esce, punteggio d'ingresso e di rientro e tipo:
    "battuta" (entra quando il titolare sta per battere) con rientro "a fine turno" o "dopo N turni"; "doppio
    cambio" (due cambi allo stesso punteggio su palleggiatore e opposto); altrimenti "altro"."""
    me = next(b for b in set_["blocchi"] if b["lato"] == side)
    tl = _turn_timeline(set_, side)
    same_score = {}
    for k, sc in me["sub_score"].items():
        same_score.setdefault(sc, []).append(k)
    out = []
    for k, n in sorted(me["subs"].items()):
        sc, back = me["sub_score"].get(k), me["back_score"].get(k)
        tipo, dettaglio = "altro", ""
        pair = sc is not None and len(same_score.get(sc, [])) == 2 and abs(same_score[sc][0] - same_score[sc][1]) == 3
        turn = None if pair else next((t for t in tl if t["pos"] == k and t["start"][0] == (sc or (None,))[0]
                     and (t["start"][1] is None or t["start"][1] == sc[1])), None)
        if turn is not None:
            tipo = "battuta"
            if back is None:
                dettaglio = "resta in campo"
            elif back[0] == turn["end"]:
                dettaglio = "rientro a fine turno di battuta"
            else:
                later = [t for t in tl if t["idx"] > turn["idx"] and t["start"][0] <= back[0]]
                dettaglio = f"rientro dopo {len(later)} turni" if later else "rientro più avanti"
        elif pair:
            tipo = "doppio cambio"
        if tipo == "altro" and sc is not None:   # chi sta battendo in quel momento: il cambio è su un altro giocatore
            cur = next((t for t in tl if t["start"][0] <= sc[0] <= t["end"]), None)
            if cur is not None:
                zona = (k - cur["pos"]) % 6 + 1
                dettaglio = "in prima linea" if zona in (2, 3, 4) else "in seconda linea"
        out.append({"pos": k, "entra": n, "esce": me["lineup"][k], "punteggio": sc, "rientro": back,
                    "tipo": tipo, "dettaglio": dettaglio, "centrale": k in centrali_pos})
    return out


# ---------------------------------------------------------------------------
# Partite del girone per lo scout a tocchi (scout/index.html): squadre, rose, liberi, formazioni di ogni set,
# chi batte per primo e ruoli, in un JSON che l'app legge accanto alla pagina (scout/referti.json,
# pubblicato su GitHub Pages con l'app).
# ---------------------------------------------------------------------------
def _player_name(full):
    """'D'ELIA MATTEO' -> "D'Elia": cognome senza il nome (ultima parola), in maiuscolo/minuscolo."""
    words = full.split()
    if len(words) >= 2:
        words = words[:-1]
    return " ".join(w[:1].upper() + w[1:].lower() if "'" not in w else
                    "'".join(p[:1].upper() + p[1:].lower() for p in w.split("'")) for w in words)


def known_setters_from_dvw(dvw_files):
    """{nome squadra normalizzato: cognomi normalizzati dei palleggiatori} dai .dvw già rilevati (ruolo 5)."""
    from src.dvw import parse_dvw
    out = {}
    for f in dvw_files:
        try:
            parsed = parse_dvw(f)
        except Exception:
            continue
        pl = parsed["players"]
        for code, name in parsed["teams"].items():
            sel = pl[(pl["squadra"] == code) & (pl["ruolo"] == 5)]
            out.setdefault(_norm(re.sub(r"\s+D$", "", name)), set()).update(_norm(c) for c in sel["cognome"])
    return out


def _team_setters(team_name, roster, by_team):
    """Numeri a referto dei palleggiatori noti dai .dvw della stessa squadra (nome contenuto l'uno nell'altro)."""
    key = _norm(team_name)
    names = set().union(*[v for k, v in by_team.items() if k and (k in key or key in k)] or [set()])
    return {n for n, x in roster.items() if _norm(_player_name(x["nome"])) in names}


def scout_setup(folder, girone=None, setters_by_team=None):
    """Una voce per referto: {gara, giornata, data, label, teams: {'*': casa, 'a': ospite}, firstServe: {set: '*'/'a'}}.
    Casa e ospite dal calendario federale (team1 = casa) se `girone` è dato e il sito risponde, altrimenti A = casa.
    Ruoli: P, M1, C2, O, M2, C1 dalla formazione quando si decidono (palleggiatori propagati tra le partite della
    stessa squadra), L per i liberi; per ogni giocatore il ruolo più frequente."""
    refs = [parse_referto(f) for f in sorted(Path(folder).glob("G*RE_*.pdf"))]
    home_by_ng = {}
    if girone:
        try:
            from src.fipav import fetch_calendar
            home_by_ng = {m["ng"]: (m.get("team1") or {}).get("title", "") for m in fetch_calendar(girone)}
        except Exception:
            home_by_ng = {}
    known = {}                                           # palleggiatori noti per squadra (dai .dvw, poi propagati)
    for r in refs:
        for side in "AB":
            known.setdefault(r["squadra_" + side], set()).update(
                _team_setters(r["squadra_" + side], r["rose"][side], setters_by_team or {}))
    for _ in range(3):
        for r in refs:
            for s in r["set"]:
                for b in s["blocchi"]:
                    lib = {n for n, x in r["rose"][b["lato"]].items() if x["libero"]}
                    ro = roles(b, lib, known.setdefault(b["squadra"], set()))
                    if ro:
                        known[b["squadra"]].add(b["lineup"][next(k for k, v in ro.items() if v == "P")])
    out = []
    for r in refs:
        home = home_by_ng.get(r["gara"], "")
        a_home = not home or _norm(home) == _norm(r["squadra_A"]) or _norm(home) != _norm(r["squadra_B"])
        code = {"A": "*" if a_home else "a", "B": "a" if a_home else "*"}
        teams = {code[side]: {"name": r["squadra_" + side].title(), "players": {}, "sets": {}, "subs": {}, "libero": {}, "candP": set()}
                 for side in "AB"}
        for side in "AB":
            for n, x in sorted(r["rose"][side].items()):
                teams[code[side]]["players"][n] = {"n": n, "c": _player_name(x["nome"]), "votes": {}, "L": x["libero"]}
        first = {}
        for s in r["set"]:
            for b in s["blocchi"]:
                t = teams[code[b["lato"]]]
                lib = {n for n, x in r["rose"][b["lato"]].items() if x["libero"]}
                ro = roles(b, lib, known.get(b["squadra"], set())) or {}
                cen = set(centrali(b, lib))
                c2 = sorted(cen)
                if not ro and len(c2) == 2 and (c2[1] - c2[0]) % 6 == 3:   # palleggiatore in una di due posizioni
                    t["candP"].update(b["lineup"][(k - 2) % 6] for k in c2)
                t["sets"][s["set"]] = b["lineup"]
                # cambi: punteggio (squadra, avversari) all'ingresso e al rientro del titolare
                ev = []
                for k, n in b["subs"].items():
                    if b["sub_score"].get(k) is not None:
                        ev.append({"at": list(b["sub_score"][k]), "out": b["lineup"][k], "in": n})
                    if b["back_score"].get(k) is not None:
                        ev.append({"at": list(b["back_score"][k]), "out": n, "in": b["lineup"][k]})
                t["subs"][s["set"]] = sorted(ev, key=lambda e: (sum(e["at"]), e["at"]))
                # colonna del libero: "30-31" = il libero 31 entra per il 30; "30-31-99" = per il 30 sono entrati
                # tutti e due i liberi; "13" da solo = un solo libero a referto (sottinteso)
                lib_for, lib_used = [], []
                for seq in b["libero_seq"]:
                    nums = [int(x) for x in seq.split("-") if x.isdigit()]
                    if not nums or nums[0] in lib:
                        continue
                    if nums[0] not in lib_for:
                        lib_for.append(nums[0])
                    lib_used += [x for x in nums[1:] if x not in lib_used]
                t["libero"][s["set"]] = {"per": lib_for, "liberi": lib_used or sorted(lib)}
                for k, n in enumerate(b["lineup"]):
                    p = t["players"].get(n)
                    r_ = (ro.get(k) or ("C" if k in cen else None))
                    if p and r_:
                        r_ = r_.rstrip("12")
                        p["votes"][r_] = p["votes"].get(r_, 0) + 1
                if not b["receiving"]:
                    first[s["set"]] = code[b["lato"]]
        for t in teams.values():
            has_p = any(p["votes"].get("P") for p in t["players"].values())
            t["candP"] = [] if has_p else sorted(n for n in t["candP"] if n is not None)
            for p in t["players"].values():
                p["r"] = "L" if p.pop("L") else (max(p["votes"], key=p["votes"].get) if p["votes"] else None)
                p.pop("votes")
            t["players"] = list(t["players"].values())
        g = re.match(r"G(\d+)", r["file"])
        out.append({"gara": r["gara"], "giornata": int(g.group(1)) if g else None, "data": r["data"],
                    "label": f"G{g.group(1) if g else '?'} {teams['*']['name']} - {teams['a']['name']}",
                    "teams": teams, "firstServe": first})
    return out


def _norm(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


if __name__ == "__main__":
    import argparse
    import json
    import os
    from dotenv import load_dotenv

    load_dotenv()
    ap = argparse.ArgumentParser(description="Partite del girone dai referti, per lo scout a tocchi")
    ap.add_argument("--season-folder", default=os.path.join(os.environ.get("VOLLEY_DRIVE_ROOT", ""),
                    "Pallavolo/Decimo Roma/2026-2027/Match Analysis"))
    ap.add_argument("--girone", default="63671")
    ap.add_argument("--out", default="scout/referti.json")
    a = ap.parse_args()
    season = Path(a.season_folder)
    dvw = [f for f in season.rglob("*.dvw") if "scambio video" not in f.parts]
    data = scout_setup(season / "scambio video" / "referti", a.girone, known_setters_from_dvw(dvw))
    Path(a.out).write_text(json.dumps(data, ensure_ascii=False))
    print(f"{len(data)} partite in {a.out}")
