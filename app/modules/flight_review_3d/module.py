import json
import os
import math
import base64
import urllib.request
import numpy as np
from concurrent.futures import ThreadPoolExecutor

from PyQt5.QtWidgets import QWidget, QVBoxLayout
from PyQt5.QtCore import QUrl, QThread, pyqtSignal
from PyQt5.QtWebEngineWidgets import QWebEngineView

from app.modules.base_module import BaseModule
from app.theme.style import COLORS

_ASSETS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'assets'))
_TMP_HTML   = os.path.join(_ASSETS_DIR, '_flight_review_3d.html')
_TILE_RES   = 64   # vertex resolution per terrain tile (64×64)

_Q_MODES = {'QSTABILIZE','QHOVER','QLOITER','QLAND','QRTL','QAUTOTUNE','QACRO','QBRAKE'}


def _is_q(mode):
    return mode.upper() in _Q_MODES or mode.upper().startswith('Q')


def _lat_lng_to_tile(lat, lng, zoom):
    n = 2 ** zoom
    tx = int((lng + 180) / 360 * n)
    lat_r = math.radians(lat)
    ty = int((1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * n)
    return tx, max(0, ty)


def _tile_bounds(tx, ty, zoom):
    """Return (west, south, east, north) degrees."""
    n = 2 ** zoom
    west  = tx / n * 360 - 180
    east  = (tx + 1) / n * 360 - 180
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ty / n))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (ty + 1) / n))))
    return west, south, east, north


