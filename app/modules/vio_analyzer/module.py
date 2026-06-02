import csv
import json
import math
import os

import numpy as np
import pyqtgraph as pg

from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QLabel,
    QPushButton, QSizePolicy, QFileDialog, QTreeWidget, QTreeWidgetItem,
)
from PyQt5.QtCore import Qt, QPoint, QUrl
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWebEngineWidgets import QWebEngineView

from app.modules.base_module import BaseModule
from app.modules.tile_fetch import HtmlBuilder, fetch_tiles, tile_bounds, make_loading_html
from app.modules.waypoints_3d_shared import waypoints_to_enu, WAYPOINTS_JS
from app.theme.style import COLORS

_ASSETS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'assets'))
_TMP_HTML   = os.path.join(_ASSETS_DIR, '_vio_analyzer.html')
_TILE_RES   = 64

_MODE_COLORS = {
    'MANUAL': '#E3B341', 'STABILIZE': '#58A6FF', 'ACRO': '#FFA657',
    'FBWA': '#58A6FF', 'FBWB': '#79C0FF', 'CRUISE': '#39C5CF',
    'AUTO': '#3FB950', 'RTL': '#F85149', 'LOITER': '#FF9F43',
    'TAKEOFF': '#56D364', 'GUIDED': '#BC8CFF', 'QSTABILIZE': '#56D364',
    'QHOVER': '#3FB950', 'QLOITER': '#2EA043', 'QLAND': '#F0883E',
    'QRTL': '#F85149', 'ALT_HOLD': '#79C0FF', 'POSHOLD': '#39C5CF',
    'LAND': '#F0883E', 'UNKNOWN': '#8B949E',
}
_DEFAULT_MODE_COLOR = '#8B949E'

_PALETTE = [
    '#58A6FF', '#F0883E', '#3FB950', '#BC8CFF',
    '#39C5CF', '#E3B341', '#FF7B72', '#FFA657',
]

_ODOM_GROUPS = [
    ('Position', [
        ('north', 'North (X NED) m'),
        ('east',  'East (Y NED) m'),
        ('alt',   'Altitude m'),
        ('hdist', 'Horiz. Distance m'),
    ]),
    ('Velocity', [
        ('vn',    'Vel North m/s'),
        ('ve',    'Vel East m/s'),
        ('speed', 'Speed m/s'),
    ]),
]


def _mode_color(name):
    return _MODE_COLORS.get(name.upper(), _DEFAULT_MODE_COLOR)


def _mode_colors_arr(time_us_arr, events):
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


def _legend_items(events):
    seen, items = set(), []
    for e in [ev for ev in events if ev.event_type == 'mode_change']:
        name = e.detail.replace('Mode: ', '').strip()
        if name not in seen:
            seen.add(name)
            items.append([name, _mode_color(name)])
    return items if len(items) > 1 else []


def _hex_rgb(h):
    h = h.lstrip('#')
    return [int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255]


# ── Module ───────────────────────────────────────────────────────────────────

