"""Typed access to persisted preferences (QSettings: registry/plist/ini)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QSettings, Signal

from ps5ffpfsc import APP_NAME, APP_ORG

DEFAULTS: dict[str, Any] = {
    "output_dir": "",
    "temp_dir": "",
    "unpack_dir": "",
    "ampr_dir": "",
    "ampr_cleanup": True,
    "level": 9,
    "cpu": 0,
    "block_size": "auto",
    "verify": False,
    "overwrite": False,
    "open_when_done": False,
    "theme": "system",
}

PRESETS: dict[str, dict[str, Any]] = {
    "Fast": {"level": 3, "cpu": 0, "block_size": "auto"},
    "Balanced": {"level": 6, "cpu": 0, "block_size": "auto"},
    "Max": {"level": 9, "cpu": 0, "block_size": "auto"},
    "Low RAM": {"level": 6, "cpu": 1, "block_size": "16384"},
}
PRESET_HINTS = {
    "Fast": "Level 3 · all cores — quickest, slightly larger files",
    "Balanced": "Level 6 · all cores — good size, good speed",
    "Max": "Level 9 · all cores — smallest files (default)",
    "Low RAM": "Level 6 · 1 core · 16 KiB blocks — for PCs with little memory",
}

BLOCK_SIZES = [
    ("auto", "Auto (64 KiB)"),
    ("auto-fit", "Auto-fit (best for many small files)"),
    ("16384", "16 KiB"),
    ("32768", "32 KiB"),
    ("65536", "64 KiB"),
]


class Settings(QObject):
    changed = Signal(str, object)

    def __init__(self, store: QSettings | None = None) -> None:
        super().__init__()
        self._qs = store or QSettings(APP_ORG, APP_NAME)

    def get(self, key: str) -> Any:
        default = DEFAULTS.get(key)
        value = self._qs.value(key, default)
        if isinstance(default, bool):
            return value in (True, "true", "1", 1)
        if isinstance(default, int):
            try:
                return int(value)
            except (TypeError, ValueError):
                return default
        return value if value is not None else default

    def set(self, key: str, value: Any) -> None:
        if self.get(key) == value:
            return
        self._qs.setValue(key, value)
        self.changed.emit(key, value)

    def raw(self) -> QSettings:
        return self._qs

    def matching_preset(self) -> str | None:
        current = {k: self.get(k) for k in ("level", "cpu", "block_size")}
        return next((name for name, vals in PRESETS.items() if vals == current), None)