def _fetch_b64(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Logalyzer/1.0'})
        data = urllib.request.urlopen(req, timeout=8).read()
        return 'data:image/png;base64,' + base64.b64encode(data).decode()
    except Exception:
        return None


def _mode_colors(time_us_arr, events):
    C_Q, C_FW = '#3FB950', '#58A6FF'
    colors = [C_FW] * len(time_us_arr)
    bps = sorted(
        [(e.time_us, C_Q if _is_q(e.detail.replace('Mode: ', '').strip()) else C_FW)
         for e in events if e.event_type == 'mode_change'],
        key=lambda x: x[0]
    )
    if not bps:
        return colors
    bi, cur = 0, C_FW
    for i, t in enumerate(time_us_arr):
        while bi < len(bps) and bps[bi][0] <= t:
            cur = bps[bi][1]; bi += 1
        colors[i] = cur
    return colors


def _hex_rgb(h):
    h = h.lstrip('#')
    return [int(h[0:2],16)/255, int(h[2:4],16)/255, int(h[4:6],16)/255]


class _HtmlBuilder(QThread):
    """Builds the HTML (including tile fetching) in a background thread."""
    ready = pyqtSignal()   # no payload — file is already written when this fires
    error = pyqtSignal(str)

    def __init__(self, log_data, make_html_fn, out_path):
        super().__init__()
        self._log  = log_data
        self._fn   = make_html_fn
        self._path = out_path

    def run(self):
        try:
            html = self._fn(self._log)
            with open(self._path, 'w', encoding='utf-8') as f:
                f.write(html)
            self.ready.emit()
        except Exception as exc:
            self.error.emit(str(exc))


class FlightReview3DModule(BaseModule):
    MODULE_ID      = 'flight_review_3d'
    DISPLAY_NAME   = '3D Flight Review'
    DESCRIPTION    = 'Replay flight over 3D terrain with aircraft model'
    ICON_CHAR      = '◈'
    REQUIRED_MESSAGES = ['POS', 'ATT']

    def __init__(self):
        self._view    = None
        self._builder = None   # keep reference so GC doesn't kill the thread

    def build_widget(self, parent=None):
        w = QWidget(parent)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        self._view = QWebEngineView()
        lay.addWidget(self._view)
        return w

    def load_data(self, log_data):
        # Show loading screen immediately so the UI isn't frozen
        self._view.setHtml(self._loading_html())

        # Cancel any previous build still running
        if self._builder and self._builder.isRunning():
            self._builder.terminate()
            self._builder.wait()

        self._builder = _HtmlBuilder(log_data, self._make_html, _TMP_HTML)
        self._builder.ready.connect(self._on_html_ready)
        self._builder.error.connect(self._on_build_error)
        self._builder.start()

    def _on_html_ready(self):
        self._view.load(QUrl.fromLocalFile(_TMP_HTML))

    def _on_build_error(self, msg):
        self._view.setHtml(self._empty_html(f'Build error: {msg}'))

    def _loading_html(self):
        return (
            f'<!DOCTYPE html><html><body style="background:{COLORS["bg_primary"]};'
            f'color:#58A6FF;display:flex;flex-direction:column;align-items:center;'
            f'justify-content:center;height:100vh;margin:0;font-family:monospace;gap:14px;">'
            f'<div style="font-size:22px">◈ 3D Flight Review</div>'
            f'<div style="font-size:13px;color:#8B949E">Fetching terrain &amp; satellite tiles…</div>'
            f'</body></html>'
        )

    def clear(self):
        if self._builder and self._builder.isRunning():
            self._builder.terminate()
            self._builder.wait()
        if self._view:
            self._view.setHtml(
                f'<body style="background:{COLORS["bg_primary"]};margin:0"></body>')

    # ─────────────────────────────────────────────────────────────────────────

    def _make_html(self, log_data):
        pos = log_data.pos
        att = log_data.att
        if 'Lat' not in pos or len(pos.get('Lat', [])) == 0:
            return self._empty_html('No POS data in this log.')

        # Downsample
        n = len(pos['Lat'])
        stride = max(1, n // 4000)
        idx   = np.arange(0, n, stride)
        lats  = pos['Lat'][idx].astype(float)
        lngs  = pos['Lng'][idx].astype(float)
        alts  = pos['RelHomeAlt'][idx].astype(float) if 'RelHomeAlt' in pos else np.zeros(len(idx))
        times = pos['TimeUS'][idx].astype(float)

        home_lat = float(log_data.home_lat) if log_data.home_lat != 0 else float(lats[0])
        home_lng = float(log_data.home_lng) if log_data.home_lng != 0 else float(lngs[0])

        # ENU (Three.js: x=East, y=Up, z=-North)
        R = 6_371_000.0
        east  = R * np.cos(np.radians(home_lat)) * np.radians(lngs - home_lng)
        north = R * np.radians(lats - home_lat)
        pts   = [[round(float(east[i]),2), round(float(alts[i]),2), round(-float(north[i]),2)]
                 for i in range(len(lats))]

        # ATT
        has_att = 'Roll' in att and len(att.get('Roll',[])) > 0
        if has_att:
            at = att['TimeUS'].astype(float)
            rolls_d  = np.interp(times, at, att['Roll'].astype(float)).tolist()
            pitches_d= np.interp(times, at, att['Pitch'].astype(float)).tolist()
            yaws_d   = np.interp(times, at, att['Yaw'].astype(float)).tolist()
        else:
            z = [0.0]*len(lats); rolls_d=z; pitches_d=z; yaws_d=z

        # Speed
        speeds = [0.0]*len(lats)
        arsp = log_data.arsp
        if 'Airspeed' in arsp and len(arsp.get('Airspeed',[])) > 0:
            mask = (arsp['U']==1) if 'U' in arsp else np.ones(len(arsp['Airspeed']),bool)
            at2 = arsp['TimeUS'][mask].astype(float)
            av  = arsp['Airspeed'][mask].astype(float)
            if len(at2): speeds = np.interp(times, at2, av).tolist()
        elif 'Spd' in log_data.gps and len(log_data.gps.get('Spd',[])) > 0:
            speeds = np.interp(times,
                log_data.gps['TimeUS'].astype(float),
                log_data.gps['Spd'].astype(float)).tolist()

        # Modes
        mode_labels = [''] * len(lats)
        sevs = sorted([e for e in log_data.events if e.event_type=='mode_change'],
                      key=lambda e: e.time_us)
        mi, cur = 0, 'UNKNOWN'
        for i, t in enumerate(times):
            while mi < len(sevs) and sevs[mi].time_us <= t:
                cur = sevs[mi].detail.replace('Mode: ','').strip(); mi+=1
            mode_labels[i] = cur

        # Colors
        colors_hex = _mode_colors(times, log_data.events)
        colors_rgb = [_hex_rgb(c) for c in colors_hex]

        # Times
        t0 = float(times[0])
        times_s = [round((float(t)-t0)/1_000_000, 3) for t in times]

        # Terrain tiles (fetched in Python → embedded as base64)
        zoom, sat_n, min_tx, min_ty, max_tx, max_ty, terr_b64, tex_subs_b64 = \
            self._fetch_tiles(lats, lngs, home_lat, home_lng)

        tiles_js = []
        for ty in range(min_ty, max_ty+1):
            for tx in range(min_tx, max_tx+1):
                w_b, s_b, e_b, n_b = _tile_bounds(tx, ty, zoom)
                def ll2xz(lat, lng):
                    e2 = R * math.cos(math.radians(home_lat)) * math.radians(lng - home_lng)
                    n2 = R * math.radians(lat - home_lat)
                    return [round(e2, 1), round(-n2, 1)]
                sw = ll2xz(s_b, w_b)   # [west_x,  south_z]
                ne = ll2xz(n_b, e_b)   # [east_x,  north_z]
                key = f'{tx}_{ty}'
                tiles_js.append({
                    'terr':     terr_b64.get(key),
                    'tex_subs': tex_subs_b64.get(key, []),
                    'tex_n':    sat_n,
                    'sw': sw,
                    'ne': ne,
                })

        data_js = f"""
const HOME_LAT={home_lat}, HOME_LNG={home_lng};
const N_PTS={len(pts)};
const TOTAL_SECS={round(times_s[-1],1) if times_s else 0};
const TILE_RES={_TILE_RES};
const TILES={json.dumps(tiles_js)};
const PTS={json.dumps(pts)};
const TIMES={json.dumps(times_s)};
const ROLLS={json.dumps([round(r,2) for r in rolls_d])};
const PITCHES={json.dumps([round(p,2) for p in pitches_d])};
const YAWS={json.dumps([round(y,2) for y in yaws_d])};
const SPEEDS={json.dumps([round(s,2) for s in speeds])};
const MODES={json.dumps(mode_labels)};
const PATH_COLORS={json.dumps([[round(c,3) for c in rgb] for rgb in colors_rgb])};
"""
        return _HTML_TEMPLATE.replace('/*DATA_JS*/', data_js)

    def _fetch_tiles(self, lats, lngs, home_lat, home_lng):
        # Find highest zoom where flight fits in ≤5×5 tiles (max zoom 15 = Terrarium tile limit)
        zoom = 13
        for z in range(15, 7, -1):
            txs = [_lat_lng_to_tile(la, lo, z)[0] for la, lo in zip(lats, lngs)]
            tys = [_lat_lng_to_tile(la, lo, z)[1] for la, lo in zip(lats, lngs)]
            if max(txs)-min(txs) <= 4 and max(tys)-min(tys) <= 4:
                zoom = z; break

        txs = [_lat_lng_to_tile(la, lo, zoom)[0] for la, lo in zip(lats, lngs)]
        tys = [_lat_lng_to_tile(la, lo, zoom)[1] for la, lo in zip(lats, lngs)]
        max_coord = 2 ** zoom - 1
        buf = 6  # extra tiles on each side
        min_tx = max(0,         min(txs) - buf)
        max_tx = min(max_coord, max(txs) + buf)
        min_ty = max(0,         min(tys) - buf)
        max_ty = min(max_coord, max(tys) + buf)

        # Satellite tiles fetched one zoom level higher than terrain (sat_n=2 → 2×2 sub-tiles
        # per terrain tile, composited in JS → 512×512 texture vs 256×256 at terrain zoom).
        # ESRI World Imagery is reliable up to zoom 19; Terrarium is capped at 15.
        sat_zoom = zoom + 1
        sat_n    = 2   # sub-tiles per axis (sat_n² total per terrain tile)

        # Build the full list of fetch jobs so we can fire them all in parallel
        jobs = []  # (kind, key, sub_idx, url)
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
            return kind, key, idx, _fetch_b64(url)

        with ThreadPoolExecutor(max_workers=24) as pool:
            for kind, key, idx, data in pool.map(_do, jobs):
                if kind == 't':
                    terr_b64[key] = data
                else:
                    tex_subs_b64[key][idx] = data

        return zoom, sat_n, min_tx, min_ty, max_tx, max_ty, terr_b64, tex_subs_b64

    def _empty_html(self, msg):
        return (f'<!DOCTYPE html><html><body style="background:{COLORS["bg_primary"]};'
                f'color:{COLORS["text_secondary"]};display:flex;align-items:center;'
                f'justify-content:center;height:100vh;margin:0;font-family:sans-serif;'
                f'font-size:16px;">{msg}</body></html>')


# ═════════════════════════════════════════════════════════════════════════════
_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0D1117;overflow:hidden}
#canvas{display:block;position:absolute;top:0;left:0}
#controls{
  position:absolute;bottom:0;left:0;right:0;height:48px;z-index:10;
  background:rgba(13,17,23,0.92);border-top:1px solid #30363D;
  display:flex;align-items:center;gap:5px;padding:0 8px;flex-wrap:nowrap;
}
.cb{background:#161B22;color:#C9D1D9;border:1px solid #30363D;
    border-radius:4px;padding:3px 9px;cursor:pointer;font-size:12px;white-space:nowrap}
