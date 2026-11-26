"""
Confronta i calcoli della pagina report/index.html (JavaScript, con node) con
quelli di Python (src/) su tutti i .dvw delle cartelle date: MVP, riconoscimenti,
punti per causa, noi e loro per set, tabellino per giocatore, palleggiatori.

    python -m tests.report_html_parity <cartella> [<cartella> ...]
"""
import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from src.dvw import decimo_side
from src.match_report import load_report_source, point_sources_by_set, setter_table, team_comparison
from src.mvp import match_awards, match_mvp
from src.set_analysis import player_kpi_table

ROOT = Path(__file__).resolve().parent.parent
NODE = """
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const code = html.match(/<script id="core">([\\s\\S]*?)<\\/script>/)[1];
const mod = { exports: {} };
new Function('module', code)(mod);
const Core = mod.exports;
const out = {};
for (const path of JSON.parse(fs.readFileSync(process.argv[3], 'utf8'))) {
  const text = new TextDecoder('windows-1252').decode(fs.readFileSync(path));
  const parsed = Core.parseDvw(text);
  const src = Core.loadSource(parsed, Core.defaultSide(parsed));
  const mvp = Core.matchMvp(src);
  out[path] = { mvp: mvp.map(r => ({ numero: r.numero, punteggio: r.punteggio, totale: r.totale,
      ...Object.fromEntries(Core.FUNDS.concat(['premio']).map(f => [f, r[f]])) })),
    awards: Core.matchAwards(src, mvp), points: Core.pointsBySet(src), comp: Core.teamComparison(src),
    players: Core.playerKpiTable(src.noi), setters: Core.setterTable(src) };
}
process.stdout.write(JSON.stringify(out));
"""


def close(a, b):
    if a is None or (isinstance(a, float) and math.isnan(a)):
        return b is None or (isinstance(b, float) and math.isnan(b))
    if b is None or (isinstance(b, float) and math.isnan(b)):
        return False
    return abs(float(a) - float(b)) < 1e-6


def main(folders):
    files = [str(p) for f in folders for p in sorted(Path(f).rglob("*.dvw"))]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fl, \
            tempfile.NamedTemporaryFile("w", suffix=".cjs", delete=False) as fj:
        json.dump(files, fl)
        fj.write(NODE)
    js = json.loads(subprocess.run(["node", fj.name, str(ROOT / "report" / "index.html"), fl.name],
                                   capture_output=True, text=True, check=True).stdout)
    bad = 0
    for path in files:
        diffs = []
        try:
            src = load_report_source(path)
        except (ValueError, KeyError):
            continue  # senza Decimo: la pagina sceglie la squadra di casa, Python no
        j = js[path]
        py = match_mvp(src)
        order = sorted(((round(-r["punteggio"], 6), int(r["giocatore"].split(" ")[0])) for r in py.to_dict("records")))
        if [n for _, n in order] != [r["numero"] for r in j["mvp"]]:
            diffs.append("ordine MVP")
        for r in py.to_dict("records"):
            n = int(r["giocatore"].split(" ")[0])
            jr = next((x for x in j["mvp"] if x["numero"] == n), None)
            for k in ["punteggio", "totale", "battuta", "ricezione", "attacco", "muro", "difesa", "alzata", "premio"]:
                if jr is None or not close(r[k], jr[k]):
                    diffs.append(f"MVP {n} {k}: {r[k]} / {jr and jr[k]}")
        aw = match_awards(src, py)
        for k, v in aw.items():
            jv = j["awards"][k]
            if (v is None) != (jv is None) or (v and any(not close(v[x], jv[x]) if not isinstance(v[x], str) else v[x] != jv[x]
                                                         for x in v)):
                diffs.append(f"riconoscimento {k}: {v} / {jv}")
        pts = point_sources_by_set(src)
        for row in j["points"]:
            for key in ("noi", "loro"):
                for causa, n in row[key].items():
                    p = pts[(pts["set"] == row["set"]) & (pts["squadra"] == key) & (pts["causa"] == causa)]["n"].iloc[0]
                    if p != n:
                        diffs.append(f"punti set {row['set']} {key} {causa}: {p} / {n}")
        comp = team_comparison(src)
        for key in ("noi", "loro"):
            for row in j["comp"][key]:
                for m, v in row.items():
                    if m != "set" and not close(comp[key].loc[row["set"], m], v):
                        diffs.append(f"{key} set {row['set']} {m}: {comp[key].loc[row['set'], m]} / {v}")
        tables = player_kpi_table(src["noi"])
        if list(tables) != [t["name"] for t in j["players"]]:
            diffs.append(f"giocatori del tabellino: {list(tables)} / {[t['name'] for t in j['players']]}")
        for t in j["players"]:
            df = tables.get(t["name"])
            if df is None:
                continue
            recs = df.to_dict("records")
            if [r["kpi"] for r in recs] != [r["kpi"] for r in t["rows"]]:
                diffs.append(f"righe di {t['name']}")
                continue
            for a, b in zip(recs, t["rows"]):
                for c, v in a.items():
                    if c not in ("kpi", "label") and not close(v, b.get(c)):
                        diffs.append(f"tabellino {t['name']} {a['kpi']} {c}: {v} / {b.get(c)}")
        st = setter_table(src)
        for s in j["setters"]:
            df = st.get(s["name"])
            if df is None:
                diffs.append(f"palleggiatore {s['name']}")
                continue
            for a, b in zip(df.to_dict("records"), s["rows"]):
                for k in ("R+#", "R!", "FB"):
                    if not close(a[f"{k} eff"], b[k]["eff"]) or a[f"{k} n"] != b[k]["n"]:
                        diffs.append(f"palleggiatore {s['name']} set {a['set']} {k}")
        status = "ok" if not diffs else f"{len(diffs)} differenze"
        print(f"{Path(path).name}: {status}")
        for d in diffs[:8]:
            print("   ", d)
        bad += bool(diffs)
    print(f"\n{len(files)} file letti (confrontati solo quelli con il Decimo), {bad} con differenze")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