class VioAnalyzerModule(BaseModule):

    MODULE_ID      = 'vio_analyzer'
    DISPLAY_NAME   = 'VIO Analyzer'
    DESCRIPTION    = 'Compare VIO odometry path against GPS flight path'
    ICON_CHAR      = '⊛'
    REQUIRED_MESSAGES = []

    def __init__(self):
        self._widget       = None
        self._log_data     = None
        self._odom         = {}     # computed channel arrays keyed by channel id
        self._odom_ts      = None   # relative time in seconds
        self._builder      = None
        self._view         = None
        self._plot         = None
        self._plot_items   = {}     # key -> (curve, color)
        self._color_idx    = 0
        self._map_ready    = False
        self._pending_vio  = None   # VIO pts to inject once map loads
        self._status_lbl   = None
        self._load_btn     = None
        self._tree         = None
        self._legend       = None
        self._hover_vline  = None
        self._hover_label  = None

    # ── BaseModule interface ────────────────────────────────────────────────

    def build_widget(self, parent=None):
        self._widget = QWidget(parent)
        self._widget.setStyleSheet(f"background-color:{COLORS['bg_primary']};")

        outer = QHBoxLayout(self._widget)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Left panel ──────────────────────────────────────────────────────
        left = QWidget()
        left.setFixedWidth(210)
        left.setStyleSheet(
            f"background-color:{COLORS['bg_secondary']};"
            f" border-right:1px solid {COLORS['border']};"
        )
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)

        hdr = QWidget()
        hdr.setFixedHeight(36)
        hdr.setStyleSheet(
            f"background-color:{COLORS['bg_secondary']};"
            f" border-bottom:1px solid {COLORS['border']};"
        )
        hdr_row = QHBoxLayout(hdr)
        hdr_row.setContentsMargins(8, 0, 6, 0)
        lbl = QLabel("VIO CHANNELS")
        lbl.setStyleSheet(
            f"color:{COLORS['text_disabled']};font-size:10px;font-weight:bold;border:none;"
        )
        hdr_row.addWidget(lbl)
        hdr_row.addStretch()
        ll.addWidget(hdr)

        # Load odom button
        btn_area = QWidget()
        btn_area.setStyleSheet(f"background:{COLORS['bg_secondary']};")
        btn_lay = QVBoxLayout(btn_area)
        btn_lay.setContentsMargins(8, 10, 8, 6)
        btn_lay.setSpacing(6)

        self._load_btn = QPushButton("⊕  Load Odom CSV")
        self._load_btn.setFixedHeight(28)
        self._load_btn.setCursor(Qt.PointingHandCursor)
        self._load_btn.setStyleSheet(
            f"QPushButton {{ background:{COLORS['bg_tertiary']};"
            f" color:{COLORS['text_secondary']};"
            f" border:1px solid {COLORS['border_active']};"
            f" border-radius:4px; font-size:11px; font-weight:bold; }}"
            f"QPushButton:hover {{ background:{COLORS['border_active']}22;"
            f" color:{COLORS['border_active']}; }}"
            f"QPushButton:pressed {{ background:{COLORS['border_active']}44; }}"
        )
        self._load_btn.clicked.connect(self._on_load_odom)
        btn_lay.addWidget(self._load_btn)

        self._status_lbl = QLabel("No odom data loaded")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setStyleSheet(
            f"color:{COLORS['text_disabled']};font-size:10px;border:none;"
        )
        btn_lay.addWidget(self._status_lbl)
        ll.addWidget(btn_area)

        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{COLORS['border']};")
        ll.addWidget(sep)

        # Channel tree
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setRootIsDecorated(True)
        self._tree.setIndentation(16)
        f = QFont(); f.setPointSize(10)
        self._tree.setFont(f)
        self._tree.setStyleSheet(
            f"QTreeWidget {{ background:{COLORS['bg_secondary']};border:none;"
            f" color:{COLORS['text_secondary']}; }}"
            f"QTreeWidget::item {{ height:26px;padding-left:4px; }}"
            f"QTreeWidget::item:hover {{ background:{COLORS['bg_hover']}; }}"
            f"QTreeWidget::item:selected {{ background:{COLORS['bg_tertiary']}; }}"
            "QTreeWidget::branch { background:transparent; }"
            f"QTreeWidget::indicator {{ width:14px;height:14px;"
            f" border:1px solid {COLORS['border']};border-radius:3px;"
            f" background:{COLORS['bg_tertiary']}; }}"
            f"QTreeWidget::indicator:hover {{ border-color:{COLORS['border_active']}; }}"
            f"QTreeWidget::indicator:checked {{ background:{COLORS['border_active']};"
            f" border-color:{COLORS['border_active']}; }}"
        )
        self._tree.setExpandsOnDoubleClick(False)
        self._tree.itemClicked.connect(self._on_item_clicked)
        self._tree.itemChanged.connect(self._on_item_changed)
        self._tree.itemExpanded.connect(
            lambda it: it.setText(0, it.text(0).replace('▸', '▾', 1))
            if it.data(0, Qt.UserRole) is None else None
        )
        self._tree.itemCollapsed.connect(
            lambda it: it.setText(0, it.text(0).replace('▾', '▸', 1))
            if it.data(0, Qt.UserRole) is None else None
        )
        ll.addWidget(self._tree, 1)
        outer.addWidget(left)

        # ── Right panel ─────────────────────────────────────────────────────
        right = QSplitter(Qt.Vertical)
        right.setHandleWidth(4)
        right.setStyleSheet(f"QSplitter::handle {{background-color:{COLORS['border']};}}")

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

        self._plot.scene().sigMouseMoved.connect(self._on_mouse_move)

        self._hover_vline = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen(COLORS['text_secondary'], width=1, style=Qt.DashLine),
        )
        self._hover_vline.setZValue(15)
        self._hover_vline.setVisible(False)
        self._plot.addItem(self._hover_vline)

        bg  = COLORS['bg_secondary']
        brd = COLORS['border']
        self._hover_label = QLabel(self._plot.viewport())
        self._hover_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._hover_label.setStyleSheet(
            f"background-color:{bg}EE;border:1px solid {brd};"
            f" padding:5px 8px;border-radius:5px;"
        )
        self._hover_label.setVisible(False)

        plot_wrap = QWidget()
        pw_lay = QVBoxLayout(plot_wrap)
        pw_lay.setContentsMargins(0, 0, 0, 0)
        pw_lay.setSpacing(0)
        pw_lay.addWidget(self._build_graph_toolbar())
        pw_lay.addWidget(self._plot)
        right.addWidget(plot_wrap)

        self._view = QWebEngineView()
        self._view.setMinimumHeight(80)
        self._view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._view.loadFinished.connect(self._on_map_loaded)
        self._view.setHtml(
            f"<html><body style='background:{COLORS['bg_primary']};margin:0'></body></html>"
        )
        right.addWidget(self._view)
        right.setSizes([360, 440])
        right.setCollapsible(0, False)
        right.setCollapsible(1, False)
        outer.addWidget(right, 1)

        return self._widget

    def load_data(self, log_data):
        self._log_data  = log_data
        self._map_ready = False

        # Reset 3D view
        self._view.setHtml(make_loading_html('⊛ VIO Analyzer'))
        if self._builder and self._builder.isRunning():
            self._builder.terminate()
            self._builder.wait()

        self._builder = HtmlBuilder(log_data, self._make_3d_html, _TMP_HTML)
        self._builder.ready.connect(self._on_html_ready)
        self._builder.error.connect(self._on_build_error)
        self._builder.progress.connect(self._on_build_progress)
        self._builder.start()

        # Re-inject VIO path when map reloads if odom already loaded
        if self._odom_ts is not None:
            self._pending_vio = self._vio_enu_pts()

    def clear(self):
        if self._builder and self._builder.isRunning():
            self._builder.terminate()
            self._builder.wait()
        self._log_data  = None
        self._map_ready = False
        self._odom      = {}
        self._odom_ts   = None
        self._pending_vio = None
        if self._plot:
            self._plot_items.clear()
            self._plot.clear()
            self._legend = self._plot.addLegend(offset=(10, 10))
            self._legend.setBrush(pg.mkBrush(COLORS['bg_secondary'] + 'CC'))
            self._legend.setPen(pg.mkPen(COLORS['border']))
        if self._tree:
            self._tree.blockSignals(True)
            self._tree.clear()
            self._tree.blockSignals(False)
        if self._status_lbl:
            self._status_lbl.setText("No odom data loaded")
        if self._hover_label:
            self._hover_label.setVisible(False)
        if self._view:
            self._view.setHtml(
                f"<html><body style='background:{COLORS['bg_primary']};margin:0'></body></html>"
            )

    # ── Odom loading ────────────────────────────────────────────────────────

    def _on_load_odom(self):
        path, _ = QFileDialog.getOpenFileName(
            self._widget, "Open Odom CSV",
            os.path.expanduser("~"),
            "CSV Files (*.csv);;All Files (*)"
        )
        if not path:
            return
        self._parse_odom(path)

    def _parse_odom(self, path):
        try:
            ts_us, xn, ye, zd, vxn, vye, vzd = [], [], [], [], [], [], []
            with open(path, newline='') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ts_us.append(float(row['timestamp_us']))
                    xn.append(float(row['x_ned_m']))
                    ye.append(float(row['y_ned_m']))
                    zd.append(float(row['z_ned_m']))
                    vxn.append(float(row['vx_mps']))
                    vye.append(float(row['vy_mps']))
                    vzd.append(float(row['vz_mps']))

            ts_us = np.array(ts_us, dtype=np.float64)
            xn    = np.array(xn,    dtype=np.float64)
            ye    = np.array(ye,    dtype=np.float64)
            zd    = np.array(zd,    dtype=np.float64)
            vxn   = np.array(vxn,   dtype=np.float64)
            vye   = np.array(vye,   dtype=np.float64)
            vzd   = np.array(vzd,   dtype=np.float64)

            t0 = ts_us[0]
            self._odom_ts = (ts_us - t0) / 1_000_000.0
            self._odom = {
                'north': xn,
                'east':  ye,
                'alt':   -zd,
                'hdist': np.sqrt(xn**2 + ye**2),
                'vn':    vxn,
                've':    vye,
                'speed': np.sqrt(vxn**2 + vye**2 + vzd**2),
            }

            n    = len(ts_us)
            dur  = self._odom_ts[-1]
            self._status_lbl.setText(
                f"{n:,} pts  ·  {dur:.1f} s\n{os.path.basename(path)}"
            )
            self._status_lbl.setStyleSheet(
                f"color:{COLORS['text_secondary']};font-size:10px;border:none;"
            )

            self._rebuild_tree()
            self._inject_vio(self._vio_enu_pts())

        except Exception as e:
            self._status_lbl.setText(f"Error: {e}")
            self._status_lbl.setStyleSheet(
                f"color:{COLORS['warning']};font-size:10px;border:none;"
            )

    def _vio_enu_pts(self):
        if self._odom_ts is None:
            return []
        xn  = self._odom['north']
        ye  = self._odom['east']
        alt = self._odom['alt']
        # NED → Three.js ENU: x=East(y_ned), y=Up(-z_ned=alt), z=-North(-x_ned)
        return [[round(float(ye[i]), 3),
                 round(float(alt[i]), 3),
                 round(-float(xn[i]), 3)]
                for i in range(len(xn))]

    def _inject_vio(self, pts):
        if self._map_ready:
            self._view.page().runJavaScript(f"loadVioPath({json.dumps(pts)});")
        else:
            self._pending_vio = pts

    # ── Graph tree ──────────────────────────────────────────────────────────

    def _rebuild_tree(self):
        self._plot.clear()
        self._plot_items.clear()
        self._color_idx = 0
        self._legend = self._plot.addLegend(offset=(10, 10))
        self._legend.setBrush(pg.mkBrush(COLORS['bg_secondary'] + 'CC'))
        self._legend.setPen(pg.mkPen(COLORS['border']))
        self._hover_vline = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen(COLORS['text_secondary'], width=1, style=Qt.DashLine),
        )
        self._hover_vline.setZValue(15)
        self._hover_vline.setVisible(False)
        self._plot.addItem(self._hover_vline)

        self._tree.blockSignals(True)
        self._tree.clear()

        for grp_label, channels in _ODOM_GROUPS:
            available = [(cid, cname) for cid, cname in channels if cid in self._odom]
            if not available:
                continue
            grp = QTreeWidgetItem([f"  ▸  {grp_label}"])
            grp.setFlags(grp.flags() & ~Qt.ItemIsUserCheckable)
            gf = QFont(); gf.setBold(True); gf.setPointSize(10)
            grp.setFont(0, gf)
            grp.setForeground(0, QColor(COLORS['text_primary']))
            for cid, cname in available:
                child = QTreeWidgetItem([f"   {cname}"])
                child.setData(0, Qt.UserRole, cid)
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Unchecked)
                grp.addChild(child)
            self._tree.addTopLevelItem(grp)
            grp.setExpanded(True)

        self._tree.blockSignals(False)

    def _on_item_clicked(self, item, column):
        if item.data(0, Qt.UserRole) is None:
            item.setExpanded(not item.isExpanded())

    def _on_item_changed(self, item, column):
        cid = item.data(0, Qt.UserRole)
        if cid is None:
            return
        checked = item.checkState(0) == Qt.Checked
        if checked and cid not in self._plot_items:
            self._add_line(item, cid)
        elif not checked and cid in self._plot_items:
            self._remove_line(item, cid)

    def _add_line(self, item, cid):
        if self._odom_ts is None or cid not in self._odom:
            return
        color = _PALETTE[self._color_idx % len(_PALETTE)]
        self._color_idx += 1
        name = dict(sum([list(g) for _, g in _ODOM_GROUPS], [])).get(cid, cid)
        curve = self._plot.plot(
            self._odom_ts, self._odom[cid],
            pen=pg.mkPen(color=color, width=1.5), name=name,
        )
        self._plot_items[cid] = (curve, color)
        item.setForeground(0, QColor(color))

    def _remove_line(self, item, cid):
        if cid not in self._plot_items:
            return
        curve, _ = self._plot_items.pop(cid)
        self._plot.removeItem(curve)
        try:
            self._legend.removeItem(curve)
        except Exception:
            pass
        item.setForeground(0, QColor(COLORS['text_secondary']))

    # ── Graph toolbar ───────────────────────────────────────────────────────

    def _build_graph_toolbar(self):
        bar = QWidget()
        bar.setFixedHeight(30)
        bar.setStyleSheet(
            f"background-color:{COLORS['bg_secondary']};"
            f" border-bottom:1px solid {COLORS['border']};"
        )
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 0, 8, 0)
        row.setSpacing(4)

        _bs = (
            f"QPushButton {{background:{COLORS['bg_tertiary']};color:{COLORS['text_disabled']};"
            f" border:1px solid {COLORS['border']};border-radius:3px;"
            f" font-size:9px;font-weight:bold;padding:0 8px;}}"
            f"QPushButton:hover {{color:{COLORS['text_secondary']};"
            f" border-color:{COLORS['border_active']}55;}}"
            f"QPushButton:pressed {{background:{COLORS['border_active']}22;"
            f" color:{COLORS['border_active']};border-color:{COLORS['border_active']};}}"
        )

        def _btn(label, slot):
            b = QPushButton(label)
            b.setFixedHeight(20)
            b.setStyleSheet(_bs)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            return b

        row.addWidget(_btn("⌂  Reset View", lambda: self._plot.plotItem.vb.autoRange()))
        row.addWidget(_btn("↕  Auto Y",     self._on_auto_y))
        row.addStretch()
        return bar

    def _on_auto_y(self):
        if not self._plot:
            return
        vb = self._plot.plotItem.vb
        xr = vb.viewRange()[0]
        vb.autoRange()
        vb.setXRange(xr[0], xr[1], padding=0)

    # ── Hover tooltip ───────────────────────────────────────────────────────

    def _on_mouse_move(self, scene_pos):
        if not self._plot or not self._hover_label:
            return
        vb = self._plot.plotItem.vb
        if not vb.sceneBoundingRect().contains(scene_pos):
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
        for cid, (curve, color) in self._plot_items.items():
            xd, yd = curve.getData()
            if xd is None or len(xd) == 0:
                continue
            idx = int(np.searchsorted(xd, t))
            idx = max(0, min(idx, len(xd) - 1))
            rows.append((color, curve.name() or cid, float(yd[idx])))

        if not rows:
            self._hover_label.setVisible(False)
            return

        ts = COLORS['text_secondary']
        tp = COLORS['text_primary']
        html = (
            f"<span style='color:{ts};font-size:10px'>t = {t:.2f} s</span>"
            "<table cellspacing='1' style='margin-top:3px'>"
            + ''.join(
                f"<tr><td><span style='color:{c}'>&#9632;&nbsp;</span></td>"
                f"<td style='color:{ts};font-size:11px;padding-right:6px'>{n}</td>"
                f"<td style='color:{tp};font-size:11px'><b>{v:.3f}</b></td></tr>"
                for c, n, v in rows
            )
            + "</table>"
        )
        self._hover_label.setText(html)
        self._hover_label.adjustSize()

        pt = self._plot.mapFromScene(scene_pos)
        vp = self._plot.viewport()
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

    # ── 3D view ─────────────────────────────────────────────────────────────

    def _on_html_ready(self):
        self._view.load(QUrl.fromLocalFile(_TMP_HTML))

    def _on_build_error(self, msg):
        self._view.setHtml(self._empty_html(f'Build error: {msg}'))

    def _on_build_progress(self, done, total):
        self._view.page().runJavaScript(f'if(window.updateProgress)updateProgress({done},{total})')

    def _on_map_loaded(self, ok):
        if self._view.url() != QUrl.fromLocalFile(_TMP_HTML):
            return
        self._map_ready = ok
        if ok and self._pending_vio is not None:
            pts = self._pending_vio
            self._pending_vio = None
            self._inject_vio(pts)

    def _empty_html(self, msg):
        return (
            f'<!DOCTYPE html><html><body style="background:{COLORS["bg_primary"]};'
            f'color:{COLORS["text_secondary"]};display:flex;align-items:center;'
            f'justify-content:center;height:100vh;margin:0;font-family:sans-serif;'
            f'font-size:16px;">{msg}</body></html>'
        )

    def _make_3d_html(self, log_data, progress_cb=None):
        pos = log_data.pos
        if 'Lat' not in pos or len(pos.get('Lat', [])) == 0:
            return self._empty_html('No GPS data in this log.')

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

        colors_hex = _mode_colors_arr(times, log_data.events)
        colors_rgb = [_hex_rgb(c) for c in colors_hex]

        zoom, sat_n, min_tx, min_ty, max_tx, max_ty, terr_b64, tex_subs_b64 = \
            fetch_tiles(lats, lngs, home_lat, home_lng, progress_cb)

        tiles_js = []
        for ty in range(min_ty, max_ty + 1):
            for tx in range(min_tx, max_tx + 1):
                w_b, s_b, e_b, n_b = tile_bounds(tx, ty, zoom)
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

        waypoints_js = waypoints_to_enu(log_data.waypoints, home_lat, home_lng, R)
        legend       = _legend_items(log_data.events)

        data_js = f"""
const HOME_LAT={home_lat}, HOME_LNG={home_lng};
const N_PTS={len(pts)};
const TILE_RES={_TILE_RES};
const TILES={json.dumps(tiles_js)};
const PTS={json.dumps(pts)};
const PATH_COLORS={json.dumps([[round(c, 3) for c in rgb] for rgb in colors_rgb])};
const LEGEND_ITEMS={json.dumps(legend)};
const WAYPOINTS={json.dumps(waypoints_js)};
"""
        return _HTML_TEMPLATE.replace('/*DATA_JS*/', data_js).replace('/*WAYPOINTS_JS*/', WAYPOINTS_JS)


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
  position:absolute;bottom:0;left:0;right:0;height:40px;z-index:10;
  background:rgba(13,17,23,0.92);border-top:1px solid #30363D;
  display:flex;align-items:center;gap:6px;padding:0 10px;
}
.cb{background:#161B22;color:#C9D1D9;border:1px solid #30363D;
    border-radius:4px;padding:3px 10px;cursor:pointer;font-size:12px;white-space:nowrap}