.cb:hover{background:#21262D}
.cb.on{background:#1F6FEB;color:#fff;border-color:#1F6FEB}
#tslider{flex:1;min-width:80px;accent-color:#1F6FEB;cursor:pointer}
.sep{width:1px;background:#30363D;height:20px;flex-shrink:0}
#tlbl{color:#8B949E;font-size:11px;white-space:nowrap}
#stl-inp{display:none}
/* FPV HUD — full-screen transparent canvas overlay */
#hud{position:absolute;top:0;left:0;pointer-events:none;z-index:9;display:none}
</style>
</head>
<body>
<canvas id="canvas"></canvas>
<canvas id="hud"></canvas>
<div id="controls">
  <button class="cb" id="bpl" onclick="togglePlay()">&#9654;</button>
  <button class="cb" onclick="stopPlay()">&#9632;</button>
  <div class="sep"></div>
  <span style="color:#8B949E;font-size:11px">Spd:</span>
  <button class="cb sp on" data-v="1"   onclick="setSp(1)">1&times;</button>
  <button class="cb sp"    data-v="2"   onclick="setSp(2)">2&times;</button>
  <button class="cb sp"    data-v="5"   onclick="setSp(5)">5&times;</button>
  <button class="cb sp"    data-v="10"  onclick="setSp(10)">10&times;</button>
  <button class="cb sp"    data-v="25"  onclick="setSp(25)">25&times;</button>
  <button class="cb sp"    data-v="50"  onclick="setSp(50)">50&times;</button>
  <div class="sep"></div>
  <input type="range" id="tslider" min="0" max="1000" value="0" oninput="onSlide(this.value)">
  <span id="tlbl">0:00 / 0:00</span>
  <div class="sep"></div>
  <button class="cb cm on" id="bfr" onclick="setCam('free')">Free</button>
  <button class="cb cm"    id="bfo" onclick="setCam('follow')">Follow</button>
  <button class="cb cm"    id="bfp" onclick="setCam('fpv')">FPV</button>
  <div class="sep"></div>
  <button class="cb" onclick="document.getElementById('stl-inp').click()">Load STL</button>
  <input type="file" id="stl-inp" accept=".stl" onchange="loadSTL(this)">
  <div class="sep"></div>
  <span style="color:#8B949E;font-size:11px">Scale:</span>
  <select id="ac-scale" class="cb" onchange="setAcScale(+this.value)" style="padding:2px 4px">
    <option value="0.1">0.1&times;</option>
    <option value="0.5">0.5&times;</option>
    <option value="1" selected>1&times;</option>
    <option value="2">2&times;</option>
    <option value="5">5&times;</option>
    <option value="10">10&times;</option>
    <option value="25">25&times;</option>
    <option value="50">50&times;</option>
  </select>
</div>
<script src="three.min.js"></script>
<script src="STLLoader.js"></script>
<script>
/*DATA_JS*/

// ── Renderer / Scene / Camera ────────────────────────────────────────────
const canvas = document.getElementById('canvas');
function W(){ return window.innerWidth; }
function H(){ return window.innerHeight - 48; }

const renderer = new THREE.WebGLRenderer({canvas, antialias:true});
renderer.setPixelRatio(window.devicePixelRatio);
renderer.shadowMap.enabled = true;
renderer.setClearColor(0x1a2a3a);

const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(0x1a2a3a, 0.00008);

const camera = new THREE.PerspectiveCamera(60, W()/H(), 0.5, 80000);

// Lights
scene.add(new THREE.AmbientLight(0xffffff, 0.55));
const sun = new THREE.DirectionalLight(0xffffff, 0.9);
sun.position.set(300, 800, 200);
sun.castShadow = true;
scene.add(sun);

// Skybox-ish gradient background via a large sphere
(function(){
  const sg = new THREE.SphereGeometry(40000,8,8);
  const sm = new THREE.MeshBasicMaterial({color:0x1a2a3a,side:THREE.BackSide});
  scene.add(new THREE.Mesh(sg,sm));
})();

// ── Orbit state ───────────────────────────────────────────────────────────
let orbTheta=0.3, orbPhi=0.9, orbRadius=300;
const orbTarget = new THREE.Vector3();
let orbDrag=false, panDrag=false, lastX=0, lastY=0;

// Follow orbit state (relative to aircraft heading)
let flwTheta=Math.PI, flwPhi=0.8, flwRadius=120;
let flwDrag=false;

let camMode='free';

canvas.addEventListener('mousedown', e=>{
  if(e.button===0){ if(camMode==='free') orbDrag=true; else if(camMode==='follow') flwDrag=true; }
  else if(e.button===2 && camMode==='free') panDrag=true;
  lastX=e.clientX; lastY=e.clientY;
});
window.addEventListener('mouseup', ()=>{ orbDrag=panDrag=flwDrag=false; });
window.addEventListener('mousemove', e=>{
  const dx=e.clientX-lastX, dy=e.clientY-lastY;
  lastX=e.clientX; lastY=e.clientY;
  if(orbDrag){
    orbTheta -= dx*0.006;
    orbPhi = Math.max(0.05, Math.min(1.55, orbPhi - dy*0.006));
  } else if(panDrag){
    const r=new THREE.Vector3(), up=new THREE.Vector3(0,1,0);
    r.crossVectors(camera.getWorldDirection(new THREE.Vector3()),up).normalize();
    orbTarget.addScaledVector(r, -dx * orbRadius * 0.0012);
    orbTarget.y += dy * orbRadius * 0.0012;
  } else if(flwDrag){
    flwTheta += dx*0.006;
    flwPhi = Math.max(0.05, Math.min(1.55, flwPhi - dy*0.006));
  }
});
canvas.addEventListener('wheel', e=>{
  const dir = e.deltaY > 0 ? 1 : -1;
  if(camMode==='free'){
    // Step = 8% of current radius, clamped so it feels smooth at any distance
    const step = Math.max(2, orbRadius * 0.08);
    orbRadius = Math.max(5, orbRadius + dir * step);
  } else if(camMode==='follow'){
    const step = Math.max(2, flwRadius * 0.08);
    flwRadius = Math.max(10, flwRadius + dir * step);
  }
  e.preventDefault();
},{passive:false});
canvas.addEventListener('contextmenu', e=>e.preventDefault());

// ── Resize ───────────────────────────────────────────────────────────────
function onResize(){
  renderer.setSize(W(), H());
  camera.aspect = W()/H();
  camera.updateProjectionMatrix();
  const hc=document.getElementById('hud');
  hc.width=W(); hc.height=H();
}
window.addEventListener('resize', onResize);
onResize();

// ── Aircraft model ───────────────────────────────────────────────────────
let aircraft = buildDefaultAircraft();
scene.add(aircraft);

function buildDefaultAircraft(){
  // Nose faces -Z so that yaw=0 (North) points the aircraft forward in ENU
  const g=new THREE.Group();
  const M=c=>new THREE.MeshPhongMaterial({color:c});
  const fuse=new THREE.Mesh(new THREE.CylinderGeometry(0.4,0.3,10,8),M(0x8B949E));
  fuse.rotation.x=-Math.PI/2; g.add(fuse);  // -PI/2 so nose tip is at -Z
  const wing=new THREE.Mesh(new THREE.BoxGeometry(22,0.25,2.8),M(0xC9D1D9));
  wing.position.z=0.5; g.add(wing);
  const htail=new THREE.Mesh(new THREE.BoxGeometry(7,0.2,2),M(0xC9D1D9));
  htail.position.z=4.8; g.add(htail);
  const vstab=new THREE.Mesh(new THREE.BoxGeometry(0.2,2.8,2),M(0xC9D1D9));
  vstab.position.set(0,1.3,4.8); g.add(vstab);
  const nose=new THREE.Mesh(new THREE.ConeGeometry(0.4,2,8),M(0x6E7681));
  nose.rotation.x=Math.PI/2; nose.position.z=-6; g.add(nose);  // tip at -Z
  return g;
}

function loadSTL(inp){
  const file=inp.files[0]; if(!file) return;
  const reader=new FileReader();
  reader.onload=e=>{
    try{
      const loader=new THREE.STLLoader();
      const geom=loader.parse(e.target.result);
      geom.computeVertexNormals();
      geom.computeBoundingBox();
      const bb=geom.boundingBox;
      geom.translate(-(bb.max.x+bb.min.x)/2,-(bb.max.y+bb.min.y)/2,-(bb.max.z+bb.min.z)/2);
      const mx=Math.max(bb.max.x-bb.min.x,bb.max.y-bb.min.y,bb.max.z-bb.min.z);
      if(mx>0) geom.scale(1/mx,1/mx,1/mx);
      const mesh=new THREE.Mesh(geom,new THREE.MeshPhongMaterial({color:0xC9D1D9,side:THREE.DoubleSide}));
      scene.remove(aircraft);
      aircraft=mesh;
      aircraft.scale.setScalar(acScale);
      scene.add(aircraft);
    }catch(err){console.error('STL error',err);}
  };
  reader.readAsArrayBuffer(file);
  inp.value='';
}

// ── Flight path ───────────────────────────────────────────────────────────
(function(){
  const pos=new Float32Array(N_PTS*3);
  const col=new Float32Array(N_PTS*3);
  for(let i=0;i<N_PTS;i++){
    pos[i*3]=PTS[i][0]; pos[i*3+1]=PTS[i][1]; pos[i*3+2]=PTS[i][2];
    col[i*3]=PATH_COLORS[i][0]; col[i*3+1]=PATH_COLORS[i][1]; col[i*3+2]=PATH_COLORS[i][2];
  }
  const g=new THREE.BufferGeometry();
  g.setAttribute('position',new THREE.BufferAttribute(pos,3));
  g.setAttribute('color',new THREE.BufferAttribute(col,3));
  const m=new THREE.LineBasicMaterial({vertexColors:true});
  scene.add(new THREE.Line(g,m));
})();

// Trail line (drawn up to current playback position)
const trailPositions = new Float32Array(N_PTS*3);
const trailGeom = new THREE.BufferGeometry();
trailGeom.setAttribute('position', new THREE.BufferAttribute(trailPositions,3));
trailGeom.setDrawRange(0,0);
const trailLine = new THREE.Line(trailGeom, new THREE.LineBasicMaterial({color:0xffffff,opacity:0.7,transparent:true}));
scene.add(trailLine);

// ── Terrain tiles ─────────────────────────────────────────────────────────
let tilesLoaded = 0;
const totalTiles = TILES.length;

function decodeTerrain(img){
  const cv=document.createElement('canvas');
  cv.width=cv.height=256;
  const ctx=cv.getContext('2d');
  ctx.drawImage(img,0,0);
  const d=ctx.getImageData(0,0,256,256).data;
  const res=TILE_RES;
  const h=new Float32Array(res*res);
  for(let iy=0;iy<res;iy++){
    for(let ix=0;ix<res;ix++){
      const px=Math.min(255,Math.round(ix*255/(res-1)));
      const py=Math.min(255,Math.round(iy*255/(res-1)));
      const i=(py*256+px)*4;
      h[iy*res+ix]=d[i]*256+d[i+1]+d[i+2]/256-32768;
    }
  }
  return h;
}

function buildTileMesh(tile, heights, texImg, homeElev){
  const res=TILE_RES;
  const sw=tile.sw, ne=tile.ne;
  const pos=new Float32Array(res*res*3);
  const uvs=new Float32Array(res*res*2);
  const idx=[];
  for(let iy=0;iy<res;iy++){
    for(let ix=0;ix<res;ix++){
      const x=sw[0]+(ne[0]-sw[0])*ix/(res-1);
      const z=ne[1]+(sw[1]-ne[1])*iy/(res-1);
      const elev=heights[iy*res+ix]-homeElev;
      const i=iy*res+ix;
      pos[i*3]=x; pos[i*3+1]=elev; pos[i*3+2]=z;
      uvs[i*2]=ix/(res-1); uvs[i*2+1]=1-iy/(res-1);
    }
  }
  for(let iy=0;iy<res-1;iy++){
    for(let ix=0;ix<res-1;ix++){
      const a=iy*res+ix, b=a+1, c=(iy+1)*res+ix, d=c+1;
      idx.push(a,c,b, b,c,d);
    }
  }
  const g=new THREE.BufferGeometry();
  g.setAttribute('position',new THREE.BufferAttribute(pos,3));
  g.setAttribute('uv',new THREE.BufferAttribute(uvs,2));
  g.setIndex(idx);
  g.computeVertexNormals();

  let mat;
  if(texImg){
    const tex=new THREE.Texture(texImg);
    tex.needsUpdate=true;
    mat=new THREE.MeshLambertMaterial({map:tex});
  } else {
    mat=new THREE.MeshLambertMaterial({color:0x4a7c4e});
  }
  return new THREE.Mesh(g,mat);
}

// Compute home elevation: find tile that contains ENU origin (0,0)
function getHomeElev(){
  for(const tile of TILES){
    if(tile._h && tile.sw[0]<=0 && 0<=tile.ne[0] && tile.ne[1]<=0 && 0<=tile.sw[1]){
      const u=(0-tile.sw[0])/(tile.ne[0]-tile.sw[0]);
      const v=(0-tile.ne[1])/(tile.sw[1]-tile.ne[1]);
      const ix=Math.max(0,Math.min(TILE_RES-1,Math.round(u*(TILE_RES-1))));
      const iy=Math.max(0,Math.min(TILE_RES-1,Math.round(v*(TILE_RES-1))));
      return tile._h[iy*TILE_RES+ix];
    }
  }
  return 0;
}

// Composite sat_n×sat_n sub-tile images onto a single canvas, then call cb(canvas|null).
function compositeSubTiles(subs, n, cb){
  if(!subs||subs.length===0){ cb(null); return; }
  const cv=document.createElement('canvas');
  cv.width=cv.height=256*n;
  const ctx=cv.getContext('2d');
  let loaded=0;
  const total=n*n;
  subs.forEach(function(src,i){
    if(!src){ loaded++; if(loaded===total) cb(cv); return; }
    const img=new Image();
    img.onload=function(){
      const ix=i%n, iy=Math.floor(i/n);
      ctx.drawImage(img, ix*256, iy*256);
      loaded++; if(loaded===total) cb(cv);
    };
    img.onerror=function(){ loaded++; if(loaded===total) cb(cv); };
    img.src=src;
  });
}

function loadAllTiles(){
  if(totalTiles===0){ startRender(); return; }

  let done=0;
  function check(){ done++; if(done===totalTiles){ finishTiles(); startRender(); } }

  TILES.forEach(function(tile){
    tile._h=null; tile._texImg=null;
    if(!tile.terr){ check(); return; }

    const terrImg=new Image();
    terrImg.onload=function(){
      tile._h=decodeTerrain(terrImg);
      const subs=tile.tex_subs, n=tile.tex_n||1;
      if(subs&&subs.length>0){
        compositeSubTiles(subs, n, function(cv){ tile._texImg=cv; check(); });
      } else { check(); }
    };
    terrImg.onerror=function(){ check(); };
    terrImg.src=tile.terr;
  });
}

function finishTiles(){
  const homeElev=getHomeElev();
  TILES.forEach(tile=>{
    if(!tile._h) return;
    const mesh=buildTileMesh(tile, tile._h, tile._texImg, homeElev);
    mesh.receiveShadow=true;
    scene.add(mesh);
  });
}

// ── Playback ──────────────────────────────────────────────────────────────
let playing=false, playIdx=0, playSpeed=1, elapsed=0, lastT=null;

function animFrame(now){
  requestAnimationFrame(animFrame);
  if(playing && lastT!==null){
    elapsed += (now-lastT)/1000 * playSpeed;
    while(playIdx<N_PTS-1 && TIMES[playIdx+1]<=elapsed) playIdx++;
    if(playIdx>=N_PTS-1){ playing=false; updatePBtn(); }
  }
  lastT=now;

  const p=PTS[playIdx];
  aircraft.position.set(p[0],p[1],p[2]);
  // Intrinsic body-frame rotations: yaw (world-up) → pitch (body-right) → roll (body-nose)
  // ENU: x=East, y=Up, z=-North. Nose faces -Z, right wing faces +X.
  // Yaw 0=North means nose at -Z, so yaw rotates around world +Y by -yaw.
  const yawRad   = YAWS[playIdx]   * Math.PI / 180;
  const pitchRad = PITCHES[playIdx] * Math.PI / 180;
  const rollRad  = ROLLS[playIdx]   * Math.PI / 180;
  // Step 1: yaw — rotate around world Y (negative because yaw=0 → nose=-Z in right-hand coords)
  const qY = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,1,0), -yawRad);
  // Step 2: pitch — rotate around body right axis (world +X after yaw)
  const bodyRight = new THREE.Vector3(1,0,0).applyQuaternion(qY);
  const qP = new THREE.Quaternion().setFromAxisAngle(bodyRight, pitchRad);
  // Step 3: roll — rotate around body nose axis (world -Z after yaw+pitch)
  const bodyNose = new THREE.Vector3(0,0,-1).applyQuaternion(qY).applyQuaternion(qP);
  const qR = new THREE.Quaternion().setFromAxisAngle(bodyNose, rollRad);
  aircraft.quaternion.multiplyQuaternions(qR, new THREE.Quaternion().multiplyQuaternions(qP, qY));

  // Update trail
  for(let i=0;i<=playIdx;i++){
    trailPositions[i*3]=PTS[i][0]; trailPositions[i*3+1]=PTS[i][1]; trailPositions[i*3+2]=PTS[i][2];
  }
  trailGeom.attributes.position.needsUpdate=true;
  trailGeom.setDrawRange(0, playIdx+1);

  updateCamera();
  if(camMode==='fpv') updateHUD();
  updateTimeLabel();
  document.getElementById('tslider').value = Math.round(playIdx/(N_PTS-1)*1000);
  renderer.render(scene, camera);
}

// ── Camera ────────────────────────────────────────────────────────────────
function updateCamera(){
  if(camMode==='free'){
    const x=orbTarget.x+orbRadius*Math.sin(orbPhi)*Math.sin(orbTheta);
    const y=orbTarget.y+orbRadius*Math.cos(orbPhi);
    const z=orbTarget.z+orbRadius*Math.sin(orbPhi)*Math.cos(orbTheta);
    camera.up.set(0,1,0);  // lock world-up so camera never rolls
    camera.position.set(x,y,z);
    camera.lookAt(orbTarget);
  } else if(camMode==='follow'){
    const ac=new THREE.Vector3(PTS[playIdx][0],PTS[playIdx][1],PTS[playIdx][2]);
    const yr=YAWS[playIdx]*Math.PI/180;
    // Nose faces -Z, so to place camera behind aircraft: subtract yaw
    const th=flwTheta-yr;
    const x=ac.x+flwRadius*Math.sin(flwPhi)*Math.sin(th);
    const y=ac.y+flwRadius*Math.cos(flwPhi);
    const z=ac.z+flwRadius*Math.sin(flwPhi)*Math.cos(th);
    camera.position.set(x,y,z);
    camera.lookAt(ac);
  } else if(camMode==='fpv'){
    const p=PTS[playIdx];
    // Use same quaternion as aircraft so camera orientation is exact match
    const yawRad   = YAWS[playIdx]   * Math.PI / 180;
    const pitchRad = PITCHES[playIdx] * Math.PI / 180;
    const rollRad  = ROLLS[playIdx]   * Math.PI / 180;
    const qY = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,1,0), -yawRad);
    const bodyRight = new THREE.Vector3(1,0,0).applyQuaternion(qY);
    const qP = new THREE.Quaternion().setFromAxisAngle(bodyRight, pitchRad);
    const bodyNose = new THREE.Vector3(0,0,-1).applyQuaternion(qY).applyQuaternion(qP);
    const qR = new THREE.Quaternion().setFromAxisAngle(bodyNose, rollRad);
    const q = new THREE.Quaternion().multiplyQuaternions(qR, new THREE.Quaternion().multiplyQuaternions(qP, qY));
    // Forward = nose direction (-Z rotated by full quaternion)
    const fwd = new THREE.Vector3(0,0,-1).applyQuaternion(q);
    // Up = +Y rotated by full quaternion
    const up  = new THREE.Vector3(0,1,0).applyQuaternion(q);
    camera.position.set(p[0], p[1]+1, p[2]);
    camera.up.copy(up);
    camera.lookAt(p[0]+fwd.x*100, p[1]+1+fwd.y*100, p[2]+fwd.z*100);
  }
}

