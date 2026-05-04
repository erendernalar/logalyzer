import json
import os
import numpy as np

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QSizePolicy,
    QAbstractItemView, QSplitter,
)
from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QColor, QPainter, QFont
from PyQt5.QtWebEngineWidgets import QWebEngineView

from app.modules.base_module import BaseModule
from app.theme.style import COLORS

_ASSETS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'assets')
_ASSETS_URL = QUrl.fromLocalFile(os.path.abspath(_ASSETS_DIR) + '/')

_Q_MODES = {
    'QSTABILIZE', 'QHOVER', 'QLOITER', 'QLAND', 'QRTL',
    'QAUTOTUNE', 'QACRO', 'QBRAKE',
}

_FWD  = 'Forward'
_BACK = 'Back'

# Colors for map segments (must be CSS hex strings)
_C_Q   = '#3FB950'   # green  — Q-mode
_C_FW  = '#58A6FF'   # blue   — FW-mode
_C_FWD = '#F85149'   # red    — forward transition phase
_C_BCK = '#FF9F43'   # orange — back transition phase


def _is_q(mode: str) -> bool:
    return mode.upper() in _Q_MODES or mode.upper().startswith('Q')


# ── Timeline bar ─────────────────────────────────────────────────────────────

class TimelineWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(54)
        self.setMaximumHeight(54)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._segments = []
        self._total_s  = 1.0

    def set_data(self, segments, total_s):
        self._segments = segments
        self._total_s  = max(total_s, 1.0)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        bar_y, bar_h = 18, 18
        p.fillRect(0, 0, W, H, QColor(COLORS['bg_primary']))
        if not self._segments:
            p.end(); return

        def t2x(t): return int(t / self._total_s * W)

        for start_s, end_s, seg_type, _ in self._segments:
            x1, x2 = t2x(start_s), t2x(end_s)
            color_map = {
                'q': _C_Q, 'fw': _C_FW, 'fwd_tr': _C_FWD, 'back_tr': _C_BCK
            }
            c = QColor(color_map.get(seg_type, COLORS['border']))
            c.setAlpha(200)
            p.fillRect(x1, bar_y, max(x2 - x1, 1), bar_h, c)

        seen = set()
        for start_s, end_s, _, _ in self._segments:
            for t in (start_s, end_s):
                if t in seen: continue
                seen.add(t)
                p.setPen(QColor(COLORS['border']))
                p.drawLine(t2x(t), bar_y, t2x(t), bar_y + bar_h)

        font = QFont('Segoe UI', 8)
        p.setFont(font)
        p.setPen(QColor(COLORS['text_disabled']))
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            t = frac * self._total_s
            x = t2x(t)
            mins, secs = int(t // 60), int(t % 60)
            p.drawText(x + 2, bar_y + bar_h + 13, f'{mins}:{secs:02d}')

        p.setFont(QFont('Segoe UI', 8))
        for label, color in [('Q-mode', _C_Q), ('FW-mode', _C_FW),
                              ('Fwd Tr.', _C_FWD), ('Back Tr.', _C_BCK)]:
            pass  # legend below is handled by a QLabel

        p.end()


# ── Leaflet map HTML ─────────────────────────────────────────────────────────

def _make_transition_map_html(segments_geo, transitions, home_lat, home_lng):
    """
    segments_geo: list of {points:[[lat,lng],...], color:str, label:str}
    transitions:  list of {lat, lng, direction, duration_s, num}
    """
    segs_json = json.dumps([
        {'pts': s['points'], 'color': s['color'], 'label': s['label']}
        for s in segments_geo
    ])
    markers_json = json.dumps([
        {
            'lat': t['lat'], 'lng': t['lng'],
            'dir': t['direction'],
            'dur': round(t['duration_s'], 1),
            'num': t['num'],
            'color': _C_FWD if t['direction'] == _FWD else _C_BCK,
        }
        for t in transitions if t['lat'] is not None
    ])

    c_border = COLORS['border']
    c_bg     = COLORS['bg_secondary']
    c_text   = COLORS['text_primary']
    c_dis    = COLORS['text_disabled']

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<link rel="stylesheet" href="leaflet.css"/>
<script src="leaflet.js"></script>
<style>
  * {{ box-sizing: border-box; }}
  html, body {{ margin:0; padding:0; width:100%; height:100%; background:{COLORS['bg_primary']}; overflow:hidden; }}
  #map {{ position:absolute; top:0; left:0; right:0; bottom:0; }}
  .leaflet-container {{ background:#1a2028; font-family:'Segoe UI',sans-serif; }}
  .tr-marker {{
    width:22px; height:22px; border-radius:50%;
    display:flex; align-items:center; justify-content:center;
    font-size:11px; font-weight:bold; color:#fff;
    border:2px solid rgba(255,255,255,0.7);
    box-shadow:0 2px 6px rgba(0,0,0,0.6);
  }}
  .popup-box {{
    background:{c_bg}; color:{c_text};
    border:1px solid {c_border}; border-radius:8px;
    padding:8px 12px; font-size:12px; line-height:1.7;
    min-width:140px;
  }}
  /* layer switcher */
  .leaflet-control-layers {{
    background:{c_bg} !important; border:1px solid {c_border} !important;
    border-radius:8px !important; box-shadow:0 4px 12px rgba(0,0,0,.6) !important;
  }}
  .leaflet-control-layers-toggle {{
    background-color:{c_bg} !important;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='%238B949E' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolygon points='12 2 2 7 12 12 22 7 12 2'/%3E%3Cpolyline points='2 17 12 22 22 17'/%3E%3Cpolyline points='2 12 12 17 22 12'/%3E%3C/svg%3E") !important;
    background-repeat:no-repeat !important; background-position:center !important;
    background-size:20px !important; border:1px solid {c_border} !important;
    border-radius:8px !important; width:36px !important; height:36px !important;
  }}
  .leaflet-control-layers-toggle:hover {{
    background-color:{COLORS['bg_hover']} !important; border-color:{COLORS['accent']} !important;
  }}
  .leaflet-control-layers-expanded {{ padding:10px 14px !important; font-size:12px; min-width:170px; color:{c_text}; }}
  .leaflet-control-layers-expanded::before {{
    content:'Map Type'; display:block; font-size:10px; font-weight:bold;
    letter-spacing:.08em; color:{c_dis}; text-transform:uppercase; margin-bottom:8px;
  }}
  .leaflet-control-layers label {{ display:flex; align-items:center; gap:8px; padding:5px 2px; cursor:pointer; color:{c_text}; }}
  .leaflet-control-layers label:hover {{ color:{COLORS['accent']}; }}
  .leaflet-control-layers-separator {{ border-top:1px solid {c_border}; margin:4px 0; }}
  .leaflet-control-layers-expanded .leaflet-control-layers-toggle {{ display:none; }}
</style>
</head>
<body>
<div id="map"></div>
<script>
function setMapHeight() {{
  document.getElementById('map').style.height = (window.innerHeight || 600) + 'px';
}}
setMapHeight();
window.addEventListener('resize', setMapHeight);

var map = L.map('map', {{ zoomControl:true, attributionControl:false, center:[{home_lat},{home_lng}], zoom:13 }});

var _orig = map._handleDOMEvent.bind(map);
map._handleDOMEvent = function(e) {{ if (e && e.type==='contextmenu') return; _orig(e); }};

var baseLayers = {{
  'Dark':             L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{maxZoom:19,subdomains:'abcd'}}),
  'Street':           L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{maxZoom:19}}),
  'Google Satellite': L.tileLayer('https://mt{{s}}.google.com/vt/lyrs=s&x={{x}}&y={{y}}&z={{z}}', {{maxZoom:20,subdomains:'0123'}}),
  'Google Hybrid':    L.tileLayer('https://mt{{s}}.google.com/vt/lyrs=y&x={{x}}&y={{y}}&z={{z}}', {{maxZoom:20,subdomains:'0123'}}),
  'Google Street':    L.tileLayer('https://mt{{s}}.google.com/vt/lyrs=m&x={{x}}&y={{y}}&z={{z}}', {{maxZoom:20,subdomains:'0123'}}),
}};
baseLayers['Dark'].addTo(map);
L.control.layers(baseLayers, {{}}, {{position:'topright', collapsed:true}}).addTo(map);

