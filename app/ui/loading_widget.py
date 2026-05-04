from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QProgressBar
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont

from app.theme.style import COLORS

_SPINNER_FRAMES = ["⠋", "⠙", "⠸", "⠴", "⠦", "⠇"]


class LoadingWidget(QWidget):
    """
    Full-panel loading screen shown in the content area while a log is parsing.
    Prevents accidental double-loads and makes progress impossible to miss.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frame = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(20)

        # Spinner
        self._spinner = QLabel(_SPINNER_FRAMES[0])
        self._spinner.setAlignment(Qt.AlignCenter)
        self._spinner.setStyleSheet(
            f"font-size: 48px; color: {COLORS['accent']}; background: transparent;"
        )
        layout.addWidget(self._spinner)

        # "Loading file…" title
        self._title = QLabel("Loading log…")
        self._title.setAlignment(Qt.AlignCenter)
        font = QFont("Segoe UI", 16, QFont.Bold)
        self._title.setFont(font)
        self._title.setStyleSheet(
            f"color: {COLORS['text_primary']}; background: transparent;"
        )
        layout.addWidget(self._title)

        # Filename
        self._fname = QLabel("")
        self._fname.setAlignment(Qt.AlignCenter)
        self._fname.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 13px; background: transparent;"
        )
        layout.addWidget(self._fname)

        layout.addSpacing(12)

        # Progress bar
        self._bar = QProgressBar()
        self._bar.setFixedWidth(420)
        self._bar.setFixedHeight(10)
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setStyleSheet(
            f"QProgressBar {{ background-color: {COLORS['bg_tertiary']};"
            f" border: none; border-radius: 5px; }}"
            f"QProgressBar::chunk {{ background-color: {COLORS['accent']};"
            f" border-radius: 5px; }}"
        )
        layout.addWidget(self._bar, 0, Qt.AlignCenter)

        # Percentage
        self._pct_lbl = QLabel("0%")
        self._pct_lbl.setAlignment(Qt.AlignCenter)
        self._pct_lbl.setStyleSheet(
            f"color: {COLORS['accent']}; font-size: 13px;"
            " font-weight: bold; background: transparent;"
        )
        layout.addWidget(self._pct_lbl)

        # Live stats (msgs · MB · rate)
        self._stats = QLabel("")
        self._stats.setAlignment(Qt.AlignCenter)
        self._stats.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 12px; background: transparent;"
        )
        layout.addWidget(self._stats)

        # Status message (phase label — "Parsing…", "Processing data…" etc.)
        self._status = QLabel("")
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setStyleSheet(
            f"color: {COLORS['text_disabled']}; font-size: 12px; background: transparent;"
        )
        layout.addWidget(self._status)

    # ── Public API ──────────────────────────────────────────

    def start(self, file_name: str):
        self._fname.setText(file_name)
        self._bar.setValue(0)
        self._pct_lbl.setText("0%")
        self._stats.setText("")
        self._status.setText("")
        self._frame = 0
        self._spinner.setStyleSheet(
            f"font-size: 48px; color: {COLORS['accent']}; background: transparent;"
        )
        self._timer.start(80)

    def stop(self):
        self._timer.stop()
        self._spinner.setText("✓")
        self._spinner.setStyleSheet(
            f"font-size: 48px; color: {COLORS['success']}; background: transparent;"
        )

    def set_progress(self, pct: int):
        self._bar.setValue(pct)
        self._pct_lbl.setText(f"{pct}%")

    def set_status(self, text: str):
        if "·" in text:
            self._stats.setText(text)
            self._status.setText("")
        else:
            self._stats.setText("")
            self._status.setText(text)

    # ── Internal ────────────────────────────────────────────

    def _tick(self):
        self._frame = (self._frame + 1) % len(_SPINNER_FRAMES)
        self._spinner.setText(_SPINNER_FRAMES[self._frame])