// ── HUD ───────────────────────────────────────────────────────────────────
// Full-screen fighter-jet HUD drawn on a single transparent canvas overlay.
function updateHUD(){
  const roll  = ROLLS[playIdx];
  const pitch = PITCHES[playIdx];
  const yaw   = ((YAWS[playIdx]%360)+360)%360;
  const spd   = SPEEDS[playIdx];
  const alt   = PTS[playIdx][1];
  const mode  = MODES[playIdx];
  const t     = TIMES[playIdx];

  const cv  = document.getElementById('hud');
  const CW  = cv.width, CH = cv.height;
  const ctx = cv.getContext('2d');
  ctx.clearRect(0,0,CW,CH);

  const G   = '#00FF41';
  const DIM = 'rgba(0,255,65,0.45)';
  const cx  = CW/2, cy = CH/2;

  ctx.shadowColor = G;
  ctx.shadowBlur  = 4;

  hudPitchLadder(ctx, cx, cy, roll, pitch, G, DIM);
  hudBoresight(ctx, cx, cy, G);
  hudHeading(ctx, cx, CW, yaw, G, DIM);
  hudTape(ctx, 14,     cy, 90, 340, spd, 5,  10, 'm/s', 'SPD', false, G, DIM);
  hudTape(ctx, CW-104, cy, 90, 340, alt, 10, 20, 'm',   'ALT', true,  G, DIM);
  hudStatus(ctx, cx, CH, t, mode, pitch, roll, G);
}

