from abc import ABC, abstractmethod
from PyQt5.QtWidgets import QWidget


class BaseModule(ABC):
    """
    Contract every analysis module must implement.

    To add a new module:
      1. Create app/modules/<your_name>/__init__.py  (empty)
      2. Create app/modules/<your_name>/module.py with a class that subclasses BaseModule
      3. Set all class-level attributes below
      4. Implement build_widget() and load_data()
      That's it — the registry picks it up automatically.
    """

    # ── Class-level metadata (set in subclass) ──────────────
    MODULE_ID: str = ""           # unique snake_case key
    DISPLAY_NAME: str = ""        # shown in sidebar
    DESCRIPTION: str = ""         # one-line tooltip
    ICON_CHAR: str = "◈"          # unicode char used as icon in sidebar

    # Message types this module reads from LogData
    REQUIRED_MESSAGES: list = []

    # ── Lifecycle ───────────────────────────────────────────

    @abstractmethod
    def build_widget(self, parent: QWidget = None) -> QWidget:
        """
        Called once on the first time the module is selected.
        Return the QWidget that will be placed in the content area.
        Widget is cached after creation; subsequent selections just bring it to front.
        """

    @abstractmethod
    def load_data(self, log_data) -> None:
        """
        Called after build_widget() whenever a new log finishes loading.
        Populate charts/tables from log_data.
        """

    def on_activated(self) -> None:
        """Called every time this module becomes the visible one."""

    def on_deactivated(self) -> None:
        """Called every time another module takes focus."""

    def clear(self) -> None:
        """Called when a log is closed — reset all charts to empty state."""
