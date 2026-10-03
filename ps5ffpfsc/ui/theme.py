"""Colour tokens and the application stylesheet (dark and light)."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    sidebar: str
    surface: str
    surface2: str
    surface3: str
    border: str
    text: str
    muted: str
    faint: str
    accent: str
    accent_hover: str
    accent_text: str
    accent_soft: str
    success: str
    warning: str
    danger: str
    success_soft: str
    warning_soft: str
    danger_soft: str


DARK = Palette(
    name="dark",
    bg="#0e1015",
    sidebar="#12151b",
    surface="#171b22",
    surface2="#1e232c",
    surface3="#272d38",
    border="#252b35",
    text="#e8eaf0",
    muted="#8d96a8",
    faint="#5c6578",
    accent="#4d8dff",
    accent_hover="#6ba1ff",
    accent_text="#ffffff",
    accent_soft="rgba(77, 141, 255, 0.15)",
    success="#3ddc97",
    warning="#f5c04a",
    danger="#ff6b6b",
    success_soft="rgba(61, 220, 151, 0.13)",
    warning_soft="rgba(245, 192, 74, 0.14)",
    danger_soft="rgba(255, 107, 107, 0.14)",
)

LIGHT = Palette(
    name="light",
    bg="#f3f4f7",
    sidebar="#ffffff",
    surface="#ffffff",
    surface2="#f1f3f7",
    surface3="#e5e8ee",
    border="#e1e4eb",
    text="#151922",
    muted="#5b6477",
    faint="#98a1b2",
    accent="#2f6fed",
    accent_hover="#4a82f0",
    accent_text="#ffffff",
    accent_soft="rgba(47, 111, 237, 0.10)",
    success="#0f9f6e",
    warning="#c27c0e",
    danger="#d93a3a",
    success_soft="rgba(15, 159, 110, 0.10)",
    warning_soft="rgba(194, 124, 14, 0.10)",
    danger_soft="rgba(217, 58, 58, 0.09)",
)

_current: Palette = DARK


def current() -> Palette:
    return _current


def system_prefers_dark() -> bool:
    hints = QGuiApplication.styleHints()
    try:
        return hints.colorScheme() != Qt.ColorScheme.Light
    except AttributeError:  # Qt < 6.5
        return True


def resolve(mode: str) -> Palette:
    if mode == "light":
        return LIGHT
    if mode == "dark":
        return DARK
    return DARK if system_prefers_dark() else LIGHT


def apply(app: QApplication, mode: str) -> Palette:
    global _current
    _current = p = resolve(mode)
    app.setStyle("Fusion")

    qp = QPalette()
    qp.setColor(QPalette.ColorRole.Window, QColor(p.bg))
    qp.setColor(QPalette.ColorRole.WindowText, QColor(p.text))
    qp.setColor(QPalette.ColorRole.Base, QColor(p.surface2))
    qp.setColor(QPalette.ColorRole.AlternateBase, QColor(p.surface))
    qp.setColor(QPalette.ColorRole.Text, QColor(p.text))
    qp.setColor(QPalette.ColorRole.Button, QColor(p.surface2))
    qp.setColor(QPalette.ColorRole.ButtonText, QColor(p.text))
    qp.setColor(QPalette.ColorRole.Highlight, QColor(p.accent))
    qp.setColor(QPalette.ColorRole.HighlightedText, QColor(p.accent_text))
    qp.setColor(QPalette.ColorRole.ToolTipBase, QColor(p.surface3))
    qp.setColor(QPalette.ColorRole.ToolTipText, QColor(p.text))
    qp.setColor(QPalette.ColorRole.PlaceholderText, QColor(p.faint))
    qp.setColor(QPalette.ColorRole.Link, QColor(p.accent))
    app.setPalette(qp)
    app.setStyleSheet(stylesheet(p))
    return p


def stylesheet(p: Palette) -> str:
    from .icons import icon_file

    check_icon = icon_file("check", p.accent_text)
    minus_icon = icon_file("minus", p.accent_text)
    return f"""
* {{
    font-size: 13px;
    color: {p.text};
}}
QMainWindow, QDialog, #Page {{
    background: {p.bg};
}}
QToolTip {{
    background: {p.surface3};
    color: {p.text};
    border: 1px solid {p.border};
    padding: 6px 8px;
    border-radius: 6px;
}}

