# src/dvw.py
"""
Lettore dei file DataVolley (.dvw) esportati dal programma di rilevazione
("Volleyball Scout"), fonte primaria dello scout dalla stagione 2026-2027.

Il .dvw è un file di testo (code page Windows-1252) a sezioni "[3NOME]".
Verificato su G1 Decimo - San Paolo (2026-10-18): le 1031 azioni del .dvw
coincidono esattamente con quelle dell'Excel esportato per la stessa partita
(per squadra, fondamentale e voto), ma il .dvw contiene in più:
- i 6 in campo di entrambe le squadre e la zona del palleggiatore su OGNI
  riga (niente ricostruzione euristica di formazioni/palleggiatore, vedi
  src/lineup.py e src/setter_report.py per gli Excel 2025-2026);
- il punteggio dopo ogni punto, quindi il punteggio esatto anche a fine set;
- formazione di partenza e ruolo di ogni giocatore, nell'intestazione;
- il minutaggio video di ogni azione;
- le sostituzioni esplicite (uscente:entrante).

Sezioni usate:
- [3MATCH]: prima riga, campo 0 = data della partita ('gg/mm/aaaa').
- [3TEAMS]: riga 1 = squadra di casa ('*' nelle righe di scout), riga 2 =
  ospite ('a').
- [3SET]: per set "giocato;parziale a 8;a 16;a 21;finale;durata", punteggi
  "casa-ospite".
- [3PLAYERS-H]/[3PLAYERS-V]: campo 1 numero, 3..7 zona di partenza nei set
  1..5 ('*' = libero, vuoto = non titolare), 9 cognome, 10 nome, 13 ruolo
  (codice DataVolley, vedi DVW_ROLES).
- [3SCOUT]: una riga per evento. Campo 0 = codice, 7 = minutaggio video,
  8 = set, 9/10 = zona del palleggiatore casa/ospite, 14..19 = numeri dei
  giocatori di casa nelle zone 1..6, 20..25 = ospiti nelle zone 1..6.
  Codici:
  - azione: squadra ('*'/'a') + numero (2 cifre) + fondamentale
    (DVW_SKILLS) + tipo di palla (5° carattere) + voto (#,+,!,-,/,=), poi
    i campi estesi del formato DataVolley (confermati sul manuale e
    sull'interprete R openvolley/datavolley): 7°-8° combinazione d'attacco
    (sull'alzata: chiamata del palleggiatore, K1/K7...), 9° destinazione
    dell'alzata, 10° zona di partenza, 11°-12° zona e sottozona di arrivo,
    13° tipo di colpo. Volleyball Scout compila tipo di palla e tipo di
    colpo solo se scelti nella rilevazione (altrimenti 'H', il default) e le
    zone solo se si rileva la direzione d'attacco; combinazioni e chiamate
    (la "base" del centrale) non le esporta mai — verificato il 2026-10-25
    su Valsugana - Volley Prati, dove la base era stata rilevata: resta solo
    nel file .vbsm del programma, cifrato. Tipo di palla sugli attacchi:
    H alta, T super, N "7" (le sole tre scelte usate nella rilevazione,
    indicazione dell'utente); tipo di colpo: H attacco, T pallonetto;
  - '*p21:25' punto, con il punteggio casa:ospite DOPO il punto;
  - '*c14:10' sostituzione (esce 14, entra 10);
  - '*z3' zona del palleggiatore (rotazione); '*P14>LUp' palleggiatore
    dichiarato a inizio set;
  - '*$$&H#' / '*$$&H=' punto/errore generico (senza un'azione associata);
  - '**2set' fine set.

`load_team_actions` produce le azioni del Decimo (o dell'avversario) nello stesso formato
degli Excel 2025-2026 (colonne Tipo/Voto/Cognome/Numero/Posizione Giocatore/
Posizione Palleggiatore/Numero Set/Punti Locali/Punti Ospiti, Tipo minuscolo,
punteggio PRIMA dell'azione), così il resto della pipeline (KPI, classifiche,
tabellino) lo usa senza modifiche, più le colonne aggiuntive del .dvw.
"""
import re

import numpy as np
import pandas as pd

TEAM = "Decimo"
DVW_ENCODING = "cp1252"

