import os
import struct
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QFileDialog, QTableWidget, QTableWidgetItem,
    QHeaderView, QFrame, QStackedWidget, QAbstractItemView, QMessageBox,
)
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt5.QtGui import QColor

from app.modules.base_module import BaseModule
from app.theme.style import COLORS

_C = COLORS
_SPINNER_FRAMES = ["⠋", "⠙", "⠸", "⠴", "⠦", "⠇"]
_PAGE_INFO       = 0
_PAGE_PROCESSING = 1
_PAGE_RESULTS    = 2


# ── Helpers ─────────────────────────────────────────────────────────────────

_GPS_EPOCH    = datetime(1980, 1, 6, tzinfo=timezone.utc)
_GPS_LEAP_SEC = 18                            # UTC-GPS leap seconds, current as of 2024
_TZ_DISPLAY   = timezone(timedelta(hours=3))  # UTC+3


def _gps_to_local_str(gps_week: int, gps_ms: int) -> str:
    """Convert GPS week + milliseconds-of-week to a UTC+3 display string."""
    try:
        total_sec = gps_week * 604800 + gps_ms / 1000.0 - _GPS_LEAP_SEC
        dt = (_GPS_EPOCH + timedelta(seconds=total_sec)).astimezone(_TZ_DISPLAY)
        return dt.strftime("%Y-%m-%d  %H:%M")
    except Exception:
        return ""


def _mtime_str(path: str) -> str:
    """File modification time formatted in UTC+3 as a fallback date string."""
    try:
        dt = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
        return dt.astimezone(_TZ_DISPLAY).strftime("%Y-%m-%d  %H:%M")
    except Exception:
        return "—"


def _fmt(seconds: float) -> str:
    """Format seconds as H:MM:SS or MM:SS."""
    if seconds < 0:
        return "—"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _fmt_parts(seconds: float) -> tuple:
    """Return (hh, mm, ss) as zero-padded strings."""
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}", f"{m:02d}", f"{sec:02d}"


# ── Fast DataFlash .BIN parser ───────────────────────────────────────────────
#
# ArduPilot DataFlash binary format:
#   Every message starts with 0xA3 0x95 <type_id>  (3-byte header)
#   FMT messages (type 0x80) define name, length, and field layout for each type.
#   FMT messages always appear at the very beginning of the file.
#
# Strategy: single pass — parse FMT messages to build a type→length table,
# then skip every non-EV message in O(1) by seeking `length` bytes forward.
# Only the handful of EV messages are actually unpacked.
# This is 50-200× faster than pymavlink's full message parser.

_BIN_HEAD1       = 0xA3
_BIN_HEAD2       = 0x95
_BIN_FMT_TYPE    = 0x80
_BIN_FMT_MSGLEN  = 89      # 3-byte header + 86-byte body (fixed by ArduPilot spec)

# Format character → byte size (ArduPilot DataFlash encoding)
_FSIZES = {
    'a': 64, 'b': 1, 'B': 1, 'c': 2,  'C': 2,  'd': 8,
    'e': 4,  'E': 8, 'f': 4, 'g': 4,  'h': 2,  'H': 2,
    'i': 4,  'I': 4, 'L': 4, 'M': 1,  'n': 4,  'N': 16,
    'q': 8,  'Q': 8, 'Z': 64,
}

# Struct format for each Id field type
_ID_STRUCT = {'B': '<B', 'H': '<H', 'I': '<I', 'b': '<b', 'h': '<h', 'i': '<i'}


