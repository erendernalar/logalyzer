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

# ── VTOL state machine constants ─────────────────────────────────────────────
# These mirror ArduPlane exactly; see the reconstruction note in _build_states.

_S_UNDEF, _S_MC, _S_T_FW, _S_T_MC, _S_FW = 0, 1, 2, 3, 4
_S_NAME = {_S_UNDEF: 'UNDEFINED', _S_MC: 'MC', _S_T_FW: 'TRANSITION_TO_FW',
           _S_T_MC: 'TRANSITION_TO_MC', _S_FW: 'FW'}

# SLT_Transition::State — ArduPlane/transition.h
_SLT_AIRSPEED_WAIT, _SLT_TIMER, _SLT_DONE = 0, 1, 2
# Tailsitter_Transition::State — ArduPlane/tailsitter.h  (NOTE: value 1 is a
# *back* transition here, unlike SLT where it is a forward phase)
_TS_ANGLE_WAIT_FW, _TS_ANGLE_WAIT_VTOL, _TS_DONE = 0, 1, 2
# QuadPlane::position_control_state — ArduPlane/quadplane.h
_QPOS_NONE, _QPOS_APPROACH, _QPOS_AIRBRAKE, _QPOS_POSITION1 = 0, 1, 2, 3

# QTUN.Ast bitmask — ArduPlane/quadplane.cpp log_assistance_flags
_AST_ACTIVE, _AST_FORCED, _AST_SPEED = 1 << 0, 1 << 1, 1 << 2
_AST_ALT, _AST_ANGLE, _AST_FW_FORCE, _AST_SPIN = 1 << 3, 1 << 4, 1 << 5, 1 << 6
_AST_NAMES = [(_AST_SPEED, 'speed'), (_AST_ALT, 'alt'), (_AST_ANGLE, 'angle'),
              (_AST_FORCED, 'forced'), (_AST_FW_FORCE, 'fw_force'),
              (_AST_SPIN, 'spin')]

# Table columns: (header, takes leftover width)
_COLUMNS = [
    ('#',           False),
    ('Type',        False),
    ('Start',       False),
    ('Mode Change', False),
    ('To Aspd',     False),
    ('Dur',         False),
    ('Result',      True),
    ('Alt',         False),
    ('Spd In',      False),
    ('Spd Out',     False),
    ('Source',      False),
]

# Transition kinds shown in the table
_K_FWD    = 'Forward'
_K_BACK_A = 'Back (auto)'
_K_BACK_M = 'Back (manual)'
_K_ASSIST = 'Assist'

# Ground speed (m/s) at or below which a manual back transition counts as
# "slowed to hover", and how long it must stay there.
_HOVER_SPD_MS  = 2.0
_HOVER_HOLD_S  = 1.0
_DECEL_LIMIT_S = 30.0

# Colors for map segments (must be CSS hex strings)
_C_Q   = '#3FB950'   # green  — Q-mode
_C_FW  = '#58A6FF'   # blue   — FW-mode
_C_FWD = '#F85149'   # red    — forward transition phase
_C_BCK = '#FF9F43'   # orange — back transition phase
_C_AST = '#BC8CFF'   # purple — assist re-transition (no mode change)

# Result column
_C_OK   = '#3FB950'  # green
_C_WARN = '#D29922'  # amber
_C_FAIL = '#F85149'  # red

_R_OK, _R_WARN, _R_FAIL, _R_UNKNOWN = 'ok', 'warn', 'fail', 'unknown'
_R_COLOR = {_R_OK: _C_OK, _R_WARN: _C_WARN, _R_FAIL: _C_FAIL}


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

        color_map = {
            'q': _C_Q, 'fw': _C_FW, 'fwd_tr': _C_FWD, 'back_tr': _C_BCK,
        }
        for start_s, end_s, seg_type, _ in self._segments:
            x1, x2 = t2x(start_s), t2x(end_s)
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

        p.end()


# ── Leaflet map HTML ─────────────────────────────────────────────────────────

