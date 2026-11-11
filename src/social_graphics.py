# src/social_graphics.py
"""
Grafiche per le storie Instagram (richiesta dell'utente, 2026-10-31): PNG
1080 x 1920, con le fasce in alto e in basso (circa 250 px) lasciate libere
per l'interfaccia di Instagram.

1. MVP della partita: la foto del roster del giocatore (già una grafica da
   storia, 1080 x 1920: ruolo, nome, foto d'azione e in primo piano) come
   sfondo, con sopra il grande "MVP", la partita e 2-3 tessere con i numeri
   dei fondamentali che hanno pesato di più. Una foto qualsiasi va in una
   cornice; senza foto, il numero di maglia. Niente punti MVP: sono un
   calcolo nostro, fuori dalla squadra nessuno li saprebbe leggere.
2. Risultato: loghi e nomi delle squadre, risultato, parziali colorati per chi
   ha vinto il set, cinque KPI a specchio (ricezione, attacco, ace, muri
   punto, errori, definizioni della dashboard) e l'MVP in fondo.

Stile ripreso dalle foto del roster (indicazione dell'utente): fondo nero con
bagliore blu a sinistra e rosso a destra, logo in filigrana, titoletti in
maiuscolo sottile e spaziato con una riga rossa; oro per l'MVP. Nomi: "Decimo
Roma" e l'avversario come in config/matches.csv. Logo: assets/logo-decimo-
roma.png; loghi avversari caricati dalla dashboard in assets/loghi_avversari/.
Foto: cartella "foto roster serie D" della stagione su Drive, un file per
giocatore con il cognome come nome.
"""
import io
import unicodedata
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch, Rectangle

from src.mvp import FUNDAMENTALS, MIN_EVENTS_FOR_PERCENT

W, H = 1080, 1920
SAFE_TOP, SAFE_BOTTOM = 250, 1670  # contenuto importante solo tra queste due quote

BLACK = "#05070b"
BLUE = "#2a78d6"
BLUE_LIGHT = "#7fb2f0"
GREY = "#8b95a5"
GREY_DARK = "#161d28"
WHITE = "#ffffff"
RED = "#c8202e"
GOLD = "#e5b80b"

ROOT = Path(__file__).resolve().parent.parent
LOGO_PATH = ROOT / "assets" / "logo-decimo-roma.png"
OPPONENT_LOGOS_DIR = ROOT / "assets" / "loghi_avversari"
PHOTOS_FOLDER = "foto roster serie D"  # nella cartella della stagione su Drive
TEAM_NAME = "Decimo Roma"


def _font(candidates):
    """Primo font disponibile tra `candidates` (nomi di famiglia), altrimenti
    DejaVu Sans (sempre presente con matplotlib)."""
    available = {f.name: f.fname for f in font_manager.fontManager.ttflist}
    for name in candidates:
        if name in available:
            return font_manager.FontProperties(fname=available[name])
    return font_manager.FontProperties(family="DejaVu Sans", weight="bold")


TITLE_FONT = _font(["DIN Condensed", "Avenir Next Condensed", "Impact"])
BODY_FONT = _font(["Avenir Next", "Helvetica Neue", "Arial"])
THIN_FONT = _font(["Avenir", "Helvetica Neue", "Gill Sans"])  # titoletti spaziati, come il ruolo nelle foto


def _px(size):
    """Dimensione del carattere in pixel -> punti (figura a 100 dpi)."""
    return size * 0.72


def _spaced(text):
    return " ".join(str(text).upper())


def _figure():
    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")
    return fig, ax


def _background(ax, watermark=True):
    """Nero con un bagliore blu a sinistra e uno rosso a destra, come nelle
    foto del roster, e il logo in filigrana."""
    h, w = H // 4, W // 4
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.zeros((h, w, 3)) + np.array(matplotlib.colors.to_rgb(BLACK))
    for cx, cy, sx, sy, color, strength in ((0.0, 0.52, 0.55, 0.30, BLUE, 0.42), (1.0, 0.60, 0.50, 0.28, RED, 0.38)):
        glow = np.exp(-(((xx / w - cx) / sx) ** 2 + ((yy / h - cy) / sy) ** 2))
        img += strength * glow[..., None] * np.array(matplotlib.colors.to_rgb(color))
    rng = np.random.default_rng(7)
    img += rng.normal(0, 0.012, img.shape)  # grana leggera, come il fumo delle foto
    ax.imshow(np.clip(img, 0, 1), extent=(0, W, H, 0), aspect="auto", zorder=0, interpolation="bicubic")
    if watermark and LOGO_PATH.exists():
        logo = plt.imread(LOGO_PATH)
        size = 760
        ax.imshow(logo, extent=(W - size + 160, W + 160, 1050 + size / 2, 1050 - size / 2), alpha=0.10, zorder=1)


