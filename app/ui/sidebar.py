from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QListWidget, QListWidgetItem,
    QLabel, QFrame,
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont

from app.theme.style import COLORS
from app.ui.log_info_panel import LogInfoPanel


class SidebarWidget(QWidget):
    """
    Left sidebar: module list on top, log info panel at bottom.
    Emits module_selected(MODULE_ID) when the user clicks a module.
    """

    module_selected = pyqtSignal(str)   # MODULE_ID

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(220)
        self._module_ids: list[str] = []
        self._standalone: list[bool] = []   # True = no log needed
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header ────────────────────────────────────────────
        header = QLabel("  MODULES")
        header.setFixedHeight(36)
        header.setStyleSheet(
            f"background-color: {COLORS['bg_secondary']};"
            f" color: {COLORS['text_disabled']};"
            f" font-size: 10px; font-weight: bold;"
            " border-bottom: 1px solid"
            f" {COLORS['border']};"
        )
        layout.addWidget(header)

        # ── Module list ───────────────────────────────────────
        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget {{ background-color: {COLORS['bg_secondary']}; border: none; }}"
            f"QListWidget::item {{ height: 44px; padding-left: 12px; color: {COLORS['text_secondary']}; border-left: 3px solid transparent; }}"
            f"QListWidget::item:hover {{ background-color: {COLORS['bg_hover']}; color: {COLORS['text_primary']}; }}"
            f"QListWidget::item:selected {{ background-color: {COLORS['bg_tertiary']}; color: {COLORS['text_primary']}; border-left: 3px solid {COLORS['accent']}; }}"
            f"QListWidget::item:disabled {{ color: {COLORS['text_disabled']}; }}"
        )
        self._list.currentRowChanged.connect(self._on_row_changed)
        layout.addWidget(self._list, 1)

        # ── Divider ───────────────────────────────────────────
        div = QFrame()
        div.setFrameShape(QFrame.HLine)
        div.setStyleSheet(f"color: {COLORS['border']}; background-color: {COLORS['border']};")
        div.setFixedHeight(1)
        layout.addWidget(div)

        # ── Log info ──────────────────────────────────────────
        self._info = LogInfoPanel()
        self._info.setStyleSheet(f"background-color: {COLORS['bg_secondary']};")
        layout.addWidget(self._info)

    # ── Public API ──────────────────────────────────────────

    def populate(self, module_classes: list):
        """Fill list from registered module classes. Called once at startup."""
        self._list.clear()
        self._module_ids.clear()
        self._standalone.clear()
        font = QFont()
        font.setPointSize(11)
        for cls in module_classes:
            item = QListWidgetItem(f"  {cls.ICON_CHAR}  {cls.DISPLAY_NAME}")
            item.setFont(font)
            item.setToolTip(cls.DESCRIPTION)
            self._list.addItem(item)
            self._module_ids.append(cls.MODULE_ID)
            self._standalone.append(not cls.REQUIRED_MESSAGES)
        self._set_enabled(False)

    def set_log_loaded(self, log_data):
        self._set_enabled(True)
        self._info.update_from_log(log_data)

    def clear_log(self):
        self._set_enabled(False)
        self._info.clear()
        self._list.clearSelection()

    # ── Private ─────────────────────────────────────────────

    def _set_enabled(self, enabled: bool):
        self._list.blockSignals(True)
        for i in range(self._list.count()):
            item = self._list.item(i)
            flags = item.flags()
            should_enable = enabled or (i < len(self._standalone) and self._standalone[i])
            if should_enable:
                item.setFlags(flags | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            else:
                item.setFlags(flags & ~Qt.ItemIsEnabled & ~Qt.ItemIsSelectable)
        self._list.blockSignals(False)

    def _on_row_changed(self, row: int):
        if 0 <= row < len(self._module_ids):
            self.module_selected.emit(self._module_ids[row])
