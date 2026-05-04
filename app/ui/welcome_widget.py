import os
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QFileDialog
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QDragEnterEvent, QDropEvent, QCursor, QPixmap

from app.theme.style import COLORS

_LOGO = os.path.join(os.path.dirname(__file__), '..', 'assets', 'logo.png')


class WelcomeWidget(QWidget):
    """
    Shown when no log is loaded.
    Accepts drag-and-drop of .BIN / .tlog files.
    """

    file_dropped = pyqtSignal(str)   # emits file path

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(16)

        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        pm = QPixmap(_LOGO)
        logo.setPixmap(pm.scaledToWidth(420, 1))
        logo.setStyleSheet("background: transparent;")

        sub = QLabel("Drop a .BIN or .tlog file here, or click the box to browse")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet(
            f"font-size: 14px; color: {COLORS['text_secondary']}; background: transparent;"
        )

        self._drop_hint = QLabel("Drop file here\nor click to browse")
        self._drop_hint.setAlignment(Qt.AlignCenter)
        self._drop_hint.setFixedSize(320, 120)
        self._drop_hint.setCursor(QCursor(Qt.PointingHandCursor))
        self._drop_hint.installEventFilter(self)
        self._drop_hint.setStyleSheet(
            f"border: 2px dashed {COLORS['border']};"
            f" border-radius: 12px;"
            f" color: {COLORS['text_disabled']};"
            f" font-size: 13px;"
            f" background: {COLORS['bg_secondary']};"
        )

        layout.addWidget(logo)
        layout.addSpacing(8)
        layout.addWidget(sub)
        layout.addSpacing(16)
        layout.addWidget(self._drop_hint, 0, Qt.AlignCenter)

    # ── Mouse click ─────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._drop_hint.underMouse():
            self._open_dialog()

    def _open_dialog(self):
        import os
        path, _ = QFileDialog.getOpenFileName(
            self, "Open ArduPilot Log",
            os.path.expanduser("~"),
            "Log Files (*.bin *.BIN *.tlog);;All Files (*)",
        )
        if path:
            self.file_dropped.emit(path)

    # ── Drag & drop ─────────────────────────────────────────

    def eventFilter(self, obj, event):
        from PyQt5.QtCore import QEvent
        if obj is self._drop_hint:
            if event.type() == QEvent.Enter:
                self._drop_hint.setStyleSheet(
                    f"border: 2px dashed {COLORS['accent']};"
                    f" border-radius: 12px;"
                    f" color: {COLORS['accent']};"
                    f" font-size: 13px;"
                    f" background: {COLORS['bg_secondary']};"
                )
            elif event.type() == QEvent.Leave:
                self._reset_hint_style()
        return super().eventFilter(obj, event)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                if path.lower().endswith(('.bin', '.tlog')):
                    event.acceptProposedAction()
                    self._drop_hint.setStyleSheet(
                        f"border: 2px dashed {COLORS['accent']};"
                        f" border-radius: 12px;"
                        f" color: {COLORS['accent']};"
                        f" font-size: 13px;"
                        f" background: {COLORS['bg_secondary']};"
                    )
                    return
        event.ignore()

    def dragLeaveEvent(self, event):
        self._reset_hint_style()

    def dropEvent(self, event: QDropEvent):
        self._reset_hint_style()
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(('.bin', '.tlog')):
                self.file_dropped.emit(path)
                return

    def _reset_hint_style(self):
        self._drop_hint.setStyleSheet(
            f"border: 2px dashed {COLORS['border']};"
            f" border-radius: 12px;"
            f" color: {COLORS['text_disabled']};"
            f" font-size: 13px;"
            f" background: {COLORS['bg_secondary']};"
        )
