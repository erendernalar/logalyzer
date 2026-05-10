import json
import os
import math
import base64
import urllib.request
import numpy as np
import pyqtgraph as pg
from concurrent.futures import ThreadPoolExecutor

from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QTreeWidget,
    QTreeWidgetItem, QLabel, QSizePolicy, QPushButton,
)
from PyQt5.QtCore import Qt, QPoint, QUrl, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWebEngineWidgets import QWebEngineView

from app.modules.base_module import BaseModule
from app.modules.waypoints_3d_shared import waypoints_to_enu, WAYPOINTS_JS
from app.theme.style import COLORS

# ── Asset paths ─────────────────────────────────────────────
_ASSETS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'assets'))
_TMP_HTML   = os.path.join(_ASSETS_DIR, '_log_inspector.html')
_TILE_RES   = 64

# ── Multi-axis layout columns ────────────────────────────────
# Extra left axes occupy cols 0 .. (_MAIN_LEFT_COL-1); supports up to 5 extra axes.
_MAIN_LEFT_COL = 5
_VB_COL        = 6

# ── Per-mode GPS track colors ────────────────────────────────
_MODE_COLORS = {
    # ── ArduPlane ──────────────────────────────────────────
    'MANUAL':       '#E3B341',   # amber
    'CIRCLE':       '#FF7B72',   # pink
    'STABILIZE':    '#58A6FF',   # blue
    'TRAINING':     '#79C0FF',   # light blue
    'ACRO':         '#FFA657',   # orange
    'FBWA':         '#58A6FF',   # blue
    'FBWB':         '#79C0FF',   # light blue
    'CRUISE':       '#39C5CF',   # cyan
    'AUTOTUNE':     '#D2A8FF',   # lavender
    'AUTO':         '#3FB950',   # green
    'RTL':          '#F85149',   # red
    'LOITER':       '#FF9F43',   # warm orange
    'TAKEOFF':      '#56D364',   # bright green
    'AVOID_ADSB':   '#FF7B72',   # pink
    'GUIDED':       '#BC8CFF',   # purple
    'INITIALISING': '#8B949E',   # grey
    'THERMAL':      '#E3B341',   # amber
    'LOITER_ALT_QLAND': '#F0883E',
    # ── Q-modes (VTOL) ────────────────────────────────────
    'QSTABILIZE':   '#56D364',   # bright green
    'QHOVER':       '#3FB950',   # green
    'QLOITER':      '#2EA043',   # dark green
    'QLAND':        '#F0883E',   # orange
    'QRTL':         '#F85149',   # red
    'QAUTOTUNE':    '#D2A8FF',   # lavender
    'QACRO':        '#BC8CFF',   # purple
    'QBRAKE':       '#FF7B72',   # pink
    # ── ArduCopter ────────────────────────────────────────
    'ALT_HOLD':     '#79C0FF',   # light blue
    'POSHOLD':      '#39C5CF',   # cyan
    'LAND':         '#F0883E',   # orange
    'DRIFT':        '#FFA657',   # orange
    'SPORT':        '#FF9F43',   # warm orange
    'FLIP':         '#FF7B72',   # pink
    'BRAKE':        '#F85149',   # red
    'THROW':        '#E3B341',   # amber
    'SMART_RTL':    '#F85149',   # red
    'FOLLOW':       '#BC8CFF',   # purple
    'ZIGZAG':       '#39C5CF',   # cyan
    'SYSTEMID':     '#8B949E',   # grey
    'AUTOROTATE':   '#D2A8FF',   # lavender
    'AUTO_RTL':     '#F85149',   # red
    # ── Fallback ──────────────────────────────────────────
    'UNKNOWN':      '#8B949E',   # grey
}
_DEFAULT_MODE_COLOR = '#8B949E'

# ── Color palette for graph lines ────────────────────────────
_PALETTE = [
    '#58A6FF', '#F0883E', '#3FB950', '#F85149',
    '#BC8CFF', '#39C5CF', '#E3B341', '#FF7B72',
]

# ── Field group definitions ──────────────────────────────────
_FIELD_GROUPS = [
    ('Attitude',         'att',  [('Roll', 'Roll °'), ('Pitch', 'Pitch °'), ('Yaw', 'Yaw °')]),
    ('Desired Attitude', 'att',  [('DesRoll', 'Des Roll °'), ('DesPitch', 'Des Pitch °'), ('DesYaw', 'Des Yaw °')]),
    ('Altitude',         'pos',  [('RelHomeAlt', 'Rel Home Alt (m)')]),
    ('Barometer',        'baro', [('Alt', 'Baro Alt (m)'), ('AltAMSL', 'Alt AMSL (m)'), ('Temp', 'Baro Temp (°C)')]),
    ('Airspeed',         'arsp', [('Airspeed', 'Airspeed (m/s)')]),
    ('GPS',              'gps',  [('Spd', 'GPS Speed (m/s)'), ('Alt', 'GPS Alt (m)')]),
    ('GPS Quality',      'gps',  [('NSats', 'Satellites'), ('HDop', 'HDOP')]),
    ('Battery',          'bat',  [('Volt', 'Voltage (V)'), ('Curr', 'Current (A)'), ('CurrTot', 'Curr Total (mAh)')]),
    ('Battery %',        'bat',  [('RemPct', 'Remaining (%)'), ('EnrgTot', 'Energy Total (Wh)')]),
    ('Throttle',         'motb', [('ThrOut', 'Throttle Out (0-1)'), ('ThLimit', 'Thr Limit (0-1)')]),
    ('Vibration',        'vibe', [('VibeX', 'Vibe X'), ('VibeY', 'Vibe Y'), ('VibeZ', 'Vibe Z')]),
    ('IMU Accel',        'imu',  [('AccX', 'Accel X (m/s2)'), ('AccY', 'Accel Y (m/s2)'), ('AccZ', 'Accel Z (m/s2)')]),
    ('IMU Gyro',         'imu',  [('GyrX', 'Gyro X (rad/s)'), ('GyrY', 'Gyro Y (rad/s)'), ('GyrZ', 'Gyro Z (rad/s)')]),
    ('RC Input',         'rcin', [('C1', 'Ch 1'), ('C2', 'Ch 2'), ('C3', 'Ch 3'),
                                   ('C4', 'Ch 4'), ('C5', 'Ch 5'), ('C6', 'Ch 6'),
                                   ('C7', 'Ch 7'), ('C8', 'Ch 8'), ('C9', 'Ch 9'),
                                   ('C10', 'Ch 10'), ('C11', 'Ch 11'), ('C12', 'Ch 12'),
                                   ('C13', 'Ch 13'), ('C14', 'Ch 14')]),
    ('RC Output',        'rcou', [('C1', 'Ch 1'), ('C2', 'Ch 2'), ('C3', 'Ch 3'),
                                   ('C4', 'Ch 4'), ('C5', 'Ch 5'), ('C6', 'Ch 6'),
                                   ('C7', 'Ch 7'), ('C8', 'Ch 8'), ('C9', 'Ch 9'),
                                   ('C10', 'Ch 10'), ('C11', 'Ch 11'), ('C12', 'Ch 12'),
                                   ('C13', 'Ch 13'), ('C14', 'Ch 14')]),
    ('VTUN (VTOL)',       'qtun', [('Tilt', 'Tilt °'), ('Dsired', 'Des Tilt °'), ('Ang', 'Ang °'), ('Dist', 'Dist (m)')]),
]


# ── Tile helpers ─────────────────────────────────────────────

