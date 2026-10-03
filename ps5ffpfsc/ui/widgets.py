"""Reusable widgets shared by the pages."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import icons, theme


def restyle(widget: QWidget) -> None:
    """Re-apply the stylesheet after changing a dynamic property."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def label(text: str = "", role: str | None = None, *, wrap: bool = False,
          selectable: bool = False) -> QLabel:
    lbl = QLabel(text)
    if role:
        lbl.setProperty("role", role)
    lbl.setWordWrap(wrap)
    if selectable:
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lbl


class ElidedLabel(QLabel):
    """Single-line label that shortens itself with an ellipsis instead of growing."""

    def __init__(self, text: str = "", role: str | None = None,
                 mode: Qt.TextElideMode = Qt.TextElideMode.ElideMiddle) -> None:
        super().__init__()
        if role:
            self.setProperty("role", role)
        self._full = ""
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        width = max(10, self.width())
        super().setText(self.fontMetrics().elidedText(self._full, self._mode, width))

    def sizeHint(self):
        hint = super().sizeHint()
        hint.setWidth(self.fontMetrics().horizontalAdvance(self._full) + 4)
        return hint

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(40)
        return hint


def chip(text: str, kind: str = "neutral") -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("chip", kind)
    lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return lbl


def set_chip(lbl: QLabel, text: str, kind: str) -> None:
    lbl.setText(text)
    if lbl.property("chip") != kind:
        lbl.setProperty("chip", kind)
        restyle(lbl)


def button(text: str, icon_name: str | None = None, variant: str | None = None,
           on_click: Callable[[], None] | None = None, *, size: str | None = None) -> QPushButton:
    btn = QPushButton(text)
    if variant:
        btn.setProperty("variant", variant)
    if size:
        btn.setProperty("size", size)
    if icon_name:
        set_button_icon(btn, icon_name)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_click:
        btn.clicked.connect(on_click)
    return btn


def set_button_icon(btn: QPushButton | QToolButton, icon_name: str, color: str | None = None,
                    size: int = 16) -> None:
    p = theme.current()
    if color is None:
        variant = btn.property("variant")
        color = {"primary": p.accent_text, "danger": p.danger, "ghost": p.muted}.get(variant, p.text)
    btn.setIcon(icons.icon(icon_name, color, size))
    btn.setIconSize(QSize(size, size))


def icon_button(icon_name: str, tooltip: str, on_click: Callable[[], None] | None = None,
                color: str | None = None, size: int = 16) -> QToolButton:
    btn = QToolButton()
    btn.setProperty("variant", "icon")
    btn.setToolTip(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setIcon(icons.icon(icon_name, color or theme.current().muted, size))
    btn.setIconSize(QSize(size, size))
    if on_click:
        btn.clicked.connect(on_click)
    return btn


def icon_label(icon_name: str, color: str, size: int = 18) -> QLabel:
    lbl = QLabel()
    lbl.setPixmap(icons.pixmap(icon_name, color, size))
    lbl.setFixedSize(size, size)
    return lbl


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None, *, padding: int = 18, spacing: int = 12) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(padding, padding, padding, padding)
        self.body.setSpacing(spacing)


def hline() -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {theme.current().border};")
    return line


