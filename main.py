import sys

import os
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QIcon

# Must be set BEFORE QApplication is created
QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
# Must be imported BEFORE QApplication is created
from PyQt5.QtWebEngineWidgets import QWebEngineView  # noqa: F401

from app.theme.style import STYLESHEET, COLORS
from app.ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)

    # pyqtgraph must be imported AFTER QApplication exists — it creates Qt objects on import
    import pyqtgraph as pg
    pg.setConfigOption('background', COLORS['bg_primary'])
    pg.setConfigOption('foreground', COLORS['text_secondary'])
    pg.setConfigOptions(antialias=True)
    app.setApplicationName("Logalyzer")
    app.setOrganizationName("Logalyzer")
    _logo = os.path.join(os.path.dirname(__file__), 'app', 'assets', 'logo.png')
    app.setWindowIcon(QIcon(_logo))

    app.setStyleSheet(STYLESHEET)

    font = QFont("Segoe UI", 10)
    font.setHintingPreference(QFont.PreferFullHinting)
    app.setFont(font)

    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