def _lat_lng_to_tile(lat, lng, zoom):
    n = 2 ** zoom
    tx = int((lng + 180) / 360 * n)
    lat_r = math.radians(lat)
    ty = int((1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * n)
    return tx, max(0, ty)


def _tile_bounds(tx, ty, zoom):
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


def _mode_color(mode_name: str) -> str:
    return _MODE_COLORS.get(mode_name.upper(), _DEFAULT_MODE_COLOR)


def _mode_colors(time_us_arr, events):
    colors = [_DEFAULT_MODE_COLOR] * len(time_us_arr)
    bps = sorted(
        [(e.time_us, _mode_color(e.detail.replace('Mode: ', '').strip()))
         for e in events if e.event_type == 'mode_change'],
        key=lambda x: x[0]
    )
    if not bps:
        return colors
    bi, cur = 0, _DEFAULT_MODE_COLOR
    for i, t in enumerate(time_us_arr):
        while bi < len(bps) and bps[bi][0] <= t:
            cur = bps[bi][1]; bi += 1
        colors[i] = cur
    return colors


def _legend_items(events) -> list:
    """Return [[mode_name, hex_color], ...] for each unique mode seen in the flight.
    Returns an empty list when no mode changes occurred (nothing to show)."""
    mode_events = [e for e in events if e.event_type == 'mode_change']
    if not mode_events:
        return []
    seen, items = set(), []
    for e in mode_events:
        name = e.detail.replace('Mode: ', '').strip()
        if name not in seen:
            seen.add(name)
            items.append([name, _mode_color(name)])
    # Only show legend when there is actually more than one mode
    return items if len(items) > 1 else []


def _hex_rgb(h):
    h = h.lstrip('#')
    return [int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255]


# ── Background HTML builder ──────────────────────────────────

class _HtmlBuilder(QThread):
    ready = pyqtSignal()
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


# ── Draggable stats overlay ──────────────────────────────────

class _StatsBox(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self._drag_pos = None
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"background-color:{COLORS['bg_secondary']}F0;"
            f" border:1px solid {COLORS['border']};"
            f" border-radius:6px;"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        self._label = QLabel()
        self._label.setTextFormat(Qt.RichText)
        self._label.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self._label)
        self.setVisible(False)
        self.setCursor(Qt.SizeAllCursor)

    def set_content(self, html: str):
        self._label.setText(html)
        self._label.adjustSize()
        self.adjustSize()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.mapToGlobal(QPoint(0, 0))
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos is not None:
            new_pos = self.parent().mapFromGlobal(event.globalPos() - self._drag_pos)
            p = self.parent()
            x = max(0, min(new_pos.x(), p.width()  - self.width()))
            y = max(0, min(new_pos.y(), p.height() - self.height()))
            self.move(x, y)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None


# ── Module ───────────────────────────────────────────────────

class LogInspectorModule(BaseModule):

    MODULE_ID         = 'log_inspector'
    DISPLAY_NAME      = 'Log Inspector'
    DESCRIPTION       = 'Interactive graphs with synchronized 3D flight view'
    ICON_CHAR         = '⊞'
    REQUIRED_MESSAGES = ['POS']

    def __init__(self):
        self._widget    = None
        self._log_data  = None
        self._t0_us     = 0
        self._hover_vline  = None
        self._hover_label  = None
        self._plot_items: dict = {}
        self._color_idx: int   = 0
        self._region    = None
        self._legend    = None
        self._plot      = None
        self._tree      = None
        self._view      = None
        self._map_ready     = False
        self._builder       = None
        self._time_cursor   = None   # InfiniteLine on the graph
        self._stats_box     = None
        self._stats_visible = False

    # ── BaseModule interface ─────────────────────────────────

    def build_widget(self, parent: QWidget = None) -> QWidget:
        self._widget = QWidget(parent)
        self._widget.setStyleSheet(f"background-color: {COLORS['bg_primary']};")

        outer = QHBoxLayout(self._widget)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Left: channel selector ───────────────────────────
        left = QWidget()
        left.setFixedWidth(210)
        left.setStyleSheet(
            f"background-color: {COLORS['bg_secondary']};"
            f" border-right: 1px solid {COLORS['border']};"
        )
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)

        hdr_widget = QWidget()
        hdr_widget.setFixedHeight(36)
        hdr_widget.setStyleSheet(
            f"background-color: {COLORS['bg_secondary']};"
            f" border-bottom: 1px solid {COLORS['border']};"
        )
        hdr_row = QHBoxLayout(hdr_widget)
        hdr_row.setContentsMargins(8, 0, 6, 0)

        hdr_lbl = QLabel("CHANNELS")
        hdr_lbl.setStyleSheet(
            f"color: {COLORS['text_disabled']}; font-size: 10px; font-weight: bold;"
            " border: none;"
        )
        hdr_row.addWidget(hdr_lbl)
        hdr_row.addStretch()

        self._stats_btn = QPushButton("SUMMARY")
        self._stats_btn.setCheckable(True)
        self._stats_btn.setFixedHeight(22)
        self._stats_btn.setStyleSheet(
            f"QPushButton {{ background:{COLORS['bg_tertiary']}; color:{COLORS['text_disabled']};"
            f" border:1px solid {COLORS['border']}; border-radius:3px;"
            f" font-size:9px; font-weight:bold; padding:0 6px; }}"
            f"QPushButton:hover {{ color:{COLORS['text_secondary']}; }}"
            f"QPushButton:checked {{ background:{COLORS['border_active']}22;"
            f" color:{COLORS['border_active']}; border-color:{COLORS['border_active']}; }}"
        )
        self._stats_btn.toggled.connect(self._toggle_stats)
        hdr_row.addWidget(self._stats_btn)

        ll.addWidget(hdr_widget)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setRootIsDecorated(True)
        self._tree.setIndentation(16)
        f = QFont(); f.setPointSize(10)
        self._tree.setFont(f)
        self._tree.setStyleSheet(
            f"QTreeWidget {{ background:{COLORS['bg_secondary']}; border:none;"
            f" color:{COLORS['text_secondary']}; }}"
            f"QTreeWidget::item {{ height:28px; padding-left:4px; }}"
            f"QTreeWidget::item:hover {{ background:{COLORS['bg_hover']}; }}"
            f"QTreeWidget::item:selected {{ background:{COLORS['bg_tertiary']}; }}"
            "QTreeWidget::branch { background:transparent; }"
            f"QTreeWidget::indicator {{ width:14px; height:14px;"
            f" border:1px solid {COLORS['border']}; border-radius:3px;"
            f" background:{COLORS['bg_tertiary']}; }}"
            f"QTreeWidget::indicator:hover {{ border-color:{COLORS['border_active']}; }}"
            f"QTreeWidget::indicator:checked {{ background:{COLORS['border_active']};"
            f" border-color:{COLORS['border_active']}; }}"
        )
        self._tree.setExpandsOnDoubleClick(False)
        self._tree.itemClicked.connect(self._on_item_clicked)
        self._tree.itemChanged.connect(self._on_item_changed)
        self._tree.itemExpanded.connect(self._on_group_expanded)
        self._tree.itemCollapsed.connect(self._on_group_collapsed)
        ll.addWidget(self._tree, 1)
        outer.addWidget(left)

        # ── Right: graph (top) + 3D view (bottom) ────────────
        right = QSplitter(Qt.Vertical)
        right.setHandleWidth(4)
        right.setStyleSheet(f"QSplitter::handle {{ background-color:{COLORS['border']}; }}")

        pg.setConfigOption('background', COLORS['bg_primary'])
        pg.setConfigOption('foreground', COLORS['text_secondary'])
        self._plot = pg.PlotWidget()
        self._plot.setMinimumHeight(120)
        self._plot.showGrid(x=True, y=True, alpha=0.2)
        self._plot.getAxis('bottom').setLabel('Time (s)')
        self._plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._legend = self._plot.addLegend(offset=(10, 10))
        self._legend.setBrush(pg.mkBrush(COLORS['bg_secondary'] + 'CC'))
        self._legend.setPen(pg.mkPen(COLORS['border']))

        # Shift the PlotItem layout to make room for extra left-side Y axes.
        # Default pyqtgraph layout: left=col0, vb=col1, right=col2, bottom=(3,1).
        # We move everything right so extra axes can occupy cols 0..(_MAIN_LEFT_COL-1).
        _pi = self._plot.plotItem
        for _ax_name in ('left', 'right', 'bottom', 'top'):
            _pi.layout.removeItem(_pi.getAxis(_ax_name))
        _pi.layout.removeItem(_pi.vb)
        _pi.layout.addItem(_pi.getAxis('left'),   2, _MAIN_LEFT_COL)
        _pi.layout.addItem(_pi.vb,                2, _VB_COL)
        _pi.layout.addItem(_pi.getAxis('right'),  2, _VB_COL + 1)
        _pi.layout.addItem(_pi.getAxis('bottom'), 3, _VB_COL)
        _pi.layout.addItem(_pi.getAxis('top'),    1, _VB_COL)
        # pyqtgraph initialises cols 0-2 with stretch=1 and then sets col1=100.
        # After moving items to higher columns, zero out the old cols so they
        # don't compete for space, and give all stretch to the new VB column.
        for _col in range(_MAIN_LEFT_COL):
            _pi.layout.setColumnStretchFactor(_col, 0)
            _pi.layout.setColumnMinimumWidth(_col, 0)
            _pi.layout.setColumnPreferredWidth(_col, 0)
        _pi.layout.setColumnStretchFactor(_VB_COL, 100)

        # Patch main ViewBox so Y changes propagate proportionally to extra ViewBoxes.
        _main_vb = self._plot.plotItem.vb

        _orig_auto_range = _main_vb.autoRange
        def _patched_auto_range(*args, **kwargs):
            _orig_auto_range(*args, **kwargs)
            for _, (_, _, extra_vb, _) in self._plot_items.items():
                if extra_vb is not None:
                    extra_vb.autoRange()
        _main_vb.autoRange = _patched_auto_range

        _orig_wheel = _main_vb.wheelEvent
        def _patched_wheel(ev, axis=None):
            y_before = _main_vb.viewRange()[1]
            _orig_wheel(ev, axis)
            # axis is not None when called from AxisItem → only move this one curve
            if axis is None:
                self._propagate_y_change(y_before, _main_vb.viewRange()[1])
        _main_vb.wheelEvent = _patched_wheel

        self._plot.scene().sigMouseClicked.connect(self._on_graph_click)
        # Left-drag on the plot defines the selection region directly.
        self._plot.plotItem.vb.mouseDragEvent = self._on_plot_drag
        self._plot.scene().sigMouseMoved.connect(self._on_mouse_move)
        self._plot.plotItem.vb.sigResized.connect(self._update_extra_views)

        # Floating tooltip label — parented to the plot viewport so it overlays the graph
        bg  = COLORS['bg_secondary']
        brd = COLORS['border']
        self._hover_label = QLabel(self._plot.viewport())
        self._hover_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._hover_label.setStyleSheet(
            f"background-color:{bg}EE; border:1px solid {brd};"
            f" padding:5px 8px; border-radius:5px;"
        )
        self._hover_label.setVisible(False)

        # Wrap the plot in a plain QWidget so the stats box (a sibling, not a
        # child of the QGraphicsView viewport) receives mouse events normally.
        plot_container = QWidget()
        pc_layout = QVBoxLayout(plot_container)
        pc_layout.setContentsMargins(0, 0, 0, 0)
        pc_layout.setSpacing(0)
        pc_layout.addWidget(self._build_graph_toolbar())
        pc_layout.addWidget(self._plot)

        self._stats_box = _StatsBox(plot_container)
        self._stats_box.move(10, 40)

        right.addWidget(plot_container)

        self._view = QWebEngineView()
        self._view.setMinimumHeight(80)
        self._view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._view.loadFinished.connect(self._on_map_loaded)
        # Push-based time sync: JS sets document.title to "t:<seconds>" on every Nth frame
        self._view.page().titleChanged.connect(self._on_title_changed)
        self._view.setHtml(
            f"<html><body style='background:{COLORS['bg_primary']};margin:0'></body></html>"
        )
        right.addWidget(self._view)

        right.setSizes([380, 420])
        right.setCollapsible(0, False)
        right.setCollapsible(1, False)
        outer.addWidget(right, 1)

        return self._widget

    def load_data(self, log_data) -> None:
        self._log_data  = log_data
        self._color_idx = 0
        self._map_ready = False

        pos = getattr(log_data, 'pos', {})
        if 'TimeUS' not in pos or len(pos['TimeUS']) == 0:
            return

        self._t0_us = int(pos['TimeUS'][0])

        # ── Rebuild tree ──────────────────────────────────────
        self._clear_extra_axes()
        self._plot_items.clear()
        self._tree.blockSignals(True)
        self._tree.clear()

        for group_label, attr, fields in _FIELD_GROUPS:
            msg = getattr(log_data, attr, {})
            if not msg or 'TimeUS' not in msg or len(msg['TimeUS']) == 0:
                continue
            available = [(fk, fn) for fk, fn in fields
                         if fk in msg and len(msg[fk]) > 0]
            if not available:
                continue

            grp = QTreeWidgetItem([f"  ▸  {group_label}"])
            grp.setFlags(grp.flags() & ~Qt.ItemIsUserCheckable)
            gf = QFont(); gf.setBold(True); gf.setPointSize(10)
            grp.setFont(0, gf)
            grp.setForeground(0, QColor(COLORS['text_primary']))

            for fk, fn in available:
                child = QTreeWidgetItem([f"   {fn}"])
                child.setData(0, Qt.UserRole, (attr, fk, fn))
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Unchecked)
                grp.addChild(child)

            self._tree.addTopLevelItem(grp)
            grp.setExpanded(False)

        self._tree.blockSignals(False)

        # ── Reset plot & region ───────────────────────────────
        self._plot.clear()
        self._legend = self._plot.addLegend(offset=(10, 10))
        self._legend.setBrush(pg.mkBrush(COLORS['bg_secondary'] + 'CC'))
        self._legend.setPen(pg.mkPen(COLORS['border']))

        dur = log_data.duration_seconds
        self._region = pg.LinearRegionItem(
            values=[0, dur],
            brush=pg.mkBrush('#58A6FF22'),
            pen=pg.mkPen('#58A6FF', width=1),
            swapMode='block',
        )
        self._region.setZValue(10)
        self._region.sigRegionChangeFinished.connect(self._on_region_changed)
        # Take the region body out of mouse-button routing entirely so left-drag
        # inside an existing selection falls through to the ViewBox handler.
        # Edge InfiniteLine children keep their own accepted buttons → still draggable.
        self._region.setAcceptedMouseButtons(Qt.NoButton)
        self._region.movable = False
        self._plot.addItem(self._region)

        # Time cursor — orange vertical line showing current 3D playback position
        self._time_cursor = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen('#F0883E', width=1.5, style=Qt.DashLine),
        )
        self._time_cursor.setZValue(20)
        self._plot.addItem(self._time_cursor)

        self._hover_vline = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen(COLORS['text_secondary'], width=1, style=Qt.DashLine),
        )
        self._hover_vline.setZValue(15)
        self._hover_vline.setVisible(False)
        self._plot.addItem(self._hover_vline)

        self._plot.setXRange(0, dur, padding=0.02)

        # ── Build 3D view ─────────────────────────────────────
        self._map_ready = False
        self._view.setHtml(self._loading_html())

        if self._builder and self._builder.isRunning():
            self._builder.terminate()
            self._builder.wait()

        self._builder = _HtmlBuilder(log_data, self._make_3d_html, _TMP_HTML)
        self._builder.ready.connect(self._on_html_ready)
        self._builder.error.connect(self._on_build_error)
        self._builder.start()

    def clear(self) -> None:
        if self._builder and self._builder.isRunning():
            self._builder.terminate()
            self._builder.wait()
        if self._plot:
            self._clear_extra_axes()
            self._plot_items.clear()
            self._plot.clear()
            self._legend = self._plot.addLegend(offset=(10, 10))
            self._legend.setBrush(pg.mkBrush(COLORS['bg_secondary'] + 'CC'))
            self._legend.setPen(pg.mkPen(COLORS['border']))
        if self._tree:
            self._tree.blockSignals(True)
            self._tree.clear()
            self._tree.blockSignals(False)
        self._plot_items.clear()
        self._color_idx = 0
        self._region    = None
        self._log_data  = None
        self._hover_vline = None
        if self._hover_label:
            self._hover_label.setVisible(False)
        self._map_ready = False
        if self._view:
            self._view.setHtml(
                f"<html><body style='background:{COLORS['bg_primary']};margin:0'></body></html>"
            )

    # ── 3D view lifecycle ────────────────────────────────────

    def _loading_html(self):
        return (
            f'<!DOCTYPE html><html><body style="background:{COLORS["bg_primary"]};'
            f'color:#58A6FF;display:flex;flex-direction:column;align-items:center;'
            f'justify-content:center;height:100vh;margin:0;font-family:monospace;gap:14px;">'
            f'<div style="font-size:20px">⊞ Log Inspector</div>'
            f'<div style="font-size:13px;color:#8B949E">Fetching terrain &amp; satellite tiles…</div>'
            f'</body></html>'
        )

    def _on_html_ready(self):
        self._view.load(QUrl.fromLocalFile(_TMP_HTML))

    def _on_build_error(self, msg):
        self._view.setHtml(
            f'<html><body style="background:{COLORS["bg_primary"]};color:{COLORS["text_secondary"]};'
            f'display:flex;align-items:center;justify-content:center;height:100vh;margin:0;'
            f'font-family:sans-serif;font-size:15px;">Build error: {msg}</body></html>'
        )

    def _on_map_loaded(self, ok: bool):
        if self._view.url() != QUrl.fromLocalFile(_TMP_HTML):
            return
        self._map_ready = ok
        if ok and self._region:
            self._on_region_changed()

    # ── 3D HTML generation ──────────────────────────────────

    def _make_3d_html(self, log_data) -> str:
        pos = log_data.pos
        att = log_data.att
        if 'Lat' not in pos or len(pos.get('Lat', [])) == 0:
            return self._empty_html('No POS data in this log.')

        n      = len(pos['Lat'])
        stride = max(1, n // 4000)
        idx    = np.arange(0, n, stride)
        lats   = pos['Lat'][idx].astype(float)
        lngs   = pos['Lng'][idx].astype(float)
        alts   = (pos['RelHomeAlt'][idx].astype(float)
                  if 'RelHomeAlt' in pos else np.zeros(len(idx)))
        times  = pos['TimeUS'][idx].astype(float)

        home_lat = float(log_data.home_lat) if log_data.home_lat != 0 else float(lats[0])
        home_lng = float(log_data.home_lng) if log_data.home_lng != 0 else float(lngs[0])

        R     = 6_371_000.0
        east  = R * np.cos(np.radians(home_lat)) * np.radians(lngs - home_lng)
        north = R * np.radians(lats - home_lat)
        pts   = [[round(float(east[i]), 2), round(float(alts[i]), 2),
                  round(-float(north[i]), 2)] for i in range(len(lats))]

        # ATT
        has_att = 'Roll' in att and len(att.get('Roll', [])) > 0
        if has_att:
            at      = att['TimeUS'].astype(float)
            rolls_d  = np.interp(times, at, att['Roll'].astype(float)).tolist()
            pitches_d = np.interp(times, at, att['Pitch'].astype(float)).tolist()
            yaws_d   = np.interp(times, at, att['Yaw'].astype(float)).tolist()
        else:
            z = [0.0] * len(lats)
            rolls_d = pitches_d = yaws_d = z

        # Speed
        speeds = [0.0] * len(lats)
        arsp = log_data.arsp
        if 'Airspeed' in arsp and len(arsp.get('Airspeed', [])) > 0:
            mask = (arsp['U'] == 1) if 'U' in arsp else np.ones(len(arsp['Airspeed']), bool)
            at2 = arsp['TimeUS'][mask].astype(float)
            av  = arsp['Airspeed'][mask].astype(float)
            if len(at2):
                speeds = np.interp(times, at2, av).tolist()
        elif 'Spd' in log_data.gps and len(log_data.gps.get('Spd', [])) > 0:
            speeds = np.interp(times,
                log_data.gps['TimeUS'].astype(float),
                log_data.gps['Spd'].astype(float)).tolist()

        # Modes
        mode_labels = [''] * len(lats)
        sevs = sorted([e for e in log_data.events if e.event_type == 'mode_change'],
                      key=lambda e: e.time_us)
        mi, cur = 0, 'UNKNOWN'
        for i, t in enumerate(times):
            while mi < len(sevs) and sevs[mi].time_us <= t:
                cur = sevs[mi].detail.replace('Mode: ', '').strip(); mi += 1
            mode_labels[i] = cur

        colors_hex = _mode_colors(times, log_data.events)
        colors_rgb = [_hex_rgb(c) for c in colors_hex]

        t0      = float(times[0])
        times_s = [round((float(t) - t0) / 1_000_000, 3) for t in times]

        zoom, sat_n, min_tx, min_ty, max_tx, max_ty, terr_b64, tex_subs_b64 = \
            self._fetch_tiles(lats, lngs, home_lat, home_lng)

        tiles_js = []
        for ty in range(min_ty, max_ty + 1):
            for tx in range(min_tx, max_tx + 1):
                w_b, s_b, e_b, n_b = _tile_bounds(tx, ty, zoom)
                def ll2xz(lat, lng, _hl=home_lat, _hg=home_lng, _R=R):
                    e2 = _R * math.cos(math.radians(_hl)) * math.radians(lng - _hg)
                    n2 = _R * math.radians(lat - _hl)
                    return [round(e2, 1), round(-n2, 1)]
                sw  = ll2xz(s_b, w_b)
                ne  = ll2xz(n_b, e_b)
                key = f'{tx}_{ty}'
                tiles_js.append({
                    'terr':     terr_b64.get(key),
                    'tex_subs': tex_subs_b64.get(key, []),
                    'tex_n':    sat_n,
                    'sw': sw, 'ne': ne,
                })

        # Waypoints → 3D scene coordinates
        waypoints_js = waypoints_to_enu(log_data.waypoints, home_lat, home_lng, R)

        legend = _legend_items(log_data.events)
        data_js = f"""
const HOME_LAT={home_lat}, HOME_LNG={home_lng};
const N_PTS={len(pts)};
const TOTAL_SECS={round(times_s[-1], 1) if times_s else 0};
const TILE_RES={_TILE_RES};
const TILES={json.dumps(tiles_js)};
const PTS={json.dumps(pts)};
const TIMES={json.dumps(times_s)};
const ROLLS={json.dumps([round(r, 2) for r in rolls_d])};
const PITCHES={json.dumps([round(p, 2) for p in pitches_d])};
const YAWS={json.dumps([round(y, 2) for y in yaws_d])};
const SPEEDS={json.dumps([round(s, 2) for s in speeds])};
const MODES={json.dumps(mode_labels)};
const PATH_COLORS={json.dumps([[round(c, 3) for c in rgb] for rgb in colors_rgb])};
const LEGEND_ITEMS={json.dumps(legend)};
const WAYPOINTS={json.dumps(waypoints_js)};
"""
        return _HTML_TEMPLATE.replace('/*DATA_JS*/', data_js).replace('/*WAYPOINTS_JS*/', WAYPOINTS_JS)

    def _fetch_tiles(self, lats, lngs, home_lat, home_lng):
        zoom = 13
        for z in range(15, 7, -1):
            txs = [_lat_lng_to_tile(la, lo, z)[0] for la, lo in zip(lats, lngs)]
            tys = [_lat_lng_to_tile(la, lo, z)[1] for la, lo in zip(lats, lngs)]
            if max(txs) - min(txs) <= 4 and max(tys) - min(tys) <= 4:
                zoom = z; break

        txs = [_lat_lng_to_tile(la, lo, zoom)[0] for la, lo in zip(lats, lngs)]
        tys = [_lat_lng_to_tile(la, lo, zoom)[1] for la, lo in zip(lats, lngs)]
        max_coord = 2 ** zoom - 1
        buf = 6
        min_tx = max(0,         min(txs) - buf)
        max_tx = min(max_coord, max(txs) + buf)
        min_ty = max(0,         min(tys) - buf)
        max_ty = min(max_coord, max(tys) + buf)

        sat_zoom = zoom + 1
        sat_n    = 2

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
            kind, key, i, url = job
            return kind, key, i, _fetch_b64(url)

        with ThreadPoolExecutor(max_workers=24) as pool:
            for kind, key, i, data in pool.map(_do, jobs):
                if kind == 't':
                    terr_b64[key] = data
                else:
                    tex_subs_b64[key][i] = data

        return zoom, sat_n, min_tx, min_ty, max_tx, max_ty, terr_b64, tex_subs_b64

    def _empty_html(self, msg):
        return (
            f'<!DOCTYPE html><html><body style="background:{COLORS["bg_primary"]};'
            f'color:{COLORS["text_secondary"]};display:flex;align-items:center;'
            f'justify-content:center;height:100vh;margin:0;font-family:sans-serif;'
            f'font-size:16px;">{msg}</body></html>'
        )

    # ── Tree interaction ────────────────────────────────────

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int):
        if item.data(0, Qt.UserRole) is None:
            item.setExpanded(not item.isExpanded())

    def _on_group_expanded(self, item: QTreeWidgetItem):
        if item.data(0, Qt.UserRole) is None:
            item.setText(0, item.text(0).replace('▸', '▾', 1))

    def _on_group_collapsed(self, item: QTreeWidgetItem):
        if item.data(0, Qt.UserRole) is None:
            item.setText(0, item.text(0).replace('▾', '▸', 1))

    def _on_item_changed(self, item: QTreeWidgetItem, column: int):
        data = item.data(0, Qt.UserRole)
        if data is None:
            return
        attr, fk, fn = data
        key     = f"{attr}.{fk}"
        checked = item.checkState(0) == Qt.Checked
        if checked and key not in self._plot_items:
            self._add_plot_line(item, attr, fk, fn, key)
        elif not checked and key in self._plot_items:
            self._remove_plot_line(item, key)

    def _add_plot_line(self, item: QTreeWidgetItem, attr: str, fk: str, fn: str, key: str):
        if self._log_data is None:
            return
        msg = getattr(self._log_data, attr, {})
        if 'TimeUS' not in msg or fk not in msg:
            return
        t_s  = (msg['TimeUS'].astype(np.float64) - self._t0_us) / 1_000_000.0
        vals = msg[fk].astype(np.float64)
        color = _PALETTE[self._color_idx % len(_PALETTE)]
        self._color_idx += 1
        name = f"{attr.upper()}.{fk}"

        if len(self._plot_items) == 0:
            # First curve — use the main left axis
            curve = self._plot.plot(t_s, vals, pen=pg.mkPen(color=color, width=1.5), name=name)
            left = self._plot.getAxis('left')
            left.setPen(pg.mkPen(color=color))
            try:
                left.setTextPen(pg.mkPen(color=color))
            except AttributeError:
                pass
            self._plot_items[key] = (curve, color, None, None)
        else:
            # Additional curves — create a new ViewBox + right AxisItem
            pi  = self._plot.plotItem
            vb  = pg.ViewBox()
            ax  = pg.AxisItem('left')
            ax.setPen(pg.mkPen(color=color))
            try:
                ax.setTextPen(pg.mkPen(color=color))
            except AttributeError:
                pass
            pi.scene().addItem(vb)
            ax.linkToView(vb)
            vb.setXLink(pi.vb)
            # Pass all mouse events through to the main ViewBox so it handles
            # panning/zooming; extra curves are Y-controlled via their AxisItems.
            vb.setAcceptedMouseButtons(Qt.NoButton)
            vb.enableAutoRange(axis=vb.YAxis, enable=True)
            curve = pg.PlotDataItem(t_s, vals, pen=pg.mkPen(color=color, width=1.5), name=name)
            vb.addItem(curve)
            self._legend.addItem(curve, name)
            self._plot_items[key] = (curve, color, vb, ax)
            self._rebuild_extra_axes()

        item.setForeground(0, QColor(color))
        self._update_stats_box()

    def _remove_plot_line(self, item: QTreeWidgetItem, key: str):
        if key not in self._plot_items:
            return
        curve, _, vb, ax = self._plot_items.pop(key)
        if vb is None:
            # Main left-axis curve
            self._plot.removeItem(curve)
            self._legend.removeItem(curve)
            left = self._plot.getAxis('left')
            left.setPen(pg.mkPen(COLORS['text_secondary']))
            try:
                left.setTextPen(pg.mkPen(COLORS['text_secondary']))
            except AttributeError:
                pass
        else:
            self._plot.plotItem.layout.removeItem(ax)
            self._plot.plotItem.scene().removeItem(vb)
            self._legend.removeItem(curve)
        self._rebuild_extra_axes()
        item.setForeground(0, QColor(COLORS['text_secondary']))
        self._update_stats_box()

    def _rebuild_extra_axes(self):
        """Reposition all extra left-side axes in the plotItem layout after add/remove."""
        pi = self._plot.plotItem
        for _, (_, _, vb, ax) in self._plot_items.items():
            if ax is not None:
                pi.layout.removeItem(ax)
        col = _MAIN_LEFT_COL - 1  # 4, 3, 2, 1, 0 — directly left of main axis
        for _, (_, _, vb, ax) in self._plot_items.items():
            if ax is not None:
                pi.layout.addItem(ax, 2, col)
                col -= 1
        self._update_extra_views()

    def _update_extra_views(self):
        """Sync extra ViewBox geometry to the main ViewBox after resize."""
        main_vb = self._plot.plotItem.vb
        for _, (_, _, vb, _) in self._plot_items.items():
            if vb is not None:
                vb.setGeometry(main_vb.sceneBoundingRect())
                vb.linkedViewChanged(main_vb, vb.XAxis)

    def _propagate_y_change(self, y_before, y_after):
        """Apply the same proportional Y shift/zoom to all extra ViewBoxes."""
        span_before = y_before[1] - y_before[0]
        if span_before == 0:
            return
        center_before = (y_before[0] + y_before[1]) / 2
        center_after  = (y_after[0]  + y_after[1])  / 2
        delta_frac = (center_after - center_before) / span_before
        scale_frac = (y_after[1] - y_after[0]) / span_before
        for _, (_, _, vb, _) in self._plot_items.items():
            if vb is None:
                continue
            ey = vb.viewRange()[1]
            e_span   = ey[1] - ey[0]
            e_center = (ey[0] + ey[1]) / 2
            new_center = e_center + delta_frac * e_span
            new_span   = e_span * scale_frac
            vb.setYRange(new_center - new_span / 2, new_center + new_span / 2, padding=0)

    def _clear_extra_axes(self):
        """Remove all extra ViewBoxes and AxisItems from the scene/layout."""
        if self._plot is None:
            return
        pi = self._plot.plotItem
        for _, (_, _, vb, ax) in self._plot_items.items():
            if ax is not None:
                try:
                    pi.layout.removeItem(ax)
                except Exception:
                    pass
            if vb is not None:
                try:
                    pi.scene().removeItem(vb)
                except Exception:
                    pass
        left = self._plot.getAxis('left')
        left.setPen(pg.mkPen(COLORS['text_secondary']))
        try:
            left.setTextPen(pg.mkPen(COLORS['text_secondary']))
        except AttributeError:
            pass

    def _build_graph_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(30)
        bar.setStyleSheet(
            f"background-color:{COLORS['bg_secondary']};"
            f" border-bottom:1px solid {COLORS['border']};"
        )
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 0, 8, 0)
        row.setSpacing(4)

        btn_style = (
            f"QPushButton {{ background:{COLORS['bg_tertiary']}; color:{COLORS['text_disabled']};"
            f" border:1px solid {COLORS['border']}; border-radius:3px;"
            f" font-size:9px; font-weight:bold; padding:0 8px; }}"
            f"QPushButton:hover {{ color:{COLORS['text_secondary']};"
            f" border-color:{COLORS['border_active']}55; }}"
            f"QPushButton:pressed {{ background:{COLORS['border_active']}22;"
            f" color:{COLORS['border_active']}; border-color:{COLORS['border_active']}; }}"
        )

        def _btn(label, slot):
            b = QPushButton(label)
            b.setFixedHeight(20)
            b.setStyleSheet(btn_style)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            return b

        row.addWidget(_btn("⌂  Reset View",     self._on_reset_view))
        row.addWidget(_btn("⇔  Fit Selection",  self._on_fit_selection))
        row.addWidget(_btn("↕  Auto Y",          self._on_auto_y))

        sep = QWidget()
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"background:{COLORS['border']};")
        row.addWidget(sep)

        row.addWidget(_btn("⊘  Clear Selection", self._on_clear_selection))
        row.addStretch()
        return bar

    def _on_reset_view(self):
        if self._plot is None:
            return
        self._plot.plotItem.vb.autoRange()

    def _on_fit_selection(self):
        if self._plot is None or self._region is None:
            return
        t0, t1 = self._region.getRegion()
        padding = (t1 - t0) * 0.02
        self._plot.setXRange(t0 - padding, t1 + padding, padding=0)

    def _on_auto_y(self):
        if self._plot is None:
            return
        vb = self._plot.plotItem.vb
        x_range = vb.viewRange()[0]
        vb.autoRange()
        vb.setXRange(x_range[0], x_range[1], padding=0)
        for _, (_, _, extra_vb, _) in self._plot_items.items():
            if extra_vb is not None:
                xr = extra_vb.viewRange()[0]
                extra_vb.autoRange()
                extra_vb.setXRange(xr[0], xr[1], padding=0)

    def _on_clear_selection(self):
        if self._plot is None or self._region is None or self._log_data is None:
            return
        dur = self._log_data.duration_seconds
        self._region.setRegion([0, dur])

    def _toggle_stats(self, checked: bool):
        self._stats_visible = checked
        if checked:
            self._update_stats_box()
        else:
            self._stats_box.setVisible(False)

    def _update_stats_box(self):
        if not self._stats_visible or self._stats_box is None or self._region is None:
            return
        if not self._plot_items:
            self._stats_box.setVisible(False)
            return

        t0, t1 = self._region.getRegion()
        ts  = COLORS['text_secondary']   # #8B949E — channel names, time
        tp  = COLORS['text_primary']     # #E6EDF3 — values
        acc = COLORS['border_active']    # #58A6FF — column headers

        rows = []
        for _key, (curve, color, _, _) in self._plot_items.items():
            xd, yd = curve.getData()
            if xd is None or len(xd) == 0:
                continue
            mask = (xd >= t0) & (xd <= t1)
            vals = yd[mask]
            if len(vals) == 0:
                continue
            rows.append((color, curve.name() or _key,
                         float(np.min(vals)), float(np.max(vals)), float(np.mean(vals))))

        if not rows:
            self._stats_box.setVisible(False)
            return

        html = (
            f"<div style='color:{ts};font-size:10px;font-weight:bold;"
            f" letter-spacing:0.05em;margin-bottom:5px'>"
            f"SUMMARY &nbsp;"
            f"<span style='color:{tp};font-weight:normal;font-size:10px'>"
            f"{t0:.1f}s – {t1:.1f}s</span></div>"
            "<table cellspacing='3'>"
            f"<tr>"
            f"<td></td>"
            f"<td style='color:{acc};font-size:10px;font-weight:bold;padding-right:10px'>MIN</td>"
            f"<td style='color:{acc};font-size:10px;font-weight:bold;padding-right:10px'>MAX</td>"
            f"<td style='color:{acc};font-size:10px;font-weight:bold'>MEAN</td>"
            f"</tr>"
            + ''.join(
                f"<tr>"
                f"<td style='padding-right:8px'>"
                f"<span style='color:{c}'>&#9632;</span>"
                f"&nbsp;<span style='color:{ts};font-size:11px'>{n}</span>"
                f"</td>"
                f"<td style='color:{tp};font-size:11px;padding-right:10px'><b>{mn:.4g}</b></td>"
                f"<td style='color:{tp};font-size:11px;padding-right:10px'><b>{mx:.4g}</b></td>"
                f"<td style='color:{tp};font-size:11px'><b>{avg:.4g}</b></td>"
                f"</tr>"
                for c, n, mn, mx, avg in rows
            )
            + "</table>"
        )
        self._stats_box.set_content(html)
        self._stats_box.setVisible(True)
        self._stats_box.raise_()

    # ── Graph ↔ 3D time sync ───────────────────────────────

    def _on_title_changed(self, title: str):
        """Receive playback time pushed by JS via document.title."""
        if not title.startswith('t:') or self._time_cursor is None:
            return
        try:
            t = float(title[2:])
        except ValueError:
            return
        self._time_cursor.setValue(t)

    def _on_graph_click(self, event):
        """Left-click on the graph → seek 3D to that time."""
        from PyQt5.QtCore import Qt as _Qt
        if event.button() != _Qt.LeftButton:
            return
        if self._view is None or not self._map_ready:
            return
        vb = self._plot.plotItem.vb
        if not vb.sceneBoundingRect().contains(event.scenePos()):
            return
        t = vb.mapSceneToView(event.scenePos()).x()
        self._view.page().runJavaScript(f"seekTo({t:.3f});")
        if self._time_cursor is not None:
            self._time_cursor.setValue(t)

    def _on_plot_drag(self, ev, axis=None):
        """Left-drag anywhere on the plot defines the selection region."""
        if ev.button() != Qt.LeftButton:
            vb = self._plot.plotItem.vb
            y_before = vb.viewRange()[1]
            pg.ViewBox.mouseDragEvent(vb, ev, axis)
            # axis is not None when called from AxisItem → only move this one curve
            if axis is None:
                self._propagate_y_change(y_before, vb.viewRange()[1])
            return
        ev.accept()
        if self._region is None:
            return
        vb = self._plot.plotItem.vb
        t0 = vb.mapSceneToView(ev.buttonDownScenePos()).x()
        t1 = vb.mapSceneToView(ev.scenePos()).x()
        a, b = (t0, t1) if t0 <= t1 else (t1, t0)
        # Block intermediate signals; push to JS once when the drag ends.
        self._region.blockSignals(True)
        self._region.setRegion([a, b])
        self._region.blockSignals(False)
        if ev.isFinish():
            self._on_region_changed()

    # ── Hover tooltip ──────────────────────────────────────

    def _on_mouse_move(self, scene_pos):
        if self._plot is None or self._hover_label is None:
            return
        vb = self._plot.plotItem.vb
        in_plot = vb.sceneBoundingRect().contains(scene_pos)
        if not in_plot:
            if self._hover_vline:
                self._hover_vline.setVisible(False)
            self._hover_label.setVisible(False)
            return

        t = vb.mapSceneToView(scene_pos).x()
        if self._hover_vline:
            self._hover_vline.setValue(t)
            self._hover_vline.setVisible(True)

        if not self._plot_items:
            self._hover_label.setVisible(False)
            return

        rows = []
        for _key, (curve, color, _, _) in self._plot_items.items():
            xd, yd = curve.getData()
            if xd is None or len(xd) == 0:
                continue
            idx = int(np.searchsorted(xd, t))
            idx = max(0, min(idx, len(xd) - 1))
            rows.append((color, curve.name() or _key, float(yd[idx])))

        if not rows:
            self._hover_label.setVisible(False)
            return

        ts  = COLORS['text_secondary']
        tp  = COLORS['text_primary']
        html = (
            f"<span style='color:{ts};font-size:10px'>t = {t:.2f} s</span>"
            "<table cellspacing='1' style='margin-top:3px'>"
            + ''.join(
                f"<tr>"
                f"<td><span style='color:{c}'>&#9632;&nbsp;</span></td>"
                f"<td style='color:{ts};font-size:11px;padding-right:6px'>{n}</td>"
                f"<td style='color:{tp};font-size:11px'><b>{v:.3f}</b></td>"
                f"</tr>"
                for c, n, v in rows
            )
            + "</table>"
        )
        self._hover_label.setText(html)
        self._hover_label.adjustSize()

        pt  = self._plot.mapFromScene(scene_pos)
        vp  = self._plot.viewport()
        pw, ph = vp.width(), vp.height()
        lw, lh = self._hover_label.width(), self._hover_label.height()
        x = pt.x() + 14
        y = pt.y() - lh // 2
        if x + lw > pw - 4:
            x = pt.x() - lw - 14
        y = max(4, min(y, ph - lh - 4))
        self._hover_label.move(x, y)
        self._hover_label.setVisible(True)
        self._hover_label.raise_()

    # ── Region → 3D sync ───────────────────────────────────

    def _on_region_changed(self):
        self._update_stats_box()
        if not self._map_ready or self._view is None:
            return
        t0, t1 = self._region.getRegion()
        self._view.page().runJavaScript(f"updateSelection({t0:.3f}, {t1:.3f});")


