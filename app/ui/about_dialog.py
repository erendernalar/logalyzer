import os
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QFrame,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap

from app.theme.style import COLORS
from app.version import __version__

_LOGO = os.path.join(os.path.dirname(__file__), '..', 'assets', 'logo.png')

_LABEL_STYLE = f"color: {COLORS['text_secondary']}; font-size: 13px; background: transparent;"
_VALUE_STYLE = f"color: {COLORS['text_primary']}; font-size: 13px; background: transparent;"
_LINK_STYLE  = "color: #58A6FF; text-decoration: none; font-size: 13px;"


class AboutDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About Logalyzer")
        self.setFixedWidth(420)
        self.setModal(True)
        self.setStyleSheet(
            f"QDialog {{ background-color: {COLORS['bg_primary']}; }}"
        )
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 24)
        root.setSpacing(0)

        # ── Logo ────────────────────────────────────────────
        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        logo.setPixmap(QPixmap(_LOGO).scaledToWidth(220, Qt.SmoothTransformation))
        logo.setStyleSheet("background: transparent;")
        root.addWidget(logo)

        root.addSpacing(6)

        # ── Version ─────────────────────────────────────────
        ver = QLabel(f"Version {__version__}")
        ver.setAlignment(Qt.AlignCenter)
        ver.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 12px; background: transparent;"
        )
        root.addWidget(ver)

        root.addSpacing(14)

        # ── Description ─────────────────────────────────────
        desc = QLabel(
            "A desktop analysis tool for <b>ArduPilot</b> flight logs "
            "(.BIN / .tlog). Visualize flight paths, attitude, range, "
            "altitude, speed and more — all in one place."
        )
        desc.setAlignment(Qt.AlignCenter)
        desc.setWordWrap(True)
        desc.setStyleSheet(_LABEL_STYLE)
        root.addWidget(desc)

        root.addSpacing(16)

        # ── Divider ─────────────────────────────────────────
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {COLORS['border']}; border: none;")
        root.addWidget(line)

        root.addSpacing(14)

        # ── Info grid ───────────────────────────────────────
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)

        def add_row(row, label_text, value_widget):
            lbl = QLabel(label_text)
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            lbl.setStyleSheet(_LABEL_STYLE)
            grid.addWidget(lbl, row, 0)
            grid.addWidget(value_widget, row, 1)

        author_lbl = QLabel("Eren Dernalar")
        author_lbl.setStyleSheet(
            f"color: {COLORS['text_primary']}; font-size: 13px; "
            f"font-weight: bold; background: transparent;"
        )
        add_row(0, "Author", author_lbl)

        email_lbl = QLabel(
            f"<a href='mailto:erendernalar@gmail.com' style='{_LINK_STYLE}'>"
            "erendernalar@gmail.com</a>"
        )
        email_lbl.setOpenExternalLinks(True)
        email_lbl.setStyleSheet("background: transparent;")
        add_row(1, "Contact", email_lbl)

        root.addLayout(grid)

        root.addSpacing(20)

        # ── Close button ────────────────────────────────────
        btn = QPushButton("Close")
        btn.setFixedWidth(100)
        btn.setStyleSheet(
            f"QPushButton {{ background: {COLORS['bg_secondary']}; color: {COLORS['text_primary']};"
            f" border: 1px solid {COLORS['border']}; border-radius: 6px;"
            f" padding: 6px 0; font-size: 13px; }}"
            f"QPushButton:hover {{ background: {COLORS['accent']}; color: #fff;"
            f" border-color: {COLORS['accent']}; }}"
        )
        btn.clicked.connect(self.accept)
        root.addWidget(btn, 0, Qt.AlignCenter)