def _parse_ev_bin(path: str) -> tuple:
    """
    Extract ARM/DISARM timestamps and flight datetime from a DataFlash .BIN file.
    Reads raw bytes once; only FMT, EV, and GPS messages are unpacked.
    Returns (flight_seconds, note_str, date_str).
    """
    try:
        with open(path, 'rb') as fh:
            raw = fh.read()
    except OSError as exc:
        return -1.0, f"Cannot read: {exc}", _mtime_str(path)

    n = len(raw)
    if n < _BIN_FMT_MSGLEN:
        return -1.0, "File too small", _mtime_str(path)

    type_lengths: dict = {}

    # EV message field offsets
    ev_type_id  = None
    ev_time_off = 0
    ev_evid_off = 8
    ev_evid_fmt = '<H'

    # GPS message field offsets (for datetime extraction)
    gps_type_id    = None
    gps_status_off = None   # byte offset of Status field in GPS body
    gps_gms_off    = None   # byte offset of GMS field
    gps_gwk_off    = None   # byte offset of GWk field

    arm_times:    list = []
    disarm_times: list = []
    last_t   = 0
    date_str = ""      # filled from first valid GPS fix
    pos      = 0

    while pos < n - 2:
        if raw[pos] != _BIN_HEAD1 or raw[pos + 1] != _BIN_HEAD2:
            pos += 1
            continue

        msg_type = raw[pos + 2]

        # ── FMT: build length table, find EV + GPS descriptors ───────────────
        if msg_type == _BIN_FMT_TYPE:
            if pos + _BIN_FMT_MSGLEN > n:
                break
            body  = raw[pos + 3 : pos + _BIN_FMT_MSGLEN]
            ftype = body[0]
            flen  = body[1]
            name  = body[2:6].rstrip(b'\x00').decode('ascii', errors='replace').strip()
            fmt   = body[6:22].rstrip(b'\x00').decode('ascii', errors='replace')
            cols  = body[22:86].rstrip(b'\x00').decode('ascii', errors='replace')

            type_lengths[ftype] = flen

            if name == 'EV':
                ev_type_id = ftype
                off = 0
                for col, fc in zip((c.strip() for c in cols.split(',')), fmt):
                    if col == 'TimeUS':
                        ev_time_off = off
                    elif col == 'Id':
                        ev_evid_off = off
                        ev_evid_fmt = _ID_STRUCT.get(fc, '<H')
                    off += _FSIZES.get(fc, 1)

            elif name == 'GPS':
                gps_type_id = ftype
                off = 0
                for col, fc in zip((c.strip() for c in cols.split(',')), fmt):
                    if col == 'Status':
                        gps_status_off = off
                    elif col == 'GMS':
                        gps_gms_off = off
                    elif col == 'GWk':
                        gps_gwk_off = off
                    off += _FSIZES.get(fc, 1)

            pos += _BIN_FMT_MSGLEN
            continue

        # ── EV: unpack ARM/DISARM event ──────────────────────────────────────
        if msg_type == ev_type_id:
            bs = pos + 3
            try:
                t   = int(struct.unpack_from('<Q', raw, bs + ev_time_off)[0])
                eid = struct.unpack_from(ev_evid_fmt, raw, bs + ev_evid_off)[0]
            except struct.error:
                pos += 1
                continue
            last_t = t
            if eid == 10:
                arm_times.append(t)
            elif eid == 11:
                disarm_times.append(t)
            pos += type_lengths[ev_type_id]
            continue

        # ── GPS: extract datetime from first valid fix ────────────────────────
        if (msg_type == gps_type_id and not date_str
                and gps_status_off is not None
                and gps_gms_off    is not None
                and gps_gwk_off    is not None):
            bs = pos + 3
            try:
                status = struct.unpack_from('<B', raw, bs + gps_status_off)[0]
                if status >= 3:
                    gms = struct.unpack_from('<I', raw, bs + gps_gms_off)[0]
                    gwk = struct.unpack_from('<H', raw, bs + gps_gwk_off)[0]
                    date_str = _gps_to_local_str(gwk, gms)
            except struct.error:
                pass
            msg_len = type_lengths.get(msg_type, 1)
            pos += msg_len
            continue

        # ── Everything else: skip by length, harvest last timestamp ──────────
        msg_len = type_lengths.get(msg_type)
        if msg_len:
            if msg_len >= 11:
                try:
                    t = struct.unpack_from('<Q', raw, pos + 3)[0]
                    if 0 < t < 10 ** 14:
                        last_t = int(t)
                except struct.error:
                    pass
            pos += msg_len
        else:
            pos += 1

    if not arm_times:
        return -1.0, "No ARM event", date_str or _mtime_str(path)

    secs, note = _pair_flights(arm_times, disarm_times, last_t)
    return secs, note, date_str or _mtime_str(path)


def _parse_ev_mavlink(path: str) -> tuple:
    """
    Fallback for .tlog (MAVLink stream format) — uses pymavlink.
    Returns (flight_seconds, note_str, date_str).
    """
    date_str = _mtime_str(path)
    try:
        from pymavlink import mavutil
        mlog = mavutil.mavlink_connection(
            path, robust_parsing=True,
            dialect='ardupilotmega', zero_time_base=True,
        )
    except Exception as exc:
        return -1.0, f"Cannot open: {exc}", date_str

    arm_times, disarm_times = [], []
    last_t = 0

    while True:
        try:
            msg = mlog.recv_match(blocking=False)
        except Exception:
            break
        if msg is None:
            break

        t = getattr(msg, 'TimeUS', None)
        if t and t > 0:
            last_t = int(t)

        if msg.get_type() == 'EV':
            ev_id = getattr(msg, 'Id', 0)
            t_ev  = int(getattr(msg, 'TimeUS', 0))
            if ev_id == 10:
                arm_times.append(t_ev)
            elif ev_id == 11:
                disarm_times.append(t_ev)

    if not arm_times:
        return -1.0, "No ARM event", date_str

    secs, note = _pair_flights(arm_times, disarm_times, last_t)
    return secs, note, date_str