# ═══════════════════════════════════════════════════════════════════════════════
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
#hud{position:absolute;top:0;left:0;pointer-events:none;z-index:9;display:none}
#mode-legend{
  position:absolute;top:8px;right:8px;z-index:9;
  background:rgba(13,17,23,0.85);border:1px solid #30363D;
  border-radius:6px;padding:8px 11px;font-family:monospace;font-size:11px;
  pointer-events:none;min-width:110px;
}
#mode-legend-title{color:#8B949E;font-size:10px;margin-bottom:5px;letter-spacing:.05em}
.ml-row{display:flex;align-items:center;gap:7px;margin:3px 0}
.ml-dot{width:9px;height:9px;border-radius:50%;flex-shrink:0}
.ml-name{color:#C9D1D9}
</style>
</head>
<body>
<canvas id="canvas"></canvas>
<canvas id="hud"></canvas>
<div id="mode-legend">
  <div id="mode-legend-title">FLIGHT MODES</div>
</div>
<div id="controls">
  <button class="cb" id="bpl" onclick="togglePlay()">&#9654;</button>
  <button class="cb" onclick="stopPlay()">&#9632;</button>
  <div class="sep"></div>
  <button class="cb on" id="bwp" onclick="toggleWaypoints()">WP</button>
  <div class="sep"></div>
  <span style="color:#8B949E;font-size:11px">Spd:</span>
  <button class="cb sp on" data-v="1"  onclick="setSp(1)">1&times;</button>
  <button class="cb sp"    data-v="2"  onclick="setSp(2)">2&times;</button>
  <button class="cb sp"    data-v="5"  onclick="setSp(5)">5&times;</button>
  <button class="cb sp"    data-v="10" onclick="setSp(10)">10&times;</button>
  <button class="cb sp"    data-v="25" onclick="setSp(25)">25&times;</button>
  <button class="cb sp"    data-v="50" onclick="setSp(50)">50&times;</button>
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

// ── Mode legend ───────────────────────────────────────────
(function(){
  const legend = document.getElementById('mode-legend');
  if(!LEGEND_ITEMS || LEGEND_ITEMS.length === 0){ legend.style.display='none'; return; }
  LEGEND_ITEMS.forEach(function([name, color]){
    const row = document.createElement('div');
    row.className = 'ml-row';
    row.innerHTML =
      '<div class="ml-dot" style="background:'+color+'"></div>' +
      '<span class="ml-name">'+name+'</span>';
    legend.appendChild(row);
  });
})();

// ── Renderer / Scene / Camera ─────────────────────────────
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

scene.add(new THREE.AmbientLight(0xffffff, 0.55));
const sun = new THREE.DirectionalLight(0xffffff, 0.9);
sun.position.set(300, 800, 200);
sun.castShadow = true;
scene.add(sun);

(function(){
  const sg = new THREE.SphereGeometry(40000,8,8);
  const sm = new THREE.MeshBasicMaterial({color:0x1a2a3a,side:THREE.BackSide});
  scene.add(new THREE.Mesh(sg,sm));
})();

// ── Orbit state ───────────────────────────────────────────
let orbTheta=0.3, orbPhi=0.9, orbRadius=300;
const orbTarget = new THREE.Vector3();
let orbDrag=false, panDrag=false, lastX=0, lastY=0;
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
    const r=new THREE.Vector3();
    r.crossVectors(camera.getWorldDirection(new THREE.Vector3()), new THREE.Vector3(0,1,0)).normalize();
    orbTarget.addScaledVector(r, -dx*orbRadius*0.0012);
    orbTarget.y += dy*orbRadius*0.0012;
  } else if(flwDrag){
    flwTheta += dx*0.006;
    flwPhi = Math.max(0.05, Math.min(1.55, flwPhi - dy*0.006));
  }
});
canvas.addEventListener('wheel', e=>{
  const dir = e.deltaY > 0 ? 1 : -1;
  if(camMode==='free'){
    const step = Math.max(2, orbRadius*0.08);
    orbRadius = Math.max(5, orbRadius + dir*step);
  } else if(camMode==='follow'){
    const step = Math.max(2, flwRadius*0.08);
    flwRadius = Math.max(10, flwRadius + dir*step);
  }
  e.preventDefault();
},{passive:false});
canvas.addEventListener('contextmenu', e=>e.preventDefault());