DVW_SKILLS = {
    "S": "battuta", "R": "ricezione", "A": "attacco", "B": "muro",
    "D": "difesa", "E": "alzata", "F": "free ball",
}
# Codici ruolo DataVolley, come impostati nel programma di rilevazione.
DVW_ROLES = {1: "libero", 2: "schiacciatore", 3: "opposto", 4: "centrale", 5: "palleggiatore"}
SETTER_ROLE = 5
MIDDLE_ROLE = 4

# Punto/errore generico dell'avversario ('a$$&H#'/'a$$&H='): voci che il
# programma aggiunge in automatico come complemento di un'azione dell'altra
# squadra (es. dopo un muro punto del Decimo, l'attacco murato avversario
# diventa un errore generico avversario). Come l'Excel esportato, che sul lato
# Decimo riporta solo quelle avversarie, con la dicitura degli Excel 2025-2026.
_OPPONENT_GENERIC_TIPO = {"#": "punto generico avversario", "=": "errore generico avversario"}

_ACTION_RE = re.compile(r"^([*a])(\d{2})([SRABDEF])(.)([#+!\-/=])")
# Tipo di palla (5° carattere) e tipo di colpo (13°) degli attacchi, con le
# diciture dell'app di rilevazione (P, "free-ball" nell'app, mai visto finora).
BALL_TYPES = {"H": "Alta", "T": "Super", "N": "7", "Q": "Quick", "M": "Mezza", "U": "Super", "F": "Fast", "O": "Altro"}
HIT_TYPES = {"H": "Attacco", "P": "Free-ball", "T": "Pallonetto"}
_POINT_RE = re.compile(r"^[*a]p(\d+):(\d+)")
_SUB_RE = re.compile(r"^([*a])c(\d+):(\d+)")
_GENERIC_RE = re.compile(r"^([*a])\$\$&.([#=])")

HOME_ZONE_FIELDS = range(14, 20)
AWAY_ZONE_FIELDS = range(20, 26)


def _read_sections(file_path):
    with open(file_path, encoding=DVW_ENCODING) as f:
        lines = f.read().splitlines()
    sections, current = {}, None
    for line in lines:
        m = re.match(r"^\[3([A-Z\-]+)\]$", line.strip())
        if m:
            current = m.group(1)
            sections[current] = []
        elif current is not None and line.strip():
            sections[current].append(line)
    return sections


def _normalize_cognome(cognome):
    """'D’ELIA ' -> 'D’Elia': stessa grafia degli Excel 2025-2026 (poi
    normalizzata dal resto della pipeline come per gli Excel)."""
    return cognome.strip().title()


def _parse_players(rows, side):
    players = []
    for row in rows:
        f = row.split(";")
        role = int(f[13]) if f[13].strip().isdigit() else None
        players.append({
            "squadra": side,
            "numero": int(f[1]),
            "cognome": _normalize_cognome(f[9]),
            "nome": f[10].strip(),
            "ruolo": role,
            "ruolo_nome": DVW_ROLES.get(role),
            "zone_partenza": [z.strip() or None for z in f[3:8]],
        })
    return players


def _parse_score(text):
    m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", text)
    return (int(m.group(1)), int(m.group(2))) if m else None


def set_completo(numero_set, punti_casa, punti_ospiti):
    """Set concluso regolarmente: 25 punti (15 al quinto) con 2 di scarto."""
    soglia = 15 if numero_set == 5 else 25
    return max(punti_casa, punti_ospiti) >= soglia and abs(punti_casa - punti_ospiti) >= 2


def _parse_sets(rows):
    sets = []
    for numero_set, row in enumerate(rows, 1):
        f = row.split(";")
        final = _parse_score(f[4]) if len(f) > 4 else None
        if f[0].strip() != "True" or final is None:
            continue
        sets.append({
            "numero_set": numero_set, "punti_casa": final[0], "punti_ospiti": final[1],
            "completo": set_completo(numero_set, *final),
        })
    return sets


def _zone_numbers(fields, zone_fields):
    return [int(fields[i]) if fields[i].strip().isdigit() else None for i in zone_fields]


