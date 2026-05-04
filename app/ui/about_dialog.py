import os
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap

from app.theme.style import COLORS

_LOGO = os.path.join(os.path.dirname(__file__), '..', 'assets', 'logo.png')


class AboutDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About Logalyzer")
        self.setFixedWidth(460)
        self.setModal(True)
        self.setStyleSheet(
            f"QDialog {{ background-color: {COLORS['bg_primary']}; }}"
        )
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 24)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignHCenter)

        # Logo
        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        pm = QPixmap(_LOGO)
        logo.setPixmap(pm.scaledToWidth(260, 1))
        logo.setStyleSheet("background: transparent;")
        layout.addWidget(logo)

        # Description
        desc = QLabel(
            "A desktop analysis tool for <b>ArduPilot</b> flight logs<br>"
            "(.BIN / .tlog). Visualize flight paths, attitude, range,<br>"
            "altitude, speed and more — all in one place."
        )
        desc.setAlignment(Qt.AlignCenter)
        desc.setWordWrap(True)
        desc.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 13px; background: transparent;"
        )
        layout.addWidget(desc)

        # Divider
        line = QLabel()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {COLORS['border']};")
        layout.addWidget(line)

        # Author & contact
        author = QLabel(
            f"<span style='color:{COLORS['text_primary']};font-size:14px;font-weight:bold;'>"
            "Eren Dernalar</span><br>"
            f"<a href='mailto:erendernalar@gmail.com' "
            f"style='color:#58A6FF; text-decoration:none; font-size:13px;'>"
            "erendernalar@gmail.com</a>"
        )
        author.setAlignment(Qt.AlignCenter)
        author.setOpenExternalLinks(True)
        author.setStyleSheet("background: transparent;")
        layout.addWidget(author)

        layout.addSpacing(4)

        # Close button
        btn = QPushButton("Close")
        btn.setFixedWidth(100)
        btn.setStyleSheet(
            f"QPushButton {{ background: {COLORS['bg_secondary']}; color: {COLORS['text_primary']};"
            f" border: 1px solid {COLORS['border']}; border-radius: 6px; padding: 6px 0; font-size: 13px; }}"
            f"QPushButton:hover {{ background: {COLORS['accent']}; color: #fff; border-color: {COLORS['accent']}; }}"
        )
        btn.clicked.connect(self.accept)
        layout.addWidget(btn, 0, Qt.AlignCenter)