function onResize(){
  renderer.setSize(W(), H());
  camera.aspect = W()/H();
  camera.updateProjectionMatrix();
  const hc=document.getElementById('hud');
  hc.width=W(); hc.height=H();
}
window.addEventListener('resize', onResize);
onResize();

// ── Aircraft model ────────────────────────────────────────
let aircraft = buildDefaultAircraft();
scene.add(aircraft);

function buildDefaultAircraft(){
  const g=new THREE.Group();
  const M=c=>new THREE.MeshPhongMaterial({color:c});
  const fuse=new THREE.Mesh(new THREE.CylinderGeometry(0.4,0.3,10,8),M(0x8B949E));
  fuse.rotation.x=-Math.PI/2; g.add(fuse);
  const wing=new THREE.Mesh(new THREE.BoxGeometry(22,0.25,2.8),M(0xC9D1D9));
  wing.position.z=0.5; g.add(wing);
  const htail=new THREE.Mesh(new THREE.BoxGeometry(7,0.2,2),M(0xC9D1D9));
  htail.position.z=4.8; g.add(htail);
  const vstab=new THREE.Mesh(new THREE.BoxGeometry(0.2,2.8,2),M(0xC9D1D9));
  vstab.position.set(0,1.3,4.8); g.add(vstab);
  const nose=new THREE.Mesh(new THREE.ConeGeometry(0.4,2,8),M(0x6E7681));
  nose.rotation.x=Math.PI/2; nose.position.z=-6; g.add(nose);
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

// ── Flight path tube — single system, rebuilt on selection ─
const PATH_TUBE_R = 0.5;
let pathMeshes = [];

function buildPathTubes(fromIdx, toIdx){
  pathMeshes.forEach(function(m){ m.geometry.dispose(); scene.remove(m); });
  pathMeshes = [];
  const SEG_MAX = 250;
  function sameCol(a,b){
    return PATH_COLORS[a][0]===PATH_COLORS[b][0] &&
           PATH_COLORS[a][1]===PATH_COLORS[b][1] &&
           PATH_COLORS[a][2]===PATH_COLORS[b][2];
  }
  function addTube(from, to){
    if(to-from < 2) return;
    const step=Math.max(1,Math.floor((to-from)/SEG_MAX));
    const pts=[];
    for(let j=from;j<to;j+=step)
      pts.push(new THREE.Vector3(PTS[j][0],PTS[j][1],PTS[j][2]));
    const last=to-1;
    const lp=pts[pts.length-1];
    if(lp.x!==PTS[last][0]||lp.y!==PTS[last][1]||lp.z!==PTS[last][2])
      pts.push(new THREE.Vector3(PTS[last][0],PTS[last][1],PTS[last][2]));
    if(pts.length<2) return;
    try{
      const curve=new THREE.CatmullRomCurve3(pts);
      const r=PATH_COLORS[from];
      const col=new THREE.Color(r[0]*0.85,r[1]*0.85,r[2]*0.85);
      const geom=new THREE.TubeGeometry(curve,pts.length*2,PATH_TUBE_R,5,false);
      const mesh=new THREE.Mesh(geom,new THREE.MeshBasicMaterial({color:col}));
      scene.add(mesh);
      pathMeshes.push(mesh);
    }catch(e){}
  }
  let segStart=fromIdx;
  for(let i=fromIdx+1;i<=toIdx;i++){
    if(i===toIdx||!sameCol(i,segStart)){ addTube(segStart,i); segStart=i; }
  }
}

// Build full path on load
buildPathTubes(0, N_PTS);

/*WAYPOINTS_JS*/

// ── Trail (white, grows during playback) ──────────────────
const trailPositions = new Float32Array(N_PTS*3);
const trailGeom = new THREE.BufferGeometry();
trailGeom.setAttribute('position', new THREE.BufferAttribute(trailPositions,3));
trailGeom.setDrawRange(0,0);
scene.add(new THREE.Line(trailGeom, new THREE.LineBasicMaterial({color:0xffffff,opacity:0.7,transparent:true})));

// ── Playback (selection-bounded) ─────────────────────────
let playing=false, playIdx=0, playSpeed=1, elapsed=0, lastT=null;
let SEL_START=0, SEL_END=N_PTS-1;
let trailLastIdx=-1;
let lastTitlePush=0, lastTitleVal=NaN;

function updateSelection(t0, t1){
  // Find index range for selection
  let ns=-1, ne=-1;
  for(let i=0;i<N_PTS;i++){
    if(TIMES[i]>=t0 && ns===-1) ns=i;
    if(TIMES[i]<=t1) ne=i;
  }
  SEL_START = ns<0 ? 0 : ns;
  SEL_END   = ne<SEL_START ? SEL_START : ne;

  // Rebuild path tube for selected range only
  buildPathTubes(SEL_START, SEL_END+1);

  // Reset playback to selection start
  playing=false; updatePBtn();
  playIdx=SEL_START;
  elapsed=TIMES[SEL_START];
  lastT=null;
  trailLastIdx=SEL_START-1;
  trailGeom.setDrawRange(0, 1);

  // Fit free camera to selection
  if(camMode==='free' && SEL_END>SEL_START){
    let minX=Infinity,maxX=-Infinity,minZ=Infinity,maxZ=-Infinity,sumY=0,cnt=0;
    for(let i=SEL_START;i<=SEL_END;i++){
      const p=PTS[i];
      if(p[0]<minX) minX=p[0]; if(p[0]>maxX) maxX=p[0];
      if(p[2]<minZ) minZ=p[2]; if(p[2]>maxZ) maxZ=p[2];
      sumY+=p[1]; cnt++;
    }
    orbTarget.set((minX+maxX)/2, sumY/cnt, (minZ+maxZ)/2);
    const span=Math.max(maxX-minX, maxZ-minZ, 30);
    orbRadius=span*1.5;
  }
}

function animFrame(now){
  requestAnimationFrame(animFrame);
  if(playing && lastT!==null){
    elapsed += (now-lastT)/1000 * playSpeed;
    while(playIdx<SEL_END && TIMES[playIdx+1]<=elapsed) playIdx++;
    if(playIdx>=SEL_END){ playing=false; updatePBtn(); }
  }
  lastT=now;

  const p=PTS[playIdx];
  aircraft.position.set(p[0],p[1],p[2]);
  const yawRad   = YAWS[playIdx]   * Math.PI/180;
  const pitchRad = PITCHES[playIdx] * Math.PI/180;
  const rollRad  = ROLLS[playIdx]   * Math.PI/180;
  const qY = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,1,0), -yawRad);
  const bodyRight = new THREE.Vector3(1,0,0).applyQuaternion(qY);
  const qP = new THREE.Quaternion().setFromAxisAngle(bodyRight, pitchRad);
  const bodyNose = new THREE.Vector3(0,0,-1).applyQuaternion(qY).applyQuaternion(qP);
  const qR = new THREE.Quaternion().setFromAxisAngle(bodyNose, rollRad);
  aircraft.quaternion.multiplyQuaternions(qR, new THREE.Quaternion().multiplyQuaternions(qP, qY));

  // Trail: positions[j] = PTS[SEL_START+j] is invariant; only copy newly-passed indices.
  if(playIdx > trailLastIdx){
    for(let i=trailLastIdx+1;i<=playIdx;i++){
      const j=i-SEL_START;
      trailPositions[j*3]=PTS[i][0]; trailPositions[j*3+1]=PTS[i][1]; trailPositions[j*3+2]=PTS[i][2];
    }
    trailLastIdx=playIdx;
    trailGeom.attributes.position.needsUpdate=true;
  }
  trailGeom.setDrawRange(0, playIdx-SEL_START+1);

  updateCamera();
  if(camMode==='fpv') updateHUD();
  updateTimeLabel();
  const selLen=SEL_END-SEL_START;
  document.getElementById('tslider').value = selLen>0 ? Math.round((playIdx-SEL_START)/selLen*1000) : 0;

  // Push playback time to Python via document.title (event-driven, no polling).
  // Throttle by REAL time (10 Hz cap) so high-speed playback doesn't flood Python.
  const tNow=TIMES[playIdx];
  if(now-lastTitlePush >= 100 && tNow !== lastTitleVal){
    document.title='t:'+tNow.toFixed(2);
    lastTitlePush=now;
    lastTitleVal=tNow;
  }
  renderer.render(scene, camera);
}