/* ── Sidebar ─────────────────────────────────────────── */
#Sidebar {{
    background: {p.sidebar};
    border-right: 1px solid {p.border};
}}
#Brand {{
    font-size: 15px;
    font-weight: 700;
}}
#BrandSub {{
    color: {p.muted};
    font-size: 11px;
}}
#NavButton {{
    text-align: left;
    padding: 9px 12px;
    border: none;
    border-radius: 8px;
    background: transparent;
    color: {p.muted};
    font-weight: 600;
}}
#NavButton:hover {{
    background: {p.surface2};
    color: {p.text};
}}
#NavButton:checked {{
    background: {p.accent_soft};
    color: {p.accent};
}}
#NavBadge {{
    background: {p.accent};
    color: {p.accent_text};
    border-radius: 8px;
    padding: 0 6px;
    font-size: 11px;
    font-weight: 700;
}}

/* ── Typography ──────────────────────────────────────── */
QLabel[role="pageTitle"] {{
    font-size: 22px;
    font-weight: 700;
}}
QLabel[role="pageSubtitle"], QLabel[role="muted"] {{
    color: {p.muted};
}}
QLabel[role="faint"] {{
    color: {p.faint};
    font-size: 12px;
}}
QLabel[role="section"] {{
    color: {p.muted};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.6px;
}}
QLabel[role="cardTitle"] {{
    font-size: 14px;
    font-weight: 650;
}}
QLabel[role="heroTitle"] {{
    font-size: 20px;
    font-weight: 700;
}}
QLabel[role="statValue"] {{
    font-size: 16px;
    font-weight: 700;
}}
QLabel[role="statLabel"] {{
    color: {p.muted};
    font-size: 11px;
}}
QLabel[role="big"] {{
    font-size: 15px;
    font-weight: 650;
}}
QLabel[role="value"] {{
    color: {p.accent};
    font-weight: 700;
}}

/* ── Cards & surfaces ────────────────────────────────── */
#Card {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 12px;
}}
#Inset {{
    background: {p.surface2};
    border-radius: 10px;
}}
#JobBar {{
    background: {p.surface};
    border-top: 1px solid {p.border};
}}
#DropZone {{
    background: {p.surface};
    border: 2px dashed {p.surface3};
    border-radius: 14px;
}}
#DropZone[active="true"] {{
    border-color: {p.accent};
    background: {p.accent_soft};
}}
#Thumb {{
    background: {p.surface3};
    border-radius: 8px;
}}

/* ── Chips / badges ──────────────────────────────────── */
QLabel[chip="neutral"], QLabel[chip="accent"], QLabel[chip="success"],
QLabel[chip="warning"], QLabel[chip="danger"] {{
    border-radius: 6px;
    padding: 2px 7px;
    font-size: 11px;
    font-weight: 650;
}}
QLabel[chip="neutral"] {{ background: {p.surface3}; color: {p.muted}; }}
QLabel[chip="accent"]  {{ background: {p.accent_soft}; color: {p.accent}; }}
QLabel[chip="success"] {{ background: {p.success_soft}; color: {p.success}; }}
QLabel[chip="warning"] {{ background: {p.warning_soft}; color: {p.warning}; }}
QLabel[chip="danger"]  {{ background: {p.danger_soft}; color: {p.danger}; }}

QLabel[step="pending"] {{ color: {p.faint}; font-weight: 600; }}
QLabel[step="active"]  {{ color: {p.accent}; font-weight: 700; }}
QLabel[step="done"]    {{ color: {p.success}; font-weight: 600; }}
QLabel[step="failed"]  {{ color: {p.danger}; font-weight: 700; }}

/* ── Buttons ─────────────────────────────────────────── */
QPushButton, QToolButton {{
    background: {p.surface2};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 7px 14px;
    font-weight: 600;
}}
QPushButton:hover, QToolButton:hover {{
    background: {p.surface3};
}}
QPushButton:pressed, QToolButton:pressed {{
    background: {p.border};
}}
QPushButton:disabled, QToolButton:disabled {{
    color: {p.faint};
    background: {p.surface2};
}}
QPushButton[variant="primary"] {{
    background: {p.accent};
    border: 1px solid {p.accent};
    color: {p.accent_text};
}}
QPushButton[variant="primary"]:hover {{
    background: {p.accent_hover};
    border-color: {p.accent_hover};
}}
QPushButton[variant="primary"]:disabled {{
    background: {p.surface3};
    border-color: {p.surface3};
    color: {p.faint};
}}
QPushButton[variant="danger"] {{
    background: {p.danger_soft};
    border: 1px solid transparent;
    color: {p.danger};
}}
QPushButton[variant="danger"]:hover {{
    border-color: {p.danger};
}}
QPushButton[variant="ghost"], QToolButton[variant="ghost"] {{
    background: transparent;
    border: 1px solid transparent;
    color: {p.muted};
}}
QPushButton[variant="ghost"]:hover, QToolButton[variant="ghost"]:hover {{
    background: {p.surface2};
    color: {p.text};
}}
QPushButton[size="large"] {{
    padding: 10px 20px;
    font-size: 14px;
}}
QToolButton[variant="icon"] {{
    background: transparent;
    border: none;
    padding: 4px;
    border-radius: 6px;
}}
QToolButton[variant="icon"]:hover {{
    background: {p.surface3};
}}

