import numpy as np
import pyqtgraph as pg
from scipy import signal as scipy_signal

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QButtonGroup, QRadioButton, QSplitter, QFrame,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPainter, QColor, QFont

from app.modules.base_module import BaseModule
from app.theme.style import COLORS


# ── Health bar thresholds (m/s²) ───────────────────────────
THRESH_OK   = 15.0
THRESH_WARN = 30.0


class _HealthBar(QWidget):
    """Single axis health bar painted with QPainter."""

    def __init__(self, axis_name: str, parent=None):
        super().__init__(parent)
        self.axis_name = axis_name
        self._value = 0.0
        self._clip = 0
        self.setFixedHeight(32)
        self.setMinimumWidth(300)

    def set_value(self, value: float, clip: int = 0):
        self._value = value
        self._clip = clip
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        bar_height = 14
        bar_y = (h - bar_height) // 2
        label_w = 60
        value_w = 90
        clip_w = 70
        bar_w = w - label_w - value_w - clip_w - 16

        # Axis label
        painter.setPen(QColor(COLORS['text_secondary']))
        font = QFont("Segoe UI", 10)
        painter.setFont(font)
        painter.drawText(0, 0, label_w, h, Qt.AlignVCenter | Qt.AlignLeft, self.axis_name)

        # Bar background
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(COLORS['bg_tertiary']))
        painter.drawRoundedRect(label_w, bar_y, bar_w, bar_height, 4, 4)

        # Bar fill
        fill_ratio = min(self._value / THRESH_WARN, 1.0)
        fill_w = int(bar_w * fill_ratio)
        if fill_w > 0:
            if self._value < THRESH_OK:
                color = QColor(COLORS['success'])
            elif self._value < THRESH_WARN:
                color = QColor(COLORS['warning'])
            else:
                color = QColor(COLORS['danger'])
            painter.setBrush(color)
            painter.drawRoundedRect(label_w, bar_y, fill_w, bar_height, 4, 4)

        # Threshold marker at THRESH_OK
        mark_ok = label_w + int(bar_w * THRESH_OK / THRESH_WARN)
        painter.setPen(QColor(COLORS['warning']))
        painter.drawLine(mark_ok, bar_y - 2, mark_ok, bar_y + bar_height + 2)

        # Value text
        if self._value < THRESH_OK:
            status_str = "OK"
            status_color = COLORS['success']
        elif self._value < THRESH_WARN:
            status_str = "WARNING"
            status_color = COLORS['warning']
        else:
            status_str = "BAD"
            status_color = COLORS['danger']

        x_val = label_w + bar_w + 8
        painter.setPen(QColor(COLORS['text_primary']))
        painter.drawText(x_val, 0, value_w - 4, h, Qt.AlignVCenter | Qt.AlignLeft,
                         f"{self._value:.1f} m/s²")
        painter.setPen(QColor(status_color))
        font2 = QFont("Segoe UI", 9, QFont.Bold)
        painter.setFont(font2)
        painter.drawText(x_val + 68, 0, 60, h, Qt.AlignVCenter | Qt.AlignLeft, status_str)

        # Clip
        x_clip = w - clip_w
        clip_color = COLORS['danger'] if self._clip > 0 else COLORS['text_disabled']
        painter.setPen(QColor(clip_color))
        font3 = QFont("Segoe UI", 9)
        painter.setFont(font3)
        painter.drawText(x_clip, 0, clip_w, h, Qt.AlignVCenter | Qt.AlignRight,
                         f"Clip: {self._clip}  ")