// ── Camera ────────────────────────────────────────────────
function updateCamera(){
  if(camMode==='free'){
    const x=orbTarget.x+orbRadius*Math.sin(orbPhi)*Math.sin(orbTheta);
    const y=orbTarget.y+orbRadius*Math.cos(orbPhi);
    const z=orbTarget.z+orbRadius*Math.sin(orbPhi)*Math.cos(orbTheta);
    camera.up.set(0,1,0);
    camera.position.set(x,y,z);
    camera.lookAt(orbTarget);
  } else if(camMode==='follow'){
    const ac=new THREE.Vector3(PTS[playIdx][0],PTS[playIdx][1],PTS[playIdx][2]);
    const yr=YAWS[playIdx]*Math.PI/180;
    const th=flwTheta-yr;
    const x=ac.x+flwRadius*Math.sin(flwPhi)*Math.sin(th);
    const y=ac.y+flwRadius*Math.cos(flwPhi);
    const z=ac.z+flwRadius*Math.sin(flwPhi)*Math.cos(th);
    camera.position.set(x,y,z);
    camera.lookAt(ac);
  } else if(camMode==='fpv'){
    const p=PTS[playIdx];
    const yawRad   = YAWS[playIdx]   * Math.PI/180;
    const pitchRad = PITCHES[playIdx] * Math.PI/180;
    const rollRad  = ROLLS[playIdx]   * Math.PI/180;
    const qY = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,1,0), -yawRad);
    const bodyRight = new THREE.Vector3(1,0,0).applyQuaternion(qY);
    const qP = new THREE.Quaternion().setFromAxisAngle(bodyRight, pitchRad);
    const bodyNose = new THREE.Vector3(0,0,-1).applyQuaternion(qY).applyQuaternion(qP);
    const qR = new THREE.Quaternion().setFromAxisAngle(bodyNose, rollRad);
    const q = new THREE.Quaternion().multiplyQuaternions(qR, new THREE.Quaternion().multiplyQuaternions(qP, qY));
    const fwd = new THREE.Vector3(0,0,-1).applyQuaternion(q);
    const up  = new THREE.Vector3(0,1,0).applyQuaternion(q);
    camera.position.set(p[0],p[1]+1,p[2]);
    camera.up.copy(up);
    camera.lookAt(p[0]+fwd.x*100, p[1]+1+fwd.y*100, p[2]+fwd.z*100);
  }
}