/* Segmented control */
#Segmented {{
    background: {p.surface2};
    border-radius: 9px;
}}
#Segmented QPushButton {{
    background: transparent;
    border: none;
    border-radius: 7px;
    padding: 6px 10px;
    color: {p.muted};
}}
#Segmented QPushButton:hover {{
    color: {p.text};
}}
#Segmented QPushButton:checked {{
    background: {p.surface};
    color: {p.text};
    border: 1px solid {p.border};
}}

/* ── Inputs ──────────────────────────────────────────── */
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {{
    background: {p.surface2};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 6px 9px;
    selection-background-color: {p.accent};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {p.accent};
}}
QLineEdit:read-only {{
    color: {p.muted};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background: {p.surface};
    border: 1px solid {p.border};
    selection-background-color: {p.accent_soft};
    selection-color: {p.text};
    outline: none;
    padding: 4px;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    width: 16px;
    border: none;
    background: transparent;
}}
QCheckBox {{
    spacing: 8px;
}}
QCheckBox::indicator, QTreeView::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {p.faint};
    background: {p.surface2};
}}
QCheckBox::indicator:hover, QTreeView::indicator:hover {{
    border-color: {p.accent};
}}
QCheckBox::indicator:checked, QTreeView::indicator:checked {{
    background: {p.accent};
    border-color: {p.accent};
    image: url({check_icon});
}}
QTreeView::indicator:indeterminate {{
    background: {p.accent};
    border-color: {p.accent};
    image: url({minus_icon});
}}
QCheckBox::indicator:disabled {{
    background: {p.surface3};
    border-color: {p.surface3};
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: {p.surface3};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {p.accent};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px;
    background: {p.text};
}}

/* ── Progress ────────────────────────────────────────── */
QProgressBar {{
    background: {p.surface3};
    border: none;
    border-radius: 4px;
    max-height: 8px;
    min-height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: {p.accent};
    border-radius: 4px;
}}
QProgressBar[state="done"]::chunk {{ background: {p.success}; }}
QProgressBar[state="failed"]::chunk {{ background: {p.danger}; }}
QProgressBar[thin="true"] {{
    max-height: 4px;
    min-height: 4px;
    border-radius: 2px;
}}

/* ── Lists & trees ───────────────────────────────────── */
QListWidget, QTreeView {{
    background: transparent;
    border: none;
    outline: none;
}}
QListWidget::item {{
    border: none;
    padding: 0;
    margin: 0 0 8px 0;
}}
QListWidget::item:selected, QListWidget::item:hover {{
    background: transparent;
}}
QTreeView {{
    alternate-background-color: {p.surface2};
}}
QTreeView::item {{
    padding: 4px 2px;
    border: none;
}}
QTreeView::item:hover {{
    background: {p.surface2};
}}
QTreeView::item:selected {{
    background: {p.accent_soft};
    color: {p.text};
}}
QHeaderView::section {{
    background: {p.surface};
    color: {p.muted};
    border: none;
    border-bottom: 1px solid {p.border};
    padding: 6px 8px;
    font-size: 11px;
    font-weight: 700;
}}

/* ── Scrollbars ──────────────────────────────────────── */
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {p.surface3};
    border-radius: 3px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {p.faint};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {p.surface3};
    border-radius: 3px;
    min-width: 30px;
}}
QScrollBar::add-line, QScrollBar::sub-line,
QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
    border: none;
    width: 0;
    height: 0;
}}

QMenu {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 14px;
    border-radius: 5px;
}}
QMenu::item:selected {{
    background: {p.accent_soft};
}}
QMenu::separator {{
    height: 1px;
    background: {p.border};
    margin: 4px 6px;
}}
QMessageBox QLabel {{
    min-width: 280px;
}}
"""
