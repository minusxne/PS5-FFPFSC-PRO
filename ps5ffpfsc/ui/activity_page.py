"""Activity page: the session log (also written to a file for bug reports)."""

from __future__ import annotations

import html
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Signal
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtWidgets import QCheckBox, QFileDialog, QHBoxLayout, QPlainTextEdit, QVBoxLayout, QWidget

from . import theme
from .widgets import Card, button, page_header

MAX_LINES = 20000


def log_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    path = Path(base or Path.home() / ".ps5ffpfsc") / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


class ActivityPage(QWidget):
    errorLogged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Page")
        self._lines: list[tuple[str, str, str]] = []  # (time, level, message)
        self._log_file = self._open_log_file()

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(14)
        head = QHBoxLayout()
        head.addWidget(page_header("Activity", "Everything that happened this session, including mkpfs output."), 1)
        self.show_backend = QCheckBox("Show mkpfs output")
        self.show_backend.setChecked(True)
        self.show_backend.toggled.connect(self._rerender)
        head.addWidget(self.show_backend)
        head.addWidget(button("Copy", "copy", on_click=self._copy))
        head.addWidget(button("Save…", "save", on_click=self._save))
        head.addWidget(button("Clear", "trash", variant="ghost", on_click=self._clear))
        root.addLayout(head)

        card = Card(padding=4)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(MAX_LINES)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSizeF(max(9.0, font.pointSizeF()))
        self.view.setFont(font)
        self.view.setStyleSheet("QPlainTextEdit { background: transparent; border: none; font-size: 12px; }")
        card.body.addWidget(self.view)
        root.addWidget(card, 1)

    def _open_log_file(self):
        try:
            folder = log_dir()
            logs = sorted(folder.glob("session-*.log"))
            for old in logs[:-9]:  # keep the last 10 sessions
                old.unlink(missing_ok=True)
            return (folder / f"session-{datetime.now():%Y%m%d-%H%M%S}.log").open("a", encoding="utf-8")
        except OSError:
            return None

    def append(self, level: str, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        for line in message.splitlines() or [""]:
            self._lines.append((stamp, level, line))
            if self._log_file:
                self._log_file.write(f"{stamp} [{level.upper():7}] {line}\n")
            if level != "backend" or self.show_backend.isChecked():
                self.view.appendHtml(self._format(stamp, level, line))
        if self._log_file:
            self._log_file.flush()
        if len(self._lines) > MAX_LINES:
            del self._lines[: len(self._lines) - MAX_LINES]
        if level == "error":
            self.errorLogged.emit()

    def _format(self, stamp: str, level: str, line: str) -> str:
        p = theme.current()
        color = {"error": p.danger, "warn": p.warning, "ok": p.success, "backend": p.muted}.get(level, p.text)
        tag = {"error": "ERROR", "warn": "WARN", "ok": "OK", "backend": "mkpfs", "info": "INFO"}.get(level, level)
        return (f'<span style="color:{p.faint}">{stamp}</span> '
                f'<span style="color:{color}; font-weight:600">{tag:<5}</span> '
                f'<span style="color:{color}">{html.escape(line)}</span>').replace("  ", "&nbsp; ")

    def _rerender(self) -> None:
        self.view.clear()
        show = self.show_backend.isChecked()
        for stamp, level, line in self._lines:
            if level != "backend" or show:
                self.view.appendHtml(self._format(stamp, level, line))

    def plain_text(self) -> str:
        return "\n".join(f"{s} [{lvl.upper()}] {line}" for s, lvl, line in self._lines)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.plain_text())

    def _save(self) -> None:
        name = f"ffpfsc-log-{datetime.now():%Y%m%d-%H%M%S}.txt"
        path, _ = QFileDialog.getSaveFileName(self, "Save log", str(Path.home() / name), "Text files (*.txt)")
        if path:
            Path(path).write_text(self.plain_text() + "\n", encoding="utf-8")

    def _clear(self) -> None:
        self._lines.clear()
        self.view.clear()