def parse_dvw(file_path):
    """
    Legge un .dvw. Ritorna un dict:
    - data: data della partita 'gg/mm/aaaa' (None se assente)
    - teams: {'*': nome casa, 'a': nome ospite}
    - players: DataFrame [squadra, numero, cognome, nome, ruolo, ruolo_nome, zone_partenza]
    - sets: lista di {numero_set, punti_casa, punti_ospiti, completo} (dal
      riepilogo [3SET]; un set non concluso nello scout, es. senza video,
      risulta completo=False)
    - actions: DataFrame, un evento per riga (azioni di entrambe le squadre,
      punti/errori generici, sostituzioni), vedi _parse_scout
    - rallies: DataFrame [Rally, Numero Set, vincitore ('*'/'a'), Punti
      Locali, Punti Ospiti (punteggio DOPO il punto)], uno per punto assegnato
    """
    sections = _read_sections(file_path)
    teams = {}
    for row in sections.get("TEAMS", []):
        f = row.split(";")
        teams["*" if f[0].strip() == "1" else "a"] = f[1].strip()
    players = pd.DataFrame(
        _parse_players(sections.get("PLAYERS-H", []), "*") + _parse_players(sections.get("PLAYERS-V", []), "a")
    )
    match_rows = sections.get("MATCH", [])
    data = match_rows[0].split(";")[0].strip() if match_rows else ""
    actions, rallies = _parse_scout(sections.get("SCOUT", []), players)
    return {
        "data": data or None,
        "teams": teams,
        "players": players,
        "sets": _parse_sets(sections.get("SET", [])),
        "actions": _middle_attacks_quick(actions, players),
        "rallies": rallies,
    }


def _middle_attacks_quick(actions, players):
    """
    Attacchi dei centrali sempre con alzata veloce ('Q', Quick): i centrali
    non attaccano mai palla alta (indicazione dell'utente, 2026-10-25). Nella
    rilevazione il tipo di palla si sceglie di rado e il programma scrive il
    default 'H' (alta): su tutti i .dvw 2025-2026/2026-2027 gli attacchi dei
    centrali risultavano 'H' (qualche '7' solo su Valsugana - Volley Prati).
    Il codice originale resta nella colonna 'Codice'.
    """
    if actions.empty:
        return actions
    middles = {(p.squadra, p.numero) for p in players.itertuples() if p.ruolo == MIDDLE_ROLE}
    is_middle = np.array([(s, n) in middles for s, n in zip(actions["squadra"], actions["Numero"])])
    actions.loc[(actions["Tipo"] == "attacco") & is_middle, "Tipo palla"] = "Q"
    return actions


def read_dvw_header(file_path):
    """Solo data, squadre e set di un .dvw (senza leggere lo scout): per
    elencare le partite disponibili di una squadra."""
    sections = _read_sections(file_path)
    teams = {}
    for row in sections.get("TEAMS", []):
        f = row.split(";")
        teams["*" if f[0].strip() == "1" else "a"] = f[1].strip()
    match_rows = sections.get("MATCH", [])
    data = match_rows[0].split(";")[0].strip() if match_rows else ""
    return {"data": data or None, "teams": teams, "sets": _parse_sets(sections.get("SET", []))}


def _cognome_by_number(players):
    """(squadra, numero) -> cognome. La rosa del programma può avere voci
    vecchie con lo stesso numero di un giocatore attuale (G1 San Paolo:
    Principato, rosa 2025-2026, e Racheli, entrambi col 10): vince chi ha un
    ruolo impostato, le voci senza ruolo sono residui di stagioni precedenti."""
    out = {}
    for p in sorted(players.itertuples(), key=lambda p: p.ruolo is not None and not pd.isna(p.ruolo)):
        out[(p.squadra, p.numero)] = p.cognome
    return out


