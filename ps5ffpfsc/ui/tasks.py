"""Run short blocking calls (folder scans, image reads) off the UI thread."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class _Bridge(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any], bridge: _Bridge) -> None:
        super().__init__()
        self._fn = fn
        self._bridge = bridge

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # reported to the UI
            self._bridge.failed.emit(str(exc) or type(exc).__name__)
        else:
            self._bridge.done.emit(result)


_alive: set[_Bridge] = set()


def run_async(fn: Callable[[], Any], on_done: Callable[[Any], None],
              on_failed: Callable[[str], None] | None = None) -> None:
    bridge = _Bridge()  # created on the UI thread, so slots run there
    _alive.add(bridge)

    def finish() -> None:
        _alive.discard(bridge)
        bridge.deleteLater()

    bridge.done.connect(on_done)
    bridge.done.connect(finish)
    if on_failed:
        bridge.failed.connect(on_failed)
    bridge.failed.connect(finish)
    QThreadPool.globalInstance().start(_Task(fn, bridge))