def _pair_flights(arm_times: list, disarm_times: list, last_t: int) -> tuple:
    """Pair ARM/DISARM timestamps and return total flight seconds + note."""
    arm_times.sort()
    disarm_times.sort()

    total        = 0.0
    used_disarms = set()
    no_disarm    = 0

    for arm_t in arm_times:
        matched = None
        for i, d in enumerate(disarm_times):
            if d > arm_t and i not in used_disarms:
                matched = d
                used_disarms.add(i)
                break
        if matched is not None:
            total += (matched - arm_t) / 1_000_000
        elif last_t > arm_t:
            total += (last_t - arm_t) / 1_000_000
            no_disarm += 1

    n    = len(arm_times)
    note = f"{n} flight{'s' if n > 1 else ''}"
    if no_disarm:
        note += " (no DISARM)"
    return total, note


# ── Background worker ────────────────────────────────────────────────────────

class _ScanWorker(QThread):
    """Scans a directory and extracts flight (air) time from every log file."""

    progress     = pyqtSignal(int)               # 0-100 overall
    file_started = pyqtSignal(str, int, int)      # fname, completed, total
    file_done    = pyqtSignal(str, float, str, str) # fname, flight_secs (-1=skip), note, date_str
    scan_finished= pyqtSignal(float, int, int)    # total_secs, ok, skipped
    scan_error   = pyqtSignal(str)

    def __init__(self, directory: str, parent=None):
        super().__init__(parent)
        self._dir       = directory
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            self._scan()
        except Exception as exc:
            self.scan_error.emit(str(exc))

    # ── Scan loop ────────────────────────────────────────────────────────────

    def _scan(self):
        try:
            entries = list(os.scandir(self._dir))
        except PermissionError as exc:
            self.scan_error.emit(f"Permission denied: {exc}")
            return

        log_files = sorted(
            e.path for e in entries
            if e.is_file() and os.path.splitext(e.name)[1].lower() in ('.bin', '.tlog')
        )

        if not log_files:
            self.scan_error.emit("No .BIN or .TLOG files found in the selected folder.")
            return

        total      = len(log_files)
        total_secs = 0.0
        ok_count   = 0
        skip_count = 0

        # Choose parser per file type; .BIN uses the fast byte-level parser,
        # .tlog falls back to pymavlink (different stream format).
        def _pick(path: str):
            return _parse_ev_bin if path.lower().endswith('.bin') else _parse_ev_mavlink

        n_workers = min(os.cpu_count() or 1, 4)
        done      = 0

        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = {pool.submit(_pick(p), p): os.path.basename(p) for p in log_files}

            for future in as_completed(futures):
                if self._cancelled:
                    return

                fname = futures[future]
                try:
                    secs, note, date_str = future.result()
                except Exception as exc:
                    secs, note, date_str = -1.0, f"Error: {exc}", _mtime_str(
                        next(p for p in log_files if os.path.basename(p) == fname)
                    )

                done += 1
                if secs >= 0:
                    total_secs += secs
                    ok_count   += 1
                else:
                    skip_count += 1

                self.file_started.emit(fname, done, total)
                self.file_done.emit(fname, secs, note, date_str)
                self.progress.emit(int(done / total * 100))

        self.scan_finished.emit(total_secs, ok_count, skip_count)


# ── Sub-widget builders ──────────────────────────────────────────────────────

def _card(parent=None) -> QFrame:
    """Styled rounded card frame."""
    f = QFrame(parent)
    f.setFrameShape(QFrame.StyledPanel)
    f.setStyleSheet(
        f"QFrame {{ background-color: {_C['bg_secondary']};"
        f" border: 1px solid {_C['border']}; border-radius: 10px; }}"
    )
    return f


class _NumericItem(QTableWidgetItem):
    """Table item whose sort order is driven by its Qt.UserRole float value."""
    def __lt__(self, other: QTableWidgetItem) -> bool:
        try:
            return float(self.data(Qt.UserRole)) < float(other.data(Qt.UserRole))
        except (TypeError, ValueError):
            return super().__lt__(other)