function hudHeading(ctx, cx, CW, yaw, G, DIM){
  const TW=300, TH=38, tx=cx-TW/2, ty=10;
  const pxPd=TW/60; // 60° visible
  const cards={0:'N',45:'NE',90:'E',135:'SE',180:'S',225:'SW',270:'W',315:'NW'};
  ctx.fillStyle='rgba(0,0,0,0.82)'; ctx.fillRect(tx,ty,TW,TH);
  ctx.strokeStyle=G; ctx.lineWidth=1.5;
  ctx.strokeRect(tx,ty,TW,TH);
  ctx.save();
  ctx.beginPath(); ctx.rect(tx,ty,TW,TH); ctx.clip();
  const startH = Math.floor((yaw-35)/5)*5;
  for(let h=startH; h<=yaw+35; h+=5){
    const hdg=((h%360)+360)%360;
    const px=cx+(h-yaw)*pxPd;
    if(px<tx+2||px>tx+TW-2) continue;
    const isCard=cards[hdg]!==undefined;
    const isMaj=hdg%10===0;
    const tkH=isCard?TH-6:isMaj?TH*0.55:TH*0.3;
    ctx.lineWidth=isCard?2:1;
    ctx.strokeStyle=G;
    ctx.beginPath(); ctx.moveTo(px,ty+TH); ctx.lineTo(px,ty+TH-tkH); ctx.stroke();
    if(isMaj){
      ctx.fillStyle=G;
      ctx.font=isCard?'bold 11px monospace':'10px monospace';
      ctx.textAlign='center';
      ctx.fillText(cards[hdg]||String(hdg), px, ty+TH-tkH-3);
    }
  }
  ctx.restore();
  // Current value box (centre)
  ctx.fillStyle='#000'; ctx.fillRect(cx-22,ty,44,TH);
  ctx.strokeStyle=G; ctx.lineWidth=2; ctx.strokeRect(cx-22,ty,44,TH);
  ctx.fillStyle=G; ctx.font='bold 14px monospace'; ctx.textAlign='center';
  ctx.shadowBlur=0;
  ctx.fillText(String(Math.round(yaw)).padStart(3,'0'), cx, ty+TH-8);
  ctx.shadowBlur=4;
  // Down-pointer
  ctx.fillStyle=G;
  ctx.beginPath(); ctx.moveTo(cx,ty+TH+8); ctx.lineTo(cx-6,ty+TH); ctx.lineTo(cx+6,ty+TH); ctx.closePath(); ctx.fill();
}