// ── HUD ───────────────────────────────────────────────────
function updateHUD(){
  const roll  = ROLLS[playIdx];
  const pitch = PITCHES[playIdx];
  const yaw   = ((YAWS[playIdx]%360)+360)%360;
  const spd   = SPEEDS[playIdx];
  const alt   = PTS[playIdx][1];
  const mode  = MODES[playIdx];
  const t     = TIMES[playIdx] - TIMES[SEL_START];
  const cv=document.getElementById('hud');
  const CW=cv.width, CH=cv.height;
  const ctx=cv.getContext('2d');
  ctx.clearRect(0,0,CW,CH);
  const G='#00FF41', DIM='rgba(0,255,65,0.45)';
  const cx=CW/2, cy=CH/2;
  ctx.shadowColor=G; ctx.shadowBlur=4;
  hudPitchLadder(ctx,cx,cy,roll,pitch,G,DIM);
  hudBoresight(ctx,cx,cy,G);
  hudHeading(ctx,cx,CW,yaw,G,DIM);
  hudTape(ctx,14,    cy,90,340,spd,5, 10,'m/s','SPD',false,G,DIM);
  hudTape(ctx,CW-104,cy,90,340,alt,10,20,'m',  'ALT',true, G,DIM);
  hudStatus(ctx,cx,CH,t,mode,pitch,roll,G);
}

