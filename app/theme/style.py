COLORS = {
    "bg_primary":    "#0D1117",
    "bg_secondary":  "#161B22",
    "bg_tertiary":   "#1C2128",
    "bg_hover":      "#21262D",
    "border":        "#30363D",
    "border_active": "#58A6FF",
    "text_primary":  "#E6EDF3",
    "text_secondary":"#8B949E",
    "text_disabled": "#484F58",
    "accent":        "#58A6FF",
    "accent_hover":  "#79C0FF",
    "success":       "#3FB950",
    "warning":       "#D29922",
    "danger":        "#F85149",
    "chart_x":       "#58A6FF",
    "chart_y":       "#3FB950",
    "chart_z":       "#FF9F43",
}

C = COLORS  # shorthand

STYLESHEET = f"""
/* ── Base ─────────────────────────────────────────────── */
QMainWindow, QDialog {{
    background-color: {C['bg_primary']};
    color: {C['text_primary']};
}}

QWidget {{
    background-color: {C['bg_primary']};
    color: {C['text_primary']};
    font-family: "Segoe UI", "Inter", "SF Pro Display", sans-serif;
    font-size: 13px;
}}

/* ── Toolbar ──────────────────────────────────────────── */
QToolBar {{
    background-color: {C['bg_secondary']};
    border-bottom: 1px solid {C['border']};
    padding: 4px 8px;
    spacing: 6px;
}}

QToolBar::separator {{
    background-color: {C['border']};
    width: 1px;
    margin: 6px 4px;
}}

QToolButton {{
    background-color: {C['bg_tertiary']};
    color: {C['text_primary']};
    border: 1px solid {C['border']};
    border-radius: 6px;
    padding: 5px 12px;
    font-size: 13px;
}}

QToolButton:hover {{
    background-color: {C['bg_hover']};
    border-color: {C['border_active']};
}}

QToolButton:pressed {{
    background-color: {C['bg_primary']};
}}

QToolButton:disabled {{
    color: {C['text_disabled']};
    border-color: {C['border']};
}}

/* ── Sidebar / list ───────────────────────────────────── */
QListWidget {{
    background-color: {C['bg_secondary']};
    border: none;
    border-right: 1px solid {C['border']};
    outline: none;
    padding: 4px 0;
}}

QListWidget::item {{
    height: 42px;
    padding-left: 16px;
    color: {C['text_secondary']};
    border-left: 3px solid transparent;
}}

QListWidget::item:hover {{
    background-color: {C['bg_hover']};
    color: {C['text_primary']};
}}

QListWidget::item:selected {{
    background-color: {C['bg_tertiary']};
    color: {C['text_primary']};
    border-left: 3px solid {C['accent']};
}}

QListWidget::item:disabled {{
    color: {C['text_disabled']};
}}

/* ── Frames / cards ───────────────────────────────────── */
QFrame[frameShape="4"],
QFrame[frameShape="5"] {{
    background-color: {C['bg_secondary']};
    border: 1px solid {C['border']};
    border-radius: 8px;
}}

/* ── Splitter ─────────────────────────────────────────── */
QSplitter::handle {{
    background-color: {C['border']};
    width: 1px;
}}

/* ── Status bar ───────────────────────────────────────── */
QStatusBar {{
    background-color: {C['bg_secondary']};
    color: {C['text_secondary']};
    border-top: 1px solid {C['border']};
    font-size: 12px;
    padding: 2px 8px;
}}

/* ── Progress bar ─────────────────────────────────────── */
QProgressBar {{
    background-color: {C['bg_tertiary']};
    border: 1px solid {C['border']};
    border-radius: 4px;
    height: 8px;
    text-align: center;
    color: transparent;
}}

QProgressBar::chunk {{
    background-color: {C['accent']};
    border-radius: 3px;
}}

/* ── Scroll bars ──────────────────────────────────────── */
QScrollBar:vertical {{
    background-color: {C['bg_secondary']};
    width: 8px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background-color: {C['border']};
    border-radius: 4px;
    min-height: 24px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {C['text_secondary']};
}}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar:horizontal {{
    background-color: {C['bg_secondary']};
    height: 8px;
    margin: 0;
}}

QScrollBar::handle:horizontal {{
    background-color: {C['border']};
    border-radius: 4px;
    min-width: 24px;
}}

QScrollBar::handle:horizontal:hover {{
    background-color: {C['text_secondary']};
}}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ── Labels ───────────────────────────────────────────── */
QLabel {{
    background-color: transparent;
    color: {C['text_primary']};
}}

/* ── Buttons ──────────────────────────────────────────── */
QPushButton {{
    background-color: {C['bg_tertiary']};
    color: {C['text_primary']};
    border: 1px solid {C['border']};
    border-radius: 6px;
    padding: 5px 14px;
}}

QPushButton:hover {{
    background-color: {C['bg_hover']};
    border-color: {C['border_active']};
}}

QPushButton:pressed {{
    background-color: {C['bg_primary']};
}}

QPushButton:checked {{
    background-color: {C['accent']};
    color: #ffffff;
    border-color: {C['accent']};
    font-weight: bold;
}}

QPushButton:disabled {{
    color: {C['text_disabled']};
    border-color: {C['border']};
}}

/* ── Radio buttons ────────────────────────────────────── */
QRadioButton {{
    background-color: transparent;
    color: {C['text_secondary']};
    spacing: 6px;
}}

QRadioButton:checked {{
    color: {C['text_primary']};
}}

QRadioButton::indicator {{
    width: 14px;
    height: 14px;
    border-radius: 7px;
    border: 2px solid {C['border']};
    background-color: {C['bg_tertiary']};
}}

QRadioButton::indicator:checked {{
    background-color: {C['accent']};
    border-color: {C['accent']};
}}

/* ── Combo box ────────────────────────────────────────── */
QComboBox {{
    background-color: {C['bg_tertiary']};
    color: {C['text_primary']};
    border: 1px solid {C['border']};
    border-radius: 6px;
    padding: 4px 10px;
}}

QComboBox:hover {{
    border-color: {C['border_active']};
}}

QComboBox QAbstractItemView {{
    background-color: {C['bg_tertiary']};
    color: {C['text_primary']};
    border: 1px solid {C['border']};
    selection-background-color: {C['bg_hover']};
}}

/* ── Tab widget ───────────────────────────────────────── */
QTabWidget::pane {{
    border: 1px solid {C['border']};
    background-color: {C['bg_primary']};
}}

QTabBar::tab {{
    background-color: {C['bg_secondary']};
    color: {C['text_secondary']};
    padding: 6px 16px;
    border: 1px solid {C['border']};
    border-bottom: none;
}}

QTabBar::tab:selected {{
    background-color: {C['bg_primary']};
    color: {C['text_primary']};
    border-bottom: 2px solid {C['accent']};
}}

QTabBar::tab:hover {{
    color: {C['text_primary']};
}}

/* ── Group box ────────────────────────────────────────── */
QGroupBox {{
    background-color: {C['bg_secondary']};
    border: 1px solid {C['border']};
    border-radius: 8px;
    margin-top: 10px;
    padding-top: 8px;
    color: {C['text_secondary']};
    font-size: 11px;
}}
"""