.cb:hover{background:#21262D}
.cb.on{background:#1F6FEB;color:#fff;border-color:#1F6FEB}
.sep{width:1px;background:#30363D;height:20px;flex-shrink:0}
#mode-legend{
  position:absolute;top:8px;right:8px;z-index:9;
  background:rgba(13,17,23,0.88);border:1px solid #30363D;
  border-radius:6px;padding:8px 11px;font-family:monospace;font-size:11px;
  pointer-events:none;
}
#mode-legend-title{color:#8B949E;font-size:10px;margin-bottom:5px;letter-spacing:.05em}
.ml-row{display:flex;align-items:center;gap:7px;margin:3px 0}
.ml-dot{width:9px;height:9px;border-radius:50%;flex-shrink:0}
.ml-name{color:#C9D1D9}
#vio-badge{
  position:absolute;top:8px;left:8px;z-index:9;
  background:rgba(13,17,23,0.88);border:1px solid #30363D;
  border-radius:6px;padding:6px 10px;font-family:monospace;font-size:11px;
  color:#8B949E;pointer-events:none;display:none;
}
#vio-badge span{color:#F0883E;font-weight:bold}
</style>
</head>
<body>
<canvas id="canvas"></canvas>
<div id="mode-legend"><div id="mode-legend-title">FLIGHT MODES</div></div>
<div id="vio-badge">VIO: <span id="vio-pts">0</span> pts</div>
<div id="controls">
  <span style="color:#8B949E;font-size:11px">Camera:</span>
  <button class="cb cm on" id="bfr" onclick="setCam('free')">Free</button>
  <button class="cb cm"    id="bfo" onclick="setCam('orbit')">Orbit Path</button>
  <div class="sep"></div>
  <button class="cb on" id="bgps" onclick="toggleGps()">GPS Path</button>
  <button class="cb on" id="bvio" onclick="toggleVio()">VIO Path</button>
  <button class="cb on" id="bwp"  onclick="toggleWaypoints()">WP</button>
  <div class="sep"></div>
  <button class="cb" onclick="resetCamera()">⌂ Reset</button>
</div>
<script src="three.min.js"></script>
<script>
/*DATA_JS*/

// ── Mode legend ───────────────────────────────────────────────────────────
(function(){
  const legend = document.getElementById('mode-legend');
  if(!LEGEND_ITEMS || LEGEND_ITEMS.length === 0){ legend.style.display='none'; return; }
  LEGEND_ITEMS.forEach(function([name, color]){
    const row = document.createElement('div');
    row.className = 'ml-row';
    row.innerHTML = '<div class="ml-dot" style="background:'+color+'"></div>'
                  + '<span class="ml-name">'+name+'</span>';
    legend.appendChild(row);
  });
  // VIO row (hidden until VIO loads)
  const vrow = document.createElement('div');
  vrow.className = 'ml-row';
  vrow.id = 'vio-legend-row';
  vrow.style.display = 'none';
  vrow.innerHTML = '<div class="ml-dot" style="background:#F0883E"></div>'
                 + '<span class="ml-name">VIO Path</span>';
  legend.appendChild(vrow);
})();

// ── Renderer / Scene ──────────────────────────────────────────────────────
const canvas = document.getElementById('canvas');
function W(){ return window.innerWidth; }
function H(){ return window.innerHeight - 40; }

const renderer = new THREE.WebGLRenderer({canvas, antialias:true});
renderer.setPixelRatio(window.devicePixelRatio);
renderer.shadowMap.enabled = true;
renderer.setClearColor(0x1a2a3a);

const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(0x1a2a3a, 0.00008);
const camera = new THREE.PerspectiveCamera(60, W()/H(), 0.5, 80000);

scene.add(new THREE.AmbientLight(0xffffff, 0.55));
const sun = new THREE.DirectionalLight(0xffffff, 0.9);
sun.position.set(300, 800, 200); sun.castShadow = true;
scene.add(sun);

(function(){
  const sg = new THREE.SphereGeometry(40000,8,8);
  scene.add(new THREE.Mesh(sg, new THREE.MeshBasicMaterial({color:0x1a2a3a,side:THREE.BackSide})));
})();

// ── Orbit controls ────────────────────────────────────────────────────────
let orbTheta=0.3, orbPhi=0.8, orbRadius=300;
const orbTarget = new THREE.Vector3();
let orbDrag=false, panDrag=false, lastX=0, lastY=0;
let camMode='free';

canvas.addEventListener('mousedown', e=>{
  if(e.button===0) orbDrag=true;
  else if(e.button===2) panDrag=true;
  lastX=e.clientX; lastY=e.clientY;
});
window.addEventListener('mouseup', ()=>{ orbDrag=panDrag=false; });
window.addEventListener('mousemove', e=>{
  const dx=e.clientX-lastX, dy=e.clientY-lastY;
  lastX=e.clientX; lastY=e.clientY;
  if(orbDrag){
    orbTheta -= dx*0.006;
    orbPhi = Math.max(0.18, Math.min(1.52, orbPhi-dy*0.006));
  } else if(panDrag){
    const r=new THREE.Vector3();
    r.crossVectors(camera.getWorldDirection(new THREE.Vector3()), new THREE.Vector3(0,1,0)).normalize();
    orbTarget.addScaledVector(r, -dx*orbRadius*0.0012);
    orbTarget.y += dy*orbRadius*0.0012;
  }
});
canvas.addEventListener('wheel', e=>{
  const step=Math.max(1,orbRadius*0.08);
  orbRadius=Math.max(1,orbRadius+(e.deltaY>0?1:-1)*step);
  e.preventDefault();
},{passive:false});
canvas.addEventListener('contextmenu', e=>e.preventDefault());

function onResize(){
  renderer.setSize(W(), H());
  camera.aspect=W()/H();
  camera.updateProjectionMatrix();
}
window.addEventListener('resize', onResize);
onResize();

// ── GPS path (colored line) ───────────────────────────────────────────────
const gpsPositions = new Float32Array(N_PTS*3);
const gpsColors    = new Float32Array(N_PTS*3);
for(let i=0;i<N_PTS;i++){
  gpsPositions[i*3]=PTS[i][0]; gpsPositions[i*3+1]=PTS[i][1]; gpsPositions[i*3+2]=PTS[i][2];
  gpsColors[i*3]=PATH_COLORS[i][0]; gpsColors[i*3+1]=PATH_COLORS[i][1]; gpsColors[i*3+2]=PATH_COLORS[i][2];
}
const gpsGeom = new THREE.BufferGeometry();
gpsGeom.setAttribute('position', new THREE.BufferAttribute(gpsPositions,3));
gpsGeom.setAttribute('color',    new THREE.BufferAttribute(gpsColors,3));
const gpsLine = new THREE.Line(gpsGeom, new THREE.LineBasicMaterial({vertexColors:true,linewidth:2}));
scene.add(gpsLine);

// Home marker (green sphere)
(function(){
  const g=new THREE.SphereGeometry(2,8,8);
  const m=new THREE.MeshBasicMaterial({color:0x3FB950});
  const mesh=new THREE.Mesh(g,m);
  mesh.position.set(0,0,0);
  scene.add(mesh);
})();

/*WAYPOINTS_JS*/

// ── VIO path (added dynamically) ─────────────────────────────────────────
let vioLine = null;

window.loadVioPath = function(pts){
  if(vioLine){ scene.remove(vioLine); vioLine.geometry.dispose(); vioLine=null; }
  if(!pts || pts.length<2) return;
  const pos=new Float32Array(pts.length*3);
  for(let i=0;i<pts.length;i++){
    pos[i*3]=pts[i][0]; pos[i*3+1]=pts[i][1]; pos[i*3+2]=pts[i][2];
  }
  const g=new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos,3));
  vioLine=new THREE.Line(g, new THREE.LineBasicMaterial({color:0xF0883E,linewidth:2}));
  scene.add(vioLine);

  document.getElementById('vio-badge').style.display='block';
  document.getElementById('vio-pts').textContent=pts.length.toLocaleString();
  const vrow=document.getElementById('vio-legend-row');
  if(vrow) vrow.style.display='flex';

  // Fit camera to show both GPS and VIO paths
  fitBoth();
};

