# src/setter_distribution.py
"""
Distribuzione del palleggiatore: da quale zona attaccano i giocatori serviti,
con quanta efficacia, e se la distribuzione cambia in modo statisticamente
significativo lungo una direzione di analisi (rotazione, qualità della
ricezione, punteggio, partita). Solo per i .dvw (servono ruoli, rotazione e
palleggiatore in campo su ogni azione).

Zona di partenza dell'attaccante (regole concordate con l'utente il
2026-10-25), sul campo a 9 zone di DataVolley visto da chi attacca, rete in
alto: prima linea 4-3-2, poi 7-8-9, in fondo 5-6-1.
- centrale: sempre zona 3, qualunque sia la direzione rilevata (anche i
  primi tempi spostati verso il 4 e la "7");
- schiacciatore in seconda linea: sempre 8 (pipe); opposto in seconda linea:
  sempre 9;
- schiacciatore/opposto in prima linea: la zona di partenza rilevata con la
  direzione d'attacco, riportata in prima linea sulla sua colonna (una
  partenza rilevata in 7/5 vale 4, in 9/1 vale 2: un giocatore di prima
  linea attacca da davanti). Il ruolo e la rotazione da soli non bastano:
  con la "P1 normale" opposto e schiacciatore si scambiano il lato, con la
  "P1 invertita" no, e la stessa squadra può fare l'una o l'altra nella
  stessa partita a seconda di chi gioca opposto. Se la direzione non è
  rilevata, zona stimata dal ruolo (schiacciatore 4, opposto 2) e marcata
  come tale (`zona_stimata`);
- gli attacchi del palleggiatore (seconda intenzione) sono esclusi;
- prima/seconda linea dalla posizione formale di rotazione (P2-P4 = prima).

Fase: side-out = primo attacco dopo la ricezione, contrattacco = tutto il
resto, free ball compresa (definizioni uniche del progetto, via
src.efficiency.separate_attack_types); "free ball" = attacco dopo una difesa
con voto '!'.

Situazioni del palleggiatore (`situazione`, le stesse famiglie del suo
"attacco alzato"): side-out da ricezione + o #, side-out da ricezione !,
free ball; gli altri attacchi (side-out da ricezione - o /, contrattacco
senza free ball) restano fuori da queste tre famiglie.

Test di significatività (2026-10-25, richieste dell'utente):
- lungo una direzione (rotazione, qualità della ricezione, tipo di
  ricevitore — libero, martello in prima o in seconda linea —, ricevitore,
  punteggio, partita): chi quadro zona x categoria con p-value per
  permutazione e correzione di Holm sulle direzioni. Il ricevitore si guarda
  "a parità di effetto della ricezione": test stratificato sul voto (chi
  quadro sommato sui voti, permutazioni dentro ogni voto), così un
  ricevitore che riceve peggio non sembra "cambiare" la distribuzione solo
  perché riceve più palloni !;
- per palleggiatore e situazione (test_setter_situations): da R+/#, da R! e
  da free ball alza in modo diverso dal resto dei suoi palloni? Test zona x
  (situazione / resto) su ogni palleggiatore, Holm su tutti i test insieme.
"""
import numpy as np
import pandas as pd

from src.dvw import BALL_TYPES, HIT_TYPES, SETTER_ROLE, _cognome_by_number, roster_by_number, side_actions
from src.efficiency import separate_attack_types

# Griglia delle zone dalla rete verso il fondo, da sinistra a destra per chi attacca.
ZONE_GRID = [[4, 3, 2], [7, 8, 9], [5, 6, 1]]
ZONES = [z for row in ZONE_GRID for z in row]
_FRONT_OF_COLUMN = {z: row[0] for row in zip(*ZONE_GRID) for z in row}  # 7,5 -> 4; 8,6 -> 3; 9,1 -> 2
ROLE_OUTSIDE, ROLE_OPPOSITE, ROLE_MIDDLE = 2, 3, 4
ALL_GRADES = ("#", "+", "!", "-", "/", "=")

SCORE_SITUATIONS = ["Sotto di 3 o più", "Punteggio in equilibrio (±2)", "Sopra di 3 o più"]

