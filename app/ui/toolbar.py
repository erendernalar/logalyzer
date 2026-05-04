from PyQt5.QtWidgets import (
    QToolBar, QLabel, QProgressBar, QWidget, QSizePolicy,
)
from PyQt5.QtCore import pyqtSignal

from app.theme.style import COLORS


class TopToolbar(QToolBar):
    """
    Top toolbar: filename label | [right] status + progress bar
    File menu lives in the QMenuBar (main_window.py).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMovable(False)
        self.setFloatable(False)
        self._build_ui()

    def _build_ui(self):
        # Filename label
        self._lbl_file = QLabel("No log loaded")
        self._lbl_file.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 13px;"
            " background: transparent; padding: 0 8px;"
        )
        self.addWidget(self._lbl_file)

        # Spacer
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        spacer.setStyleSheet("background: transparent;")
        self.addWidget(spacer)

        # Status label (right of spacer)
        self._lbl_status = QLabel("")
        self._lbl_status.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 12px;"
            " background: transparent; padding: 0 6px;"
        )
        self.addWidget(self._lbl_status)

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setFixedWidth(160)
        self._progress.setFixedHeight(8)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self.addWidget(self._progress)

        # Right padding
        pad = QWidget()
        pad.setFixedWidth(8)
        pad.setStyleSheet("background: transparent;")
        self.addWidget(pad)

    # ── Public API ──────────────────────────────────────────

    def set_loading(self, file_name: str):
        self._lbl_file.setText(f"  Loading: {file_name}")
        self._lbl_file.setStyleSheet(
            f"color: {COLORS['warning']}; font-size: 13px; background: transparent; padding: 0 8px;"
        )
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._lbl_status.setText("")

    def set_progress(self, pct: int):
        self._progress.setValue(pct)

    def set_status_text(self, text: str):
        self._lbl_status.setText(text)

    def set_loaded(self, file_name: str, status_line: str = ""):
        import os
        self._lbl_file.setText(f"  {os.path.basename(file_name)}")
        self._lbl_file.setStyleSheet(
            f"color: {COLORS['text_primary']}; font-size: 13px; font-weight: bold;"
            " background: transparent; padding: 0 8px;"
        )
        self._progress.setVisible(False)
        self._lbl_status.setText(status_line)

    def set_closed(self):
        self._lbl_file.setText("No log loaded")
        self._lbl_file.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 13px;"
            " background: transparent; padding: 0 8px;"
        )
        self._progress.setVisible(False)
        self._lbl_status.setText("")
