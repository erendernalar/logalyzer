"""Shared tile-fetching infrastructure for all 3-D HTML views.

Provides:
  - lat_lng_to_tile / tile_bounds / fetch_b64   — low-level geo/HTTP helpers
  - fetch_tiles(...)                             — parallel terrain+satellite fetch
  - make_loading_html(title)                    — progress-bar loading screen
  - HtmlBuilder                                 — QThread that builds and writes HTML
"""
import math
import base64
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from PyQt5.QtCore import QThread, pyqtSignal

from app.theme.style import COLORS


# ── Geo helpers ───────────────────────────────────────────────────────────────

def lat_lng_to_tile(lat, lng, zoom):
    n = 2 ** zoom
    tx = int((lng + 180) / 360 * n)
    lat_r = math.radians(lat)
    ty = int((1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * n)
    return tx, max(0, ty)


def tile_bounds(tx, ty, zoom):
    """Return (west, south, east, north) in degrees."""
    n = 2 ** zoom
    west  = tx / n * 360 - 180
    east  = (tx + 1) / n * 360 - 180
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ty / n))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (ty + 1) / n))))
    return west, south, east, north


def fetch_b64(url):
    try:
        req  = urllib.request.Request(url, headers={'User-Agent': 'Logalyzer/1.0'})
        data = urllib.request.urlopen(req, timeout=8).read()
        return 'data:image/png;base64,' + base64.b64encode(data).decode()
    except Exception:
        return None


# ── Tile fetcher ──────────────────────────────────────────────────────────────

def fetch_tiles(lats, lngs, home_lat, home_lng, progress_cb=None):
    """Fetch terrain (Terrarium) and satellite (ESRI) tiles for the given track.

    progress_cb(done: int, total: int) is called after each tile completes.

    Returns: (zoom, sat_n, min_tx, min_ty, max_tx, max_ty, terr_b64, tex_subs_b64)
    """
    zoom = 13
    for z in range(15, 7, -1):
        txs = [lat_lng_to_tile(la, lo, z)[0] for la, lo in zip(lats, lngs)]
        tys = [lat_lng_to_tile(la, lo, z)[1] for la, lo in zip(lats, lngs)]
        if max(txs) - min(txs) <= 4 and max(tys) - min(tys) <= 4:
            zoom = z; break

    txs = [lat_lng_to_tile(la, lo, zoom)[0] for la, lo in zip(lats, lngs)]
    tys = [lat_lng_to_tile(la, lo, zoom)[1] for la, lo in zip(lats, lngs)]
    max_coord = 2 ** zoom - 1
    buf = 6
    min_tx = max(0,         min(txs) - buf)
    max_tx = min(max_coord, max(txs) + buf)
    min_ty = max(0,         min(tys) - buf)
    max_ty = min(max_coord, max(tys) + buf)

    sat_zoom = zoom + 1
    sat_n    = 2   # 2×2 sub-tiles per terrain tile → 512×512 sat texture

    jobs = []
    for ty in range(min_ty, max_ty + 1):
        for tx in range(min_tx, max_tx + 1):
            key = f'{tx}_{ty}'
            jobs.append(('t', key, 0,
                f'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{zoom}/{tx}/{ty}.png'))
            idx = 0
            for sy in range(ty * sat_n, ty * sat_n + sat_n):
                for sx in range(tx * sat_n, tx * sat_n + sat_n):
                    jobs.append(('s', key, idx,
                        f'https://server.arcgisonline.com/ArcGIS/rest/services/'
                        f'World_Imagery/MapServer/tile/{sat_zoom}/{sy}/{sx}'))
                    idx += 1

    terr_b64     = {}
    tex_subs_b64 = {f'{tx}_{ty}': [None] * (sat_n * sat_n)
                    for ty in range(min_ty, max_ty + 1)
                    for tx in range(min_tx, max_tx + 1)}

    def _do(job):
        kind, key, idx, url = job
        return kind, key, idx, fetch_b64(url)

    total = len(jobs)
    done  = 0
    with ThreadPoolExecutor(max_workers=24) as pool:
        futures = {pool.submit(_do, job): job for job in jobs}
        for fut in as_completed(futures):
            kind, key, idx, data = fut.result()
            if kind == 't':
                terr_b64[key] = data
            else:
                tex_subs_b64[key][idx] = data
            done += 1
            if progress_cb:
                progress_cb(done, total)

    return zoom, sat_n, min_tx, min_ty, max_tx, max_ty, terr_b64, tex_subs_b64


# ── Loading screen HTML ───────────────────────────────────────────────────────

def make_loading_html(title, color='#58A6FF'):
    return (
        f'<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
        f'<body style="background:{COLORS["bg_primary"]};display:flex;flex-direction:column;'
        f'align-items:center;justify-content:center;height:100vh;margin:0;font-family:monospace;gap:16px;">'
        f'<div style="font-size:22px;color:{color}">{title}</div>'
        f'<div style="font-size:13px;color:#8B949E">Fetching terrain &amp; satellite tiles…</div>'
        f'<div style="width:320px;background:#21262D;border-radius:6px;height:10px;overflow:hidden;">'
        f'<div id="bar" style="width:0%;height:100%;background:#1F6FEB;border-radius:6px;transition:width 0.15s;"></div>'
        f'</div>'
        f'<div id="count" style="color:#8B949E;font-size:12px;">Connecting…</div>'
        f'<script>'
        f'function updateProgress(done,total){{'
        f'  var pct=total>0?Math.round(done/total*100):0;'
        f'  document.getElementById("bar").style.width=pct+"%";'
        f'  document.getElementById("count").textContent=done+" / "+total+"  ("+pct+"%)";'
        f'}}'
        f'</script>'
        f'</body></html>'
    )


# ── Background thread ─────────────────────────────────────────────────────────

class HtmlBuilder(QThread):
    """Builds a 3-D HTML view (including tile fetching) in a background thread."""
    ready    = pyqtSignal()
    error    = pyqtSignal(str)
    progress = pyqtSignal(int, int)   # (done, total)

    def __init__(self, log_data, make_html_fn, out_path):
        super().__init__()
        self._log  = log_data
        self._fn   = make_html_fn
        self._path = out_path

    def run(self):
        try:
            html = self._fn(self._log, self.progress.emit)
            with open(self._path, 'w', encoding='utf-8') as f:
                f.write(html)
            self.ready.emit()
        except Exception as exc:
            self.error.emit(str(exc))