# Situazioni del palleggiatore (colonna `situazione`), vedi docstring di modulo.
SITUATIONS = {"R+#": "Side-out da R+ / R#", "R!": "Side-out da R!", "FB": "Free ball"}
# Contrattacco selezionabile nella vista: tutto, solo free ball, senza free ball.
COUNTER_MODES = {"tutto": "Tutto il contrattacco (free ball comprese)", "fb": "Solo free ball (difesa !)",
                 "no_fb": "Contrattacco senza free ball"}

# Direzioni di analisi per il test di significatività: colonna di
# attack_rows e titolo. Ricezione e ricevitore si guardano solo sugli
# attacchi di side-out; il ricevitore a parità di voto di ricezione.
DIRECTIONS = {
    "rotazione": "Rotazione",
    "ricezione": "Qualità della ricezione",
    "tipo_ricevitore": "Libero o martello (davanti/dietro) in ricezione",
    "ricevitore": "Chi riceve (a parità di ricezione)",
    "situazione_punteggio": "Punteggio",
    "partita": "Partita",
}
SIDE_OUT_DIRECTIONS = ("ricezione", "tipo_ricevitore", "ricevitore")
STRATA = {"ricevitore": "ricezione", "tipo_ricevitore": "ricezione"}
# Chi riceve per ruolo e linea (richiesta dell'utente, 2026-10-25): più dati
# per categoria che per singolo giocatore, e risponde a domande come "quando
# riceve il martello di prima linea il palleggiatore non lo serve mai?".
# Opposti e centrali in ricezione (rari) restano fuori (None).
RECEIVER_TYPES = ["Libero", "Martello in prima linea", "Martello in seconda linea"]
ROLE_LIBERO = 1


def receiver_type(role, front):
    if role == ROLE_LIBERO:
        return RECEIVER_TYPES[0]
    if role == ROLE_OUTSIDE and front is not None:
        return RECEIVER_TYPES[1] if front else RECEIVER_TYPES[2]
    return None


def _is_front(pos):
    return None if pos is None or pd.isna(pos) else int(str(pos)[1:]) in (2, 3, 4)
MIN_PER_CATEGORY = 8
MIN_TOTAL = 30
N_PERMUTATIONS = 2000
ALPHA = 0.05


# Colore di una zona sul campo (dashboard e PDF): rampa sequenziale blu della
# dataviz skill (passi 100 -> 700) sulla quota delle alzate, su scala assoluta
# (SEQ_FULL_PCT o più = passo più scuro) così griglie diverse restano
# confrontabili; dal passo SEQ_WHITE_TEXT_FROM in su il testo è bianco.
SEQ_BLUE = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
            "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
SEQ_FULL_PCT = 50
SEQ_WHITE_TEXT_FROM = 6
COURT_FLOOR = "#e8d2ae"
COURT_FLOOR_DARK = "#4b3f30"
# zona libera attorno al campo: fa risaltare le righe bianche del perimetro
FREE_ZONE = "#c9ab80"
FREE_ZONE_DARK = "#33291e"
NET_COLOR = "#2b2f36"
NET_MESH = "#4d545f"
CELL_INK = "#14202e"
ANTENNA_RED = "#d03b3b"


def share_color(pct):
    """(colore, testo bianco?) della cella di una zona con questa quota (%)."""
    i = min(len(SEQ_BLUE) - 1, int(round(pct / SEQ_FULL_PCT * (len(SEQ_BLUE) - 1))))
    return SEQ_BLUE[i], i >= SEQ_WHITE_TEXT_FROM


def short_player(attaccante):
    """'9 Edemetti' -> 'Edemetti'."""
    parts = str(attaccante).split(" ", 1)
    return parts[1] if len(parts) == 2 and parts[0].isdigit() else str(attaccante)


def _roles(parsed, side):
    """numero -> ruolo DataVolley della squadra `side` (con più voci per lo
    stesso numero, quella scelta da src.dvw.roster_by_number)."""
    return {num: (None if pd.isna(p.ruolo) else int(p.ruolo))
            for (sq, num), p in roster_by_number(parsed["players"]).items() if sq == side}