function hudTape(ctx, tx, cy, TW, TH, val, minStep, labStep, unit, label, flipSide, G, DIM){
  const ty=cy-TH/2;
  ctx.strokeStyle=G; ctx.lineWidth=1.5;
  ctx.strokeRect(tx,ty,TW,TH);
  const pxU=TH/80; // 80 units visible range
  ctx.save();
  ctx.beginPath(); ctx.rect(tx,ty,TW,TH); ctx.clip();
  ctx.fillStyle=G; ctx.strokeStyle=G;
  const range=Math.ceil(TH/pxU/minStep+2)*minStep;
  for(let v=Math.floor((val-range)/minStep)*minStep; v<=val+range; v+=minStep){
    const py=cy-(v-val)*pxU;
    if(py<ty||py>ty+TH) continue;
    const isMaj=v%labStep===0;
    const tl=isMaj?14:8;
    ctx.lineWidth=isMaj?1.5:0.8;
    ctx.strokeStyle=G;
    ctx.beginPath();
    if(flipSide){ ctx.moveTo(tx,py); ctx.lineTo(tx+tl,py); }
    else         { ctx.moveTo(tx+TW,py); ctx.lineTo(tx+TW-tl,py); }
    ctx.stroke();
    if(isMaj){
      ctx.font='13px monospace';
      ctx.textAlign=flipSide?'left':'right';
      ctx.fillText(String(Math.round(v)), flipSide?tx+tl+4:tx+TW-tl-4, py+5);
    }
  }
  ctx.restore();
  // Current value highlight box
  ctx.fillStyle='rgba(0,0,0,0.75)'; ctx.fillRect(tx,cy-16,TW,32);
  ctx.strokeStyle=G; ctx.lineWidth=2; ctx.strokeRect(tx,cy-16,TW,32);
  ctx.fillStyle=G; ctx.font='bold 18px monospace'; ctx.textAlign='center';
  ctx.fillText(String(Math.round(val)), tx+TW/2, cy+7);
  // Side pointer
  ctx.fillStyle=G;
  ctx.beginPath();
  if(flipSide){ ctx.moveTo(tx-11,cy); ctx.lineTo(tx,cy-9); ctx.lineTo(tx,cy+9); }
  else         { ctx.moveTo(tx+TW+11,cy); ctx.lineTo(tx+TW,cy-9); ctx.lineTo(tx+TW,cy+9); }
  ctx.closePath(); ctx.fill();
  // Labels
  ctx.fillStyle=G; ctx.font='bold 13px monospace'; ctx.textAlign='center';
  ctx.fillText(label, tx+TW/2, ty+14);
  ctx.font='11px monospace'; ctx.fillText(unit, tx+TW/2, ty+TH-5);
}