def _text(ax, x, y, s, size, color=WHITE, font=None, ha="center", va="center", **kw):
    return ax.text(x, y, s, fontproperties=font or BODY_FONT, fontsize=_px(size), color=color, ha=ha, va=va,
                   zorder=6, **kw)


def _fit_size(ax, s, size, max_width, font):
    """Riduce la dimensione finché il testo sta in `max_width` pixel."""
    renderer = ax.figure.canvas.get_renderer()
    t = ax.text(0, 0, s, fontproperties=font, fontsize=_px(size))
    width = t.get_window_extent(renderer=renderer).width
    t.remove()
    return size * min(1.0, max_width / width) if width else size


def _label(ax, x, y, text, size=30, color=WHITE, line_width=380):
    """Titoletto sottile e spaziato con la riga rossa sotto, come il ruolo
    nelle foto del roster."""
    _text(ax, x, y, _spaced(text), size, color=color, font=THIN_FONT)
    ax.plot([x - line_width / 2, x + line_width / 2], [y + size * 0.95, y + size * 0.95], color=RED, lw=2.5,
            zorder=6, alpha=0.9)


def _box(ax, x, y, w, h, color, radius=24, alpha=1.0, edge=None, lw=0, z=2):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={radius}", fc=color,
                                ec=edge or color, lw=lw, alpha=alpha, zorder=z))


def _image(ax, img, cx, cy, height, z=5):
    """Un'immagine (array) alta `height` px centrata in (cx, cy)."""
    w = height * img.shape[1] / img.shape[0]
    ax.imshow(img, extent=(cx - w / 2, cx + w / 2, cy + height / 2, cy - height / 2), zorder=z)


def _to_png(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100)
    plt.close(fig)
    return buf.getvalue()


def _normalize(name):
    s = unicodedata.normalize("NFKD", str(name).replace("’", "'")).encode("ascii", "ignore").decode()
    return "".join(ch for ch in s.casefold() if ch.isalnum())


def find_player_photo(cognome, folder):
    """La foto del giocatore nella cartella (nome del file = cognome, senza
    badare a maiuscole, apostrofi e accenti; jpg/jpeg/png), o None."""
    folder = Path(folder)
    if not folder.is_dir():
        return None
    key = _normalize(cognome)
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() in (".jpg", ".jpeg", ".png") and _normalize(f.stem) == key:
            return f
    return None


def opponent_logo_path(name):
    """Dove sta (o andrà salvato) il logo di una squadra avversaria."""
    return OPPONENT_LOGOS_DIR / f"{_normalize(name) or 'avversario'}.png"


def save_opponent_logo(name, data):
    """Salva il logo caricato (bytes di un'immagine) come PNG, per le
    prossime partite con la stessa squadra."""
    from PIL import Image
    OPPONENT_LOGOS_DIR.mkdir(parents=True, exist_ok=True)
    path = opponent_logo_path(name)
    Image.open(io.BytesIO(data)).convert("RGBA").save(path)
    return path


def _load_image(src, mode="RGB"):
    """Array da un percorso o da bytes (orientamento EXIF corretto)."""
    from PIL import Image, ImageOps
    img = Image.open(io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src)
    return np.asarray(ImageOps.exif_transpose(img).convert(mode))


def _is_story_graphic(img):
    """Foto già in formato storia (9:16, almeno 1000 px di larghezza), come
    quelle del roster: si usa a tutto schermo."""
    h, w = img.shape[:2]
    return w >= 1000 and abs(w / h - W / H) < 0.02


# ---------------------------------------------------------------------------
# MVP
# ---------------------------------------------------------------------------
def _count(row, key):
    v = row.get(key, 0)
    return 0 if v is None or (isinstance(v, float) and np.isnan(v)) else int(v)