def attack_zone(role, front, recorded):
    """(zona, stimata) con le regole della docstring di modulo; zona None se
    non determinabile (ruolo sconosciuto e direzione non rilevata)."""
    if role == ROLE_MIDDLE:
        return 3, False
    if front is False and role == ROLE_OUTSIDE:
        return 8, False
    if front is False and role == ROLE_OPPOSITE:
        return 9, False
    if recorded:
        return (_FRONT_OF_COLUMN[recorded] if front else recorded), False
    if role == ROLE_OUTSIDE:
        return 4, True
    if role == ROLE_OPPOSITE:
        return 2, True
    return None, True


def _code_value(row, col):
    v = row.get(col)
    return None if v is None or pd.isna(v) else v


def score_situation(diff):
    if diff <= -3:
        return SCORE_SITUATIONS[0]
    if diff >= 3:
        return SCORE_SITUATIONS[2]
    return SCORE_SITUATIONS[1]


def attack_rows(parsed, side, match_label=""):
    """
    Un attacco per riga della squadra `side` ('*'/'a') di un .dvw già letto
    (src.dvw.parse_dvw), escluse le seconde intenzioni del palleggiatore.
    Colonne: partita, set, palleggiatore, rotazione ('P1'..'P6', zona del
    palleggiatore), attaccante, ruolo, prima_linea, zona, zona_stimata,
    tipo_alzata, tipo_colpo, voto, fase ('Side-out'/'Contrattacco'),
    free_ball, ricezione (voto della ricezione, side-out), ricevitore
    ('<numero> <cognome>' di chi ha ricevuto, side-out), tipo_ricevitore
    (RECEIVER_TYPES o None, side-out), martello_davanti ('<numero>
    <cognome>' dello schiacciatore in prima linea al momento della
    ricezione, se ce n'è esattamente uno; side-out), difesa (voto
    della difesa da cui parte il contrattacco, se c'è), situazione (chiave
    di SITUATIONS o None), punti_noi/punti_loro e diff_punteggio (prima
    dell'attacco), situazione_punteggio.
    """
    df = side_actions(parsed, side)
    roles = _roles(parsed, side)
    names = {n: c for (sq, n), c in _cognome_by_number(parsed["players"]).items() if sq == side}
    lineup_cols = [f"{'Casa' if side == '*' else 'Ospiti'} P{z}" for z in (2, 3, 4)]
    so, fb, _ = separate_attack_types(df, rec_vote=ALL_GRADES)
    so_idx, fb_idx = set(so.index), set(fb.index)
    own_score, opp_score = ("Punti Locali", "Punti Ospiti") if side == "*" else ("Punti Ospiti", "Punti Locali")

    def front_outside(r):
        outs = [int(n) for n in (r[c] for c in lineup_cols) if pd.notna(n) and roles.get(int(n)) == ROLE_OUTSIDE]
        return f"{outs[0]} {names.get(outs[0])}" if len(outs) == 1 else None

    # Rally -> (Tipo, Voto, giocatore, tipo di ricevitore, martello in prima
    # linea) dell'ultima ricezione/difesa vista
    last_touch = {}
    rows = []
    for i, r in df.iterrows():
        tipo = r["Tipo"]
        if tipo in ("ricezione", "difesa", "free ball"):
            known = pd.notna(r["Numero"])
            player = f"{int(r['Numero'])} {r['Cognome']}" if known else None
            rtype = receiver_type(roles.get(int(r["Numero"])), _is_front(r["Posizione Giocatore"])) if known else None
            last_touch[r["Rally"]] = (tipo, r["Voto"], player, rtype, front_outside(r))
            continue
        if tipo != "attacco" or pd.isna(r["Numero"]):
            continue
        numero = int(r["Numero"])
        role = roles.get(numero)
        if role == SETTER_ROLE:
            continue
        front = _is_front(r["Posizione Giocatore"])
        recorded = r.get("Zona partenza")
        recorded = None if recorded is None or pd.isna(recorded) else int(recorded)
        zona, stimata = attack_zone(role, front, recorded)
        prev = last_touch.get(r["Rally"])
        is_so = i in so_idx
        received = prev if is_so and prev and prev[0] == "ricezione" else None
        diff = int(r[own_score]) - int(r[opp_score])
        rows.append({
            "partita": match_label, "set": r["Numero Set"],
            "palleggiatore": r["Palleggiatore in campo"] if pd.notna(r["Palleggiatore in campo"]) else "n/d",
            "rotazione": str(r["Posizione Palleggiatore"]).upper() if pd.notna(r["Posizione Palleggiatore"]) else None,
            "attaccante": f"{numero} {r['Cognome']}", "ruolo": role, "prima_linea": front,
            "zona": zona, "zona_stimata": stimata,
            # codici corti (alcuni scout 2025-2026) senza tipo di palla/colpo:
            # valgono i default del programma, alta e attacco
            "tipo_alzata": BALL_TYPES.get(_code_value(r, "Tipo palla") or "H", _code_value(r, "Tipo palla")),
            "tipo_colpo": HIT_TYPES.get(_code_value(r, "Tipo colpo") or "H", _code_value(r, "Tipo colpo")),
            "voto": r["Voto"],
            "fase": "Side-out" if is_so else "Contrattacco",
            "free_ball": i in fb_idx,
            "ricezione": received[1] if received else None,
            "ricevitore": received[2] if received else None,
            "tipo_ricevitore": received[3] if received else None,
            "martello_davanti": received[4] if received else None,
            "difesa": prev[1] if not is_so and prev and prev[0] != "ricezione" else None,
            "situazione": _situation(is_so, i in fb_idx, received[1] if received else None),
            "punti_noi": int(r[own_score]), "punti_loro": int(r[opp_score]),
            "diff_punteggio": diff, "situazione_punteggio": score_situation(diff),
        })
    return pd.DataFrame(rows)


