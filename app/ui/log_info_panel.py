from PyQt5.QtWidgets import QWidget, QGridLayout, QLabel, QFrame
from PyQt5.QtCore import Qt

from app.theme.style import COLORS


def _stat(key: str, value: str) -> tuple:
    """Return (key_label, value_label) pair."""
    k = QLabel(key)
    k.setStyleSheet(
        f"color: {COLORS['text_secondary']}; font-size: 11px;"
        " background: transparent;"
    )
    v = QLabel(value)
    v.setStyleSheet(
        f"color: {COLORS['text_primary']}; font-size: 13px;"
        " font-weight: bold; background: transparent;"
    )
    return k, v


class LogInfoPanel(QWidget):
    """
    Shows a compact summary of the loaded log at the bottom of the sidebar.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.clear()

    def _build_ui(self):
        layout = QGridLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setVerticalSpacing(4)
        layout.setHorizontalSpacing(8)

        self._labels = {}

        rows = [
            ("vehicle",   "Vehicle",   "—"),
            ("duration",  "Duration",  "—"),
            ("distance",  "Distance",  "—"),
            ("max_alt",   "Max Alt",   "—"),
            ("messages",  "Messages",  "—"),
        ]

        for row_idx, (key, label_text, default) in enumerate(rows):
            k, v = _stat(label_text, default)
            layout.addWidget(k, row_idx, 0, Qt.AlignLeft)
            layout.addWidget(v, row_idx, 1, Qt.AlignRight)
            self._labels[key] = v

    def update_from_log(self, log_data):
        from app.core.log_data import LogData

        dur = log_data.flight_time_seconds
        minutes = int(dur // 60)
        seconds = int(dur % 60)

        self._labels["vehicle"].setText(log_data.vehicle_type or "Unknown")
        self._labels["duration"].setText(f"{minutes}m {seconds:02d}s")
        self._labels["distance"].setText(f"{log_data.total_distance_2d_m / 1000:.2f} km")
        self._labels["max_alt"].setText(f"{log_data.max_altitude_m:.1f} m")
        self._labels["messages"].setText(f"{log_data.total_messages:,}")

    def clear(self):
        for lbl in self._labels.values():
            lbl.setText("—")