def mvp_tiles(row, n=3):
    """Le tessere della storia MVP: [(numero grande, etichetta, dettaglio)]
    per i fondamentali che hanno pesato di più (contributo positivo), dal
    più importante. Percentuali solo con abbastanza azioni."""
    c = lambda fund, v: _count(row, f"{fund} {v}")
    tiles = []
    for fund in sorted(FUNDAMENTALS, key=lambda f: -row.get(f, 0)):
        if row.get(fund, 0) < 0.2:
            continue
        if fund == "attacco":
            tot = sum(c(fund, v) for v in "#+!-/=")
            pt, neg = c(fund, "#"), c(fund, "=") + c(fund, "/")
            detail = f"su {tot} attacchi" + (f" · eff {100 * (pt - neg) / tot:.0f}%"
                                              if tot >= MIN_EVENTS_FOR_PERCENT else "")
            tiles.append((str(pt), "punto in attacco" if pt == 1 else "punti in attacco", detail))
        elif fund == "muro":
            if not c(fund, "#"):
                continue  # i tocchi a muro senza muri punto non si raccontano
            tiles.append((str(c(fund, "#")), "muro punto" if c(fund, "#") == 1 else "muri punto", ""))
        elif fund == "battuta":
            if not c(fund, "#"):
                continue  # senza ace la battuta non si racconta
            tiles.append((str(c(fund, "#")), "ace", ""))
        elif fund == "ricezione":
            tot = sum(c(fund, v) for v in "#+!-/=")
            good = c(fund, "#") + c(fund, "+")
            tiles.append((str(good), "ricezione positiva" if good == 1 else "ricezioni positive", f"su {tot}"))
        elif fund == "difesa":
            good = c(fund, "#") + c(fund, "+")
            tiles.append((str(good), "difesa" if good == 1 else "difese", "che ci fanno contrattaccare"))
        elif fund == "alzata" and row.get("alzati_n"):
            tiles.append((str(int(row.get("alzati_pt", 0))), "punti dei suoi attaccanti",
                          f"su {int(row['alzati_n'])} palloni alzati"))
        if len(tiles) == n:
            break
    # gli ace ci sono sempre, anche uno solo (indicazione dell'utente)
    aces = c("battuta", "#")
    if aces and all(label != "ace" for _, label, _ in tiles):
        tiles = tiles[: n - 1] + [(str(aces), "ace", "")]
    return tiles


def _tiles(ax, tiles, top):
    gap = 24
    tw, th = (W - 120 - gap * (len(tiles) - 1)) / len(tiles), 200
    for i, (big, label, detail) in enumerate(tiles):
        x = 60 + i * (tw + gap)
        _box(ax, x, top, tw, th, GREY_DARK, radius=26, alpha=0.88, z=4)
        ax.add_patch(Rectangle((x + 26, top), tw - 52, 5, color=GOLD if i == 0 else RED, zorder=5))
        _text(ax, x + tw / 2, top + 78, big, 110, font=TITLE_FONT)
        _text(ax, x + tw / 2, top + 148, label, _fit_size(ax, label, 30, tw - 30, BODY_FONT))
        if detail:
            _text(ax, x + tw / 2, top + 184, detail, _fit_size(ax, detail, 24, tw - 30, BODY_FONT), color=GREY)