def _situation(is_so, is_fb, rec):
    if is_so:
        return "R+#" if rec in ("#", "+") else "R!" if rec == "!" else None
    return "FB" if is_fb else None


def filter_rows(rows, setters=None, rotations=None, ball_types=None):
    """Filtri comuni della vista: None = nessun filtro."""
    keep = pd.Series(True, index=rows.index)
    for col, values in (("palleggiatore", setters), ("rotazione", rotations), ("tipo_alzata", ball_types)):
        if values is not None:
            keep &= rows[col].isin(values)
    return rows[keep]


def side_out_rows(rows, rec_grades=None):
    """Attacchi di side-out dopo una ricezione con voto in `rec_grades`
    (None = tutti)."""
    so = rows[rows["fase"] == "Side-out"]
    return so if rec_grades is None else so[so["ricezione"].isin(rec_grades)]


def counter_rows(rows, mode="tutto"):
    """Attacchi di contrattacco: tutti, solo free ball o senza free ball
    (chiavi di COUNTER_MODES)."""
    ca = rows[rows["fase"] == "Contrattacco"]
    if mode == "fb":
        return ca[ca["free_ball"]]
    if mode == "no_fb":
        return ca[~ca["free_ball"]]
    return ca


def _eff(sub):
    n = len(sub)
    if not n:
        return None
    pt = (sub["voto"] == "#").sum()
    neg = sub["voto"].isin(["=", "/"]).sum()
    return 100 * (pt - neg) / n


BEST_MIN_PE = 2  # coppa del miglior p-e/m della zona solo sopra questo saldo


def zone_stats(rows):
    """
    Per ognuna delle 9 zone (anche vuote): n attacchi, pct (sul totale
    con zona nota), pt (#), err (=), mur (/), pt_pct, eff ((# - = - /) /
    n, come l'efficienza d'attacco del progetto), giocatori serviti
    [(attaccante, n, pt, p-e/m)] in ordine di palloni ricevuti (p-e/m =
    punti meno errori meno murate), best = (attaccante, p-e/m, n) del giocatore con il
    p-e/m più alto, solo se supera BEST_MIN_PE (con un saldo di 1 o 2 la
    coppa ha poco senso, indicazione dell'utente) nella zona (a parità, più punti e più attacchi) o None.
    """
    known = rows[rows["zona"].notna()] if not rows.empty else rows
    total = len(known)
    out = []
    for z in ZONES:
        sub = known[known["zona"] == z] if total else known
        players, best = [], None
        if len(sub):
            g = sub.groupby("attaccante").agg(n=("voto", "size"), pt=("voto", lambda v: int((v == "#").sum())),
                                              neg=("voto", lambda v: int(v.isin(["=", "/"]).sum())))
            g["pe"] = g["pt"] - g["neg"]
            players = [(name, int(row.n), int(row.pt), int(row.pe))
                       for name, row in g.sort_values(["n", "pt"], ascending=False).iterrows()]
            top = g.sort_values(["pe", "pt", "n"], ascending=False).iloc[0]
            if top.pe > BEST_MIN_PE:
                best = (top.name, int(top.pe), int(top.n))
        n = len(sub)
        out.append({
            "zona": z, "n": n, "pct": 100 * n / total if total else 0.0,
            "pt": int((sub["voto"] == "#").sum()) if n else 0,
            "err": int((sub["voto"] == "=").sum()) if n else 0,
            "mur": int((sub["voto"] == "/").sum()) if n else 0,
            "pt_pct": 100 * (sub["voto"] == "#").sum() / n if n else None,
            "eff": _eff(sub),
            "giocatori": players, "best": best,
        })
    return pd.DataFrame(out)


