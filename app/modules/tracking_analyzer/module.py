"""
Tracking Analyzer — how well does the vehicle follow what the controller asked?

Desired-vs-actual pairs (ATT attitude, RATE body rates) are scored with
error-based metrics: MAE, RMSE, 95th-percentile and max |error|, a
setpoint-normalized MAE, the cross-correlation lag between the two signals,
and overshoot after fast setpoint changes. Mean error on its own is not
reported as a quality number — it only appears as a trim/bias hint.
"""

from collections import namedtuple

import numpy as np
import pyqtgraph as pg

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox, QComboBox,
    QSplitter, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QPushButton,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont

from app.modules.base_module import BaseModule
from app.modules.tracking_analyzer.metrics import tracking_metrics, wrap180
from app.theme.style import COLORS


# ── Axis definitions ─────────────────────────────────────────
# `floor` is the smallest |desired| that counts as "the axis is being worked":
# it gates the normalized MAE and sets the step size for overshoot detection.
# `normalize` is off for heading — |desired| there is an absolute bearing, so
# the error ratio would be divided by ~180° and always look excellent.
# `max_lag` bounds the delay search: a rate loop that is 300 ms late is broken,
# while an airframe taking 700 ms to reach a commanded bank angle is ordinary.
Axis = namedtuple(
    'Axis', 'label attr des act unit angular floor normalize max_lag')

_AXES = [
    Axis('Roll',       'att',  'DesRoll',  'Roll',  '°',   True,  2.0, True,  1.0),
    Axis('Pitch',      'att',  'DesPitch', 'Pitch', '°',   True,  2.0, True,  1.0),
    Axis('Yaw',        'att',  'DesYaw',   'Yaw',   '°',   True,  5.0, False, 1.0),
    Axis('Roll rate',  'rate', 'RDes',     'R',     '°/s', False, 5.0, True,  0.5),
    Axis('Pitch rate', 'rate', 'PDes',     'P',     '°/s', False, 5.0, True,  0.5),
    Axis('Yaw rate',   'rate', 'YDes',     'Y',     '°/s', False, 5.0, True,  0.5),
]

# Below this correlation, actual barely follows desired at all: the axis was
# not under closed-loop tracking in the modes flown (ArduPlane's DesYaw is the
# usual case), so its error says nothing about the tune.
_CORR_MEANINGFUL = 0.5

# Rules of thumb for cell colouring — (good below, acceptable below).
_THRESH = {
    '°':   {'mae': (2.0,  5.0),  'p95': (5.0,  12.0)},
    '°/s': {'mae': (8.0, 20.0),  'p95': (20.0, 45.0)},
}
_LAG_THRESH = (0.080, 0.150)      # seconds

_COLUMNS = [
    ('Axis',       'Desired-vs-actual pair being scored'),
    ('MAE',        'Mean absolute error — overall tracking error'),
    ('RMSE',       'Root mean squared error — penalises large misses harder than MAE'),
    ('P95 |e|',    '95th percentile absolute error — how bad the hard parts get,\n'
                   'without one spike dominating'),
    ('Max |e|',    'Worst single sample — secondary metric, noise and spikes distort it'),
    ('Norm MAE',   'MAE ÷ mean |desired|, over samples where the axis was actually\n'
                   'being worked. Comparable across flights of different intensity.'),
    ('Lag',        'Delay at which actual best correlates with desired'),
    ('r @ lag',    'Correlation at that lag — high r with high MAE means the shape\n'
                   'is right but the response is late or scaled wrong'),
    ('Overshoot',  'Median overshoot past the setpoint after fast setpoint steps,\n'
                   'in percent of the step size (step count in brackets)'),
    ('Bias',       'Mean signed error — trim hint only, not a quality metric'),
    ('Samples',    'Samples inside the analysis window'),
]

_WINDOWS = [
    ('armed', 'Armed only'),
    ('full',  'Full log'),
    ('sel',   'Selection'),
]


def _fmt(v, unit='', digits=2):
    if v is None or not np.isfinite(v):
        return '—'
    return f'{v:.{digits}f}{unit}'


def _longest_run(mask):
    """(start, stop) of the longest contiguous True run, or None."""
    if not mask.any():
        return None
    idx = np.flatnonzero(mask)
    breaks = np.flatnonzero(np.diff(idx) > 1)
    starts = np.r_[idx[0], idx[breaks + 1]]
    stops  = np.r_[idx[breaks], idx[-1]]
    k = int(np.argmax(stops - starts))
    return int(starts[k]), int(stops[k]) + 1