function hudPitchLadder(ctx, cx, cy, roll, pitch, G, DIM){
  const clipW=520, clipH=340, pxDeg=12;
  const rollRad=roll*Math.PI/180;

  // ── Pitch ladder (rotates + translates with attitude) ──────────────────
  ctx.save();
  ctx.beginPath(); ctx.rect(cx-clipW/2, cy-clipH/2, clipW, clipH); ctx.clip();
  ctx.translate(cx, cy);
  ctx.rotate(-rollRad);
  const pitchOff=pitch*pxDeg;

  // Horizon line (0°) — split in center
  ctx.strokeStyle=G; ctx.lineWidth=2.5;
  ctx.beginPath(); ctx.moveTo(-clipW, pitchOff); ctx.lineTo(-35, pitchOff); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(35, pitchOff); ctx.lineTo(clipW, pitchOff); ctx.stroke();

  // Pitch ladder lines ±5° to ±60°
  for(let d=-60; d<=60; d+=5){
    if(d===0) continue;
    const py=pitchOff-d*pxDeg;
    const isMaj=d%10===0;
    const lineLen=isMaj?90:52;
    const gap=34;
    ctx.lineWidth=isMaj?1.8:1;
    if(d<0){ ctx.setLineDash([6,4]); ctx.strokeStyle=DIM; }
    else    { ctx.setLineDash([]);   ctx.strokeStyle=isMaj?G:DIM; }
    ctx.beginPath(); ctx.moveTo(-lineLen,py); ctx.lineTo(-gap,py); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(gap,py);      ctx.lineTo(lineLen,py); ctx.stroke();
    ctx.setLineDash([]);
    if(isMaj){
      // End ticks pointing toward horizon
      const tk=d>0?8:-8;
      ctx.strokeStyle=G; ctx.lineWidth=1.5;
      ctx.beginPath(); ctx.moveTo(-lineLen,py); ctx.lineTo(-lineLen,py+tk); ctx.stroke();
      ctx.beginPath(); ctx.moveTo( lineLen,py); ctx.lineTo( lineLen,py+tk); ctx.stroke();
      // Degree labels
      ctx.fillStyle=G; ctx.font='11px monospace';
      ctx.textAlign='right'; ctx.fillText(String(Math.abs(d)), -lineLen-6, py+4);
      ctx.textAlign='left';  ctx.fillText(String(Math.abs(d)),  lineLen+6, py+4);
    }
  }
  ctx.restore();

  // ── Roll arc (fixed position, pointer rotates) ─────────────────────────
  const arcR=170;
  ctx.save();
  ctx.translate(cx, cy);
  ctx.strokeStyle=G; ctx.lineWidth=1.5;
  // Arc from -72° to +72° (measured from top)
  ctx.beginPath();
  ctx.arc(0, 0, arcR, -Math.PI*0.7, -Math.PI*0.3);
  ctx.stroke();
  // Fixed tick marks
  for(const deg of [-60,-45,-30,-20,-10,0,10,20,30,45,60]){
    const ar=(deg-90)*Math.PI/180;
    const tl=deg===0?16:deg%30===0?12:7;
    ctx.lineWidth=deg===0?2.5:1;
    ctx.beginPath();
    ctx.moveTo(Math.cos(ar)*arcR,         Math.sin(ar)*arcR);
    ctx.lineTo(Math.cos(ar)*(arcR-tl),    Math.sin(ar)*(arcR-tl));
    ctx.stroke();
  }
  // Roll pointer (rotates)
  ctx.rotate(-rollRad);
  ctx.fillStyle=G;
  ctx.beginPath();
  ctx.moveTo(0, -(arcR+2)); ctx.lineTo(-6, -(arcR+16)); ctx.lineTo(6, -(arcR+16));
  ctx.closePath(); ctx.fill();
  ctx.restore();
}