def distributions_by_setter(rows):
    """{palleggiatore: {situazione: {stats (zone_stats), n, eff}, ...,
    'stimate': n}} sugli attacchi con lui in campo, una griglia per
    situazione (SITUATIONS) per il report partita; 'stimate' = attacchi
    delle tre situazioni con zona stimata."""
    out = {}
    for setter in sorted(s for s in rows["palleggiatore"].unique() if s != "n/d") if not rows.empty else []:
        sub = rows[rows["palleggiatore"] == setter]
        out[setter] = {key: {"stats": zone_stats(part), "n": len(part), "eff": _eff(part)}
                       for key, part in ((k, sub[sub["situazione"] == k]) for k in SITUATIONS)}
        out[setter]["stimate"] = int(sub[sub["situazione"].isin(list(SITUATIONS))]["zona_stimata"].sum())
    return out


def _chi2(zones, cats, n_z, n_c):
    table = np.bincount(zones * n_c + cats, minlength=n_z * n_c).reshape(n_z, n_c).astype(float)
    expected = table.sum(1, keepdims=True) * table.sum(0, keepdims=True) / table.sum()
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.nansum((table - expected) ** 2 / expected), table, expected


def _stratified_chi2(zones, cats, strata, n_z, n_c):
    """Chi quadro sommato sugli strati (None = uno solo), tabella e attesi
    sommati sugli strati."""
    if strata is None:
        return _chi2(zones, cats, n_z, n_c)
    stat, table, expected = 0.0, np.zeros((n_z, n_c)), np.zeros((n_z, n_c))
    for s_ in np.unique(strata):
        m = strata == s_
        st_, tb, ex = _chi2(zones[m], cats[m], n_z, n_c)
        stat, table, expected = stat + st_, table + tb, expected + ex
    return stat, table, expected


def _permute(cats, strata, rng):
    if strata is None:
        return rng.permutation(cats)
    out = cats.copy()
    for s_ in np.unique(strata):
        idx = np.flatnonzero(strata == s_)
        out[idx] = cats[rng.permutation(idx)]
    return out


def direction_categories(rows, direction):
    """Le categorie della direzione presenti nei dati, nell'ordine naturale."""
    if direction in SIDE_OUT_DIRECTIONS:
        rows = rows[rows["fase"] == "Side-out"]
    if direction == "ricezione":
        vals = rows["ricezione"].dropna().unique()
        return [g for g in ALL_GRADES if g in vals]
    if direction == "situazione_punteggio":
        return [s for s in SCORE_SITUATIONS if s in set(rows[direction])]
    if direction == "partita":
        return list(dict.fromkeys(rows["partita"]))
    if direction == "ricevitore":
        return list(rows["ricevitore"].value_counts().index)
    if direction == "tipo_ricevitore":
        return [t for t in RECEIVER_TYPES if t in set(rows[direction])]
    return sorted(rows[direction].dropna().unique())