def _parse_scout(rows, players):
    """
    Un evento per riga, con il punteggio PRIMA dell'evento (convenzione degli
    Excel: il punteggio di una riga non include l'esito di quella stessa riga).
    Colonne: squadra ('*'/'a'), Tipo, Voto, Cognome, Numero, Posizione
    Giocatore (zona del giocatore nella rotazione della sua squadra, 'p1'..'p6';
    vuota per il libero, che non figura nella rotazione), Posizione
    Palleggiatore (zona del palleggiatore della sua squadra), Numero Set, Rally
    (numero progressivo dello scambio, chiuso dalla riga di punto successiva),
    Punti Locali, Punti Ospiti, Video, Casa P1..P6 / Ospiti P1..P6 (numeri in
    campo per zona), Palleggiatore casa/ospite (numero in campo).

    Nota su Posizione Giocatore: è la zona FORMALE di rotazione. Gli Excel
    riportano invece la posizione giocata dopo i cambi di posto (centrale a
    muro in 3, palleggiatore in difesa in 1, libero in 5): coincidono solo
    sulle battute (sempre p1), le uniche righe in cui la pipeline usa questa
    colonna (src/lineup.py, src/setter_report.py per gli Excel 2025-2026).
    """
    cognomi = _cognome_by_number(players)
    setter_numbers = {
        side: set(players[(players["squadra"] == side) & (players["ruolo"] == SETTER_ROLE)]["numero"])
        for side in ("*", "a")
    }

    out, rallies = [], []
    score = (0, 0)
    current_set = None
    rally = 0
    for row in rows:
        f = row.split(";")
        code = f[0]
        if code.startswith("**"):
            continue  # fine set
        numero_set = int(f[8]) if f[8].strip().isdigit() else None
        if numero_set != current_set:
            current_set, score = numero_set, (0, 0)

        point = _POINT_RE.match(code)
        if point:
            score = (int(point.group(1)), int(point.group(2)))
            rallies.append({
                "Rally": rally, "Numero Set": numero_set, "vincitore": code[0],
                "Punti Locali": score[0], "Punti Ospiti": score[1],
            })
            rally += 1
            continue

        home = _zone_numbers(f, HOME_ZONE_FIELDS)
        away = _zone_numbers(f, AWAY_ZONE_FIELDS)
        setter_zone = {
            "*": int(f[9]) if f[9].strip().isdigit() else None,
            "a": int(f[10]) if f[10].strip().isdigit() else None,
        }
        base = {
            "Numero Set": numero_set, "Rally": rally, "Punti Locali": score[0], "Punti Ospiti": score[1],
            "Video": f[7].strip() or None,
            **{f"Casa P{z}": n for z, n in enumerate(home, 1)},
            **{f"Ospiti P{z}": n for z, n in enumerate(away, 1)},
            "Palleggiatore casa": _setter_on_court(home, setter_zone["*"], setter_numbers["*"]),
            "Palleggiatore ospite": _setter_on_court(away, setter_zone["a"], setter_numbers["a"]),
        }

        action = _ACTION_RE.match(code)
        generic = _GENERIC_RE.match(code)
        sub = _SUB_RE.match(code)
        if action:
            side, numero = action.group(1), int(action.group(2))
            lineup = home if side == "*" else away
            out.append({
                "squadra": side, "Tipo": DVW_SKILLS[action.group(3)], "Voto": action.group(5),
                **_code_details(code),
                "Cognome": cognomi.get((side, numero)), "Numero": numero,
                "Posizione Giocatore": f"p{lineup.index(numero) + 1}" if numero in lineup else None,
                "Posizione Palleggiatore": f"p{setter_zone[side]}" if setter_zone[side] else None,
                **base,
            })
        elif generic or sub:
            side = (generic or sub).group(1)
            out.append({
                "squadra": side,
                # 'generico' viene riportato alla dicitura degli Excel in
                # load_team_actions
                "Tipo": "generico" if generic else "cambio configurazione",
                "Voto": generic.group(2) if generic else None, "Cognome": None, "Numero": None, "Posizione Giocatore": None,
                "Posizione Palleggiatore": f"p{setter_zone[side]}" if setter_zone[side] else None,
                "Sostituzione": f"{sub.group(2)}:{sub.group(3)}" if sub else None,
                **base,
            })
    return pd.DataFrame(out), pd.DataFrame(rallies)


def _code_char(code, i):
    c = code[i] if len(code) > i else "~"
    return None if c in ("~", " ") else c


def _code_details(code):
    """Campi estesi del codice di un'azione (vedi docstring di modulo): None
    dove il programma non li ha compilati."""
    start, end = _code_char(code, 9), _code_char(code, 10)
    return {
        "Codice": code,
        "Tipo palla": _code_char(code, 4),
        "Zona partenza": int(start) if start and start.isdigit() else None,
        "Zona arrivo": int(end) if end and end.isdigit() else None,
        "Tipo colpo": _code_char(code, 12),
    }


def _setter_on_court(lineup, setter_zone, setter_numbers):
    """
    Numero del palleggiatore in campo: chi occupa la zona del palleggiatore
    registrata dal programma (che la aggiorna anche dopo un cambio di
    palleggiatore — verificato su G1 San Paolo, Racheli per Caranzetti nella
    stessa zona). Se in quella zona non c'è un giocatore con ruolo
    palleggiatore ma ce n'è esattamente uno in campo, si prende quello.
    """
    if setter_zone and lineup[setter_zone - 1] in setter_numbers:
        return lineup[setter_zone - 1]
    on_court = [n for n in lineup if n in setter_numbers]
    return on_court[0] if len(on_court) == 1 else None