class TrackingAnalyzerModule(BaseModule):

    MODULE_ID    = 'tracking_analyzer'
    DISPLAY_NAME = 'Tracking Analyzer'
    DESCRIPTION  = 'Desired-vs-actual tracking error per axis — MAE, RMSE, P95, lag, overshoot'
    ICON_CHAR    = '⤢'
    REQUIRED_MESSAGES = ['ATT']

    def __init__(self):
        self._widget    = None
        self._log_data  = None
        self._t0        = 0
        self._table     = None
        self._results   = []          # list of (axis_def, metrics dict)
        self._sel_axis  = 0
        self._window    = 'armed'
        self._region    = None
        self._suppress  = False       # guard against region/combo feedback

    # ── BaseModule interface ────────────────────────────────

    def build_widget(self, parent=None) -> QWidget:
        self._widget = QWidget(parent)
        self._widget.setStyleSheet(f'background-color: {COLORS["bg_primary"]};')
        layout = QVBoxLayout(self._widget)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # ── Header ───────────────────────────────────────────
        top = QHBoxLayout()
        title = QLabel('Tracking Analyzer')
        title.setStyleSheet(
            f'font-size:18px; font-weight:bold; color:{COLORS["text_primary"]};'
            ' background:transparent;'
        )
        top.addWidget(title)

        self._window_lbl = QLabel('')
        self._window_lbl.setStyleSheet(
            f'color:{COLORS["text_secondary"]}; font-size:12px; background:transparent;'
            ' padding-left:10px;'
        )
        top.addWidget(self._window_lbl)
        top.addStretch()

        win_lbl = QLabel('Window:')
        win_lbl.setStyleSheet(f'color:{COLORS["text_secondary"]}; background:transparent;')
        top.addWidget(win_lbl)

        self._window_combo = QComboBox()
        for key, label in _WINDOWS:
            self._window_combo.addItem(label, key)
        self._window_combo.setFixedWidth(130)
        self._window_combo.currentIndexChanged.connect(self._on_window_changed)
        top.addWidget(self._window_combo)

        reset_btn = QPushButton('Reset zoom')
        reset_btn.setCursor(Qt.PointingHandCursor)
        reset_btn.clicked.connect(self._on_reset_zoom)
        top.addWidget(reset_btn)

        layout.addLayout(top)

        # ── Metrics table ────────────────────────────────────
        box_style = (
            f'QGroupBox {{ background-color:{COLORS["bg_secondary"]};'
            f' border:1px solid {COLORS["border"]}; border-radius:8px;'
            f' margin-top:10px; padding-top:8px; color:{COLORS["text_secondary"]};'
            f' font-size:11px; }}'
            f'QGroupBox::title {{ subcontrol-origin: margin; left:10px; padding:0 4px; }}'
        )

        table_box = QGroupBox('Per-Axis Tracking Error')
        table_box.setStyleSheet(box_style)
        tb_layout = QVBoxLayout(table_box)
        tb_layout.setContentsMargins(10, 8, 10, 10)

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels([c[0] for c in _COLUMNS])
        for i, (_, tip) in enumerate(_COLUMNS):
            self._table.horizontalHeaderItem(i).setToolTip(tip)
        hdr = self._table.horizontalHeader()
        hdr.setMinimumSectionSize(56)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for i in range(1, len(_COLUMNS)):
            hdr.setSectionResizeMode(i, QHeaderView.Stretch)
        hdr.setStyleSheet(
            f'QHeaderView::section {{ background-color:{COLORS["bg_tertiary"]};'
            f' color:{COLORS["text_secondary"]}; font-size:11px; font-weight:bold;'
            f' border:none; border-bottom:1px solid {COLORS["border"]}; padding:6px; }}'
        )
        self._table.setStyleSheet(
            f'QTableWidget {{ background-color:{COLORS["bg_secondary"]};'
            f' color:{COLORS["text_primary"]}; border:1px solid {COLORS["border"]};'
            f' border-radius:6px; gridline-color:{COLORS["border"]}; font-size:13px; }}'
            f'QTableWidget::item {{ padding:5px 8px; border:none; }}'
            f'QTableWidget::item:selected {{ background-color:{COLORS["bg_hover"]};'
            f' color:{COLORS["text_primary"]}; }}'
        )
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.setMinimumHeight(120)
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        tb_layout.addWidget(self._table)

        c_ok, c_warn, c_bad = COLORS['success'], COLORS['warning'], COLORS['danger']
        self._verdict = QLabel('')
        self._verdict.setStyleSheet(
            f'color:{COLORS["text_secondary"]}; font-size:12px; background:transparent;'
        )
        self._verdict.setWordWrap(True)
        tb_layout.addWidget(self._verdict)

        legend = QLabel(
            f'Rules of thumb — attitude MAE '
            f'<span style="color:{c_ok}">●&lt;2°</span> '
            f'<span style="color:{c_warn}">●&lt;5°</span> '
            f'<span style="color:{c_bad}">●≥5°</span>&nbsp;&nbsp;·&nbsp;&nbsp;'
            f'rate MAE '
            f'<span style="color:{c_ok}">●&lt;8°/s</span> '
            f'<span style="color:{c_warn}">●&lt;20°/s</span> '
            f'<span style="color:{c_bad}">●≥20°/s</span>&nbsp;&nbsp;·&nbsp;&nbsp;'
            f'lag '
            f'<span style="color:{c_ok}">●&lt;80 ms</span> '
            f'<span style="color:{c_warn}">●&lt;150 ms</span> '
            f'<span style="color:{c_bad}">●≥150 ms</span>'
            f'&nbsp;&nbsp;·&nbsp;&nbsp;airframe-dependent, compare against your own baseline.'
        )
        legend.setStyleSheet(f'font-size:11px; background:transparent; color:{COLORS["text_disabled"]};')
        tb_layout.addWidget(legend)

        layout.addWidget(table_box)

        # ── Plots ────────────────────────────────────────────
        splitter = QSplitter(Qt.Vertical)
        splitter.setHandleWidth(4)
        splitter.setStyleSheet(f'QSplitter::handle {{ background:{COLORS["border"]}; }}')

        pg.setConfigOption('background', COLORS['bg_primary'])
        pg.setConfigOption('foreground', COLORS['text_secondary'])

        self._track_box = QGroupBox('Desired vs Actual')
        self._track_box.setStyleSheet(box_style)
        tvl = QVBoxLayout(self._track_box)
        tvl.setContentsMargins(4, 8, 4, 4)
        self._track_plot = pg.PlotWidget()
        self._track_plot.showGrid(x=True, y=True, alpha=0.15)
        self._track_plot.setLabel('bottom', 'Time', units='s')
        self._track_legend = self._track_plot.addLegend(offset=(10, 10))
        self._des_curve = self._track_plot.plot(
            pen=pg.mkPen(COLORS['accent'], width=1.4, style=Qt.DashLine), name='Desired')
        self._act_curve = self._track_plot.plot(
            pen=pg.mkPen(COLORS['chart_y'], width=1.4), name='Actual')

        # Drag-able analysis window
        self._region = pg.LinearRegionItem(
            brush=pg.mkBrush(COLORS['accent'] + '12'),
            pen=pg.mkPen(COLORS['accent'] + '66'))
        self._region.setZValue(-10)
        self._region.sigRegionChangeFinished.connect(self._on_region_changed)
        self._track_plot.addItem(self._region)
        tvl.addWidget(self._track_plot)
        splitter.addWidget(self._track_box)

        self._err_box = QGroupBox('Tracking Error  e(t) = actual − desired')
        self._err_box.setStyleSheet(box_style)
        evl = QVBoxLayout(self._err_box)
        evl.setContentsMargins(4, 8, 4, 4)
        self._err_plot = pg.PlotWidget()
        self._err_plot.showGrid(x=True, y=True, alpha=0.15)
        self._err_plot.setLabel('bottom', 'Time', units='s')
        self._err_plot.setXLink(self._track_plot)
        self._err_curve = self._err_plot.plot(pen=pg.mkPen(COLORS['chart_x'], width=1.2))
        self._err_plot.addItem(pg.InfiniteLine(
            pos=0, angle=0, pen=pg.mkPen(COLORS['text_disabled'], width=1)))
        self._band_lines = []
        for value, color, label in ((0, COLORS['success'], 'MAE'),
                                    (0, COLORS['warning'], 'P95')):
            for sign in (1, -1):
                ln = pg.InfiniteLine(
                    pos=0, angle=0,
                    pen=pg.mkPen(color, width=1, style=Qt.DashLine))
                ln.setVisible(False)
                self._err_plot.addItem(ln)
                self._band_lines.append((ln, sign, label))
        evl.addWidget(self._err_plot)
        splitter.addWidget(self._err_box)

        splitter.setSizes([280, 200])
        layout.addWidget(splitter, 1)

        self._placeholder = QLabel('Load a log with ATT (and ideally RATE) messages.')
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setStyleSheet(
            f'color:{COLORS["text_disabled"]}; font-size:14px; background:transparent;')
        self._placeholder.setVisible(False)
        layout.addWidget(self._placeholder)

        return self._widget

    def load_data(self, log_data) -> None:
        self._log_data = log_data
        self._t0 = log_data.start_time_us
        self._sel_axis = 0
        if self._widget is None:
            return
        self._suppress = True
        self._region.setRegion([0.0, max(log_data.duration_seconds, 1.0)])
        self._window_combo.setCurrentIndex(0)
        self._suppress = False
        self._window = 'armed'
        self._analyze()

    def clear(self) -> None:
        self._log_data = None
        self._results = []
        if self._widget is None:
            return
        self._table.setRowCount(0)
        self._verdict.setText('')
        self._window_lbl.setText('')
        self._des_curve.setData([], [])
        self._act_curve.setData([], [])
        self._err_curve.setData([], [])
        for ln, _, _ in self._band_lines:
            ln.setVisible(False)

    # ── Data plumbing ───────────────────────────────────────

    def _axis_series(self, axis):
        """(t_s, desired, actual) for one axis, or None when not logged."""
        msg = getattr(self._log_data, axis.attr, {}) or {}
        if 'TimeUS' not in msg or axis.des not in msg or axis.act not in msg:
            return None
        t = (msg['TimeUS'].astype(np.float64) - self._t0) / 1e6
        if len(t) < 8:
            return None
        return t, msg[axis.des].astype(np.float64), msg[axis.act].astype(np.float64)

    def _armed_intervals(self):
        """[(t_start, t_end)] in seconds from ARM/DISARM events, or None."""
        log = self._log_data
        arms    = sorted(ev.time_us for ev in log.events if ev.event_type == 'arm')
        disarms = sorted(ev.time_us for ev in log.events if ev.event_type == 'disarm')
        if not arms:
            return None
        out, used = [], set()
        for a in arms:
            end = log.end_time_us
            for i, d in enumerate(disarms):
                if d > a and i not in used:
                    end = d
                    used.add(i)
                    break
            out.append(((a - self._t0) / 1e6, (end - self._t0) / 1e6))
        return out or None

    def _window_mask(self, t):
        """Boolean mask over t for the active analysis window, plus a label."""
        if self._window == 'full':
            return np.ones(len(t), dtype=bool), 'full log'
        if self._window == 'sel':
            lo, hi = self._region.getRegion()
            return (t >= lo) & (t <= hi), f'selection {lo:.0f}–{hi:.0f} s'
        intervals = self._armed_intervals()
        if not intervals:
            return np.ones(len(t), dtype=bool), 'full log (no ARM event found)'
        mask = np.zeros(len(t), dtype=bool)
        for lo, hi in intervals:
            mask |= (t >= lo) & (t <= hi)
        return mask, f'armed · {len(intervals)} interval(s)'

    # ── Analysis ────────────────────────────────────────────

    def _analyze(self):
        if self._log_data is None or self._widget is None:
            return

        self._results = []
        window_label = ''
        for axis in _AXES:
            series = self._axis_series(axis)
            if series is None:
                continue
            t, des, act = series
            mask, window_label = self._window_mask(t)
            if mask.sum() < 8:
                continue
            run = _longest_run(mask)
            dyn = None
            if run is not None and (run[1] - run[0]) >= 16:
                sl = slice(*run)
                dyn = (t[sl], des[sl], act[sl])
            m = tracking_metrics(
                t[mask], des[mask], act[mask],
                angular=axis.angular, norm_floor=axis.floor,
                normalize=axis.normalize, max_lag_s=axis.max_lag, dyn=dyn,
            )
            self._results.append((axis, m))

        has_data = bool(self._results)
        self._placeholder.setVisible(not has_data)
        if not has_data:
            self._table.setRowCount(0)
            self._verdict.setText('')
            self._window_lbl.setText('')
            self._des_curve.setData([], [])
            self._act_curve.setData([], [])
            self._err_curve.setData([], [])
            return

        span = max(m['duration'] for _a, m in self._results)
        self._window_lbl.setText(f'{window_label} · {span:.0f} s analysed')
        self._fill_table()
        self._update_verdict()
        self._sel_axis = min(self._sel_axis, len(self._results) - 1)
        self._table.selectRow(self._sel_axis)
        self._update_plots()

    def _cell(self, text, color=None, bold=False, tip=''):
        it = QTableWidgetItem(text)
        it.setTextAlignment(Qt.AlignVCenter | Qt.AlignRight)
        if color:
            it.setForeground(QColor(color))
        if bold:
            f = QFont()
            f.setBold(True)
            it.setFont(f)
        if tip:
            it.setToolTip(tip)
        return it

    def _grade(self, value, limits):
        if value is None or not np.isfinite(value):
            return COLORS['text_disabled']
        good, ok = limits
        if value < good:
            return COLORS['success']
        if value < ok:
            return COLORS['warning']
        return COLORS['danger']

    def _fill_table(self):
        self._table.setRowCount(len(self._results))
        for row, (axis, m) in enumerate(self._results):
            unit = axis.unit
            th = _THRESH[unit]

            tracked = self._is_tracked(m)
            name = QTableWidgetItem(
                f'  {axis.label} ({unit})' if tracked else f'  {axis.label} ({unit}) ⚠')
            name.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            name.setForeground(QColor(
                COLORS['text_primary'] if tracked else COLORS['text_disabled']))
            if not tracked:
                name.setToolTip(
                    'Actual barely correlates with desired here, so this axis was '
                    'not being tracked in the modes flown — ArduPlane logs a '
                    'navigation bearing in DesYaw, for example. Its error numbers '
                    'say nothing about the tune, and it is left out of the verdict.')
            self._table.setItem(row, 0, name)

            self._table.setItem(row, 1, self._cell(
                _fmt(m['mae']), self._grade(m['mae'], th['mae']), bold=True))
            self._table.setItem(row, 2, self._cell(_fmt(m['rmse'])))
            self._table.setItem(row, 3, self._cell(
                _fmt(m['p95']), self._grade(m['p95'], th['p95']), bold=True))
            self._table.setItem(row, 4, self._cell(
                _fmt(m['max']), COLORS['text_secondary']))

            if np.isfinite(m['norm_mae']):
                nm = self._cell(
                    f"{m['norm_mae'] * 100:.1f} %",
                    tip=f"Computed over {m['norm_frac'] * 100:.0f} % of samples — "
                        f"those where the axis was actually being worked.")
            elif not axis.normalize:
                nm = self._cell('n/a', COLORS['text_disabled'],
                                tip='Heading is an absolute bearing — normalising the '
                                    'error against it would not mean anything.')
            else:
                nm = self._cell('—', COLORS['text_disabled'],
                                tip='Setpoint stayed too close to zero to normalise against.')
            self._table.setItem(row, 5, nm)

            if m['lag_s'] is None:
                self._table.setItem(row, 6, self._cell(
                    '—', COLORS['text_disabled'],
                    tip='The setpoint moved too slowly here for a delay to be '
                        'separable — correlation is nearly the same at every lag.'))
            else:
                pinned = m['lag_pinned']
                text = f"{m['lag_s'] * 1000.0:+.0f} ms"
                self._table.setItem(row, 6, self._cell(
                    ('≥ ' + text) if pinned else text,
                    self._grade(abs(m['lag_s']), _LAG_THRESH),
                    tip=('Best correlation sits at the edge of the search window '
                         f'(±{axis.max_lag * 1000:.0f} ms) — the true delay is at '
                         'least this large.') if pinned else ''))
            self._table.setItem(row, 7, self._cell(
                '—' if m['corr'] is None else f"{m['corr']:.3f}",
                COLORS['text_disabled'] if m['corr'] is None else None))

            if m['overshoot'] is None:
                self._table.setItem(row, 8, self._cell(
                    '—', COLORS['text_disabled'],
                    tip='No clean setpoint step found in this window.'))
            else:
                self._table.setItem(row, 8, self._cell(
                    f"{m['overshoot']:.0f} %  ({m['n_steps']})",
                    self._grade(abs(m['overshoot']), (10.0, 25.0))))

            self._table.setItem(row, 9, self._cell(
                f"{m['bias']:+.2f}", COLORS['text_secondary'],
                tip='Mean signed error — a persistent offset points at trim, '
                    'not at gains.'))
            self._table.setItem(row, 10, self._cell(
                f"{m['n']:,}", COLORS['text_secondary']))

        # Size to content — six axes must not need a scrollbar.
        rows_h = sum(self._table.rowHeight(r) for r in range(self._table.rowCount()))
        self._table.setFixedHeight(
            self._table.horizontalHeader().height() + rows_h + 4)

    def _is_tracked(self, m):
        """Did actual actually follow desired on this axis at all?"""
        return m['corr'] is None or m['corr'] >= _CORR_MEANINGFUL

    def _update_verdict(self):
        """One line naming the axis that most needs attention."""
        worst, worst_score = None, -1.0
        candidates = [r for r in self._results if self._is_tracked(r[1])]
        for axis, m in candidates:
            th = _THRESH[axis.unit]
            if not np.isfinite(m['p95']):
                continue
            score = m['p95'] / th['p95'][1]
            if score > worst_score:
                worst, worst_score = (axis, m), score
        if worst is None:
            self._verdict.setText('')
            return
        axis, m = worst
        label, unit = axis.label, axis.unit
        if worst_score < _THRESH[unit]['p95'][0] / _THRESH[unit]['p95'][1]:
            color, head = COLORS['success'], 'Tracking looks tight'
        elif worst_score < 1.0:
            color, head = COLORS['warning'], 'Tracking is acceptable'
        else:
            color, head = COLORS['danger'], 'Tracking is loose'
        detail = (f'worst axis is <b>{label}</b> — MAE {_fmt(m["mae"])}{unit}, '
                  f'P95 {_fmt(m["p95"])}{unit}')
        if m['lag_s'] is not None and abs(m['lag_s']) >= _LAG_THRESH[1] and \
                m['corr'] is not None and m['corr'] > 0.85:
            detail += (f', and it follows the setpoint shape closely (r={m["corr"]:.2f}) '
                       f'but {abs(m["lag_s"]) * 1000:.0f} ms late — that is a rate/attitude '
                       f'response speed problem, not a shape problem')
        if m['overshoot'] is not None and m['overshoot'] >= 25:
            detail += f'; step overshoot is {m["overshoot"]:.0f} % — likely too much P or D'
        skipped = [a.label for a, mm in self._results if not self._is_tracked(mm)]
        if skipped:
            detail += (f'. Ignored {", ".join(skipped)} — actual barely follows '
                       f'desired there, so that setpoint is not a tracked demand')
        self._verdict.setText(
            f'<span style="color:{color}; font-weight:bold;">{head}</span> · {detail}.')

    # ── Plots ───────────────────────────────────────────────

    def _update_plots(self):
        if not self._results:
            return
        axis, m = self._results[self._sel_axis]
        series = self._axis_series(axis)
        if series is None:
            return
        t, des, act = series

        self._track_box.setTitle(f'Desired vs Actual — {axis.label} ({axis.unit})')
        self._err_box.setTitle(
            f'Tracking Error — {axis.label}   e(t) = actual − desired')
        self._track_plot.setLabel('left', axis.label, units=axis.unit)
        self._err_plot.setLabel('left', 'Error', units=axis.unit)

        # Full trace stays on screen; the region shows what is being scored.
        self._des_curve.setData(t, des)
        self._act_curve.setData(t, act)
        err = act - des
        if axis.angular:
            err = wrap180(err)
        self._err_curve.setData(t, err)

        for ln, sign, kind in self._band_lines:
            value = m['mae'] if kind == 'MAE' else m['p95']
            if value is None or not np.isfinite(value):
                ln.setVisible(False)
                continue
            ln.setValue(sign * value)
            ln.setVisible(True)

    # ── Interaction ─────────────────────────────────────────

    def _on_window_changed(self, idx: int):
        if self._suppress:
            return
        self._window = self._window_combo.itemData(idx)
        self._analyze()

    def _on_region_changed(self):
        if self._suppress or self._log_data is None:
            return
        # Dragging the region is a request to score exactly that stretch.
        if self._window != 'sel':
            self._suppress = True
            self._window = 'sel'
            self._window_combo.setCurrentIndex(
                [k for k, _ in _WINDOWS].index('sel'))
            self._suppress = False
        self._analyze()

    def _on_row_selected(self):
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        if 0 <= row < len(self._results) and row != self._sel_axis:
            self._sel_axis = row
            self._update_plots()

    def _on_reset_zoom(self):
        self._track_plot.enableAutoRange()
        self._err_plot.enableAutoRange()