def _independence_test(sub, col, cats, strata_col=None, n_perm=N_PERMUTATIONS, seed=0):
    """
    Chi quadro zona x `col` (categorie `cats`, già filtrate) con p-value per
    permutazione; con `strata_col` stratificato (permutazioni dentro ogni
    strato, attesi = somma degli attesi per strato). Scostamenti = celle con
    residuo standardizzato |r| >= 2. None se mancano i dati.
    """
    zones_present = [z for z in ZONES if (sub["zona"] == z).any()]
    if len(zones_present) < 2 or len(cats) < 2:
        return None
    z_codes = sub["zona"].map({z: i for i, z in enumerate(zones_present)}).to_numpy()
    c_codes = sub[col].map({c: i for i, c in enumerate(cats)}).to_numpy()
    strata = None if strata_col is None else sub[strata_col].astype(str).to_numpy()
    n_z, n_c = len(zones_present), len(cats)
    stat, table, expected = _stratified_chi2(z_codes, c_codes, strata, n_z, n_c)
    rng = np.random.default_rng(seed)
    perm = np.array([_stratified_chi2(z_codes, _permute(c_codes, strata, rng), strata, n_z, n_c)[0]
                     for _ in range(n_perm)])
    p = (1 + (perm >= stat - 1e-9).sum()) / (n_perm + 1)
    n = table.sum()
    v = float(np.sqrt(stat / (n * (min(n_z, n_c) - 1))))
    row_share, col_share = table.sum(1) / n, table.sum(0) / n
    with np.errstate(divide="ignore", invalid="ignore"):
        if strata is None:
            resid = (table - expected) / np.sqrt(expected * np.outer(1 - row_share, 1 - col_share))
        else:  # residuo di Pearson sugli attesi stratificati
            resid = (table - expected) / np.sqrt(expected)
    deviations = []
    for zi, z in enumerate(zones_present):
        for ci, c in enumerate(cats):
            if np.isfinite(resid[zi, ci]) and abs(resid[zi, ci]) >= 2:
                col_n = table[:, ci].sum()
                deviations.append({
                    "categoria": c, "zona": z, "pct": 100 * table[zi, ci] / col_n,
                    # riferimento: quota sul totale, o quella attesa a parità di strato
                    "pct_totale": 100 * (row_share[zi] if strata is None else expected[zi, ci] / col_n),
                    "residuo": float(resid[zi, ci]),
                })
    deviations.sort(key=lambda d: -abs(d["residuo"]))
    return {"n": int(n), "chi2": float(stat), "p": float(p), "cramer_v": v, "scostamenti": deviations}


def test_direction(rows, direction, n_perm=N_PERMUTATIONS, seed=0, stratify=True):
    """
    La distribuzione per zona cambia lungo `direction` (vedi DIRECTIONS)?
    Test chi quadro di indipendenza zona x categoria con p-value per
    permutazione (corretto anche con poche osservazioni per cella), sulle
    categorie con almeno MIN_PER_CATEGORY attacchi; il ricevitore
    stratificato sul voto di ricezione (STRATA). Ritorna None se i dati
    non bastano (meno di 2 categorie o di MIN_TOTAL attacchi), altrimenti
    {direzione, categorie [(nome, n)], n, chi2, p, cramer_v, scostamenti}
    con gli scostamenti = celle con residuo standardizzato |r| >= 2:
    {categoria, zona, pct (nella categoria), pct_totale (quota di
    riferimento: sul totale o, con lo strato, attesa a parità di strato),
    residuo}.
    """
    sub = rows[rows["zona"].notna()]
    if direction in SIDE_OUT_DIRECTIONS:
        sub = sub[sub["fase"] == "Side-out"]
    sub = sub[sub[direction].notna()]
    counts = sub[direction].value_counts()
    cats = [c for c in direction_categories(sub, direction) if counts.get(c, 0) >= MIN_PER_CATEGORY]
    sub = sub[sub[direction].isin(cats)]
    strata_col = STRATA.get(direction) if stratify else None
    if strata_col:
        # strati in cui c'è almeno un confronto possibile (due categorie)
        ok = sub.groupby(strata_col)[direction].transform("nunique") > 1
        sub = sub[ok]
        cats = [c for c in cats if (sub[direction] == c).any()]
    if len(cats) < 2 or len(sub) < MIN_TOTAL:
        return None
    res = _independence_test(sub, direction, cats, strata_col, n_perm, seed)
    if res is None:
        return None
    sub_counts = sub[direction].value_counts()
    return {"direzione": direction, "categorie": [(c, int(sub_counts[c])) for c in cats], **res}