var segs    = {segs_json};
var markers = {markers_json};
var allLatLngs = [];

segs.forEach(function(s) {{
  if (s.pts.length < 2) return;
  var line = L.polyline(s.pts, {{color:s.color, weight:3, opacity:0.9}}).addTo(map);
  s.pts.forEach(function(p) {{ allLatLngs.push(p); }});
  if (s.label) line.bindTooltip(s.label, {{sticky:true}});
}});

markers.forEach(function(m) {{
  var icon = L.divIcon({{
    html: '<div class="tr-marker" style="background:' + m.color + '">' + m.num + '</div>',
    iconSize:[22,22], iconAnchor:[11,11], className:'',
  }});
  var mk = L.marker([m.lat, m.lng], {{icon:icon}}).addTo(map);
  mk.bindPopup(
    '<div class="popup-box"><b>#' + m.num + ' ' + m.dir + '</b><br>' +
    'Duration: ' + m.dur + ' s</div>'
  );
}});

if (allLatLngs.length > 1) {{
  map.fitBounds(L.latLngBounds(allLatLngs).pad(0.15));
}}
setTimeout(function() {{ setMapHeight(); map.invalidateSize(); }}, 250);
setTimeout(function() {{ setMapHeight(); map.invalidateSize(); }}, 800);
</script>
</body>
</html>"""


# ── Module ───────────────────────────────────────────────────────────────────

class TransitionAnalyzerModule(BaseModule):
    MODULE_ID    = 'transition_analyzer'
    DISPLAY_NAME = 'Transition Analyzer'
    DESCRIPTION  = 'Shows VTOL transition events and their durations'
    ICON_CHAR    = '⇅'
    REQUIRED_MESSAGES = ['MODE']

    def __init__(self):
        self._widget   = None
        self._log_data = None

    # ── BaseModule interface ─────────────────────────────────

    def build_widget(self, parent=None) -> QWidget:
        self._widget = QWidget(parent)
        self._widget.setStyleSheet(f'background-color: {COLORS["bg_primary"]};')
        layout = QVBoxLayout(self._widget)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Title
        title = QLabel('Transition Analyzer')
        title.setStyleSheet(
            f'font-size:18px; font-weight:bold; color:{COLORS["text_primary"]}; background:transparent;'
        )
        layout.addWidget(title)

        # Summary stats
        stats_box = QGroupBox('Summary')
        stats_box.setStyleSheet(
            f'QGroupBox {{ background-color:{COLORS["bg_secondary"]}; border:1px solid {COLORS["border"]};'
            f' border-radius:8px; margin-top:10px; padding-top:8px;'
            f' color:{COLORS["text_secondary"]}; font-size:11px; }}'
        )
        stats_row = QHBoxLayout(stats_box)
        stats_row.setContentsMargins(16, 8, 16, 12)
        stats_row.setSpacing(32)

        self._stat_labels = {}
        for key, label in [
            ('total',    'Total Transitions'),
            ('fwd_avg',  'Avg Fwd Duration'),
            ('back_avg', 'Avg Back Duration'),
            ('longest',  'Longest Transition'),
        ]:
            col = QVBoxLayout(); col.setSpacing(2)
            k = QLabel(label.upper())
            k.setStyleSheet(f'color:{COLORS["text_secondary"]}; font-size:10px; font-weight:bold; background:transparent;')
            v = QLabel('—')
            v.setStyleSheet(f'color:{COLORS["text_primary"]}; font-size:16px; font-weight:bold; background:transparent;')
            col.addWidget(k); col.addWidget(v)
            stats_row.addLayout(col)
            self._stat_labels[key] = v
        layout.addWidget(stats_box)

        # Legend row
        legend = QLabel(
            f'<span style="color:{_C_Q}">&#9632; Q-mode</span>&nbsp;&nbsp;'
            f'<span style="color:{_C_FW}">&#9632; FW-mode</span>&nbsp;&nbsp;'
            f'<span style="color:{_C_FWD}">&#9632; Fwd Transition</span>&nbsp;&nbsp;'
            f'<span style="color:{_C_BCK}">&#9632; Back Transition</span>'
        )
        legend.setStyleSheet(f'font-size:11px; background:transparent; padding:0 2px;')
        layout.addWidget(legend)

        # Timeline
        self._timeline = TimelineWidget()
        layout.addWidget(self._timeline)

        # Splitter: table (top) | map (bottom)
        splitter = QSplitter(Qt.Vertical)
        splitter.setHandleWidth(4)
        splitter.setStyleSheet(f'QSplitter::handle {{ background:{COLORS["border"]}; }}')

        # Table
        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels([
            '#', 'Direction', 'From Mode', 'To Mode', 'Duration', 'Alt (m)', 'Spd Start', 'Spd End',
        ])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.horizontalHeader().setStyleSheet(
            f'QHeaderView::section {{ background-color:{COLORS["bg_tertiary"]};'
            f' color:{COLORS["text_secondary"]}; font-size:11px; font-weight:bold;'
            f' border:none; border-bottom:1px solid {COLORS["border"]}; padding:6px; }}'
        )
        self._table.setStyleSheet(
            f'QTableWidget {{ background-color:{COLORS["bg_secondary"]}; color:{COLORS["text_primary"]};'
            f' border:1px solid {COLORS["border"]}; border-radius:8px;'
            f' gridline-color:{COLORS["border"]}; font-size:13px; }}'
            f'QTableWidget::item {{ padding:6px 10px; border:none; }}'
            f'QTableWidget::item:selected {{ background-color:{COLORS["bg_hover"]}; color:{COLORS["text_primary"]}; }}'
        )
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setAlternatingRowColors(False)
        self._table.verticalHeader().setVisible(False)
        splitter.addWidget(self._table)

        # Map
        self._webview = QWebEngineView()
        self._webview.setMinimumHeight(250)
        splitter.addWidget(self._webview)
        splitter.setSizes([260, 340])

        layout.addWidget(splitter, 1)

        # Placeholder
        self._placeholder = QLabel('Load a VTOL/QuadPlane log to see transition analysis.')
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setStyleSheet(f'color:{COLORS["text_disabled"]}; font-size:14px; background:transparent;')
        self._placeholder.setVisible(False)
        layout.addWidget(self._placeholder)

        self._webview.setHtml(self._placeholder_html())
        return self._widget

    def load_data(self, log_data) -> None:
        self._log_data = log_data
        self._analyze()

    def clear(self) -> None:
        self._log_data = None
        self._table.setRowCount(0)
        self._timeline.set_data([], 1.0)
        for v in self._stat_labels.values():
            v.setText('—')
        self._placeholder.setVisible(False)
        self._table.setVisible(True)
        if self._webview:
            self._webview.setHtml(self._placeholder_html())

    # ── Speed settling ───────────────────────────────────────

    def _find_speed_settled(self, spd_t, spd_arr, start_us, direction,
                            max_window_s=60.0, flat_window_s=3.0, flat_thresh=0.5):
        t0    = float(start_us)
        t_end = t0 + max_window_s * 1e6
        mask  = (spd_t >= t0) & (spd_t <= t_end)
        t_seg   = spd_t[mask]
        spd_seg = spd_arr[mask]
        if len(t_seg) < 4:
            return None
        win_us = flat_window_s * 1e6
        for i in range(len(t_seg)):
            wm = (t_seg >= t_seg[i]) & (t_seg <= t_seg[i] + win_us)
            if wm.sum() < 3:
                continue
            if spd_seg[wm].std() < flat_thresh:
                return int(t_seg[i])
        return int(t_seg[-1])

    # ── Main analysis ────────────────────────────────────────

    def _analyze(self):
        log = self._log_data
        if log is None:
            return

        mode_events = sorted(
            [e for e in log.events if e.event_type == 'mode_change'],
            key=lambda e: e.time_us,
        )

        def mode_name(ev):
            d = ev.detail
            return d[6:].strip() if d.startswith('Mode: ') else d.strip()

        # ── Speed source ─────────────────────────────────────
        has_arsp = ('TimeUS' in log.arsp and 'Airspeed' in log.arsp
                    and len(log.arsp['Airspeed']) > 4)
        has_gps  = ('TimeUS' in log.gps and 'Spd' in log.gps
                    and len(log.gps['Spd']) > 4)

        if has_arsp:
            arsp_t   = log.arsp['TimeUS'].astype(np.float64)
            arsp_spd = log.arsp['Airspeed'].astype(np.float64)
            if 'U' in log.arsp:
                u_mask = log.arsp['U'].astype(np.int32) == 1
                if u_mask.sum() > 4:
                    arsp_t   = arsp_t[u_mask]
                    arsp_spd = arsp_spd[u_mask]
            spd_t   = arsp_t
            spd_arr = arsp_spd
            spd_source = 'airspeed'
        elif has_gps:
            spd_t   = log.gps['TimeUS'].astype(np.float64)
            spd_arr = log.gps['Spd'].astype(np.float64)
            spd_source = 'GPS'
        else:
            spd_t = spd_arr = None
            spd_source = 'none'

        has_speed = spd_t is not None and len(spd_t) > 4

        # ── Baro altitude ────────────────────────────────────
        has_baro = ('TimeUS' in log.baro and 'Alt' in log.baro
                    and len(log.baro['Alt']) > 4)
        if has_baro:
            baro_t   = log.baro['TimeUS'].astype(np.float64)
            baro_alt = log.baro['Alt'].astype(np.float64)

        # ── GPS track for map ─────────────────────────────────
        has_gps_pos = ('TimeUS' in log.gps and 'Lat' in log.gps
                       and len(log.gps['Lat']) > 4)
        if has_gps_pos:
            gps_t   = log.gps['TimeUS'].astype(np.float64)
            gps_lat = log.gps['Lat'].astype(np.float64)
            gps_lng = log.gps['Lng'].astype(np.float64)
            if 'Status' in log.gps:
                fix = log.gps['Status'] >= 3
                gps_t   = gps_t[fix]
                gps_lat = gps_lat[fix]
                gps_lng = gps_lng[fix]

        # ── QTUN tilt ────────────────────────────────────────
        has_qtun = ('TimeUS' in log.qtun and 'Tilt' in log.qtun
                    and len(log.qtun['Tilt']) > 4)
        if has_qtun:
            qtun_t    = log.qtun['TimeUS'].astype(np.float64)
            qtun_tilt = log.qtun['Tilt'].astype(np.float64)

        # ── Detect transitions ───────────────────────────────
        transitions = []
        for i in range(len(mode_events) - 1):
            a, b   = mode_events[i], mode_events[i + 1]
            m_a, m_b = mode_name(a), mode_name(b)
            if _is_q(m_a) == _is_q(m_b):
                continue

            direction    = _FWD if _is_q(m_a) else _BACK
            start_us     = a.time_us
            next_mode_us = b.time_us

            end_us = None
            method = 'mode change'

            if has_speed:
                s = self._find_speed_settled(
                    spd_t, spd_arr, start_us, direction,
                    max_window_s=min(60.0, (next_mode_us - start_us) / 1e6),
                )
                if s is not None:
                    end_us = s
                    method = f'{spd_source} settled'

            if has_qtun and end_us is None:
                t0f   = float(start_us)
                t_max = float(next_mode_us)
                mask  = (qtun_t >= t0f) & (qtun_t <= t_max)
                if mask.sum() > 2:
                    tilt_seg = qtun_tilt[mask]
                    t_seg    = qtun_t[mask]
                    target   = 0.0 if direction == _FWD else 100.0
                    settled  = np.where(np.abs(tilt_seg - target) < 5.0)[0]
                    if len(settled) > 0:
                        end_us = int(t_seg[settled[0]])
                        method = 'tilt settled'

            if end_us is None:
                end_us = next_mode_us
                method = 'mode change'

            duration_s = (end_us - start_us) / 1_000_000.0

            spd_start = spd_end = alt_m = tr_lat = tr_lng = None
            if has_speed:
                spd_start = float(np.interp(float(start_us), spd_t, spd_arr))
                spd_end   = float(np.interp(float(end_us),   spd_t, spd_arr))
            if has_baro:
                alt_m = float(np.interp(float(start_us), baro_t, baro_alt))
            if has_gps_pos and len(gps_t) > 0:
                tr_lat = float(np.interp(float(start_us), gps_t, gps_lat))
                tr_lng = float(np.interp(float(start_us), gps_t, gps_lng))

            transitions.append({
                'num':        len(transitions) + 1,
                'direction':  direction,
                'from_mode':  m_a,
                'to_mode':    m_b,
                'start_us':   start_us,
                'end_us':     end_us,
                'duration_s': duration_s,
                'spd_start':  spd_start,
                'spd_end':    spd_end,
                'alt_m':      alt_m,
                'method':     method,
                'lat':        tr_lat,
                'lng':        tr_lng,
            })

        # ── Build timeline segments ───────────────────────────
        t0     = log.start_time_us
        total_s = log.duration_seconds or 1.0
        # Build a flat list of segments including transition sub-phases
        tl_segs = []
        tr_lookup = {tr['start_us']: tr for tr in transitions}

        for i, ev in enumerate(mode_events):
            seg_start = ev.time_us
            seg_end   = mode_events[i + 1].time_us if i + 1 < len(mode_events) else log.end_time_us
            m = mode_name(ev)
            seg_type = 'q' if _is_q(m) else 'fw'

            # Check if a transition starts here
            if seg_start in tr_lookup:
                tr = tr_lookup[seg_start]
                tr_end = tr['end_us']
                tr_type = 'fwd_tr' if tr['direction'] == _FWD else 'back_tr'
                tl_segs.append((
                    (seg_start - t0) / 1e6,
                    (tr_end    - t0) / 1e6,
                    tr_type, tr['direction'],
                ))
                if tr_end < seg_end:
                    tl_segs.append((
                        (tr_end  - t0) / 1e6,
                        (seg_end - t0) / 1e6,
                        seg_type, m,
                    ))
            else:
                tl_segs.append((
                    (seg_start - t0) / 1e6,
                    (seg_end   - t0) / 1e6,
                    seg_type, m,
                ))

        # ── Placeholder if no transitions ─────────────────────
        if not transitions:
            self._table.setRowCount(0)
            self._table.setVisible(False)
            self._placeholder.setVisible(True)
            self._timeline.set_data(tl_segs, total_s)
            for v in self._stat_labels.values():
                v.setText('—')
            self._webview.setHtml(self._placeholder_html('No VTOL transitions detected.'))
            return

        self._table.setVisible(True)
        self._placeholder.setVisible(False)

        # ── Summary stats ────────────────────────────────────
        fwd_durs  = [t['duration_s'] for t in transitions if t['direction'] == _FWD]
        back_durs = [t['duration_s'] for t in transitions if t['direction'] == _BACK]
        self._stat_labels['total'].setText(str(len(transitions)))
        self._stat_labels['fwd_avg'].setText(
            f'{sum(fwd_durs)/len(fwd_durs):.1f} s' if fwd_durs else '—')
        self._stat_labels['back_avg'].setText(
            f'{sum(back_durs)/len(back_durs):.1f} s' if back_durs else '—')
        self._stat_labels['longest'].setText(
            f'{max(t["duration_s"] for t in transitions):.1f} s')

        # ── Timeline ──────────────────────────────────────────
        self._timeline.set_data(tl_segs, total_s)

        # ── Table ─────────────────────────────────────────────
        self._table.setRowCount(len(transitions))
        fwd_color  = QColor(_C_FWD)
        back_color = QColor(_C_BCK)

        for row, tr in enumerate(transitions):
            color = fwd_color if tr['direction'] == _FWD else back_color

            def cell(text, align=Qt.AlignCenter):
                item = QTableWidgetItem(text)
                item.setTextAlignment(align)
                return item

            self._table.setItem(row, 0, cell(str(tr['num'])))
            dir_item = cell(tr['direction'])
            dir_item.setForeground(color)
            dir_item.setFont(QFont('Segoe UI', 10, QFont.Bold))
            self._table.setItem(row, 1, dir_item)
            self._table.setItem(row, 2, cell(tr['from_mode']))
            self._table.setItem(row, 3, cell(tr['to_mode']))
            self._table.setItem(row, 4, cell(f"{tr['duration_s']:.1f} s"))
            self._table.setItem(row, 5, cell(
                f"{tr['alt_m']:.0f}" if tr['alt_m'] is not None else '—'))
            self._table.setItem(row, 6, cell(
                f"{tr['spd_start']:.1f}" if tr['spd_start'] is not None else '—'))
            self._table.setItem(row, 7, cell(
                f"{tr['spd_end']:.1f}" if tr['spd_end'] is not None else '—'))

        # ── Map ───────────────────────────────────────────────
        if has_gps_pos and len(gps_t) > 1:
            # Build colored GPS track segments between mode changes
            seg_boundaries = sorted(set(
                [int(gps_t[0]), int(gps_t[-1])]
                + [e.time_us for e in mode_events]
                + [tr['start_us'] for tr in transitions]
                + [tr['end_us']   for tr in transitions]
            ))

            # Map from timestamp → color
            def seg_color_at(us):
                # Find which phase this timestamp falls in
                for tr in transitions:
                    if tr['start_us'] <= us <= tr['end_us']:
                        return (_C_FWD if tr['direction'] == _FWD else _C_BCK,
                                f"#{tr['num']} {tr['direction']} transition")
                # Find mode at this time
                active_mode = mode_name(mode_events[0]) if mode_events else ''
                for ev in mode_events:
                    if ev.time_us <= us:
                        active_mode = mode_name(ev)
                return (_C_Q if _is_q(active_mode) else _C_FW, active_mode)

            segments_geo = []
            step = max(1, len(gps_t) // 2000)
            for si in range(len(seg_boundaries) - 1):
                t_lo = seg_boundaries[si]
                t_hi = seg_boundaries[si + 1]
                mask = (gps_t >= t_lo) & (gps_t <= t_hi)
                pts_idx = np.where(mask)[0][::step]
                if len(pts_idx) < 2:
                    # Include at least endpoints
                    lo_idx = np.searchsorted(gps_t, t_lo)
                    hi_idx = min(np.searchsorted(gps_t, t_hi), len(gps_t) - 1)
                    pts_idx = np.array([lo_idx, hi_idx])
                color, label = seg_color_at(t_lo + 1)
                pts = [[float(gps_lat[i]), float(gps_lng[i])] for i in pts_idx
                       if 0 <= i < len(gps_lat)]
                if len(pts) >= 2:
                    segments_geo.append({'points': pts, 'color': color, 'label': label})

            home_lat = float(gps_lat[0])
            home_lng = float(gps_lng[0])

            html = _make_transition_map_html(segments_geo, transitions, home_lat, home_lng)
            self._webview.setHtml(html, _ASSETS_URL)
        else:
            self._webview.setHtml(self._placeholder_html('No GPS data for map.'))

    @staticmethod
    def _placeholder_html(msg='Load a VTOL/QuadPlane log to see transition analysis.'):
        return (f'<!DOCTYPE html><html><body style="margin:0;background:{COLORS["bg_primary"]};'
                f'display:flex;align-items:center;justify-content:center;height:100vh;'
                f'font-family:Segoe UI,sans-serif;color:{COLORS["text_disabled"]};'
                f'font-size:15px;">{msg}</body></html>')