def build_mvp_story(row, match_line, date, photo=None):
    """PNG della storia MVP. `row`: riga di src.mvp.match_mvp; `match_line`:
    es. "Volley Prati 3-1 Decimo Roma"; `photo`: percorso o bytes (None =
    numero di maglia al posto della foto)."""
    fig, ax = _figure()
    numero, _, cognome = str(row["giocatore"]).partition(" ")
    img = _load_image(photo) if photo is not None else None
    tiles = mvp_tiles(row)

    if img is not None and _is_story_graphic(img):
        # foto del roster a tutto schermo: ruolo e nome ci sono già in alto
        ax.imshow(img, extent=(0, W, H, 0), aspect="auto", zorder=0)
        # "MVP" sopra la filigrana a destra, su un velo scuro
        _box(ax, 610, 445, 440, 365, BLACK, radius=30, alpha=0.45, z=3)
        _text(ax, 830, 568, "MVP", 230, color=GOLD, font=TITLE_FONT)
        _label(ax, 830, 690, "della partita", size=28, line_width=360)
        _text(ax, 830, 758, match_line, _fit_size(ax, match_line, 32, 410, BODY_FONT))
        if date:
            _text(ax, 830, 796, date, 26, color=GREY)
        # tessere su una sfumatura scura sul busto
        fade = np.linspace(0, 0.9, 200)[:, None, None] * np.ones((1, 4, 1))
        ax.imshow(np.concatenate([np.zeros((200, 4, 3)), fade], axis=2), extent=(0, W, 1700, 1330),
                  aspect="auto", zorder=3)
        ax.add_patch(Rectangle((0, 1700), W, H - 1700, color=BLACK, alpha=0.9, zorder=3))
        if tiles:
            _tiles(ax, tiles, 1440)
        return _to_png(fig)

    _background(ax)
    _label(ax, W / 2, 300, "mvp della partita", size=34, color=GOLD, line_width=460)
    _text(ax, W / 2, 392, match_line, _fit_size(ax, match_line, 40, 900, BODY_FONT))
    if date:
        _text(ax, W / 2, 440, date, 28, color=GREY)
    fx, fy, fw, fh = (210, 485, 660, 700) if img is not None else (290, 500, 500, 560)
    _box(ax, fx - 8, fy - 8, fw + 16, fh + 16, GOLD, radius=40, z=3)
    if img is not None:
        ih, iw = img.shape[:2]
        target = fw / fh
        if iw / ih > target:  # troppo larga: si taglia ai lati
            nw = int(ih * target)
            img = img[:, (iw - nw) // 2:(iw - nw) // 2 + nw]
        else:  # troppo alta: si tiene la parte alta (il viso)
            img = img[: int(iw / target)]
        im = ax.imshow(img, extent=(fx, fx + fw, fy + fh, fy), zorder=4)
        im.set_clip_path(FancyBboxPatch((fx, fy), fw, fh, boxstyle="round,pad=0,rounding_size=34",
                                        transform=ax.transData))
    else:
        _box(ax, fx, fy, fw, fh, GREY_DARK, radius=34, z=4)
        _text(ax, W / 2, fy + fh / 2 + 20, numero, 360, color=BLUE, font=TITLE_FONT)
    name = cognome.upper()
    name_y = fy + fh + 85
    _text(ax, W / 2, name_y, name, _fit_size(ax, name, 140, 900, TITLE_FONT), font=TITLE_FONT)
    if img is not None:
        _text(ax, W / 2, name_y + 78, f"#{numero}", 44, color=BLUE_LIGHT, font=TITLE_FONT)
    if tiles:
        _tiles(ax, tiles, 1385 if img is not None else 1290)
    return _to_png(fig)


# ---------------------------------------------------------------------------
# Risultato
# ---------------------------------------------------------------------------
# (etichetta, chiave, percentuale, più alto è meglio)
RESULT_KPIS = [
    ("Ricezione", "ric", True, True), ("Attacco", "att", True, True), ("Ace", "ace", False, True),
    ("Muri punto", "muri", False, True), ("Errori", "err", False, False),
]


def build_result_story(teams, sets_won, partials, kpis, date=None, mvp=None, logos=None):
    """
    PNG della storia con il risultato. `teams`: [(nome, è il Decimo)] casa e
    ospite; `sets_won`: (casa, ospite); `partials`: [(punti casa, punti
    ospite)]; `kpis`: {chiave di RESULT_KPIS: (valore casa, valore ospite)};
    `mvp`: (cognome, numero) del nostro MVP; `logos`: [logo casa, logo
    ospite] (percorso, bytes o None).
    """
    fig, ax = _figure()
    _background(ax)
    _label(ax, W / 2, 290, "risultato finale", size=34, line_width=440)
    if date:
        _text(ax, W / 2, 360, date, 28, color=GREY)

    colors = [BLUE if ours else GREY for _, ours in teams]
    for i, (name, ours) in enumerate(teams):
        x = W / 4 + 20 if i == 0 else 3 * W / 4 - 20
        logo = (logos or [None, None])[i]
        if logo is not None:
            _image(ax, _load_image(logo, "RGBA"), x, 480, 150)
        label = name.upper()
        _text(ax, x, 610, label, _fit_size(ax, label, 58, 440, TITLE_FONT), color=BLUE_LIGHT if ours else WHITE,
              font=TITLE_FONT)
        ax.add_patch(Rectangle((x - 60, 646), 120, 6, color=colors[i], zorder=5))
        won = sets_won[i] > sets_won[1 - i]
        _text(ax, x, 770, str(sets_won[i]), 220, color=WHITE if won else GREY, font=TITLE_FONT)
    _text(ax, W / 2, 770, "-", 140, color=GREY, font=TITLE_FONT)

    pw, ph, gap = 168, 70, 18
    x0 = W / 2 - (len(partials) * pw + (len(partials) - 1) * gap) / 2
    for i, (a, b) in enumerate(partials):
        x = x0 + i * (pw + gap)
        _box(ax, x, 880, pw, ph, colors[0] if a > b else colors[1], radius=35, z=4)
        _text(ax, x + pw / 2, 880 + ph / 2 + 2, f"{a}-{b}", 38, font=TITLE_FONT)

    _text(ax, W / 2 - 30, 1010, teams[0][0].upper(), 24, color=BLUE_LIGHT if teams[0][1] else GREY, ha="right")
    _text(ax, W / 2 + 30, 1010, teams[1][0].upper(), 24, color=BLUE_LIGHT if teams[1][1] else GREY, ha="left")
    max_len = 380
    for i, (label, key, pct, higher) in enumerate(RESULT_KPIS):
        y = 1065 + i * 98
        a, b = kpis.get(key, (None, None))
        _text(ax, W / 2, y, _spaced(label), 22, color=GREY, font=THIN_FONT)
        scale = max([abs(v) for v in (a, b) if v is not None] + [1])
        better = None
        if a is not None and b is not None and a != b:
            better = 0 if (a > b) == higher else 1
        for side, v in enumerate((a, b)):
            if v is None:
                continue
            length = max(8, max_len * abs(v) / scale)
            x = W / 2 - 18 - length if side == 0 else W / 2 + 18
            _box(ax, x, y + 20, length, 32, colors[side], radius=16, z=4, alpha=1.0 if better == side else 0.5)
            tx = x - 14 if side == 0 else x + length + 14
            _text(ax, tx, y + 37, f"{v:.0f}%" if pct else f"{v:.0f}", 42,
                  color=WHITE if better in (side, None) else GREY, font=TITLE_FONT,
                  ha="right" if side == 0 else "left")

    if mvp:
        cognome, numero = mvp
        _label(ax, W / 2, 1560, f"mvp {TEAM_NAME}", size=26, color=GOLD, line_width=420)
        _text(ax, W / 2, 1625, f"{cognome.upper()}  #{numero}", 54, font=TITLE_FONT)
    return _to_png(fig)


def result_story_inputs(source, opponent_name, official_result=None):
    """Dati della storia col risultato da un .dvw (src.match_report.
    load_report_source): squadre casa/ospite con i nomi per i social, set
    vinti, parziali e KPI di squadra con le definizioni della dashboard."""
    from src.match_report import match_header, point_sources, set_kpis
    header = match_header(source, official_result)
    names = {source["side"]: (TEAM_NAME, True), source["other"]: (opponent_name, False)}
    teams = [names["*"], names["a"]]
    sources = point_sources(source)

    def team_kpis(df, key):
        k = set_kpis(df)
        tot = k[(k["entita"] == "Squadra") & (k["set"] == "Tot")].set_index("kpi")["value"]
        get = lambda name: float(tot[name]) if name in tot.index and tot[name] == tot[name] else None
        return {"ric": get("Ricezione%"), "att": get("Attacco%"), "ace": sources[key]["Ace"],
                "muri": get("Muri punto"), "err": get("Errori")}

    noi, loro = team_kpis(source["noi"], "noi"), team_kpis(source["loro"], "loro")
    casa, ospite = (noi, loro) if source["side"] == "*" else (loro, noi)
    return {
        "teams": teams, "sets_won": (header["sets_casa"], header["sets_ospite"]), "partials": header["parziali"],
        "kpis": {k: (casa[k], ospite[k]) for k in casa}, "date": header["data"],
        "match_line": f"{teams[0][0]} {header['sets_casa']}-{header['sets_ospite']} {teams[1][0]}",
        "decimo_casa": source["side"] == "*",
    }