def _holm(results):
    """Correzione di Holm sui risultati non None di un dict: aggiunge p_holm
    e significativo (p_holm < ALPHA)."""
    valid = sorted((r["p"], k) for k, r in results.items() if r)
    m, running = len(valid), 0.0
    for k, (p, key) in enumerate(valid):
        running = max(running, min(1.0, (m - k) * p))
        results[key]["p_holm"] = running
        results[key]["significativo"] = running < ALPHA
    return results


def test_all_directions(rows, directions=tuple(DIRECTIONS), stratify=True):
    """test_direction su ogni direzione, con correzione di Holm sui p-value
    (più direzioni guardate insieme = più occasioni di un falso allarme):
    ogni risultato ha in più p_holm e significativo (p_holm < ALPHA).
    Direzioni senza dati sufficienti: risultato None."""
    return _holm({d: test_direction(rows, d, stratify=stratify) for d in directions})


# Test della distribuzione nei due casi indicati dall'utente (2026-10-30):
# side-out da ricezione positiva (R+ e R# insieme) e da ricezione !. Dentro
# ciascun caso la qualità della ricezione è già fissata: i test lungo le
# altre direzioni non sono stratificati sul voto; la qualità della ricezione
# diventa il confronto tra i due casi.
RECEPTION_CASES = {"pos": ("Da ricezione positiva (R+ / R#)", ("#", "+")), "escl": ("Da ricezione R!", ("!",))}
CASE_DIRECTIONS = ("rotazione", "tipo_ricevitore", "ricevitore", "situazione_punteggio", "partita")


def test_reception_cases(rows, n_perm=N_PERMUTATIONS):
    """
    {'qualita': confronto R+/R# contro R! (_independence_test, categorie
    'R+ / R#' e 'R!', o None), 'pos': {direzione: risultato}, 'escl':
    {direzione: risultato}} sugli attacchi di side-out di `rows`; dentro
    ciascun caso test_direction lungo CASE_DIRECTIONS (partita solo se ce n'è
    più d'una) con Holm sulle direzioni del caso.
    """
    so = rows[(rows["fase"] == "Side-out") & rows["zona"].notna()]
    out = {}
    labels = {}
    for key, (_, grades) in RECEPTION_CASES.items():
        case = so[so["ricezione"].isin(grades)]
        dirs = [d for d in CASE_DIRECTIONS if d != "partita" or case["partita"].nunique() > 1]
        out[key] = _holm({d: test_direction(case, d, n_perm=n_perm, stratify=False) for d in dirs})
        labels.update({g: "R+ / R#" if key == "pos" else "R!" for g in grades})
    both = so[so["ricezione"].isin(list(labels))].assign(_qualita=lambda d: d["ricezione"].map(labels))
    counts = both["_qualita"].value_counts()
    cats = [c for c in ("R+ / R#", "R!") if counts.get(c, 0) >= MIN_PER_CATEGORY]
    out["qualita"] = (_independence_test(both[both["_qualita"].isin(cats)], "_qualita", cats, n_perm=n_perm)
                      if len(cats) == 2 and len(both) >= MIN_TOTAL else None)
    return out


def test_setter_situations(rows, n_perm=N_PERMUTATIONS):
    """
    Per ogni palleggiatore e ogni situazione (SITUATIONS): i palloni di
    quella situazione vanno in zone diverse dal resto dei suoi palloni?
    Test zona x (situazione / resto) sugli attacchi con lui in campo, senza
    guardare altre direzioni. {(palleggiatore, situazione): risultato o
    None se i dati non bastano}, risultato = {n_situazione, n, chi2, p,
    cramer_v, p_holm, significativo, scostamenti (solo della situazione:
    pct nella situazione contro pct_totale = nel resto), quote (zona -> %
    nella situazione)}. Holm su tutti i test insieme.
    """
    results = {}
    known = rows[rows["zona"].notna() & (rows["palleggiatore"] != "n/d")]
    for setter in sorted(known["palleggiatore"].unique()):
        mine = known[known["palleggiatore"] == setter]
        for key in SITUATIONS:
            grp = np.where(mine["situazione"] == key, key, "resto")
            sub = mine.assign(_gruppo=grp)
            n_key = int((grp == key).sum())
            if n_key < MIN_PER_CATEGORY or len(sub) - n_key < MIN_PER_CATEGORY or len(sub) < MIN_TOTAL:
                results[(setter, key)] = None
                continue
            res = _independence_test(sub, "_gruppo", [key, "resto"], n_perm=n_perm)
            if res is None:
                results[(setter, key)] = None
                continue
            in_key = sub[sub["_gruppo"] == key]
            rest = sub[sub["_gruppo"] == "resto"]
            res["scostamenti"] = [
                {**d, "pct_totale": 100 * (rest["zona"] == d["zona"]).mean()}
                for d in res["scostamenti"] if d["categoria"] == key
            ]
            res["n_situazione"] = n_key
            res["quote"] = (100 * in_key["zona"].value_counts(normalize=True)).to_dict()
            results[(setter, key)] = res
    return _holm(results)