function hudHeading(ctx,cx,CW,yaw,G,DIM){
  const TW=300,TH=38,tx=cx-TW/2,ty=10;
  const pxPd=TW/60;
  const cards={0:'N',45:'NE',90:'E',135:'SE',180:'S',225:'SW',270:'W',315:'NW'};
  ctx.strokeStyle=G; ctx.lineWidth=1.5;
  ctx.strokeRect(tx,ty,TW,TH);
  ctx.save();
  ctx.beginPath(); ctx.rect(tx,ty,TW,TH); ctx.clip();
  for(let d=-35;d<=35;d+=5){
    const hdg=((Math.round(yaw)+d)%360+360)%360;
    const px=cx+d*pxPd;
    if(px<tx+2||px>tx+TW-2) continue;
    const isCard=cards[hdg]!==undefined;
    const isMaj=hdg%10===0;
    const tkH=isCard?TH-6:isMaj?TH*0.55:TH*0.3;
    ctx.lineWidth=isCard?2:1; ctx.strokeStyle=G;
    ctx.beginPath(); ctx.moveTo(px,ty+TH); ctx.lineTo(px,ty+TH-tkH); ctx.stroke();
    if(isMaj){
      ctx.fillStyle=G;
      ctx.font=isCard?'bold 11px monospace':'10px monospace';
      ctx.textAlign='center';
      ctx.fillText(cards[hdg]||String(hdg),px,ty+TH-tkH-3);
    }
  }
  ctx.restore();
  ctx.fillStyle='rgba(0,0,0,0.7)'; ctx.fillRect(cx-22,ty,44,TH);
  ctx.strokeStyle=G; ctx.lineWidth=2; ctx.strokeRect(cx-22,ty,44,TH);
  ctx.fillStyle=G; ctx.font='bold 14px monospace'; ctx.textAlign='center';
  ctx.fillText(String(Math.round(yaw)).padStart(3,'0'),cx,ty+TH-8);
  ctx.fillStyle=G;
  ctx.beginPath(); ctx.moveTo(cx,ty+TH+8); ctx.lineTo(cx-6,ty+TH); ctx.lineTo(cx+6,ty+TH); ctx.closePath(); ctx.fill();
}

function hudTape(ctx,tx,cy,TW,TH,val,minStep,labStep,unit,label,flipSide,G,DIM){
  const ty=cy-TH/2;
  ctx.strokeStyle=G; ctx.lineWidth=1.5;
  ctx.strokeRect(tx,ty,TW,TH);
  const pxU=TH/80;
  ctx.save();
  ctx.beginPath(); ctx.rect(tx,ty,TW,TH); ctx.clip();
  ctx.fillStyle=G; ctx.strokeStyle=G;
  const range=Math.ceil(TH/pxU/minStep+2)*minStep;
  for(let v=Math.floor((val-range)/minStep)*minStep;v<=val+range;v+=minStep){
    const py=cy-(v-val)*pxU;
    if(py<ty||py>ty+TH) continue;
    const isMaj=v%labStep===0;
    const tl=isMaj?14:8;
    ctx.lineWidth=isMaj?1.5:0.8; ctx.strokeStyle=G;
    ctx.beginPath();
    if(flipSide){ctx.moveTo(tx,py);ctx.lineTo(tx+tl,py);}
    else        {ctx.moveTo(tx+TW,py);ctx.lineTo(tx+TW-tl,py);}
    ctx.stroke();
    if(isMaj){
      ctx.font='13px monospace';
      ctx.textAlign=flipSide?'left':'right';
      ctx.fillText(String(Math.round(v)),flipSide?tx+tl+4:tx+TW-tl-4,py+5);
    }
  }
  ctx.restore();
  ctx.fillStyle='rgba(0,0,0,0.75)'; ctx.fillRect(tx,cy-16,TW,32);
  ctx.strokeStyle=G; ctx.lineWidth=2; ctx.strokeRect(tx,cy-16,TW,32);
  ctx.fillStyle=G; ctx.font='bold 18px monospace'; ctx.textAlign='center';
  ctx.fillText(String(Math.round(val)),tx+TW/2,cy+7);
  ctx.fillStyle=G;
  ctx.beginPath();
  if(flipSide){ctx.moveTo(tx-11,cy);ctx.lineTo(tx,cy-9);ctx.lineTo(tx,cy+9);}
  else        {ctx.moveTo(tx+TW+11,cy);ctx.lineTo(tx+TW,cy-9);ctx.lineTo(tx+TW,cy+9);}
  ctx.closePath(); ctx.fill();
  ctx.fillStyle=G; ctx.font='bold 13px monospace'; ctx.textAlign='center';
  ctx.fillText(label,tx+TW/2,ty+14);
  ctx.font='11px monospace'; ctx.fillText(unit,tx+TW/2,ty+TH-5);
}

function hudPitchLadder(ctx,cx,cy,roll,pitch,G,DIM){
  const clipW=520,clipH=340,pxDeg=12;
  const rollRad=roll*Math.PI/180;
  ctx.save();
  ctx.beginPath(); ctx.rect(cx-clipW/2,cy-clipH/2,clipW,clipH); ctx.clip();
  ctx.translate(cx,cy);
  ctx.rotate(-rollRad);
  const pitchOff=pitch*pxDeg;
  ctx.strokeStyle=G; ctx.lineWidth=2.5;
  ctx.beginPath(); ctx.moveTo(-clipW,pitchOff); ctx.lineTo(-35,pitchOff); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(35,pitchOff); ctx.lineTo(clipW,pitchOff); ctx.stroke();
  for(let d=-60;d<=60;d+=5){
    if(d===0) continue;
    const py=pitchOff-d*pxDeg;
    const isMaj=d%10===0;
    const lineLen=isMaj?90:52;
    const gap=34;
    ctx.lineWidth=isMaj?1.8:1;
    if(d<0){ctx.setLineDash([6,4]);ctx.strokeStyle=DIM;}
    else   {ctx.setLineDash([]);   ctx.strokeStyle=isMaj?G:DIM;}
    ctx.beginPath(); ctx.moveTo(-lineLen,py); ctx.lineTo(-gap,py); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(gap,py); ctx.lineTo(lineLen,py); ctx.stroke();
    ctx.setLineDash([]);
    if(isMaj){
      const tk=d>0?8:-8;
      ctx.strokeStyle=G; ctx.lineWidth=1.5;
      ctx.beginPath(); ctx.moveTo(-lineLen,py); ctx.lineTo(-lineLen,py+tk); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(lineLen,py); ctx.lineTo(lineLen,py+tk); ctx.stroke();
      ctx.fillStyle=G; ctx.font='11px monospace';
      ctx.textAlign='right'; ctx.fillText(String(Math.abs(d)),-lineLen-6,py+4);
      ctx.textAlign='left';  ctx.fillText(String(Math.abs(d)), lineLen+6,py+4);
    }
  }
  ctx.restore();
  const arcR=170;
  ctx.save();
  ctx.translate(cx,cy);
  ctx.strokeStyle=G; ctx.lineWidth=1.5;
  ctx.beginPath(); ctx.arc(0,0,arcR,-Math.PI*0.7,-Math.PI*0.3); ctx.stroke();
  for(const deg of [-60,-45,-30,-20,-10,0,10,20,30,45,60]){
    const ar=(deg-90)*Math.PI/180;
    const tl=deg===0?16:deg%30===0?12:7;
    ctx.lineWidth=deg===0?2.5:1;
    ctx.beginPath();
    ctx.moveTo(Math.cos(ar)*arcR,Math.sin(ar)*arcR);
    ctx.lineTo(Math.cos(ar)*(arcR-tl),Math.sin(ar)*(arcR-tl));
    ctx.stroke();
  }
  ctx.rotate(-rollRad);
  ctx.fillStyle=G;
  ctx.beginPath(); ctx.moveTo(0,-(arcR+2)); ctx.lineTo(-6,-(arcR+16)); ctx.lineTo(6,-(arcR+16)); ctx.closePath(); ctx.fill();
  ctx.restore();
}