class VibrationAnalyzerModule(BaseModule):
    MODULE_ID    = "vibration_analyzer"
    DISPLAY_NAME = "Vibration Analyzer"
    DESCRIPTION  = "Analyze IMU vibration levels and frequency spectrum"
    ICON_CHAR    = "〜"
    REQUIRED_MESSAGES = ["VIBE", "IMU"]

    def __init__(self):
        self._widget = None
        self._log_data = None
        self._selected_instance = 0

    # ── BaseModule interface ────────────────────────────────

    def build_widget(self, parent=None) -> QWidget:
        self._widget = QWidget(parent)
        self._widget.setStyleSheet(f"background-color: {COLORS['bg_primary']};")
        layout = QVBoxLayout(self._widget)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # ── Top bar: title + instance selector ───────────────
        top = QHBoxLayout()
        title = QLabel("Vibration Analyzer")
        title.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {COLORS['text_primary']}; background: transparent;"
        )
        top.addWidget(title)
        top.addStretch()

        inst_lbl = QLabel("IMU Instance:")
        inst_lbl.setStyleSheet(f"color: {COLORS['text_secondary']}; background: transparent;")
        top.addWidget(inst_lbl)

        self._inst_group = QButtonGroup(self._widget)
        self._inst_btns = []
        for i in range(3):
            btn = QRadioButton(str(i))
            btn.setChecked(i == 0)
            btn.toggled.connect(lambda checked, idx=i: self._on_instance_changed(idx) if checked else None)
            self._inst_group.addButton(btn, i)
            self._inst_btns.append(btn)
            top.addWidget(btn)

        layout.addLayout(top)

        # ── Health summary ────────────────────────────────────
        health_box = QGroupBox("Vibration Health")
        health_box.setStyleSheet(
            f"QGroupBox {{ background-color: {COLORS['bg_secondary']}; border: 1px solid {COLORS['border']};"
            f" border-radius: 8px; margin-top: 10px; padding-top: 8px;"
            f" color: {COLORS['text_secondary']}; font-size: 11px; }}"
        )
        health_layout = QVBoxLayout(health_box)
        health_layout.setContentsMargins(16, 8, 16, 12)
        health_layout.setSpacing(4)

        self._bar_x = _HealthBar("Vibe X")
        self._bar_y = _HealthBar("Vibe Y")
        self._bar_z = _HealthBar("Vibe Z")
        health_layout.addWidget(self._bar_x)
        health_layout.addWidget(self._bar_y)
        health_layout.addWidget(self._bar_z)

        # Threshold legend
        c_ok   = COLORS['success']
        c_warn = COLORS['warning']
        c_bad  = COLORS['danger']
        legend = QLabel(
            f"  Thresholds:  "
            f"<span style='color:{c_ok};'>● OK &lt; {THRESH_OK}</span>  "
            f"<span style='color:{c_warn};'>● Warning &lt; {THRESH_WARN}</span>  "
            f"<span style='color:{c_bad};'>● Bad ≥ {THRESH_WARN}</span>  m/s²"
        )
        legend.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 11px; background: transparent;")
        health_layout.addWidget(legend)

        layout.addWidget(health_box)

        # ── Charts splitter ───────────────────────────────────
        charts_splitter = QSplitter(Qt.Vertical)
        charts_splitter.setHandleWidth(4)

        # Vibration time-series
        vibe_box = QGroupBox("Vibration Over Time")
        vibe_box.setStyleSheet(health_box.styleSheet())
        vibe_vl = QVBoxLayout(vibe_box)
        vibe_vl.setContentsMargins(4, 8, 4, 4)

        self._vibe_plot = pg.PlotWidget()
        self._vibe_plot.setLabel('left', 'Vibration', units='m/s²')
        self._vibe_plot.setLabel('bottom', 'Time', units='s')
        self._vibe_plot.showGrid(x=True, y=True, alpha=0.15)
        self._vibe_plot.addLegend()
        self._vibe_plot.getAxis('left').setTextPen(pg.mkPen(COLORS['text_secondary']))
        self._vibe_plot.getAxis('bottom').setTextPen(pg.mkPen(COLORS['text_secondary']))

        # Reference lines
        self._vibe_plot.addItem(pg.InfiniteLine(
            pos=THRESH_OK, angle=0,
            pen=pg.mkPen(COLORS['warning'], width=1, style=Qt.DashLine),
            label=f'{THRESH_OK} m/s²', labelOpts={'color': COLORS['warning'], 'position': 0.05}
        ))
        self._vibe_plot.addItem(pg.InfiniteLine(
            pos=THRESH_WARN, angle=0,
            pen=pg.mkPen(COLORS['danger'], width=1, style=Qt.DashLine),
            label=f'{THRESH_WARN} m/s²', labelOpts={'color': COLORS['danger'], 'position': 0.05}
        ))

        self._curve_x = self._vibe_plot.plot(pen=pg.mkPen(COLORS['chart_x'], width=1.5), name='VibeX')
        self._curve_y = self._vibe_plot.plot(pen=pg.mkPen(COLORS['chart_y'], width=1.5), name='VibeY')
        self._curve_z = self._vibe_plot.plot(pen=pg.mkPen(COLORS['chart_z'], width=1.5), name='VibeZ')

        vibe_vl.addWidget(self._vibe_plot)
        charts_splitter.addWidget(vibe_box)

        # FFT spectrum
        fft_box = QGroupBox("Frequency Spectrum (AccZ — FFT)")
        fft_box.setStyleSheet(health_box.styleSheet())
        fft_vl = QVBoxLayout(fft_box)
        fft_vl.setContentsMargins(4, 8, 4, 4)

        self._fft_plot = pg.PlotWidget()
        self._fft_plot.setLabel('left', 'PSD', units='(m/s²)²/Hz')
        self._fft_plot.setLabel('bottom', 'Frequency', units='Hz')
        self._fft_plot.showGrid(x=True, y=True, alpha=0.15)
        self._fft_plot.setLogMode(x=False, y=True)
        self._fft_plot.getAxis('left').setTextPen(pg.mkPen(COLORS['text_secondary']))
        self._fft_plot.getAxis('bottom').setTextPen(pg.mkPen(COLORS['text_secondary']))
        self._fft_plot.setXRange(0, 150)

        self._fft_curve = self._fft_plot.plot(
            pen=pg.mkPen(COLORS['chart_z'], width=1.5)
        )
        self._fft_line = pg.InfiniteLine(angle=90, pen=pg.mkPen(COLORS['accent'], width=1, style=Qt.DashLine))
        self._fft_plot.addItem(self._fft_line)
        self._fft_label = pg.TextItem("", color=COLORS['accent'], anchor=(0, 1))
        self._fft_plot.addItem(self._fft_label)

        fft_vl.addWidget(self._fft_plot)
        charts_splitter.addWidget(fft_box)

        charts_splitter.setSizes([300, 200])
        layout.addWidget(charts_splitter, 1)

        return self._widget

    def load_data(self, log_data) -> None:
        self._log_data = log_data
        self._update_plots()

    def clear(self) -> None:
        self._log_data = None
        if self._widget is None:
            return
        self._bar_x.set_value(0)
        self._bar_y.set_value(0)
        self._bar_z.set_value(0)
        self._curve_x.setData([], [])
        self._curve_y.setData([], [])
        self._curve_z.setData([], [])
        self._fft_curve.setData([], [])

    # ── Internal ────────────────────────────────────────────

    def _on_instance_changed(self, idx: int):
        self._selected_instance = idx
        if self._log_data:
            self._update_plots()

    def _update_plots(self):
        if self._log_data is None or self._widget is None:
            return

        inst = self._selected_instance
        vibe = self._log_data.vibe
        imu  = self._log_data.imu

        # ── VIBE data ─────────────────────────────────────────
        if 'IMU' in vibe and len(vibe['IMU']) > 0:
            mask = vibe['IMU'] == inst
            t0 = self._log_data.start_time_us

            if mask.any():
                t = (vibe['TimeUS'][mask].astype(np.float64) - t0) / 1e6
                vx = vibe['VibeX'][mask].astype(np.float64)
                vy = vibe['VibeY'][mask].astype(np.float64)
                vz = vibe['VibeZ'][mask].astype(np.float64)
                clip = int(vibe['Clip'][mask].max()) if 'Clip' in vibe else 0

                self._curve_x.setData(t, vx)
                self._curve_y.setData(t, vy)
                self._curve_z.setData(t, vz)

                self._bar_x.set_value(float(vx.max()), 0)
                self._bar_y.set_value(float(vy.max()), 0)
                self._bar_z.set_value(float(vz.max()), clip)
            else:
                self._curve_x.setData([], [])
                self._curve_y.setData([], [])
                self._curve_z.setData([], [])

        # ── FFT ───────────────────────────────────────────────
        if 'I' in imu and len(imu['I']) > 0:
            mask_imu = imu['I'] == inst
            if mask_imu.any() and 'AccZ' in imu:
                acc_z = imu['AccZ'][mask_imu].astype(np.float64)
                if len(acc_z) > 256:
                    # Estimate sample rate from TimeUS
                    t_imu = imu['TimeUS'][mask_imu]
                    dt_mean = float(np.diff(t_imu).mean()) / 1e6
                    fs = 1.0 / dt_mean if dt_mean > 0 else 400.0
                    fs = min(max(fs, 50.0), 2000.0)

                    freqs, psd = scipy_signal.welch(acc_z, fs=fs, nperseg=min(1024, len(acc_z) // 4))
                    # Limit to 0-150 Hz
                    mask_f = freqs <= 150
                    freqs = freqs[mask_f]
                    psd = psd[mask_f]

                    self._fft_curve.setData(freqs, psd)

                    dom_idx = int(np.argmax(psd))
                    dom_freq = float(freqs[dom_idx])
                    self._fft_line.setValue(dom_freq)
                    self._fft_label.setText(f"  {dom_freq:.1f} Hz")
                    self._fft_label.setPos(dom_freq, np.log10(float(psd[dom_idx])))