# Domanda dell'utente (2026-10-25): solo sulle ricezioni positive (R+ e R#
# insieme), la distribuzione cambia a seconda di chi riceve tra libero,
# martello in prima linea e martello in seconda linea? E il palleggiatore
# serve il martello di prima linea quando è lui a ricevere?
POSITIVE_GRADES = ("#", "+")


def positive_reception_by_receiver(rows, n_perm=N_PERMUTATIONS, seed=0):
    """
    Side-out dopo ricezione + o # (insieme), per tipo di ricevitore
    (RECEIVER_TYPES). Ritorna {
      'per_tipo': {tipo: {stats (zone_stats), n, eff}},
      'test': chi quadro zona x tipo di ricevitore con p-value per
        permutazione (_independence_test, categorie con almeno
        MIN_PER_CATEGORY attacchi; None se i dati non bastano), una sola
        domanda decisa prima di guardare i dati: niente correzione per
        confronti multipli,
      'martello_davanti': quanto attacca lo schiacciatore di prima linea
        quando riceve lui e quando riceve un altro (libero o martello di
        seconda linea): {riceve_lui: (attacchi suoi, totale), riceve_altro:
        (attacchi suoi, totale), p} con p = permutazione sulla tabella 2x2
        (None se uno dei due gruppi ha meno di MIN_PER_CATEGORY attacchi)}.
    """
    pos = rows[(rows["fase"] == "Side-out") & rows["ricezione"].isin(POSITIVE_GRADES) & rows["tipo_ricevitore"].notna()]
    per_tipo = {t: {"stats": zone_stats(sub), "n": len(sub), "eff": _eff(sub)}
                for t, sub in ((t, pos[pos["tipo_ricevitore"] == t]) for t in RECEIVER_TYPES)}
    known = pos[pos["zona"].notna()]
    counts = known["tipo_ricevitore"].value_counts()
    cats = [t for t in RECEIVER_TYPES if counts.get(t, 0) >= MIN_PER_CATEGORY]
    sub = known[known["tipo_ricevitore"].isin(cats)]
    test = _independence_test(sub, "tipo_ricevitore", cats, n_perm=n_perm, seed=seed) \
        if len(cats) >= 2 and len(sub) >= MIN_TOTAL else None

    fo = pos[pos["martello_davanti"].notna()]
    received_self = (fo["ricevitore"] == fo["martello_davanti"]).to_numpy()
    attacked = (fo["attaccante"] == fo["martello_davanti"]).to_numpy()
    front = None
    if received_self.sum() >= MIN_PER_CATEGORY and (~received_self).sum() >= MIN_PER_CATEGORY:
        def gap(labels):
            return attacked[labels].mean() - attacked[~labels].mean()
        obs = gap(received_self)
        rng = np.random.default_rng(seed)
        perm = np.array([gap(rng.permutation(received_self)) for _ in range(n_perm)])
        p = (1 + (np.abs(perm) >= abs(obs) - 1e-12).sum()) / (n_perm + 1)
        front = {"riceve_lui": (int(attacked[received_self].sum()), int(received_self.sum())),
                 "riceve_altro": (int(attacked[~received_self].sum()), int((~received_self).sum())), "p": float(p)}
    return {"per_tipo": per_tipo, "test": test, "martello_davanti": front}
