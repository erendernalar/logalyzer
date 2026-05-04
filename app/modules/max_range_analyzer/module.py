import json
import math
import os
import numpy as np

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QPushButton, QButtonGroup, QSizePolicy,
)
from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtWebEngineWidgets import QWebEngineView

from app.modules.base_module import BaseModule
from app.theme.style import COLORS

# Local assets directory (Leaflet files downloaded here)
_ASSETS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'assets')
_ASSETS_URL = QUrl.fromLocalFile(os.path.abspath(_ASSETS_DIR) + '/')

# Speed threshold below which a sample is considered "hover" (m/s).
# Adaptive: max(MIN_CRUISE_SPD, max_speed * CRUISE_RATIO).
_MIN_CRUISE_SPD  = 1.0   # absolute floor
_CRUISE_RATIO    = 0.15  # 15 % of max speed


def _make_leaflet_html(
    home_lat: float,
    home_lng: float,
    max_one_way_m: float,
    max_rtl_m: float,
    track_points: list,
    initial_mode: str = "one_way",
) -> str:
    """
    Single range circle (blue) that switches between one-way and RTL radius
    based on the mode button. Right-click moves the home point.
    """
    track_json = json.dumps(track_points)
    mode_str   = json.dumps(initial_mode)

    c_border   = COLORS['border']
    c_success  = COLORS['success']
    c_accent   = COLORS['accent']      # blue  — max range

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<link rel="stylesheet" href="leaflet.css"/>
<script src="leaflet.js"></script>
<style>
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; width: 100%; height: 100%; background: {COLORS['bg_primary']}; overflow: hidden; }}
  #map {{ position: absolute; top: 0; left: 0; right: 0; bottom: 0; background: {COLORS['bg_primary']}; }}
  .leaflet-container {{ background: #1a2028; font-family: 'Segoe UI', sans-serif; }}
  .range-label {{
    background: {COLORS['bg_secondary']};
    border: 1px solid {c_border};
    border-radius: 6px;
    padding: 3px 9px;
    font-size: 12px;
    font-weight: bold;
    white-space: nowrap;
  }}
  .info-box {{
    background: {COLORS['bg_secondary']};
    color: {COLORS['text_primary']};
    border: 1px solid {c_border};
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 13px;
    line-height: 1.6;
  }}
  .legend {{
    background: {COLORS['bg_secondary']};
    border: 1px solid {c_border};
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 12px;
    color: {COLORS['text_primary']};
    line-height: 1.8;
    pointer-events: none;
  }}
  .dot {{ display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; vertical-align:middle; }}
  /* ── Layer switcher ── */
  .leaflet-control-layers {{
    background: {COLORS['bg_secondary']} !important;
    border: 1px solid {c_border} !important;
    border-radius: 8px !important;
    color: {COLORS['text_primary']} !important;
    box-shadow: 0 4px 12px rgba(0,0,0,0.6) !important;
  }}
  .leaflet-control-layers-toggle {{
    background-color: {COLORS['bg_secondary']} !important;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='%238B949E' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolygon points='12 2 2 7 12 12 22 7 12 2'/%3E%3Cpolyline points='2 17 12 22 22 17'/%3E%3Cpolyline points='2 12 12 17 22 12'/%3E%3C/svg%3E") !important;
    background-repeat: no-repeat !important;
    background-position: center !important;
    background-size: 20px 20px !important;
    border: 1px solid {c_border} !important;
    border-radius: 8px !important;
    width: 36px !important;
    height: 36px !important;
    transition: border-color 0.15s, background-color 0.15s;
  }}
  .leaflet-control-layers-toggle:hover {{
    background-color: {COLORS['bg_hover']} !important;
    border-color: {COLORS['accent']} !important;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='%2358A6FF' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolygon points='12 2 2 7 12 12 22 7 12 2'/%3E%3Cpolyline points='2 17 12 22 22 17'/%3E%3Cpolyline points='2 12 12 17 22 12'/%3E%3C/svg%3E") !important;
  }}
  .leaflet-control-layers-expanded .leaflet-control-layers-toggle {{
    display: none;
  }}
  .leaflet-control-layers-expanded {{
    padding: 10px 14px !important;
    font-size: 12px;
    min-width: 170px;
  }}
  .leaflet-control-layers-expanded::before {{
    content: 'Map Type';
    display: block;
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 0.08em;
    color: {COLORS['text_secondary']};
    text-transform: uppercase;
    margin-bottom: 8px;
  }}
  .leaflet-control-layers label {{
    display: flex; align-items: center; gap: 8px;
    padding: 5px 2px; cursor: pointer;
    color: {COLORS['text_primary']};
    border-radius: 4px;
  }}
  .leaflet-control-layers label:hover {{ color: {COLORS['accent']}; }}
  .leaflet-control-layers-separator {{
    border-top: 1px solid {c_border}; margin: 4px 0;
  }}
</style>
</head>
<body>
<div id="map"></div>
<script>
function setMapHeight() {{
  var h = window.innerHeight || document.documentElement.clientHeight || 600;
  document.getElementById('map').style.height = h + 'px';
}}
setMapHeight();
window.addEventListener('resize', setMapHeight);

var map = L.map('map', {{ zoomControl: true, attributionControl: false, center: [{home_lat}, {home_lng}], zoom: 10 }});

var _origHandleDOMEvent = map._handleDOMEvent.bind(map);
map._handleDOMEvent = function(e) {{
  if (e && e.type === 'contextmenu') return;
  _origHandleDOMEvent(e);
}};

var baseLayers = {{
  'Dark':             L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png',   {{ maxZoom: 19, subdomains: 'abcd' }}),
  'Street':           L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',                {{ maxZoom: 19 }}),
  'Google Satellite': L.tileLayer('https://mt{{s}}.google.com/vt/lyrs=s&x={{x}}&y={{y}}&z={{z}}',             {{ maxZoom: 20, subdomains: '0123' }}),
  'Google Hybrid':    L.tileLayer('https://mt{{s}}.google.com/vt/lyrs=y&x={{x}}&y={{y}}&z={{z}}',             {{ maxZoom: 20, subdomains: '0123' }}),
  'Google Street':    L.tileLayer('https://mt{{s}}.google.com/vt/lyrs=m&x={{x}}&y={{y}}&z={{z}}',             {{ maxZoom: 20, subdomains: '0123' }}),
}};
baseLayers['Dark'].addTo(map);
L.control.layers(baseLayers, {{}}, {{ position: 'topright', collapsed: true }}).addTo(map);

var homeLat        = {home_lat};
var homeLng        = {home_lng};
var maxOneWayM     = {max_one_way_m:.0f};
var maxRtlM        = {max_rtl_m:.0f};
var mode           = {mode_str};
var trackPts       = {track_json};

if (trackPts.length > 1) {{
  L.polyline(trackPts, {{ color: '#F85149', weight: 2, opacity: 0.85 }}).addTo(map);
}}

var homeIcon = L.divIcon({{
  html: '<div style="width:14px;height:14px;border-radius:50%;background:{c_success};border:2px solid #fff;"></div>',
  iconSize: [14,14], iconAnchor: [7,7], className: '',
}});
var homeMarker = L.marker([homeLat, homeLng], {{ icon: homeIcon }}).addTo(map);
homeMarker.bindPopup('<div class="info-box"><b>Home Point</b><br>Lat: ' + homeLat.toFixed(6) + '<br>Lng: ' + homeLng.toFixed(6) + '</div>');

function getMaxR() {{ return mode === 'one_way' ? maxOneWayM : maxRtlM; }}

// Max range circle (blue)
var maxCircle = L.circle([homeLat, homeLng], {{
  radius: getMaxR(), color: '{c_accent}', weight: 2,
  fillColor: '{c_accent}', fillOpacity: 0.07,
}}).addTo(map);

var maxLabel = L.marker([homeLat + getMaxR() / 111320, homeLng], {{
  icon: L.divIcon({{ html: '<div class="range-label" style="color:{c_accent}">' + (getMaxR()/1000).toFixed(2) + ' km</div>', className: '', iconSize: [0,0], iconAnchor: [44, 10] }}),
  interactive: false,
}}).addTo(map);

// Legend
var legend = L.control({{ position: 'bottomright' }});
legend.onAdd = function() {{
  var d = L.DomUtil.create('div', 'legend');
  d.innerHTML = '<span class="dot" style="background:{c_accent}"></span>Max Range';
  return d;
}};
legend.addTo(map);

function updateCircles() {{
  var mr = getMaxR();
  maxCircle.setLatLng([homeLat, homeLng]);
  maxCircle.setRadius(mr);
  maxLabel.setLatLng([homeLat + mr / 111320, homeLng]);
  maxLabel.setIcon(L.divIcon({{
    html: '<div class="range-label" style="color:{c_accent}">' + (mr/1000).toFixed(2) + ' km</div>',
    className: '', iconSize: [0,0], iconAnchor: [44, 10],
  }}));
}}

// ── Context menu ─────────────────────────────────────
// ── Context menu ─────────────────────────────────────
var ctxMenu = document.createElement('div');
ctxMenu.id = 'ctx-menu';
ctxMenu.style.cssText = 'display:none;position:fixed;z-index:9999;'
  + 'background:{COLORS["bg_secondary"]};border:1px solid {c_border};'
  + 'border-radius:8px;padding:4px;min-width:160px;'
  + 'box-shadow:0 4px 16px rgba(0,0,0,0.6);font-size:13px;';
// Stop clicks inside the menu from bubbling to the document dismiss listener
ctxMenu.addEventListener('click', function(e) {{ e.stopPropagation(); }});
document.body.appendChild(ctxMenu);

var _pendingLatLng = null;

function closeCtxMenu() {{ ctxMenu.style.display = 'none'; _pendingLatLng = null; }}

function addMenuItem(label, onclick) {{
  var item = document.createElement('div');
  item.style.cssText = 'padding:7px 14px;cursor:pointer;color:{COLORS["text_primary"]};border-radius:5px;';
  item.textContent = label;
  item.onmouseenter = function() {{ item.style.background='{COLORS["bg_hover"]}'; item.style.color='{COLORS["accent"]}'; }};
  item.onmouseleave = function() {{ item.style.background=''; item.style.color='{COLORS["text_primary"]}'; }};
  item.onclick = function() {{ onclick(); closeCtxMenu(); }};
  ctxMenu.appendChild(item);
}}

addMenuItem('Set Home Here', function() {{
  if (!_pendingLatLng) return;
  homeLat = _pendingLatLng.lat;
  homeLng = _pendingLatLng.lng;
  homeMarker.setLatLng(_pendingLatLng);
  homeMarker.setPopupContent('<div class="info-box"><b>Custom Home</b><br>Lat: '
    + homeLat.toFixed(6) + '<br>Lng: ' + homeLng.toFixed(6) + '</div>');
  updateCircles();
}});

addMenuItem('Reset to Original', function() {{
  homeLat = {home_lat};
  homeLng = {home_lng};
  var ll = L.latLng(homeLat, homeLng);
  homeMarker.setLatLng(ll);
  homeMarker.setPopupContent('<div class="info-box"><b>Home Point</b><br>Lat: '
    + homeLat.toFixed(6) + '<br>Lng: ' + homeLng.toFixed(6) + '</div>');
  updateCircles();
}});

document.getElementById('map').addEventListener('contextmenu', function(e) {{
  e.preventDefault();
  e.stopImmediatePropagation();
  try {{
    var rect = document.getElementById('map').getBoundingClientRect();
    var point = L.point(e.clientX - rect.left, e.clientY - rect.top);
    _pendingLatLng = map.containerPointToLatLng(point);
    ctxMenu.style.display = 'block';
    var mw = ctxMenu.offsetWidth || 170, mh = ctxMenu.offsetHeight || 70;
    var x = Math.min(e.clientX, window.innerWidth  - mw - 8);
    var y = Math.min(e.clientY, window.innerHeight - mh - 8);
    ctxMenu.style.left = x + 'px';
    ctxMenu.style.top  = y + 'px';
  }} catch(err) {{ console.log('contextmenu err:', err); }}
}}, true);

document.addEventListener('click',   closeCtxMenu);
document.addEventListener('keydown', function(e) {{ if (e.key === 'Escape') closeCtxMenu(); }});

function setMode(m) {{ mode = m; updateCircles(); }}

map.fitBounds(maxCircle.getBounds().pad(0.15));
setTimeout(function() {{ setMapHeight(); map.invalidateSize(); }}, 250);
setTimeout(function() {{ setMapHeight(); map.invalidateSize(); }}, 800);
</script>
</body>
</html>"""


class MaxRangeAnalyzerModule(BaseModule):
    MODULE_ID    = "max_range_analyzer"
    DISPLAY_NAME = "Max Range Analyzer"
    DESCRIPTION  = "Estimate maximum flight range based on actual log data"
    ICON_CHAR    = "◎"
    REQUIRED_MESSAGES = ["BAT", "GPS"]

    def __init__(self):
        self._widget   = None
        self._webview  = None
        self._log_data = None
        self._mode     = "one_way"

    # ── BaseModule interface ────────────────────────────────

    def build_widget(self, parent=None) -> QWidget:
        self._widget = QWidget(parent)
        self._widget.setStyleSheet(f"background-color: {COLORS['bg_primary']};")
        layout = QVBoxLayout(self._widget)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # ── Title + mode toggle ───────────────────────────────
        top = QHBoxLayout()
        title = QLabel("Max Range Analyzer")
        title.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {COLORS['text_primary']}; background: transparent;"
        )
        top.addWidget(title)
        top.addStretch()

        mode_lbl = QLabel("Mode:")
        mode_lbl.setStyleSheet(f"color: {COLORS['text_secondary']}; background: transparent;")
        top.addWidget(mode_lbl)

        self._btn_one_way = QPushButton("One Way")
        self._btn_one_way.setCheckable(True)
        self._btn_one_way.setChecked(True)
        self._btn_one_way.setFixedWidth(100)
        self._btn_one_way.clicked.connect(lambda: self._set_mode("one_way"))

        self._btn_rtl = QPushButton("Return to Launch")
        self._btn_rtl.setCheckable(True)
        self._btn_rtl.setFixedWidth(140)
        self._btn_rtl.clicked.connect(lambda: self._set_mode("rtl"))

        self._mode_group = QButtonGroup(self._widget)
        self._mode_group.addButton(self._btn_one_way)
        self._mode_group.addButton(self._btn_rtl)
        self._mode_group.setExclusive(True)

        top.addWidget(self._btn_one_way)
        top.addWidget(self._btn_rtl)
        layout.addLayout(top)

        # ── Stats bar ─────────────────────────────────────────
        stats_box = QGroupBox("Flight Statistics")
        stats_box.setStyleSheet(
            f"QGroupBox {{ background-color: {COLORS['bg_secondary']}; border: 1px solid {COLORS['border']};"
            f" border-radius: 8px; margin-top: 10px; padding-top: 8px;"
            f" color: {COLORS['text_secondary']}; font-size: 11px; }}"
        )
        stats_grid = QHBoxLayout(stats_box)
        stats_grid.setContentsMargins(16, 8, 16, 12)
        stats_grid.setSpacing(24)

        self._stat_labels = {}
        stats_def = [
            ("battery_cap",    "Battery Cap",    "—"),
            ("cruise_speed",   "Cruise Speed",   "—"),
            ("cruise_current", "Cruise Current", "—"),
            ("max_range",      "Max Range",      "—"),
        ]
        accent_keys = {"max_range"}

        for key, lbl_text, default in stats_def:
            col = QVBoxLayout()
            col.setSpacing(2)
            k = QLabel(lbl_text.upper())
            k.setStyleSheet(
                f"color: {COLORS['text_secondary']}; font-size: 10px; font-weight: bold; background: transparent;"
            )
            v = QLabel(default)
            color = COLORS['accent'] if key in accent_keys else COLORS['text_primary']
            v.setStyleSheet(
                f"color: {color}; font-size: {'18' if key in accent_keys else '16'}px;"
                f" font-weight: bold; background: transparent;"
            )
            col.addWidget(k)
            col.addWidget(v)
            stats_grid.addLayout(col)
            self._stat_labels[key] = v

        layout.addWidget(stats_box)

        # ── Map ───────────────────────────────────────────────
        self._webview = QWebEngineView()
        self._webview.setMinimumHeight(300)
        self._webview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self._webview, 1)

        # Note
        self._note = QLabel("")
        self._note.setStyleSheet(
            f"color: {COLORS['text_disabled']}; font-size: 11px; background: transparent;"
        )
        self._note.setWordWrap(True)
        layout.addWidget(self._note)

        self._webview.setHtml(self._placeholder_html())
        return self._widget

    def load_data(self, log_data) -> None:
        self._log_data = log_data
        self._compute_and_render()

    def clear(self) -> None:
        self._log_data = None
        for v in self._stat_labels.values():
            v.setText("—")
        self._note.setText("")
        if self._webview:
            self._webview.setHtml(self._placeholder_html())

    # ── Internal ────────────────────────────────────────────

    def _set_mode(self, mode: str):
        self._mode = mode
        if self._webview:
            self._webview.page().runJavaScript(f"setMode('{mode}');")

    def _compute_and_render(self):
        log = self._log_data
        if log is None:
            return

        # ── Battery capacity ──────────────────────────────────
        batt_cap_mah = log.params.get('BATT_CAPACITY', 0.0)
        if batt_cap_mah <= 0 and 'CurrTot' in log.bat and len(log.bat['CurrTot']) > 0:
            batt_cap_mah = float(log.bat['CurrTot'][-1])  # CurrTot is already in mAh

        # ── mAh consumed during armed flight ─────────────────
        # Prefer CurrTot delta; fall back to trapezoidal integration.
        mah_consumed = 0.0
        if 'TimeUS' in log.bat and 'Curr' in log.bat and len(log.bat['Curr']) > 1:
            bat_t    = log.bat['TimeUS'].astype(np.float64)
            bat_curr = log.bat['Curr'].astype(np.float64)
            lo = float(log.arm_time_us)
            hi = float(log.disarm_time_us) if log.disarm_time_us > log.arm_time_us else bat_t[-1]

            if 'CurrTot' in log.bat and log.arm_time_us > 0:
                currtot = log.bat['CurrTot'].astype(np.float64)  # already mAh
                ct_arm  = float(np.interp(lo, bat_t, currtot))
                ct_dis  = float(np.interp(hi, bat_t, currtot))
                mah_consumed = ct_dis - ct_arm
            else:
                mask_arm = (bat_t >= lo) & (bat_t <= hi)
                t_seg  = bat_t[mask_arm]
                c_seg  = bat_curr[mask_arm]
                if len(t_seg) > 1:
                    dt_s = np.diff(t_seg) / 1e6
                    mah_consumed = float(((c_seg[:-1] + c_seg[1:]) / 2 * dt_s).sum()
                                        * 1000.0 / 3600.0)

        # ── GPS data setup ────────────────────────────────────
        has_gps = ('Spd' in log.gps and 'TimeUS' in log.gps
                   and len(log.gps['Spd']) > 5)

        avg_cruise_speed   = 0.0
        avg_cruise_current = 0.0
        cruise_threshold   = _MIN_CRUISE_SPD

        if has_gps:
            gps_t   = log.gps['TimeUS'].astype(np.float64)
            gps_spd = log.gps['Spd'].astype(np.float64)
            status  = log.gps.get('Status', np.ones(len(gps_t)))
            fix_mask = status >= 3

            lo = float(log.arm_time_us)
            hi = float(log.disarm_time_us) if log.disarm_time_us > log.arm_time_us else gps_t[-1]
            armed_mask = (gps_t >= lo) & (gps_t <= hi)
            valid_mask = fix_mask & armed_mask

            if valid_mask.any():
                max_spd = float(gps_spd[valid_mask].max())
                cruise_threshold = max(_MIN_CRUISE_SPD, max_spd * _CRUISE_RATIO)
                cruise_mask = valid_mask & (gps_spd >= cruise_threshold)

                if cruise_mask.sum() >= 5:
                    avg_cruise_speed = float(gps_spd[cruise_mask].mean())

                    # Interpolate BAT current at GPS timestamps → cruise average
                    if 'TimeUS' in log.bat and 'Curr' in log.bat and len(log.bat['Curr']) > 1:
                        bat_t    = log.bat['TimeUS'].astype(np.float64)
                        bat_curr = log.bat['Curr'].astype(np.float64)
                        curr_at_gps = np.interp(gps_t, bat_t, bat_curr)
                        avg_cruise_current = float(curr_at_gps[cruise_mask].mean())
                else:
                    # Not enough cruise data — fall back to full armed averages
                    avg_cruise_speed = float(gps_spd[valid_mask].mean())

        # Full armed average current (fallback for cruise current)
        avg_current_a = 0.0
        if 'Curr' in log.bat and len(log.bat['Curr']) > 0:
            curr = log.bat['Curr'].astype(np.float64)
            if log.arm_time_us > 0 and 'TimeUS' in log.bat:
                bat_t = log.bat['TimeUS']
                lo = log.arm_time_us
                hi = log.disarm_time_us if log.disarm_time_us > lo else bat_t[-1]
                mask = (bat_t >= lo) & (bat_t <= hi)
                armed = curr[mask]
                avg_current_a = float(armed.mean()) if len(armed) > 0 else float(curr.mean())
            else:
                avg_current_a = float(curr.mean())

        if avg_cruise_current <= 0.1:
            avg_cruise_current = avg_current_a

        # ── Option A — Max Range (cruise only) ────────────────
        max_one_way_m = 0.0
        max_rtl_m     = 0.0
        if avg_cruise_current > 0.5 and batt_cap_mah > 0 and avg_cruise_speed > 0:
            cruise_time_s = (batt_cap_mah / 1000.0) / avg_cruise_current * 3600.0
            max_one_way_m = cruise_time_s * avg_cruise_speed
            max_rtl_m     = max_one_way_m / 2.0

        # ── Update stats ──────────────────────────────────────
        self._stat_labels["battery_cap"].setText(
            f"{batt_cap_mah:.0f} mAh" if batt_cap_mah > 0 else "Unknown"
        )
        self._stat_labels["cruise_speed"].setText(
            f"{avg_cruise_speed:.1f} m/s" if avg_cruise_speed > 0 else "—"
        )
        self._stat_labels["cruise_current"].setText(
            f"{avg_cruise_current:.1f} A" if avg_cruise_current > 0 else "—"
        )
        self._stat_labels["max_range"].setText(
            f"{max_one_way_m / 1000:.2f} km" if max_one_way_m > 0 else "—"
        )

        # ── Note ─────────────────────────────────────────────
        parts = []
        if max_one_way_m > 0:
            parts.append(
                f"Max Range: cruise speed {avg_cruise_speed:.1f} m/s at {avg_cruise_current:.1f} A "
                f"(threshold ≥ {cruise_threshold:.1f} m/s)."
            )
        parts.append("Right-click map to set a custom home point.")
        self._note.setText("  " + "  ".join(parts))

        # ── Build GPS track ───────────────────────────────────
        track_points = []
        if 'Lat' in log.gps and len(log.gps['Lat']) > 0:
            status = log.gps.get('Status', np.ones(len(log.gps['Lat'])))
            mask = status >= 3
            lats = log.gps['Lat'][mask]
            lngs = log.gps['Lng'][mask]
            step = max(1, len(lats) // 2000)
            track_points = [[float(lats[i]), float(lngs[i])]
                            for i in range(0, len(lats), step)]

        home_lat = log.home_lat if log.home_lat != 0 else (track_points[0][0] if track_points else 51.5)
        home_lng = log.home_lng if log.home_lng != 0 else (track_points[0][1] if track_points else -0.1)

        if max_one_way_m > 0:
            html = _make_leaflet_html(
                home_lat, home_lng,
                max_one_way_m, max_rtl_m,
                track_points, self._mode,
            )
            self._webview.setHtml(html, _ASSETS_URL)
        else:
            self._webview.setHtml(self._placeholder_html("Insufficient data to calculate range."))

    @staticmethod
    def _placeholder_html(msg: str = "Load a log to see range analysis.") -> str:
        return f"""<!DOCTYPE html><html>
<body style="margin:0;background:{COLORS['bg_primary']};display:flex;align-items:center;
justify-content:center;height:100vh;font-family:'Segoe UI',sans-serif;
color:{COLORS['text_disabled']};font-size:16px;">{msg}</body></html>"""
