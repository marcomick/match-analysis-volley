"""Server locale per l'app dello scout, con le richieste a pezzi (Range) che Safari usa per i video.

Serve i file dell'app da scout/ e, per quello che lì non c'è (il video di prova G4_set1.mp4), da data/scout_mockup/.
All'avvio rilegge i referti da Drive e riscrive scout/referti.json.
Uso: python3 scout/serve.py   (poi sull'iPad http://<IP del Mac>:8766)
Da http://<IP> il service worker non parte (serve https): l'app funziona, ma non offline. Il salvataggio dello scout
(IndexedDB) funziona; è separato da quello dell'app pubblicata: per passare dall'una all'altra, copia di sicurezza.
"""
import os
import re
import subprocess
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

PORT = 8766


HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DATA_DIR = os.path.join(REPO, "data", "scout_mockup")


class RangeHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        p = SimpleHTTPRequestHandler.translate_path(self, path)       # dentro scout/ (directory di lavoro)
        if not os.path.exists(p):
            alt = os.path.join(DATA_DIR, os.path.relpath(p, HERE))
            if os.path.exists(alt):
                return alt
        return p

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def send_head(self):
        rng = self.headers.get("Range")
        path = self.translate_path(self.path)
        if not rng or not os.path.isfile(path):
            return super().send_head()
        m = re.match(r"bytes=(\d*)-(\d*)", rng)
        size = os.path.getsize(path)
        if not m:
            return super().send_head()
        start = int(m.group(1)) if m.group(1) else max(0, size - int(m.group(2) or 0))
        end = int(m.group(2)) if m.group(1) and m.group(2) else size - 1
        end = min(end, size - 1)
        if start > end:
            self.send_error(416)
            return None
        f = open(path, "rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        self._remaining = end - start + 1
        return f

    def copyfile(self, source, outputfile):
        remaining = getattr(self, "_remaining", None)
        try:
            if remaining is None:
                return super().copyfile(source, outputfile)
            while remaining > 0:
                chunk = source.read(min(1 << 16, remaining))
                if not chunk:
                    break
                outputfile.write(chunk)
                remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # il browser ha chiuso la richiesta (salto nel video): normale

    def log_message(self, *args):
        pass


def update_referti():
    """Rilegge i referti da Drive e riscrive referti.json (partite, rose, formazioni, cambi). Se non riesce, si usa
    il file di prima."""
    py = os.path.join(REPO, ".venv", "bin", "python")
    try:
        r = subprocess.run([py if os.path.exists(py) else sys.executable, "-m", "src.referti",
                            "--out", os.path.join(HERE, "referti.json")], cwd=REPO, capture_output=True, text=True, timeout=300)
        print(r.stdout.strip() or r.stderr.strip().splitlines()[-1])
    except Exception as e:
        print(f"Referti non aggiornati ({e}): uso referti.json di prima")


if __name__ == "__main__":
    update_referti()
    os.chdir(HERE)
    print(f"Scout su http://localhost:{PORT}  (Ctrl+C per fermare)")
    ThreadingHTTPServer(("", PORT), RangeHandler).serve_forever()
