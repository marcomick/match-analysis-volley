"""
Server locale dello studio video (richiesta dell'utente del 2026-11-27): serve la pagina `video/index.html`, i dati
(tutte le azioni degli scout con il minuto del video, src.video_study.season_data), i file video su Drive con le
richieste a intervalli (Range) che servono per saltare a un minuto, salva link e allineamenti in video_scout.json e
prepara il montaggio (src.video_clips).

Avvio: `python -m src.video_server` (apre il browser su http://127.0.0.1:8791/), oppure dalla dashboard (tab "Studio
video"), che lo avvia in un thread. Ascolta solo su 127.0.0.1 (funziona sul Mac dove c'è Drive) e accetta scritture
solo dalla sua pagina. Senza stato: se la porta è già occupata da un'altra istanza, si usa quella.
"""
import json
import mimetypes
import os
import socket
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

PORT = int(os.environ.get("VOLLEY_VIDEO_PORT", 8791))      # altra porta solo per le prove
VIDEO_EXT = {".mp4", ".m4v", ".mov", ".webm", ".mkv"}
PAGE = Path(__file__).resolve().parent.parent / "video" / "index.html"
_lock = threading.Lock()
_started = False
_jobs = {}                                                  # montaggi in preparazione


def _root():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    r = os.environ.get("VOLLEY_DRIVE_ROOT")
    return Path(r).resolve() if r else None


def allowed(path):
    root, p = _root(), Path(path).resolve()
    return root is not None and p.suffix.lower() in VIDEO_EXT and root in p.parents and p.is_file()


def url_for(path):
    return f"/v/{quote(str(Path(path).resolve()))}"


def src_for(link, base):
    """{"kind": "yt", "id"} o {"kind": "file", "url", "path"} dal link salvato, o None."""
    from src.video_study import youtube_id
    url = (link or {}).get("url", "")
    vid = youtube_id(url)
    if vid:
        return {"kind": "yt", "id": vid}
    p = Path(url).expanduser()
    if not p.is_absolute():
        p = Path(base) / p
    if url and allowed(p):
        return {"kind": "file", "url": url_for(p), "path": str(p)}
    return None


def seasons():
    from config.paths import load_seasons, season_base_path
    s = load_seasons()
    s = s[s["active"] == 1]
    return {r.season: season_base_path(r.base_path_drive) for r in s.itertuples()}


def _season_base(season):
    base = seasons().get(season)
    if base is None:
        raise ValueError("stagione sconosciuta")
    return base


def _montage(job, clips, caption=""):
    import importlib
    import src.video_clips as vc
    importlib.reload(vc)          # il server vive dentro la dashboard: così le modifiche al montaggio valgono subito
    try:
        job["data"] = vc.build_montage(clips, progress=lambda i, n: job.update(done=i, total=n), caption=caption)
    except Exception as e:  # noqa: BLE001 - errore di rete o di ffmpeg: si mostra nella pagina
        job["error"] = str(e)[-400:]


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def _same_origin(self):
        o = self.headers.get("Origin")
        return o is None or o in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}")

    def _body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")

    # ------------------------------------------------------------------ scritture
    def do_POST(self):
        if not self._same_origin():
            return self._send(403, "no")
        try:
            d = self._body()
            if self.path == "/fix":
                # istante vero di un'azione: {"base", "file", "key", "t" (o null)}
                from src.video_study import save_fix
                root, base = _root(), Path(d["base"]).resolve()
                if root is None or not (base == root or root in base.parents) or not base.is_dir():
                    return self._send(403, "no")
                with _lock:
                    save_fix(base, str(d["file"]), str(d["key"]), None if d.get("t") is None else float(d["t"]))
                return self._send(200, "ok")
            if self.path == "/api/link":
                from src.video_study import save_video_link
                with _lock:
                    save_video_link(_season_base(d["season"]), str(d["file"]), str(d.get("url") or ""),
                                    float(d.get("offset") or 0))
                return self._send(200, "ok")
            if self.path == "/api/montage":
                clips = d["clips"]
                if not clips or len(clips) > 80:
                    return self._send(400, "da 1 a 80 clip")
                for c in clips:
                    if c["src"]["kind"] == "file" and not allowed(c["src"].get("path", "")):
                        return self._send(403, "file non permesso")
                job_id = uuid.uuid4().hex[:10]
                job = _jobs[job_id] = {"done": 0, "total": len(clips)}
                threading.Thread(target=_montage, args=(job, clips, str(d.get("caption") or "")[:200]), daemon=True).start()
                for k in list(_jobs)[:-5]:                  # si tengono solo gli ultimi montaggi
                    _jobs.pop(k, None)
                return self._json({"job": job_id})
            return self._send(404, "no")
        except Exception as e:  # noqa: BLE001 - richiesta sbagliata: la si rifiuta
            return self._send(400, str(e)[:300])

    # ------------------------------------------------------------------ letture
    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if u.path == "/api/seasons":
            return self._json(list(seasons()))
        if u.path == "/api/data":
            try:
                from src.video_study import season_data
                return self._json(season_data(_season_base(parse_qs(u.query).get("season", [""])[0]), src_for))
            except Exception as e:  # noqa: BLE001
                return self._json({"error": str(e)[:300]}, 500)
        if u.path.startswith("/api/montage/"):
            name = u.path.rsplit("/", 1)[1]
            job = _jobs.get(name.split(".")[0])
            if job is None:
                return self._send(404, "no")
            if name.endswith(".mp4"):
                if "data" not in job:
                    return self._send(409, "non pronto")
                return self._send(200, job["data"], "video/mp4",
                                  {"Content-Disposition": 'attachment; filename="Studio video.mp4"'})
            return self._json({"done": job["done"], "total": job["total"], "error": job.get("error"),
                               "ready": "data" in job})
        if u.path.startswith("/v/"):
            return self._video(unquote(u.path[3:]))
        return self._send(404, "no")

    def _video(self, path):
        if not allowed(path):
            return self.send_error(403)
        size = os.path.getsize(path)
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].split(",")[0].partition("-")
            if a:
                start, end = int(a), int(b) if b else size - 1
            elif b:
                start = max(0, size - int(b))
            end = min(end, size - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path)[0] or "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass


def _busy():
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def ensure_server():
    """Avvia il server in un thread una volta per processo (o usa quello già in ascolto sulla porta)."""
    global _started
    with _lock:
        if _started:
            return
        if not _busy():
            srv = ThreadingHTTPServer(("127.0.0.1", PORT), _Handler)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
        _started = True


def page_url(season=None):
    return f"http://127.0.0.1:{PORT}/" + (f"?season={quote(season)}" if season else "")


if __name__ == "__main__":
    import webbrowser
    if _busy():
        print(f"Lo studio video è già aperto: {page_url()}")
        webbrowser.open(page_url())
    else:
        print(f"Studio video su {page_url()} (Ctrl+C per chiudere)")
        threading.Timer(0.8, lambda: webbrowser.open(page_url())).start()
        try:
            ThreadingHTTPServer(("127.0.0.1", PORT), _Handler).serve_forever()
        except KeyboardInterrupt:
            pass