class Thumbnail(QLabel):
    """Rounded game icon with a fallback glyph."""

    def __init__(self, size: int = 48, fallback: str = "gamepad") -> None:
        super().__init__()
        self.setObjectName("Thumb")
        self._size = size
        self._fallback = fallback
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_png(None)

    def set_fallback(self, name: str) -> None:
        self._fallback = name

    def set_png(self, data: bytes | None) -> None:
        if data:
            px = QPixmap()
            if px.loadFromData(data):
                self.setPixmap(_rounded(px, self._size, radius=max(6, self._size // 6)))
                return
        self.setPixmap(icons.pixmap(self._fallback, theme.current().faint, int(self._size * 0.5)))


def _rounded(src: QPixmap, size: int, radius: int) -> QPixmap:
    from PySide6.QtGui import QPainter, QPainterPath

    ratio = 2.0
    target = QPixmap(int(size * ratio), int(size * ratio))
    target.fill(Qt.GlobalColor.transparent)
    scaled = src.scaled(target.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation)
    painter = QPainter(target)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, target.width(), target.height(), radius * ratio, radius * ratio)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, scaled)
    painter.end()
    target.setDevicePixelRatio(ratio)
    return target


class DropZone(QFrame):
    """Dashed drop target that also offers browse buttons."""

    pathsDropped = Signal(list)

    def __init__(self, title: str, hint: str, icon_name: str, accepts: Callable[[Path], bool],
                 compact: bool = False) -> None:
        super().__init__()
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self._accepts = accepts
        p = theme.current()

        lay = QHBoxLayout(self) if compact else QVBoxLayout(self)
        lay.setContentsMargins(20, 14 if compact else 32, 20, 14 if compact else 32)
        lay.setSpacing(12 if compact else 8)
        if not compact:
            lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        badge = QLabel()
        badge.setPixmap(icons.pixmap(icon_name, p.accent, 22 if compact else 30))
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(badge, 0, Qt.AlignmentFlag.AlignCenter if not compact else Qt.AlignmentFlag.AlignVCenter)

        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        self.title = label(title, "big")
        self.hint = label(hint, "muted", wrap=compact)
        for w in (self.title, self.hint):
            if not compact:
                w.setAlignment(Qt.AlignmentFlag.AlignCenter)
            text_box.addWidget(w)
        lay.addLayout(text_box, 1)

        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(8)
        if not compact:
            self.buttons.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addSpacing(8)
        lay.addLayout(self.buttons)

    def add_button(self, btn: QPushButton) -> None:
        self.buttons.addWidget(btn)

    def _set_active(self, active: bool) -> None:
        self.setProperty("active", "true" if active else "false")
        restyle(self)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        paths = dropped_paths(event)
        if paths and any(self._accepts(p) for p in paths):
            event.acceptProposedAction()
            self._set_active(True)

    def dragLeaveEvent(self, event) -> None:
        self._set_active(False)

    def dropEvent(self, event: QDropEvent) -> None:
        self._set_active(False)
        paths = [p for p in dropped_paths(event) if self._accepts(p)]
        if paths:
            event.acceptProposedAction()
            self.pathsDropped.emit(paths)


def dropped_paths(event: QDragEnterEvent | QDropEvent) -> list[Path]:
    mime = event.mimeData()
    if not mime.hasUrls():
        return []
    return [Path(u.toLocalFile()) for u in mime.urls() if u.isLocalFile()]


class PathPicker(QWidget):
    """Read-only path field with a browse button."""

    pathChanged = Signal(str)

    def __init__(self, placeholder: str, dialog_title: str, *, directory: bool = True,
                 file_filter: str = "", clearable: bool = False) -> None:
        super().__init__()
        self._dialog_title = dialog_title
        self._directory = directory
        self._filter = file_filter
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.edit = QLineEdit()
        self.edit.setReadOnly(True)
        self.edit.setPlaceholderText(placeholder)
        lay.addWidget(self.edit, 1)
        if clearable:
            self.clear_btn = icon_button("x", "Reset to default", lambda: self.set_path("", emit=True))
            lay.addWidget(self.clear_btn)
        browse = button("Browse…", "folder-open" if directory else "file", on_click=self._browse)
        lay.addWidget(browse)

        self._path = ""

    def path(self) -> str:
        return self._path

    def set_path(self, value: str, *, emit: bool = False) -> None:
        self._path = value
        self.edit.setToolTip(value)
        self._render()
        if emit:
            self.pathChanged.emit(value)

    def _render(self) -> None:
        # Show the end of long paths (the folder name matters most).
        room = self.edit.width() - 22
        self.edit.setText(self.edit.fontMetrics().elidedText(self._path, Qt.TextElideMode.ElideLeft, max(40, room)))
        self.edit.setCursorPosition(0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._render()

    def _browse(self) -> None:
        start = self.path() or str(Path.home())
        if self._directory:
            chosen = QFileDialog.getExistingDirectory(self, self._dialog_title, start)
        else:
            chosen, _ = QFileDialog.getOpenFileName(self, self._dialog_title, start, self._filter)
        if chosen:
            self.set_path(chosen, emit=True)


class Segmented(QFrame):
    """Row of mutually exclusive toggle buttons."""

    selected = Signal(str)

    def __init__(self, options: list[str]) -> None:
        super().__init__()
        self.setObjectName("Segmented")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(False)  # allow "no selection" (custom values)
        self._buttons: dict[str, QPushButton] = {}
        for name in options:
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, n=name: self._click(n))
            self._group.addButton(btn)
            self._buttons[name] = btn
            lay.addWidget(btn, 1)

    def _click(self, name: str) -> None:
        self.set_value(name)
        self.selected.emit(name)

    def set_value(self, name: str | None) -> None:
        for n, btn in self._buttons.items():
            btn.setChecked(n == name)

    def set_tooltip(self, name: str, text: str) -> None:
        self._buttons[name].setToolTip(text)


class StatTile(QWidget):
    def __init__(self, caption: str) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.value = label("—", "statValue")
        lay.addWidget(self.value)
        lay.addWidget(label(caption.upper(), "statLabel"))

    def set(self, text: str) -> None:
        self.value.setText(text)


class EmptyState(QWidget):
    def __init__(self, icon_name: str, title: str, text: str) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(8)
        ic = QLabel()
        ic.setPixmap(icons.pixmap(icon_name, theme.current().faint, 40))
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(ic)
        t = label(title, "big")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)
        self.text = label(text, "muted", wrap=True)
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text.setMaximumWidth(380)
        lay.addWidget(self.text, 0, Qt.AlignmentFlag.AlignCenter)


def page_header(title: str, subtitle: str) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(2)
    lay.addWidget(label(title, "pageTitle"))
    lay.addWidget(label(subtitle, "pageSubtitle"))
    return w