def _make_transition_map_html(segments_geo, transitions, home_lat, home_lng):
    """
    segments_geo: list of {points:[[lat,lng],...], color:str, label:str}
    transitions:  list of {lat, lng, kind, duration_s, source, num}
    """
    segs_json = json.dumps([
        {'pts': s['points'], 'color': s['color'], 'label': s['label']}
        for s in segments_geo
    ])
    _kind_color = {
        _K_FWD: _C_FWD, _K_BACK_A: _C_BCK, _K_BACK_M: _C_BCK, _K_ASSIST: _C_AST,
    }
    markers_json = json.dumps([
        {
            'lat': t['lat'], 'lng': t['lng'],
            'dir': t['kind'],
            'dur': ('%.1f s' % t['duration_s']) if t['duration_s'] is not None else '—',
            'src': t['result_text'],
            'ok': {_R_OK: '✓', _R_WARN: '⚠', _R_FAIL: '✗'}.get(t['result'], '?'),
            'okcolor': _R_COLOR.get(t['result'], '#8B949E'),
            'num': t['num'],
            'color': _kind_color.get(t['kind'], _C_FW),
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
    'Duration: ' + m.dur + '<br>' +
    '<span style=\"color:' + m.okcolor + '\">' + m.ok + ' ' + m.src + '</span></div>'
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
        self._transitions = []
        self._state_series = None
        self._assists = []
        self._assists = []
        self._spd_src = 'none'

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
        stats_row.setSpacing(28)

        self._stat_labels = {}
        for key, label in [
            ('total',    'Total Transitions'),
            ('fwd_avg',  'Avg Fwd Duration'),
            ('back_avg', 'Avg Back Duration'),
            ('longest',  'Longest Transition'),
            ('done',     'Completed'),
            ('assist',   'Assist Events'),
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
            f'<span style="color:{_C_BCK}">&#9632; Back Transition</span>&nbsp;&nbsp;'
            f'<span style="color:{_C_AST}">&#9632; Assist Re-transition</span>'
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
        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels([c[0] for c in _COLUMNS])
        hdr = self._table.horizontalHeader()
        hdr.setMinimumSectionSize(44)
        for i, (_, stretch) in enumerate(_COLUMNS):
            hdr.setSectionResizeMode(
                i, QHeaderView.Stretch if stretch else QHeaderView.ResizeToContents)
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
        self._transitions = []
        self._state_series = None
        self._table.setRowCount(0)
        self._timeline.set_data([], 1.0)
        for v in self._stat_labels.values():
            v.setText('—')
        self._placeholder.setVisible(False)
        self._table.setVisible(True)
        if self._webview:
            self._webview.setHtml(self._placeholder_html())

    # ── Channel helpers ──────────────────────────────────────

    @staticmethod
    def _chan(d, *fields):
        """[t_us, f1, f2, …] for sensor instance 0 only, sorted by time.

        The loader keeps every instance of a message interleaved in one array
        (two baros, two airspeed sensors, …). Anything that interpolates or
        takes a standard deviation has to pick one instance first, or it reads
        two sensors as a single very noisy one.
        """
        if 'TimeUS' not in d or len(d['TimeUS']) == 0:
            return None
        n = len(d['TimeUS'])
        if any(f not in d or len(d[f]) != n for f in fields):
            return None
        if 'I' in d and len(d['I']) == n:
            m = d['I'].astype(np.int32) == 0
            if m.sum() < 2:
                m = np.ones(n, dtype=bool)
        else:
            m = np.ones(n, dtype=bool)
        t = d['TimeUS'][m].astype(np.float64)
        order = np.argsort(t, kind='stable')
        out = [t[order]]
        for f in fields:
            out.append(d[f][m][order].astype(np.float64))
        return out

    @staticmethod
    def _step(sample_t, ev_t, ev_v, default):
        """Value of a right-continuous step function at each sample time."""
        vals = np.asarray(ev_v)
        out = np.full(len(sample_t), default, dtype=vals.dtype)
        if len(ev_t) == 0:
            return out
        idx = np.searchsorted(np.asarray(ev_t, dtype=np.float64),
                              np.asarray(sample_t, dtype=np.float64),
                              side='right') - 1
        ok = idx >= 0
        out[ok] = vals[idx[ok]]
        return out

    @staticmethod
    def _at(t_us, xp, fp):
        """np.interp guarded against an empty or single-sample channel."""
        if xp is None or len(xp) == 0:
            return None
        return float(np.interp(float(t_us), xp, fp))

    # ── VTOL state reconstruction ────────────────────────────

    def _build_states(self, log, mode_events):
        """Rebuild the vehicle's MAV_VTOL_STATE at every QTUN sample.

        This is a direct port of ArduPlane's own Transition::get_mav_vtol_state()
        — the function that fills the EXTENDED_SYS_STATE telemetry message, which
        is never written to a dataflash log. Every input it uses *is* logged, so
        the state can be reconstructed exactly rather than inferred from speed:

            SLT (quadplane / tiltrotor)     ArduPlane/quadplane.cpp
                in VTOL mode  -> T_MC if QPOS is AIRBRAKE or POSITION1 else MC
                otherwise     -> T_FW while Trn is AIRSPEED_WAIT or TIMER, else FW

            Tailsitter                      ArduPlane/tailsitter.cpp
                Trn ANGLE_WAIT_VTOL -> T_MC
                Trn DONE            -> FW
                Trn ANGLE_WAIT_FW   -> MC if in a VTOL mode else T_FW

        Returns None when the log has no QTUN (not a QuadPlane, or too old).
        """
        q = self._chan(log.qtun, 'Trn', 'Ast')
        if q is None or len(q[0]) < 2:
            return None
        qt, trn, ast = q[0], q[1].astype(np.int32), q[2].astype(np.int32)

        params = log.params or {}
        tailsitter = float(params.get('Q_TAILSIT_ENABLE', 0)) >= 1

        # Mode class at each sample
        if mode_events:
            ev_t = np.array([e.time_us for e in mode_events], dtype=np.float64)
            ev_q = np.array([_is_q(self._mode_name(e)) for e in mode_events], dtype=bool)
            in_q_mode = self._step(qt, ev_t, ev_q, bool(ev_q[0]))
        else:
            in_q_mode = np.zeros(len(qt), dtype=bool)

        # QuadPlane position-control state (only logged during automatic
        # VTOL approaches and landings)
        qp = self._chan(log.qpos, 'State')
        if qp is not None:
            qpos = self._step(qt, qp[0], qp[1].astype(np.int32), _QPOS_NONE)
        else:
            qpos = np.full(len(qt), _QPOS_NONE, dtype=np.int32)

        # in_vtol_mode(): a VTOL mode, or an automatic VTOL land sequence that
        # has progressed past the fixed-wing APPROACH/AIRBRAKE phases.
        in_vtol = in_q_mode.copy()
        land_seq = (~in_q_mode) & (qpos >= _QPOS_APPROACH)
        if land_seq.any():
            in_vtol = np.where(
                land_seq,
                (qpos != _QPOS_APPROACH) & (qpos != _QPOS_AIRBRAKE),
                in_vtol)

        if tailsitter:
            st = np.where(
                trn == _TS_ANGLE_WAIT_VTOL, _S_T_MC,
                np.where(trn == _TS_DONE, _S_FW,
                         np.where(in_vtol, _S_MC, _S_T_FW)))
        else:
            st = np.where(
                in_vtol,
                np.where((qpos == _QPOS_AIRBRAKE) | (qpos == _QPOS_POSITION1),
                         _S_T_MC, _S_MC),
                np.where(trn <= _SLT_TIMER, _S_T_FW, _S_FW))

        # Armed state, so mode switches on the ground are not counted as
        # transitions. STAT is authoritative; ARM/DISARM events are the fallback.
        sa = self._chan(log.stat, 'Armed')
        if sa is not None and len(sa[0]) > 2:
            armed = self._step(qt, sa[0], sa[1].astype(np.int32) == 1, False)
        else:
            arm_ev = sorted([e for e in log.events
                             if e.event_type in ('arm', 'disarm')],
                            key=lambda e: e.time_us)
            if arm_ev:
                armed = self._step(
                    qt,
                    np.array([e.time_us for e in arm_ev], dtype=np.float64),
                    np.array([e.event_type == 'arm' for e in arm_ev], dtype=bool),
                    False)
            else:
                armed = np.ones(len(qt), dtype=bool)

        return {
            't': qt, 'state': st.astype(np.int32), 'trn': trn, 'ast': ast,
            'in_vtol': in_vtol, 'tailsitter': tailsitter, 'armed': armed,
            'has_qpos': qp is not None,
        }

    @staticmethod
    def _runs(states):
        """[(state, first_idx, last_idx)] for each run of equal state."""
        if len(states) == 0:
            return []
        cut = np.flatnonzero(np.diff(states)) + 1
        starts = np.concatenate(([0], cut))
        ends = np.concatenate((cut, [len(states)]))
        return [(int(states[a]), int(a), int(b - 1)) for a, b in zip(starts, ends)]

    @staticmethod
    def _mode_name(ev):
        d = ev.detail
        return d[6:].strip() if d.startswith('Mode: ') else d.strip()

    def _modes_around(self, mode_events, t_us, window_s=2.0):
        """(from_mode, to_mode) for a transition at t_us.

        If no mode change happened within window_s the transition was not
        commanded by the pilot (assist re-transition), so both are the same.
        """
        if not mode_events:
            return '—', '—'
        idx = -1
        for i, ev in enumerate(mode_events):
            if ev.time_us <= t_us:
                idx = i
            else:
                break
        if idx < 0:
            return '—', self._mode_name(mode_events[0])
        cur = self._mode_name(mode_events[idx])
        if abs(t_us - mode_events[idx].time_us) > window_s * 1e6:
            return cur, cur
        prev = self._mode_name(mode_events[idx - 1]) if idx > 0 else '—'
        return prev, cur

    # ── Physical deceleration (manual back transitions) ──────

    def _decel_time_s(self, gt, gs, t0_us, t_limit_us):
        """Seconds until ground speed drops to hover and stays there.

        ArduPilot has no back-transition state for a manual mode switch — the
        firmware reports MC immediately — so this is a derived physical
        measurement, not a state duration, and is labelled as such.
        Returns None if it never settles inside the window (never a guess).
        """
        if gt is None:
            return None
        t_end = min(float(t_limit_us), float(t0_us) + _DECEL_LIMIT_S * 1e6)
        m = (gt >= float(t0_us)) & (gt <= t_end)
        t_seg, v_seg = gt[m], gs[m]
        if len(t_seg) < 3:
            return None
        hold_us = _HOVER_HOLD_S * 1e6
        for i in range(len(t_seg)):
            if v_seg[i] > _HOVER_SPD_MS:
                continue
            wm = (t_seg >= t_seg[i]) & (t_seg <= t_seg[i] + hold_us)
            if wm.sum() < 2:
                break
            if (v_seg[wm] <= _HOVER_SPD_MS).all():
                return (t_seg[i] - float(t0_us)) / 1e6
        return None

    @staticmethod
    def _assist_events(states):
        """Rising edges of QTUN.Ast bit0, with the reason bits that came with them.

        Each edge is one moment VTOL assist kicked in during forward flight —
        these have no mode change, so a mode-based analysis cannot see them.
        """
        if states is None:
            return []
        ast = states['ast']
        active = (ast & _AST_ACTIVE) != 0
        if len(active) < 2:
            return []
        edges = np.flatnonzero((~active[:-1]) & active[1:]) + 1
        out = []
        for i in edges:
            if not bool(states['armed'][i]):
                continue
            flags = int(ast[i])
            why = [name for bit, name in _AST_NAMES if flags & bit]
            out.append({'t_us': float(states['t'][i]),
                        'reasons': why or ['unspecified']})
        return out

    # ── MSG cross-check ──────────────────────────────────────

    @staticmethod
    def _msg_marks(log):
        """Transition timestamps announced in the MSG text stream.

        An independent second source: where it disagrees with the reconstructed
        state machine the row is flagged rather than silently trusted.
        """
        out = {'started': [], 'reached': [], 'done': [], 'failed': []}
        for t_us, txt in (log.messages or []):
            low = txt.lower()
            if 'transition started' in low:
                out['started'].append(t_us)
            elif 'transition airspeed reached' in low:
                out['reached'].append(t_us)
            elif 'transition done' in low:
                out['done'].append(t_us)
            elif 'transition failed' in low:
                out['failed'].append(t_us)
        return out

    @staticmethod
    def _nearest(marks, t_us, tol_s=2.0):
        best = None
        for m in marks:
            d = abs(m - t_us) / 1e6
            if d <= tol_s and (best is None or d < best):
                best = d
        return best

    # ── Main analysis ────────────────────────────────────────

    def _analyze(self):
        log = self._log_data
        if log is None:
            return

        mode_events = sorted(
            [e for e in log.events if e.event_type == 'mode_change'],
            key=lambda e: e.time_us,
        )

        # ── Speed / altitude / position channels ─────────────
        arsp = self._chan(log.arsp, 'Airspeed', 'U')
        if arsp is not None:
            use = arsp[2].astype(np.int32) == 1
            if use.sum() > 4:
                arsp = [arsp[0][use], arsp[1][use]]
            else:
                arsp = [arsp[0], arsp[1]]
            if len(arsp[0]) <= 4:
                arsp = None

        gps = self._chan(log.gps, 'Spd', 'Lat', 'Lng', 'Status')
        gt = gs = glat = glng = None
        if gps is not None:
            fix = gps[4].astype(np.int32) >= 3
            if fix.sum() > 4:
                gt, gs, glat, glng = gps[0][fix], gps[1][fix], gps[2][fix], gps[3][fix]
            elif len(gps[0]) > 4:
                gt, gs, glat, glng = gps[0], gps[1], gps[2], gps[3]

        baro = self._chan(log.baro, 'Alt')
        bt, balt = (baro[0], baro[1]) if baro is not None and len(baro[0]) > 4 else (None, None)

        # Airspeed for the table if we have it, else GPS ground speed
        if arsp is not None:
            spd_t, spd_v, self._spd_src = arsp[0], arsp[1], 'airspeed'
        elif gt is not None:
            spd_t, spd_v, self._spd_src = gt, gs, 'GPS'
        else:
            spd_t = spd_v = None
            self._spd_src = 'none'

        # ── Reconstruct the VTOL state machine ───────────────
        states = self._build_states(log, mode_events)
        marks = self._msg_marks(log)

        if states is not None:
            transitions, tl_segs = self._transitions_from_states(
                log, states, mode_events, marks, gt, gs)
            self._state_series = states
            self._assists = self._assist_events(states)
        else:
            transitions, tl_segs = self._transitions_from_modes(
                log, mode_events, gt, gs)
            self._state_series = None
            self._assists = []

        # ── Attach per-transition sample values ──────────────
        for tr in transitions:
            t0, t1 = tr['start_us'], tr['end_us']
            tr['spd_start'] = self._at(t0, spd_t, spd_v) if spd_t is not None else None
            tr['spd_end']   = self._at(t1, spd_t, spd_v) if spd_t is not None else None
            tr['alt_m']     = self._at(t0, bt, balt) if bt is not None else None
            tr['lat'] = self._at(t0, gt, glat) if gt is not None else None
            tr['lng'] = self._at(t0, gt, glng) if gt is not None else None

        self._transitions = transitions
        self._render(log, transitions, tl_segs, gt, glat, glng, mode_events)

    def _transitions_from_states(self, log, states, mode_events, marks, gt, gs):
        """Transitions as runs of the reconstructed state series."""
        qt, st = states['t'], states['state']
        trn = states['trn']
        runs = self._runs(st)
        t0_log = log.start_time_us

        def run_bounds(k):
            _, a, b = runs[k]
            start = qt[a]
            end = qt[runs[k + 1][1]] if k + 1 < len(runs) else qt[b]
            return float(start), float(end)

        transitions = []
        tl_segs = []
        seg_type = {_S_MC: 'q', _S_FW: 'fw', _S_T_FW: 'fwd_tr',
                    _S_T_MC: 'back_tr', _S_UNDEF: 'fw'}

        for k, (state, a, b) in enumerate(runs):
            start_us, end_us = run_bounds(k)
            tl_segs.append(((start_us - t0_log) / 1e6, (end_us - t0_log) / 1e6,
                            seg_type.get(state, 'fw'), _S_NAME.get(state, '')))

            prev_state = runs[k - 1][0] if k > 0 else None
            next_state = runs[k + 1][0] if k + 1 < len(runs) else None

            if not bool(states['armed'][a]):
                continue    # mode switching on the ground is not a transition

            if state == _S_T_FW:
                # A forward transition proper starts from multicopter flight;
                # starting from FW means assist re-triggered mid-cruise.
                kind = _K_ASSIST if prev_state == _S_FW else _K_FWD
                to_air = None
                notes = []
                if not states['tailsitter']:
                    seg = trn[a:b + 1]
                    hit = np.flatnonzero(seg == _SLT_TIMER)
                    if len(hit):
                        to_air = (float(qt[a + hit[0]]) - start_us) / 1e6
                    # TIMER -> AIRSPEED_WAIT means airspeed fell back below the
                    # transition threshold and the timer restarted.
                    lost = int(np.count_nonzero(
                        (seg[:-1] == _SLT_TIMER) & (seg[1:] == _SLT_AIRSPEED_WAIT)))
                    if lost:
                        notes.append('airspeed lost %d×' % lost)
                dur_s = (end_us - start_us) / 1e6

                # Q_TRANS_FAIL: the firmware aborts a forward transition that
                # exceeds the timeout and switches to Q_TRANS_FAIL_ACT.
                fail_to = float((log.params or {}).get('Q_TRANS_FAIL', 0) or 0)
                failed_msg = any(start_us <= t <= end_us for t in marks['failed'])

                if failed_msg or (fail_to > 0 and dur_s > fail_to):
                    result = _R_FAIL
                    rtext = ('Failed — over Q_TRANS_FAIL (%.0fs)' % fail_to
                             if fail_to > 0 else 'Failed — time limit')
                elif next_state != _S_FW:
                    result = _R_FAIL
                    rtext = 'Aborted — back to VTOL'
                elif kind == _K_ASSIST:
                    result = _R_WARN
                    rtext = 'Recovered to FW'
                elif notes:
                    result = _R_WARN
                    rtext = 'Completed — ' + ', '.join(notes)
                else:
                    result = _R_OK
                    rtext = 'Completed'

                if kind == _K_ASSIST and result == _R_FAIL and not failed_msg:
                    rtext = 'Ended in VTOL'

                xchk = self._nearest(marks['started'], start_us)
                transitions.append(self._mk(
                    len(transitions) + 1, kind, mode_events, start_us, end_us,
                    duration_s=dur_s, to_airspeed_s=to_air,
                    source='QTUN.Trn' + (' ✓msg' if xchk is not None else ''),
                    xcheck_s=xchk, note=', '.join(notes),
                    result=result, result_text=rtext))

            elif state == _S_T_MC:
                if next_state == _S_MC:
                    result, rtext = _R_OK, 'Completed'
                else:
                    result, rtext = _R_FAIL, 'Aborted — returned to forward flight'
                transitions.append(self._mk(
                    len(transitions) + 1, _K_BACK_A, mode_events, start_us, end_us,
                    duration_s=(end_us - start_us) / 1e6,
                    to_airspeed_s=None, source='QPOS', xcheck_s=None,
                    result=result, result_text=rtext))

            elif state == _S_MC and prev_state in (_S_FW, _S_T_FW):
                # Manual back transition: the firmware switches to MC with no
                # intermediate state, so the state duration is zero by
                # definition. Report the physical deceleration instead.
                limit = qt[runs[k + 1][1]] if k + 1 < len(runs) else qt[-1]
                dec = self._decel_time_s(gt, gs, start_us, limit)
                if dec is None:
                    result = _R_UNKNOWN
                    rtext = 'Never slowed to hover'
                else:
                    result = _R_OK
                    rtext = 'Slowed to hover'
                transitions.append(self._mk(
                    len(transitions) + 1, _K_BACK_M, mode_events, start_us,
                    start_us + (dec or 0.0) * 1e6,
                    duration_s=dec, to_airspeed_s=None,
                    source='decel (derived)', xcheck_s=None,
                    note='firmware reports MC immediately',
                    result=result, result_text=rtext))

        return transitions, tl_segs

    def _transitions_from_modes(self, log, mode_events, gt, gs):
        """Fallback for logs without QTUN: mode-class changes only.

        The transition begins when the *new* mode is entered and runs until the
        next mode change that switches class again — same-class changes in
        between (QLOITER -> QSTABILIZE) must not truncate it.
        """
        transitions = []
        tl_segs = []
        t0_log = log.start_time_us
        if not mode_events:
            return transitions, tl_segs

        cls = [(e.time_us, _is_q(self._mode_name(e)), self._mode_name(e))
               for e in mode_events]
        # collapse runs of the same class
        flips = [0]
        for i in range(1, len(cls)):
            if cls[i][1] != cls[flips[-1]][1]:
                flips.append(i)

        for j, fi in enumerate(flips):
            t_start = cls[fi][0]
            t_end = cls[flips[j + 1]][0] if j + 1 < len(flips) else log.end_time_us
            is_q = cls[fi][1]
            tl_segs.append(((t_start - t0_log) / 1e6, (t_end - t0_log) / 1e6,
                            'q' if is_q else 'fw', cls[fi][2]))
            if j == 0:
                continue
            if is_q:
                dec = self._decel_time_s(gt, gs, t_start, t_end)
                transitions.append(self._mk(
                    len(transitions) + 1, _K_BACK_M, mode_events, t_start,
                    t_start + (dec or 0.0) * 1e6, duration_s=dec,
                    to_airspeed_s=None, source='decel (derived)', xcheck_s=None,
                    note='no QTUN in log',
                    result=_R_OK if dec is not None else _R_UNKNOWN,
                    result_text=('Slowed to hover' if dec is not None
                                 else 'Never slowed to hover')))
            else:
                transitions.append(self._mk(
                    len(transitions) + 1, _K_FWD, mode_events, t_start, t_end,
                    duration_s=None, to_airspeed_s=None,
                    source='mode change only', xcheck_s=None,
                    note='no QTUN in log',
                    result=_R_UNKNOWN,
                    result_text='Unknown — no QTUN'))
        return transitions, tl_segs

    def _mk(self, num, kind, mode_events, start_us, end_us, duration_s,
            to_airspeed_s, source, xcheck_s, note='',
            result=_R_OK, result_text='Completed'):
        frm, to = self._modes_around(mode_events, start_us)
        return {
            'num': num, 'kind': kind, 'from_mode': frm, 'to_mode': to,
            'start_us': int(start_us), 'end_us': int(end_us),
            'duration_s': duration_s, 'to_airspeed_s': to_airspeed_s,
            'source': source, 'xcheck_s': xcheck_s, 'note': note,
            'result': result, 'result_text': result_text,
            'completed': result == _R_OK,
            'spd_start': None, 'spd_end': None, 'alt_m': None,
            'lat': None, 'lng': None,
        }

    # ── Rendering ────────────────────────────────────────────

    _KIND_COLOR = {
        _K_FWD: _C_FWD, _K_BACK_A: _C_BCK, _K_BACK_M: _C_BCK, _K_ASSIST: _C_AST,
    }

    def _render(self, log, transitions, tl_segs, gt, glat, glng, mode_events):
        total_s = log.duration_seconds or 1.0
        self._timeline.set_data(tl_segs, total_s)

        if not transitions:
            self._table.setRowCount(0)
            self._table.setVisible(False)
            self._placeholder.setVisible(True)
            for v in self._stat_labels.values():
                v.setText('—')
            self._webview.setHtml(self._placeholder_html('No VTOL transitions detected.'))
            return

        self._table.setVisible(True)
        self._placeholder.setVisible(False)

        # ── Summary ──────────────────────────────────────────
        def durs(kinds):
            return [t['duration_s'] for t in transitions
                    if t['kind'] in kinds and t['completed']
                    and t['duration_s'] is not None]

        fwd  = durs((_K_FWD,))
        back = durs((_K_BACK_A, _K_BACK_M))
        assists = getattr(self, '_assists', [])
        measured = fwd + back

        self._stat_labels['total'].setText(
            str(len([t for t in transitions if t['kind'] != _K_ASSIST])))
        self._stat_labels['fwd_avg'].setText(
            f'{sum(fwd)/len(fwd):.1f} s' if fwd else '—')
        self._stat_labels['back_avg'].setText(
            f'{sum(back)/len(back):.1f} s' if back else '—')
        self._stat_labels['longest'].setText(
            f'{max(measured):.1f} s' if measured else '—')
        real = [t for t in transitions if t['kind'] != _K_ASSIST]
        n_ok = len([t for t in real if t['result'] == _R_OK])
        self._stat_labels['done'].setText(f'{n_ok} / {len(real)}' if real else '—')
        self._stat_labels['assist'].setText(str(len(assists)))

        # ── Table ────────────────────────────────────────────
        self._table.setRowCount(len(transitions))
        t0_log = log.start_time_us

        def cell(text, align=Qt.AlignCenter):
            item = QTableWidgetItem(text)
            item.setTextAlignment(align)
            return item

        for row, tr in enumerate(transitions):
            color = QColor(self._KIND_COLOR.get(tr['kind'], _C_FW))
            rel_s = (tr['start_us'] - t0_log) / 1e6

            kind_item = cell(tr['kind'])
            kind_item.setForeground(color)
            kind_item.setFont(QFont('Segoe UI', 10, QFont.Bold))

            mark = {_R_OK: '✓', _R_WARN: '⚠', _R_FAIL: '✗'}.get(tr['result'], '?')
            res_item = cell(f"{mark}  {tr['result_text']}", Qt.AlignVCenter | Qt.AlignLeft)
            res_item.setForeground(QColor(_R_COLOR.get(tr['result'],
                                                       COLORS['text_disabled'])))
            res_item.setFont(QFont('Segoe UI', 10, QFont.Bold))

            dur = tr['duration_s']
            dur_txt = f'{dur:.1f} s' if dur is not None else '—'
            air = tr['to_airspeed_s']

            src = tr['source']

            mode_txt = (tr['to_mode'] if tr['from_mode'] == tr['to_mode']
                        else f"{tr['from_mode']} → {tr['to_mode']}")

            vals = [
                str(tr['num']),
                kind_item,
                f"{int(rel_s // 60)}:{int(rel_s % 60):02d}",
                mode_txt,
                f'{air:.1f} s' if air is not None else '—',
                dur_txt,
                res_item,
                f"{tr['alt_m']:.0f}" if tr['alt_m'] is not None else '—',
                f"{tr['spd_start']:.1f}" if tr['spd_start'] is not None else '—',
                f"{tr['spd_end']:.1f}" if tr['spd_end'] is not None else '—',
                src,
            ]
            for col, val in enumerate(vals):
                item = val if isinstance(val, QTableWidgetItem) else cell(val)
                if tr['note']:
                    item.setToolTip(tr['note'])
                self._table.setItem(row, col, item)

        # ── Map ──────────────────────────────────────────────
        if gt is None or len(gt) < 2:
            self._webview.setHtml(self._placeholder_html('No GPS data for map.'))
            return

        segments_geo = self._map_segments(log, gt, glat, glng, tl_segs)
        html = _make_transition_map_html(
            segments_geo, transitions, float(glat[0]), float(glng[0]))
        self._webview.setHtml(html, _ASSETS_URL)

    def _map_segments(self, log, gt, glat, glng, tl_segs):
        """Colour the GPS track with the same segments the timeline uses."""
        color_of = {'q': _C_Q, 'fw': _C_FW, 'fwd_tr': _C_FWD, 'back_tr': _C_BCK}
        t0_log = log.start_time_us
        step = max(1, len(gt) // 2000)
        out = []
        for start_s, end_s, seg_type, label in tl_segs:
            t_lo = t0_log + start_s * 1e6
            t_hi = t0_log + end_s * 1e6
            idx = np.flatnonzero((gt >= t_lo) & (gt <= t_hi))
            if len(idx) < 2:
                lo = int(np.searchsorted(gt, t_lo))
                hi = int(min(np.searchsorted(gt, t_hi), len(gt) - 1))
                if hi <= lo:
                    continue
                idx = np.array([lo, hi])
            else:
                idx = idx[::step]
                if len(idx) < 2:
                    continue
            pts = [[float(glat[i]), float(glng[i])] for i in idx
                   if 0 <= i < len(glat)]
            if len(pts) >= 2:
                out.append({'points': pts,
                            'color': color_of.get(seg_type, _C_FW),
                            'label': label})
        return out

    @staticmethod
    def _placeholder_html(msg='Load a VTOL/QuadPlane log to see transition analysis.'):
        return (f'<!DOCTYPE html><html><body style="margin:0;background:{COLORS["bg_primary"]};'
                f'display:flex;align-items:center;justify-content:center;height:100vh;'
                f'font-family:Segoe UI,sans-serif;color:{COLORS["text_disabled"]};'
                f'font-size:15px;">{msg}</body></html>')