function fitBoth(){
  // Compute bounding box of GPS + VIO combined
  let minX=Infinity,maxX=-Infinity,minY=Infinity,maxY=-Infinity,minZ=Infinity,maxZ=-Infinity;
  for(let i=0;i<N_PTS;i++){
    minX=Math.min(minX,PTS[i][0]); maxX=Math.max(maxX,PTS[i][0]);
    minY=Math.min(minY,PTS[i][1]); maxY=Math.max(maxY,PTS[i][1]);
    minZ=Math.min(minZ,PTS[i][2]); maxZ=Math.max(maxZ,PTS[i][2]);
  }
  if(vioLine){
    const pa=vioLine.geometry.attributes.position.array;
    for(let i=0;i<pa.length;i+=3){
      minX=Math.min(minX,pa[i]);   maxX=Math.max(maxX,pa[i]);
      minY=Math.min(minY,pa[i+1]); maxY=Math.max(maxY,pa[i+1]);
      minZ=Math.min(minZ,pa[i+2]); maxZ=Math.max(maxZ,pa[i+2]);
    }
  }
  orbTarget.set((minX+maxX)/2, (minY+maxY)/2, (minZ+maxZ)/2);
  orbRadius = Math.max(50, Math.max(maxX-minX, maxY-minY, maxZ-minZ) * 1.2);
  orbTheta = 0.4; orbPhi = 0.75;
}