def _make_table(parent=None, sortable: bool = False) -> QTableWidget:
    t = QTableWidget(0, 4, parent)
    t.setHorizontalHeaderLabels(["Filename", "Date & Time", "Flight Time", "Notes"])
    t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
    t.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
    t.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
    t.setColumnWidth(1, 148)
    t.setColumnWidth(2, 100)
    t.setColumnWidth(3, 150)
    t.verticalHeader().setDefaultSectionSize(30)
    t.verticalHeader().hide()
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setAlternatingRowColors(False)
    t.setShowGrid(False)
    header_hover = (
        f"QHeaderView::section:hover {{"
        f"  background-color: {_C['bg_hover']};"
        f"  color: {_C['text_primary']};"
        f"  cursor: pointer;"
        f"}}"
    ) if sortable else ""

    t.setStyleSheet(
        f"QTableWidget {{"
        f"  background-color: {_C['bg_secondary']};"
        f"  border: 1px solid {_C['border']}; border-radius: 8px;"
        f"  gridline-color: {_C['border']};"
        f"  color: {_C['text_primary']};"
        f"}}"
        f"QHeaderView::section {{"
        f"  background-color: {_C['bg_tertiary']};"
        f"  color: {_C['text_secondary']};"
        f"  border: none;"
        f"  border-bottom: 1px solid {_C['border']};"
        f"  padding: 6px 10px;"
        f"  font-size: 12px;"
        f"  font-weight: bold;"
        f"}}"
        f"{header_hover}"
        f"QTableWidget::item {{"
        f"  padding: 4px 10px;"
        f"  border-bottom: 1px solid {_C['bg_tertiary']};"
        f"}}"
        f"QTableWidget::item:selected {{"
        f"  background-color: {_C['bg_hover']};"
        f"  color: {_C['text_primary']};"
        f"}}"
    )

    if sortable:
        t.setSortingEnabled(True)
        t.horizontalHeader().setSortIndicatorShown(True)

    return t


def _table_item(text: str, align=Qt.AlignLeft | Qt.AlignVCenter) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setTextAlignment(align)
    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
    return item


# ── Module ───────────────────────────────────────────────────────────────────