function hudBoresight(ctx, cx, cy, G){
  ctx.save();
  ctx.strokeStyle=G; ctx.lineWidth=2.5;
  ctx.shadowBlur=8;
  // F-16 style waterline — circle with three extending lines
  ctx.beginPath(); ctx.arc(cx, cy, 13, 0, Math.PI*2); ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(cx-40,cy); ctx.lineTo(cx-14,cy); // left wing
  ctx.moveTo(cx+14,cy); ctx.lineTo(cx+40,cy); // right wing
  ctx.moveTo(cx,   cy-40); ctx.lineTo(cx,cy-14); // top bar
  ctx.stroke();
  // Centre dot
  ctx.beginPath(); ctx.arc(cx,cy,2.5,0,Math.PI*2);
  ctx.fillStyle=G; ctx.fill();
  ctx.restore();
}

function hudStatus(ctx, cx, CH, t, mode, pitch, roll, G){
  ctx.shadowBlur=6;
  ctx.fillStyle=G; ctx.font='bold 12px monospace'; ctx.textAlign='center';
  const ps=pitch>=0?'+':'';
  const rs=roll>=0?'+':'';
  ctx.fillText(
    fmtT(t)+'  |  '+mode+'  |  P:'+ps+pitch.toFixed(1)+'\xb0  R:'+rs+roll.toFixed(1)+'\xb0',
    cx, CH-12
  );
}

// ── Controls ──────────────────────────────────────────────────────────────
function togglePlay(){
  if(playIdx>=N_PTS-1){ playIdx=0; elapsed=0; lastT=null; trailGeom.setDrawRange(0,0); }
  playing=!playing; updatePBtn();
}
function stopPlay(){
  playing=false; playIdx=0; elapsed=0; lastT=null;
  trailGeom.setDrawRange(0,0);
  updatePBtn();
}
function updatePBtn(){ document.getElementById('bpl').innerHTML=playing?'&#9646;&#9646;':'&#9654;'; }
function setSp(s){
  playSpeed=s;
  document.querySelectorAll('.sp').forEach(b=>b.classList.toggle('on',+b.dataset.v===s));
}
function onSlide(v){
  playIdx=Math.round(v/1000*(N_PTS-1));
  elapsed=TIMES[playIdx]; lastT=null;
}
let acScale=1;
function setAcScale(v){
  acScale=v;
  aircraft.scale.setScalar(v);
}

function setCam(m){
  camMode=m;
  if(m==='free'){ orbTarget.set(PTS[playIdx][0],PTS[playIdx][1],PTS[playIdx][2]); }
  document.getElementById('hud').style.display=m==='fpv'?'block':'none';
  aircraft.visible=m!=='fpv';
  document.getElementById('bfr').classList.toggle('on',m==='free');
  document.getElementById('bfo').classList.toggle('on',m==='follow');
  document.getElementById('bfp').classList.toggle('on',m==='fpv');
}
function updateTimeLabel(){
  document.getElementById('tlbl').textContent=fmtT(TIMES[playIdx])+' / '+fmtT(TOTAL_SECS);
}
function fmtT(s){ const m=Math.floor(s/60); return m+':'+(Math.floor(s%60)+'').padStart(2,'0'); }

// ── Init orbit target to mid-path ─────────────────────────────────────────
const midP = PTS[Math.floor(N_PTS/2)];
orbTarget.set(midP[0], midP[1], midP[2]);
orbRadius = 300;
orbPhi = 0.7;

// ── Start ──────────────────────────────────────────────────────────────────
function startRender(){ requestAnimationFrame(animFrame); }
loadAllTiles();
</script>
</body>
</html>
"""