def decimo_side(parsed):
    """'*' se il Decimo è la squadra di casa, 'a' se ospite. Fallisce
    rumorosamente se nessuna delle due squadre è il Decimo."""
    for side, name in parsed["teams"].items():
        if TEAM.lower() in name.lower():
            return side
    raise ValueError(f"Nessuna squadra '{TEAM}' nel file: {parsed['teams']}")


def load_team_actions(file_path, team="decimo"):
    """
    Azioni del Decimo o del suo avversario (team='decimo' o 'avversario')
    in un .dvw, vedi side_actions.

    Ritorna (df, decimo_locali, parsed): decimo_locali=True se il Decimo è la
    squadra di casa (= 'Punti Locali' è il punteggio del Decimo), parsed =
    l'output completo di parse_dvw (avversari, set, formazioni).
    """
    if team not in ("decimo", "avversario"):
        raise ValueError(f"team deve essere 'decimo' o 'avversario', non {team!r}")
    parsed = parse_dvw(file_path)
    decimo = decimo_side(parsed)
    side = decimo if team == "decimo" else ("a" if decimo == "*" else "*")
    return side_actions(parsed, side), decimo == "*", parsed


def normalize_team_name(name):
    """Nome di squadra per i confronti: senza maiuscole, spazi superflui e il
    suffisso " D" (Serie D) che alcuni file aggiungono ('Appio D' = 'Appio',
    visto negli scout 2025-2026)."""
    words = str(name).casefold().split()
    if len(words) > 1 and words[-1] == "d":
        words = words[:-1]
    return " ".join(words)


def team_side(parsed, team_name):
    """'*'/'a' della squadra con questo nome nel file (vedi
    normalize_team_name). Fallisce se nel file non c'è."""
    for side, name in parsed["teams"].items():
        if normalize_team_name(name) == normalize_team_name(team_name):
            return side
    raise ValueError(f"Nessuna squadra '{team_name}' nel file: {parsed['teams']}")


def side_actions(parsed, side):
    """
    Azioni della squadra `side` ('*'/'a') di un .dvw già letto, nel formato
    degli Excel 2025-2026 (vedi docstring di modulo), più le colonne Video,
    Casa/Ospiti P1..P6, i campi estesi del codice (_code_details) e
    'Palleggiatore in campo' (cognome del palleggiatore di quella squadra in
    campo).
    """
    actions = parsed["actions"]
    # azioni e sostituzioni della squadra + punti/errori generici dell'altra
    # (vedi _OPPONENT_GENERIC_TIPO); i generici della squadra stessa sono il
    # complemento di azioni dell'altra, che qui non compaiono
    generic = actions["Tipo"] == "generico"
    own = actions["squadra"] == side
    df = actions[(own & ~generic) | (~own & generic)].copy()
    is_generic = df["Tipo"] == "generico"
    df.loc[is_generic, "Tipo"] = df.loc[is_generic, "Voto"].map(_OPPONENT_GENERIC_TIPO)
    df.loc[is_generic, "Voto"] = None
    df = df.drop(columns="squadra")

    cognomi = {num: cog for (sq, num), cog in _cognome_by_number(parsed["players"]).items() if sq == side}
    setter_col = "Palleggiatore casa" if side == "*" else "Palleggiatore ospite"
    df["Palleggiatore in campo"] = df[setter_col].map(cognomi)
    return df.reset_index(drop=True)


def dvw_official_result(parsed):
    """
    Risultato della partita dal riepilogo set del .dvw, nello stesso formato
    di src.leg_comparison.parse_official_results ({squadra_a, squadra_b,
    sets_a, sets_b, parziali}, a = casa) — solo se tutti i set sono conclusi
    nello scout e una squadra è arrivata a 3 set; altrimenti None (es. G1 San
    Paolo: 5° set fermo sul 4-4 perché manca il video).
    """
    sets = parsed["sets"]
    if not sets or not all(s["completo"] for s in sets):
        return None
    sets_a = sum(s["punti_casa"] > s["punti_ospiti"] for s in sets)
    sets_b = len(sets) - sets_a
    if max(sets_a, sets_b) != 3:
        return None
    return {
        "squadra_a": parsed["teams"]["*"], "squadra_b": parsed["teams"]["a"],
        "sets_a": sets_a, "sets_b": sets_b,
        "parziali": [(s["punti_casa"], s["punti_ospiti"]) for s in sets],
    }