class TotalFlightTimeModule(BaseModule):
    MODULE_ID    = "total_flight_time"
    DISPLAY_NAME = "Total Flight Time"
    DESCRIPTION  = "Calculate cumulative airtime across a folder of ArduPilot logs"
    ICON_CHAR    = "⏱"
    REQUIRED_MESSAGES = []

    def __init__(self):
        self._widget           = None
        self._worker           = None
        self._spinner_frame    = 0
        self._spinner_timer    = None
        self._row_idx          = 0
        self._running_total    = 0.0
        self._table_proc       = None
        self._table_res        = None
        self._stack            = None
        self._scan_folder      = ""
        self._open_log_handler = None   # set by MainWindow via set_open_log_handler()

    # ── Public: back-channel to MainWindow ────────────────────────────────────

    def set_open_log_handler(self, fn) -> None:
        """Called by MainWindow to provide the log-loading callback."""
        self._open_log_handler = fn

    # ── BaseModule ────────────────────────────────────────────────────────────

    def build_widget(self, parent=None) -> QWidget:
        self._widget = QWidget(parent)
        root = QVBoxLayout(self._widget)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._stack = QStackedWidget()
        root.addWidget(self._stack)

        self._stack.addWidget(self._build_info_page())
        self._stack.addWidget(self._build_processing_page())
        self._stack.addWidget(self._build_results_page())

        self._stack.setCurrentIndex(_PAGE_INFO)
        return self._widget

    def load_data(self, log_data) -> None:
        pass

    def clear(self) -> None:
        pass

    # ── Page builders ─────────────────────────────────────────────────────────

    def _build_info_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setAlignment(Qt.AlignCenter)
        outer.setContentsMargins(60, 40, 60, 40)
        outer.setSpacing(0)

        # ── Icon ──────────────────────────────────────────────────────────────
        icon_lbl = QLabel("⏱")
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon_lbl.setStyleSheet(
            f"font-size: 52px; color: {_C['accent']}; background: transparent;"
        )
        outer.addWidget(icon_lbl)
        outer.addSpacing(12)

        # ── Title ─────────────────────────────────────────────────────────────
        title = QLabel("Total Flight Time")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"font-size: 22px; font-weight: bold;"
            f" color: {_C['text_primary']}; background: transparent;"
        )
        outer.addWidget(title)
        outer.addSpacing(6)

        subtitle = QLabel(
            "Calculate cumulative airtime across multiple ArduPilot log files."
        )
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(
            f"font-size: 13px; color: {_C['text_secondary']}; background: transparent;"
        )
        outer.addWidget(subtitle)
        outer.addSpacing(28)

        # ── How it works card ─────────────────────────────────────────────────
        card = _card()
        card.setMaximumWidth(500)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 20, 24, 20)
        card_layout.setSpacing(14)

        how_title = QLabel("How it works")
        how_title.setStyleSheet(
            f"font-size: 12px; font-weight: bold; letter-spacing: 0.8px;"
            f" color: {_C['text_secondary']}; background: transparent;"
            f" text-transform: uppercase;"
        )
        card_layout.addWidget(how_title)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"border: none; border-top: 1px solid {_C['border']};")
        sep.setFixedHeight(1)
        card_layout.addWidget(sep)

        steps = [
            ("1", "Select a folder containing ArduPilot .BIN or .TLOG log files."),
            ("2", "Each log is scanned for ARM & DISARM events — only actual airtime\n"
                  "is measured, ground idle time is excluded."),
            ("3", "Individual flight times are summed into a total across all files."),
        ]
        for num, text in steps:
            row = QHBoxLayout()
            row.setSpacing(14)
            row.setContentsMargins(0, 0, 0, 0)

            badge = QLabel(num)
            badge.setFixedSize(22, 22)
            badge.setAlignment(Qt.AlignCenter)
            badge.setStyleSheet(
                f"background-color: {_C['accent']}; color: #ffffff;"
                f" border-radius: 11px; font-size: 11px; font-weight: bold;"
            )

            step_lbl = QLabel(text)
            step_lbl.setWordWrap(True)
            step_lbl.setStyleSheet(
                f"color: {_C['text_primary']}; background: transparent;"
                f" font-size: 13px; line-height: 1.4;"
            )

            row.addWidget(badge, 0, Qt.AlignTop)
            row.addWidget(step_lbl, 1)
            card_layout.addLayout(row)

        outer.addWidget(card, 0, Qt.AlignCenter)
        outer.addSpacing(32)

        # ── Select folder button ──────────────────────────────────────────────
        btn = QPushButton("  Select Log Folder…")
        btn.setFixedHeight(44)
        btn.setFixedWidth(220)
        btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {_C['accent']};"
            f"  color: #ffffff;"
            f"  border: none;"
            f"  border-radius: 8px;"
            f"  font-size: 14px;"
            f"  font-weight: bold;"
            f"}}"
            f"QPushButton:hover {{"
            f"  background-color: {_C['accent_hover']};"
            f"}}"
            f"QPushButton:pressed {{"
            f"  background-color: {_C['border_active']};"
            f"}}"
        )
        btn.clicked.connect(self._on_select_folder)
        outer.addWidget(btn, 0, Qt.AlignCenter)
        outer.addSpacing(20)

        return page

    def _build_processing_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 32, 40, 32)
        layout.setSpacing(0)

        # ── Top section: spinner + progress ───────────────────────────────────
        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setAlignment(Qt.AlignCenter)
        top_layout.setSpacing(12)
        top_layout.setContentsMargins(0, 0, 0, 0)

        self._proc_spinner = QLabel(_SPINNER_FRAMES[0])
        self._proc_spinner.setAlignment(Qt.AlignCenter)
        self._proc_spinner.setStyleSheet(
            f"font-size: 40px; color: {_C['accent']}; background: transparent;"
        )
        top_layout.addWidget(self._proc_spinner)

        self._proc_title = QLabel("Analyzing Logs…")
        self._proc_title.setAlignment(Qt.AlignCenter)
        self._proc_title.setStyleSheet(
            f"font-size: 17px; font-weight: bold;"
            f" color: {_C['text_primary']}; background: transparent;"
        )
        top_layout.addWidget(self._proc_title)

        self._proc_file_lbl = QLabel("")
        self._proc_file_lbl.setAlignment(Qt.AlignCenter)
        self._proc_file_lbl.setStyleSheet(
            f"font-size: 12px; color: {_C['text_secondary']}; background: transparent;"
        )
        top_layout.addWidget(self._proc_file_lbl)

        top_layout.addSpacing(16)

        bar_row = QHBoxLayout()
        bar_row.setContentsMargins(0, 0, 0, 0)
        bar_row.setSpacing(10)

        self._proc_bar = QProgressBar()
        self._proc_bar.setFixedHeight(8)
        self._proc_bar.setRange(0, 100)
        self._proc_bar.setValue(0)
        self._proc_bar.setTextVisible(False)
        self._proc_bar.setStyleSheet(
            f"QProgressBar {{ background-color: {_C['bg_tertiary']};"
            f" border: none; border-radius: 4px; }}"
            f"QProgressBar::chunk {{ background-color: {_C['accent']};"
            f" border-radius: 4px; }}"
        )
        bar_row.addWidget(self._proc_bar)

        self._proc_pct = QLabel("0%")
        self._proc_pct.setFixedWidth(38)
        self._proc_pct.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._proc_pct.setStyleSheet(
            f"font-size: 12px; font-weight: bold;"
            f" color: {_C['accent']}; background: transparent;"
        )
        bar_row.addWidget(self._proc_pct)
        top_layout.addLayout(bar_row)

        top_layout.addSpacing(8)

        # Running total during scan
        self._proc_running_lbl = QLabel("")
        self._proc_running_lbl.setAlignment(Qt.AlignCenter)
        self._proc_running_lbl.setStyleSheet(
            f"font-size: 12px; color: {_C['text_secondary']}; background: transparent;"
        )
        top_layout.addWidget(self._proc_running_lbl)

        top.setFixedHeight(220)
        layout.addWidget(top)
        layout.addSpacing(12)

        # ── Table label ───────────────────────────────────────────────────────
        tbl_hdr = QLabel("Results so far")
        tbl_hdr.setStyleSheet(
            f"font-size: 11px; font-weight: bold; letter-spacing: 0.6px;"
            f" color: {_C['text_secondary']}; background: transparent;"
        )
        layout.addWidget(tbl_hdr)
        layout.addSpacing(6)

        # ── Live-updating results table ───────────────────────────────────────
        self._table_proc = _make_table()
        layout.addWidget(self._table_proc, 1)

        return page

    def _build_results_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(48, 32, 48, 32)
        layout.setSpacing(0)

        # ── Page header ───────────────────────────────────────────────────────
        hdr = QLabel("Total Flight Time")
        hdr.setAlignment(Qt.AlignCenter)
        hdr.setStyleSheet(
            f"font-size: 13px; font-weight: bold; letter-spacing: 1px;"
            f" color: {_C['text_secondary']}; background: transparent;"
        )
        layout.addWidget(hdr)
        layout.addSpacing(16)

        # ── Big time display card ─────────────────────────────────────────────
        time_card = _card()
        time_card.setMaximumWidth(480)
        time_card.setFixedHeight(120)
        time_card_layout = QHBoxLayout(time_card)
        time_card_layout.setAlignment(Qt.AlignCenter)
        time_card_layout.setContentsMargins(24, 12, 24, 12)
        time_card_layout.setSpacing(0)

        def _digit_block(unit: str) -> tuple:
            col = QVBoxLayout()
            col.setSpacing(2)
            col.setContentsMargins(0, 0, 0, 0)
            num = QLabel("00")
            num.setAlignment(Qt.AlignCenter)
            num.setStyleSheet(
                f"font-size: 54px; font-weight: bold; font-family: 'Segoe UI', monospace;"
                f" color: {_C['accent']}; background: transparent; letter-spacing: 2px;"
            )
            lbl = QLabel(unit)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(
                f"font-size: 10px; letter-spacing: 1px; font-weight: bold;"
                f" color: {_C['text_disabled']}; background: transparent;"
            )
            col.addWidget(num)
            col.addWidget(lbl)
            return col, num

        def _colon():
            c = QLabel(":")
            c.setAlignment(Qt.AlignCenter)
            c.setStyleSheet(
                f"font-size: 42px; font-weight: bold; color: {_C['border']};"
                f" background: transparent; padding-bottom: 14px;"
            )
            return c

        col_h, self._res_hours = _digit_block("HH")
        col_m, self._res_mins  = _digit_block("MM")
        col_s, self._res_secs  = _digit_block("SS")

        time_card_layout.addLayout(col_h)
        time_card_layout.addWidget(_colon())
        time_card_layout.addLayout(col_m)
        time_card_layout.addWidget(_colon())
        time_card_layout.addLayout(col_s)

        layout.addWidget(time_card, 0, Qt.AlignCenter)
        layout.addSpacing(16)

        # ── Summary line ──────────────────────────────────────────────────────
        self._res_summary = QLabel("")
        self._res_summary.setAlignment(Qt.AlignCenter)
        self._res_summary.setStyleSheet(
            f"font-size: 13px; color: {_C['text_secondary']}; background: transparent;"
        )
        layout.addWidget(self._res_summary)
        layout.addSpacing(20)

        # ── Results table ─────────────────────────────────────────────────────
        tbl_hdr = QLabel("Log details")
        tbl_hdr.setStyleSheet(
            f"font-size: 11px; font-weight: bold; letter-spacing: 0.6px;"
            f" color: {_C['text_secondary']}; background: transparent;"
        )
        layout.addWidget(tbl_hdr)
        layout.addSpacing(6)

        self._table_res = _make_table(sortable=True)
        self._table_res.itemSelectionChanged.connect(self._update_open_btn)
        self._table_res.itemDoubleClicked.connect(self._on_open_log_clicked)
        layout.addWidget(self._table_res, 1)

        layout.addSpacing(14)

        # ── Open-log action bar ───────────────────────────────────────────────
        action_bar = QHBoxLayout()
        action_bar.setContentsMargins(0, 0, 0, 0)
        action_bar.setSpacing(10)

        self._res_sel_lbl = QLabel("Select a log row to open it in Logalyzer")
        self._res_sel_lbl.setStyleSheet(
            f"font-size: 12px; color: {_C['text_disabled']}; background: transparent;"
        )
        action_bar.addWidget(self._res_sel_lbl, 1)

        self._res_open_btn = QPushButton("Open in Logalyzer  →")
        self._res_open_btn.setFixedHeight(34)
        self._res_open_btn.setEnabled(False)
        self._res_open_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {_C['accent']}; color: #ffffff;"
            f"  border: none; border-radius: 6px;"
            f"  font-size: 13px; font-weight: bold; padding: 0 16px;"
            f"}}"
            f"QPushButton:hover {{ background-color: {_C['accent_hover']}; }}"
            f"QPushButton:pressed {{ background-color: {_C['border_active']}; }}"
            f"QPushButton:disabled {{"
            f"  background-color: {_C['bg_tertiary']};"
            f"  color: {_C['text_disabled']};"
            f"}}"
        )
        self._res_open_btn.clicked.connect(self._on_open_log_clicked)
        action_bar.addWidget(self._res_open_btn)

        layout.addLayout(action_bar)
        layout.addSpacing(12)

        # ── Analyze another folder button ─────────────────────────────────────
        reset_btn = QPushButton("Analyze Another Folder")
        reset_btn.setFixedHeight(34)
        reset_btn.setFixedWidth(200)
        reset_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {_C['bg_tertiary']};"
            f"  color: {_C['text_secondary']};"
            f"  border: 1px solid {_C['border']};"
            f"  border-radius: 6px; font-size: 12px;"
            f"}}"
            f"QPushButton:hover {{"
            f"  background-color: {_C['bg_hover']};"
            f"  color: {_C['text_primary']};"
            f"  border-color: {_C['border_active']};"
            f"}}"
        )
        reset_btn.clicked.connect(self._reset_to_info)
        layout.addWidget(reset_btn, 0, Qt.AlignCenter)
        layout.addSpacing(4)

        return page

    # ── Slot: folder selection ────────────────────────────────────────────────

    def _on_select_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self._widget,
            "Select Folder Containing ArduPilot Logs",
            os.path.expanduser("~"),
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )
        if not folder:
            return
        self._start_scan(folder)

    def _start_scan(self, folder: str):
        self._scan_folder = folder
        # Reset processing UI
        self._row_idx = 0
        self._table_proc.setRowCount(0)
        self._proc_bar.setValue(0)
        self._proc_pct.setText("0%")
        self._proc_file_lbl.setText("")
        self._proc_running_lbl.setText("")
        self._running_total = 0.0

        # Switch to processing page
        self._stack.setCurrentIndex(_PAGE_PROCESSING)

        # Start spinner
        self._spinner_frame = 0
        if self._spinner_timer is None:
            self._spinner_timer = QTimer(self._widget)
            self._spinner_timer.timeout.connect(self._tick_spinner)
        self._spinner_timer.start(80)

        # Launch worker
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait()

        self._worker = _ScanWorker(folder, parent=self._widget)
        self._worker.progress.connect(self._on_progress)
        self._worker.file_started.connect(self._on_file_started)
        self._worker.file_done.connect(self._on_file_done)
        self._worker.scan_finished.connect(self._on_scan_finished)
        self._worker.scan_error.connect(self._on_scan_error)
        self._worker.start()

    # ── Worker signal handlers ────────────────────────────────────────────────

    def _on_progress(self, pct: int):
        self._proc_bar.setValue(pct)
        self._proc_pct.setText(f"{pct}%")

    def _on_file_started(self, fname: str, done: int, total: int):
        self._proc_file_lbl.setText(
            f"{done} of {total} complete  ·  {fname}"
        )

    def _on_file_done(self, fname: str, secs: float, note: str, date_str: str):
        if secs >= 0:
            self._running_total += secs

        row = self._table_proc.rowCount()
        self._table_proc.insertRow(row)

        fname_item = _table_item(fname)
        fname_item.setData(Qt.UserRole, os.path.join(self._scan_folder, fname))
        self._table_proc.setItem(row, 0, fname_item)

        date_item = _table_item(date_str, Qt.AlignCenter | Qt.AlignVCenter)
        date_item.setForeground(QColor(_C['text_secondary']))
        self._table_proc.setItem(row, 1, date_item)

        time_item = _NumericItem(_fmt(secs))
        time_item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
        time_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        # Use raw seconds as sort key; skipped/error rows sort to the bottom
        time_item.setData(Qt.UserRole, secs if secs >= 0 else float('inf'))
        time_item.setForeground(
            QColor(_C['text_primary'] if secs >= 0 else _C['text_disabled'])
        )
        self._table_proc.setItem(row, 2, time_item)

        note_item = _table_item(note)
        if "Error" in note:
            note_item.setForeground(QColor(_C['danger']))
        elif "no DISARM" in note:
            note_item.setForeground(QColor(_C['warning']))
        else:
            note_item.setForeground(QColor(_C['success']))
        self._table_proc.setItem(row, 3, note_item)

        self._table_proc.scrollToBottom()
        self._proc_running_lbl.setText(
            f"Running total  ·  {_fmt(self._running_total)}"
        )

    def _on_scan_finished(self, total_secs: float, ok: int, skipped: int):
        self._stop_spinner()
        self._populate_results(total_secs, ok, skipped)
        self._stack.setCurrentIndex(_PAGE_RESULTS)

    def _on_scan_error(self, msg: str):
        self._stop_spinner()
        # Go back to info page and surface the error via title temporarily
        self._stack.setCurrentIndex(_PAGE_INFO)

        QMessageBox.warning(self._widget, "Scan Error", msg)

    # ── Results population ────────────────────────────────────────────────────

    def _populate_results(self, total_secs: float, ok: int, skipped: int):
        # Big time display
        hh, mm, ss = _fmt_parts(total_secs)
        self._res_hours.setText(hh)
        self._res_mins.setText(mm)
        self._res_secs.setText(ss)

        # Summary
        parts = [f"{ok + skipped} log{'s' if ok + skipped != 1 else ''} scanned"]
        if ok:
            parts.append(f"{ok} with flight data")
        if skipped:
            parts.append(f"{skipped} skipped")
        self._res_summary.setText("  ·  ".join(parts))

        # Copy rows — disable sort during bulk insert to prevent mid-copy reshuffling
        self._table_res.setSortingEnabled(False)
        self._table_res.setRowCount(0)
        for r in range(self._table_proc.rowCount()):
            self._table_res.insertRow(r)
            for c in range(4):
                src = self._table_proc.item(r, c)
                if src:
                    dst = _NumericItem(src.text()) if c == 2 else QTableWidgetItem(src.text())
                    dst.setTextAlignment(src.textAlignment())
                    dst.setForeground(src.foreground())
                    dst.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    dst.setData(Qt.UserRole, src.data(Qt.UserRole))
                    self._table_res.setItem(r, c, dst)
        self._table_res.setSortingEnabled(True)

        self._update_open_btn()

    # ── Open-log helpers ──────────────────────────────────────────────────────

    def _update_open_btn(self):
        """Enable the Open button and update the label when a row is selected."""
        rows = self._table_res.selectionModel().selectedRows()
        if not rows or self._open_log_handler is None:
            self._res_open_btn.setEnabled(False)
            self._res_sel_lbl.setText("Select a log row to open it in Logalyzer")
            self._res_sel_lbl.setStyleSheet(
                f"font-size: 12px; color: {_C['text_disabled']}; background: transparent;"
            )
            return

        fname = self._table_res.item(rows[0].row(), 0).text()
        self._res_open_btn.setEnabled(True)
        self._res_sel_lbl.setText(fname)
        self._res_sel_lbl.setStyleSheet(
            f"font-size: 12px; color: {_C['text_secondary']}; background: transparent;"
        )

    def _on_open_log_clicked(self):
        """Open the selected log file via the MainWindow callback."""
        if self._open_log_handler is None:
            return
        rows = self._table_res.selectionModel().selectedRows()
        if not rows:
            return
        path = self._table_res.item(rows[0].row(), 0).data(Qt.UserRole)
        if path and os.path.isfile(path):
            self._open_log_handler(path)
        else:
            QMessageBox.warning(self._widget, "File Not Found",
                                f"Could not locate:\n{path}")

    # ── Reset ─────────────────────────────────────────────────────────────────

    def _reset_to_info(self):
        self._stack.setCurrentIndex(_PAGE_INFO)

    # ── Spinner ───────────────────────────────────────────────────────────────

    def _tick_spinner(self):
        self._spinner_frame = (self._spinner_frame + 1) % len(_SPINNER_FRAMES)
        self._proc_spinner.setText(_SPINNER_FRAMES[self._spinner_frame])

    def _stop_spinner(self):
        if self._spinner_timer:
            self._spinner_timer.stop()
        self._proc_spinner.setText("✓")
        self._proc_spinner.setStyleSheet(
            f"font-size: 40px; color: {_C['success']}; background: transparent;"
        )