// ── Terrain tiles ─────────────────────────────────────────────────────────
function decodeTerrain(img){
  const cv=document.createElement('canvas'); cv.width=cv.height=256;
  const ctx=cv.getContext('2d'); ctx.drawImage(img,0,0);
  const d=ctx.getImageData(0,0,256,256).data;
  const res=TILE_RES; const h=new Float32Array(res*res);
  for(let iy=0;iy<res;iy++) for(let ix=0;ix<res;ix++){
    const px=Math.min(255,Math.round(ix*255/(res-1)));
    const py=Math.min(255,Math.round(iy*255/(res-1)));
    const i=(py*256+px)*4;
    h[iy*res+ix]=d[i]*256+d[i+1]+d[i+2]/256-32768;
  }
  return h;
}

function buildTileMesh(tile,heights,texImg,homeElev){
  const res=TILE_RES; const sw=tile.sw,ne=tile.ne;
  const pos=new Float32Array(res*res*3), uvs=new Float32Array(res*res*2), idx=[];
  for(let iy=0;iy<res;iy++) for(let ix=0;ix<res;ix++){
    const x=sw[0]+(ne[0]-sw[0])*ix/(res-1);
    const z=ne[1]+(sw[1]-ne[1])*iy/(res-1);
    const elev=heights[iy*res+ix]-homeElev;
    const i=iy*res+ix;
    pos[i*3]=x; pos[i*3+1]=elev; pos[i*3+2]=z;
    uvs[i*2]=ix/(res-1); uvs[i*2+1]=1-iy/(res-1);
  }
  for(let iy=0;iy<res-1;iy++) for(let ix=0;ix<res-1;ix++){
    const a=iy*res+ix,b=a+1,c=(iy+1)*res+ix,d2=c+1;
    idx.push(a,c,b,b,c,d2);
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
  const ctx=cv.getContext('2d'); let loaded=0; const total=n*n;
  subs.forEach(function(src,i){
    if(!src){loaded++;if(loaded===total)cb(cv);return;}
    const img=new Image();
    img.onload=function(){ctx.drawImage(img,(i%n)*256,Math.floor(i/n)*256);loaded++;if(loaded===total)cb(cv);};
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
    const ti=new Image();
    ti.onload=function(){
      tile._h=decodeTerrain(ti);
      compositeSubTiles(tile.tex_subs,tile.tex_n||1,function(cv){tile._texImg=cv;check();});
    };
    ti.onerror=function(){check();};
    ti.src=tile.terr;
  });
}

function finishTiles(){
  const homeElev=getHomeElev();
  TILES.forEach(tile=>{
    if(!tile._h)return;
    const mesh=buildTileMesh(tile,tile._h,tile._texImg,homeElev);
    mesh.receiveShadow=true;
    scene.add(mesh);
  });
}

// ── Camera controls ───────────────────────────────────────────────────────
function setCam(m){
  camMode=m;
  document.getElementById('bfr').classList.toggle('on',m==='free');
  document.getElementById('bfo').classList.toggle('on',m==='orbit');
}

function resetCamera(){
  const mid=PTS[Math.floor(N_PTS/2)];
  orbTarget.set(mid[0],mid[1],mid[2]);
  orbRadius=300; orbTheta=0.3; orbPhi=0.8;
}

function toggleGps(){
  gpsLine.visible=!gpsLine.visible;
  document.getElementById('bgps').classList.toggle('on',gpsLine.visible);
}
function toggleVio(){
  if(vioLine){vioLine.visible=!vioLine.visible;}
  document.getElementById('bvio').classList.toggle('on',vioLine?vioLine.visible:false);
}

// ── Render loop ───────────────────────────────────────────────────────────
function updateCamera(){
  const x=orbTarget.x+orbRadius*Math.sin(orbPhi)*Math.sin(orbTheta);
  const y=orbTarget.y+orbRadius*Math.cos(orbPhi);
  const z=orbTarget.z+orbRadius*Math.sin(orbPhi)*Math.cos(orbTheta);
  camera.up.set(0,1,0);
  camera.position.set(x,y,z);
  camera.lookAt(orbTarget);
}

function animFrame(){
  requestAnimationFrame(animFrame);
  updateCamera();
  renderer.render(scene,camera);
}

function startRender(){
  // Init camera to mid-path
  const mid=PTS[Math.floor(N_PTS/2)];
  orbTarget.set(mid[0],mid[1],mid[2]);
  orbRadius=300; orbPhi=0.7;
  requestAnimationFrame(animFrame);
}

loadAllTiles();
</script>
</body>
</html>
"""
