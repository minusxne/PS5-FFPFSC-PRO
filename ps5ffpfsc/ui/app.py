"""GUI bootstrap."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from ps5ffpfsc import APP_NAME, APP_ORG, APP_VERSION
from ps5ffpfsc.core.games import PACKED_SUFFIXES


def run_gui(args: list[str]) -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ORG)
    app.setApplicationVersion(APP_VERSION)
    app.setDesktopFileName("ps5ffpfsc")

    from . import theme
    from .main_window import MainWindow
    from .settings import Settings

    settings = Settings()
    theme.apply(app, settings.get("theme"))
    window = MainWindow(settings)
    window.show()

    # Paths passed on the command line (e.g. "Open with…" or drag onto the exe).
    paths = [Path(a) for a in args if not a.startswith("-") and Path(a).exists()]
    images = [p for p in paths if p.suffix.lower() in PACKED_SUFFIXES]
    others = [p for p in paths if p not in images]
    if images:
        window.unpack.open_paths(images)
        window.show_page("unpack")
    if others:
        window.compress.add_paths(others)

    return app.exec()