function hudBoresight(ctx,cx,cy,G){
  ctx.strokeStyle=G; ctx.lineWidth=2.5; ctx.shadowBlur=8;
  ctx.beginPath(); ctx.arc(cx,cy,13,0,Math.PI*2); ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(cx-40,cy); ctx.lineTo(cx-14,cy);
  ctx.moveTo(cx+14,cy); ctx.lineTo(cx+40,cy);
  ctx.moveTo(cx,cy-40); ctx.lineTo(cx,cy-14);
  ctx.stroke();
  ctx.beginPath(); ctx.arc(cx,cy,2.5,0,Math.PI*2);
  ctx.fillStyle=G; ctx.fill();
}

function hudStatus(ctx,cx,CH,t,mode,pitch,roll,G){
  ctx.shadowBlur=6;
  ctx.fillStyle=G; ctx.font='bold 12px monospace'; ctx.textAlign='center';
  const ps=pitch>=0?'+':'';
  const rs=roll>=0?'+':'';
  ctx.fillText(fmtT(t)+'  |  '+mode+'  |  P:'+ps+pitch.toFixed(1)+'\xb0  R:'+rs+roll.toFixed(1)+'\xb0',cx,CH-12);
}

// ── Controls ──────────────────────────────────────────────
function togglePlay(){
  if(playIdx>=SEL_END){
    playIdx=SEL_START; elapsed=TIMES[SEL_START]; lastT=null;
    trailGeom.setDrawRange(0,1);
  }
  playing=!playing; updatePBtn();
}
function stopPlay(){
  playing=false; playIdx=SEL_START; elapsed=TIMES[SEL_START]; lastT=null;
  trailGeom.setDrawRange(0,1);
  updatePBtn();
}
function updatePBtn(){ document.getElementById('bpl').innerHTML=playing?'&#9646;&#9646;':'&#9654;'; }
function setSp(s){
  playSpeed=s;
  document.querySelectorAll('.sp').forEach(b=>b.classList.toggle('on',+b.dataset.v===s));
}
function onSlide(v){
  const selLen=SEL_END-SEL_START;
  playIdx = SEL_START + (selLen>0 ? Math.round(v/1000*selLen) : 0);
  playIdx = Math.max(SEL_START, Math.min(SEL_END, playIdx));
  elapsed=TIMES[playIdx]; lastT=null;
}
let acScale=1;
function setAcScale(v){
  acScale=v;
  aircraft.scale.setScalar(v);
}
function setCam(m){
  camMode=m;
  if(m==='free') orbTarget.set(PTS[playIdx][0],PTS[playIdx][1],PTS[playIdx][2]);
  document.getElementById('hud').style.display=m==='fpv'?'block':'none';
  aircraft.visible=m!=='fpv';
  document.getElementById('bfr').classList.toggle('on',m==='free');
  document.getElementById('bfo').classList.toggle('on',m==='follow');
  document.getElementById('bfp').classList.toggle('on',m==='fpv');
}
function updateTimeLabel(){
  const selDur=TIMES[SEL_END]-TIMES[SEL_START];
  const cur=TIMES[playIdx]-TIMES[SEL_START];
  document.getElementById('tlbl').textContent=fmtT(cur)+' / '+fmtT(selDur);
}
function fmtT(s){ const m=Math.floor(s/60); return m+':'+(Math.floor(s%60)+'').padStart(2,'0'); }

// ── Seek to absolute log time (called from Python on graph click) ─
function seekTo(t){
  let best=SEL_START;
  for(let i=SEL_START;i<=SEL_END;i++){
    if(TIMES[i]<=t) best=i; else break;
  }
  playIdx=best;
  elapsed=TIMES[best];
  lastT=null;
}

// ── Terrain tiles (identical to flight_review_3d) ─────────
function decodeTerrain(img){
  const cv=document.createElement('canvas'); cv.width=cv.height=256;
  const ctx=cv.getContext('2d'); ctx.drawImage(img,0,0);
  const d=ctx.getImageData(0,0,256,256).data;
  const res=TILE_RES, h=new Float32Array(res*res);
  for(let iy=0;iy<res;iy++) for(let ix=0;ix<res;ix++){
    const px=Math.min(255,Math.round(ix*255/(res-1)));
    const py=Math.min(255,Math.round(iy*255/(res-1)));
    const i=(py*256+px)*4;
    h[iy*res+ix]=d[i]*256+d[i+1]+d[i+2]/256-32768;
  }
  return h;
}
function buildTileMesh(tile,heights,texImg,homeElev){
  const res=TILE_RES,sw=tile.sw,ne=tile.ne;
  const pos=new Float32Array(res*res*3),uvs=new Float32Array(res*res*2),idx=[];
  for(let iy=0;iy<res;iy++) for(let ix=0;ix<res;ix++){
    const x=sw[0]+(ne[0]-sw[0])*ix/(res-1);
    const z=ne[1]+(sw[1]-ne[1])*iy/(res-1);
    const elev=heights[iy*res+ix]-homeElev;
    const i=iy*res+ix;
    pos[i*3]=x; pos[i*3+1]=elev; pos[i*3+2]=z;
    uvs[i*2]=ix/(res-1); uvs[i*2+1]=1-iy/(res-1);
  }
  for(let iy=0;iy<res-1;iy++) for(let ix=0;ix<res-1;ix++){
    const a=iy*res+ix,b=a+1,c=(iy+1)*res+ix,d=c+1;
    idx.push(a,c,b,b,c,d);
  }
  const g=new THREE.BufferGeometry();
  g.setAttribute('position',new THREE.BufferAttribute(pos,3));
  g.setAttribute('uv',new THREE.BufferAttribute(uvs,2));
  g.setIndex(idx); g.computeVertexNormals();
  let mat;
  if(texImg){const tex=new THREE.Texture(texImg);tex.needsUpdate=true;mat=new THREE.MeshLambertMaterial({map:tex});}
  else mat=new THREE.MeshLambertMaterial({color:0x4a7c4e});
  return new THREE.Mesh(g,mat);
}
function getHomeElev(){
  for(const tile of TILES){
    if(tile._h&&tile.sw[0]<=0&&0<=tile.ne[0]&&tile.ne[1]<=0&&0<=tile.sw[1]){
      const u=(0-tile.sw[0])/(tile.ne[0]-tile.sw[0]);
      const v=(0-tile.ne[1])/(tile.sw[1]-tile.ne[1]);
      const ix=Math.max(0,Math.min(TILE_RES-1,Math.round(u*(TILE_RES-1))));
      const iy=Math.max(0,Math.min(TILE_RES-1,Math.round(v*(TILE_RES-1))));
      return tile._h[iy*TILE_RES+ix];
    }
  }
  return 0;
}
function compositeSubTiles(subs,n,cb){
  if(!subs||subs.length===0){cb(null);return;}
  const cv=document.createElement('canvas'); cv.width=cv.height=256*n;
  const ctx=cv.getContext('2d');
  let loaded=0,total=n*n;
  subs.forEach(function(src,i){
    if(!src){loaded++;if(loaded===total)cb(cv);return;}
    const img=new Image();
    img.onload=function(){const ix=i%n,iy=Math.floor(i/n);ctx.drawImage(img,ix*256,iy*256);loaded++;if(loaded===total)cb(cv);};
    img.onerror=function(){loaded++;if(loaded===total)cb(cv);};
    img.src=src;
  });
}
function loadAllTiles(){
  if(TILES.length===0){startRender();return;}
  let done=0;
  function check(){done++;if(done===TILES.length){finishTiles();startRender();}}
  TILES.forEach(function(tile){
    tile._h=null;tile._texImg=null;
    if(!tile.terr){check();return;}
    const terrImg=new Image();
    terrImg.onload=function(){
      tile._h=decodeTerrain(terrImg);
      const subs=tile.tex_subs,n=tile.tex_n||1;
      if(subs&&subs.length>0){compositeSubTiles(subs,n,function(cv){tile._texImg=cv;check();});}
      else check();
    };
    terrImg.onerror=function(){check();};
    terrImg.src=tile.terr;
  });
}
function finishTiles(){
  const homeElev=getHomeElev();
  TILES.forEach(tile=>{
    if(!tile._h) return;
    const mesh=buildTileMesh(tile,tile._h,tile._texImg,homeElev);
    mesh.receiveShadow=true;
    scene.add(mesh);
  });
}

function startRender(){ requestAnimationFrame(animFrame); }

// ── Init ──────────────────────────────────────────────────
const midP=PTS[Math.floor(N_PTS/2)];
orbTarget.set(midP[0],midP[1],midP[2]);
orbRadius=300; orbPhi=0.7;
loadAllTiles();
</script>
</body>
</html>"""
