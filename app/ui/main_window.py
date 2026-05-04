import os
from PyQt5.QtWidgets import (
    QMainWindow, QSplitter, QFileDialog, QStatusBar, QLabel,
    QMenuBar, QAction,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QKeySequence, QIcon

from app.theme.style import COLORS
from app.ui.toolbar import TopToolbar
from app.ui.sidebar import SidebarWidget
from app.ui.content_area import ContentArea
from app.ui.about_dialog import AboutDialog
from app.core.module_registry import ModuleRegistry
from app.core.log_loader import LogLoader


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Logalyzer — ArduPilot Log Analyzer")
        self.setMinimumSize(1280, 800)
        self.resize(1440, 900)
        _logo = os.path.join(os.path.dirname(__file__), '..', 'assets', 'logo.png')
        self.setWindowIcon(QIcon(_logo))

        self._log_data = None
        self._loader: LogLoader = None

        self._registry = ModuleRegistry()
        self._registry.discover()

        self._build_ui()
        self._connect_signals()

    # ── UI construction ─────────────────────────────────────

    def _build_ui(self):
        # ── Menu bar ────────────────────────────────────────
        mb = self.menuBar()
        mb.setStyleSheet(
            f"QMenuBar {{ background-color: {COLORS['bg_secondary']}; color: {COLORS['text_primary']};"
            f" border-bottom: 1px solid {COLORS['border']}; font-size: 13px; }}"
            f"QMenuBar::item {{ background: transparent; padding: 4px 12px; }}"
            f"QMenuBar::item:selected {{ background-color: {COLORS['accent']}; color: {COLORS['bg_primary']};"
            f" border-radius: 4px; }}"
            f"QMenu {{ background-color: {COLORS['bg_secondary']}; color: {COLORS['text_primary']};"
            f" border: 1px solid {COLORS['border']}; border-radius: 6px; padding: 4px; }}"
            f"QMenu::item {{ padding: 6px 24px 6px 16px; border-radius: 4px; }}"
            f"QMenu::item:selected {{ background-color: {COLORS['accent']}; color: {COLORS['bg_primary']}; }}"
            f"QMenu::item:disabled {{ color: {COLORS['text_disabled']}; }}"
            f"QMenu::separator {{ height: 1px; background: {COLORS['border']}; margin: 4px 8px; }}"
        )

        file_menu = mb.addMenu("File")

        self._action_open = QAction("Open Log", self)
        self._action_open.setShortcut(QKeySequence("Ctrl+O"))
        self._action_open.triggered.connect(self._open_log)
        file_menu.addAction(self._action_open)

        self._action_close = QAction("Close Log", self)
        self._action_close.setShortcut(QKeySequence("Ctrl+W"))
        self._action_close.setEnabled(False)
        self._action_close.triggered.connect(self._close_log)
        file_menu.addAction(self._action_close)

        file_menu.addSeparator()

        action_exit = QAction("Exit", self)
        action_exit.setShortcut(QKeySequence("Ctrl+Q"))
        action_exit.triggered.connect(self.close)
        file_menu.addAction(action_exit)

        help_menu = mb.addMenu("Help")
        action_about = QAction("About", self)
        action_about.triggered.connect(lambda: AboutDialog(self).exec_())
        help_menu.addAction(action_about)

        # ── Toolbar ─────────────────────────────────────────
        self._toolbar = TopToolbar(self)
        self.addToolBar(Qt.TopToolBarArea, self._toolbar)

        # Splitter: sidebar | content
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet(f"QSplitter::handle {{ background-color: {COLORS['border']}; }}")

        self._sidebar = SidebarWidget()
        self._sidebar.populate(self._registry.get_all())

        self._content = ContentArea()

        splitter.addWidget(self._sidebar)
        splitter.addWidget(self._content)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([220, 1220])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)

        self.setCentralWidget(splitter)

        # Status bar
        self._statusbar = QStatusBar()
        self._statusbar.setSizeGripEnabled(False)
        self._status_lbl = QLabel("Ready")
        self._statusbar.addWidget(self._status_lbl)
        self.setStatusBar(self._statusbar)

    def _connect_signals(self):
        self._sidebar.module_selected.connect(self._on_module_selected)
        self._content.welcome.file_dropped.connect(self._load_file)

    # ── Log loading ─────────────────────────────────────────

    def _open_log(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open ArduPilot Log",
            os.path.expanduser("~"),
            "Log Files (*.bin *.BIN *.tlog);;All Files (*)"
        )
        if path:
            self._load_file(path)

    def _load_file(self, path: str):
        if self._loader and self._loader.isRunning():
            self._loader.cancel()
            self._loader.wait()

        self._close_log(silent=True)

        fname = os.path.basename(path)
        self._toolbar.set_loading(fname)
        self._set_status(f"Loading {fname}…")
        self._content.show_loading(fname)

        self._action_open.setEnabled(False)
        self._action_close.setEnabled(False)
        self._loader = LogLoader(path, self)
        self._loader.progress.connect(self._toolbar.set_progress)
        self._loader.progress.connect(self._content.loading.set_progress)
        self._loader.status.connect(self._toolbar.set_status_text)
        self._loader.status.connect(self._content.loading.set_status)
        self._loader.finished.connect(self._on_load_finished)
        self._loader.error.connect(self._on_load_error)
        self._loader.start()

    def _on_load_finished(self, log_data):
        self._log_data = log_data
        self._content.loading.stop()

        fname = os.path.basename(log_data.file_path)
        dur = log_data.flight_time_seconds
        mins, secs = int(dur // 60), int(dur % 60)
        status = (
            f"{fname}  ·  {log_data.total_messages:,} messages  ·  "
            f"{mins}m {secs:02d}s  ·  "
            f"{log_data.total_distance_2d_m / 1000:.2f} km"
        )
        self._toolbar.set_loaded(log_data.file_path, "")
        self._action_open.setEnabled(True)
        self._action_close.setEnabled(True)
        self._set_status(status)

        self._content.reload_all(log_data, self._registry)
        self._sidebar.set_log_loaded(log_data)

    def _on_load_error(self, msg: str):
        self._content.show_welcome()
        self._toolbar.set_closed()
        self._action_open.setEnabled(True)
        self._action_close.setEnabled(False)
        self._set_status(f"Error: {msg}")

    def _close_log(self, silent: bool = False):
        self._log_data = None
        self._content.clear_all(self._registry)
        self._sidebar.clear_log()
        self._toolbar.set_closed()
        self._action_open.setEnabled(True)
        self._action_close.setEnabled(False)
        if not silent:
            self._set_status("Ready")

    # ── Module switching ────────────────────────────────────

    def _on_module_selected(self, module_id: str):
        inst = self._registry.get_instance(module_id)
        if inst:
            if hasattr(inst, 'set_open_log_handler'):
                inst.set_open_log_handler(self._load_file)
            self._content.show_module(module_id, inst, self._log_data)

    # ── Helpers ─────────────────────────────────────────────

    def _set_status(self, text: str):
        self._status_lbl.setText(f"  {text}")
