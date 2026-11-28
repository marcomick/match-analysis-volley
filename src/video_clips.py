"""
Montaggio delle clip dello studio video in un unico mp4 da scaricare (richiesta dell'utente del 2026-11-27).

Per YouTube yt-dlp (l'ultima versione, lanciata con uvx: quella installabile con Python 3.9 non funziona più con
YouTube; node come motore JavaScript) serve solo a ricavare l'indirizzo del flusso video, una volta per video; poi
ffmpeg legge da lì soltanto i secondi di ogni clip, come dai file su Drive. Le clip si preparano in parallelo, tutte
nello stesso formato (720p, 30 fps, senza audio) con una breve dissolvenza in entrata e in uscita, e si uniscono.
Dal 2026-11-28 (richiesta dell'utente) su ogni clip in alto a sinistra la scritta dei filtri (es. "Attacco da R!"):
l'ffmpeg di Homebrew non ha drawtext, quindi la scritta è un PNG disegnato con Pillow e sovrapposto (overlay).
Prima (yt-dlp per ogni clip) ci volevano circa 6 s a clip, con ricodifica doppia.
"""
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
UVX = shutil.which("uvx") or str(Path.home() / ".local/bin/uvx")
NODE = shutil.which("node") or "/opt/homebrew/bin/node"
FADE = 0.3
WORKERS = 4


def youtube_stream_url(video_id):
    """Indirizzo del flusso video (≤ 720p) di un video YouTube; vale qualche ora."""
    cmd = [UVX, "yt-dlp", "-q", "--no-warnings", "-g", "-f", "bv*[height<=720][ext=mp4]/bv*[height<=720]/b[height<=720]/b",
           "--js-runtimes", f"node:{NODE}", f"https://www.youtube.com/watch?v={video_id}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    url = (r.stdout or "").strip().splitlines()
    if r.returncode or not url:
        raise RuntimeError(f"YouTube {video_id}: {(r.stderr or r.stdout).strip()[-300:]}")
    return url[0]


FONTS = ["/System/Library/Fonts/Avenir Next.ttc", "/System/Library/Fonts/HelveticaNeue.ttc",
         "/System/Library/Fonts/Helvetica.ttc"]


def caption_png(text, out, height=720, max_width=1232):
    """Riquadro scuro semitrasparente con la scritta, per un video alto `height`."""
    from PIL import Image, ImageDraw, ImageFont
    size = round(height * 0.062)
    font = None
    for f in FONTS:
        try:
            font = ImageFont.truetype(f, size, index=2 if f.endswith("Avenir Next.ttc") else 0)   # Avenir Next Demi Bold
            break
        except OSError:
            continue
    if font is None:
        return None
    while size > 18 and font.getlength(text) > max_width - size * 1.3:   # scritte lunghe: carattere più piccolo
        size -= 2
        font = font.font_variant(size=size)
    pad, bar = round(size * 0.5), round(size * 0.22)
    x0, y0, x1, y1 = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox((0, 0), text, font=font)
    img = Image.new("RGBA", (x1 - x0 + 2 * pad + bar, y1 - y0 + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=round(pad * 0.6), fill=(8, 10, 18, 225))
    d.rectangle((0, 0, bar, img.height - 1), fill=(220, 40, 50, 255))        # riga rossa come nelle grafiche
    d.text((bar + pad - x0 + 2, pad - y0 + 2), text, font=font, fill=(0, 0, 0, 200))   # ombra
    d.text((bar + pad - x0, pad - y0), text, font=font, fill=(255, 255, 255, 255))
    img.save(out)
    return out


def _segment(src, start, end, out, overlay=None):
    dur = max(0.5, end - start)
    fades = f"fade=t=in:st=0:d={FADE},fade=t=out:st={max(0, dur - FADE):.2f}:d={FADE}"
    base = "scale=-2:720,fps=30,format=yuv420p"
    if overlay:
        filt = ["-i", str(overlay), "-filter_complex", f"[0:v]{base}[v];[v][1:v]overlay=20:18,format=yuv420p,{fades}"]
    else:
        filt = ["-vf", f"{base},{fades}"]
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-t", f"{dur:.2f}", "-i", src, *filt,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-an", "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"ffmpeg: {r.stderr.strip()[-300:]}")
    return out


def build_montage(clips, progress=None, caption=""):
    """clip dello studio video (src kind 'yt' con id o 'file' con path), con start/end. Byte dell'mp4."""
    urls = {}
    for c in clips:
        if c["src"]["kind"] == "yt" and c["src"]["id"] not in urls:
            urls[c["src"]["id"]] = youtube_stream_url(c["src"]["id"])
    with tempfile.TemporaryDirectory() as tmp:
        parts = [Path(tmp) / f"seg_{i:03d}.mp4" for i in range(len(clips))]
        overlay = caption_png(caption.strip(), Path(tmp) / "scritta.png") if caption.strip() else None
        done = 0
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = [ex.submit(_segment, urls[c["src"]["id"]] if c["src"]["kind"] == "yt" else c["src"]["path"],
                              c["start"], c["end"], parts[i], overlay) for i, c in enumerate(clips)]
            for fu in as_completed(futs):
                fu.result()
                done += 1
                if progress:
                    progress(done, len(clips))
        lst = Path(tmp) / "list.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts))
        out = Path(tmp) / "montaggio.mp4"
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                        "-movflags", "+faststart", str(out)], check=True, capture_output=True)
        return out.read_bytes()
