"""Offscreen UI tests: the real window, real worker, real files."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest
from conftest import tree_bytes

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp(prefix="ffpfsc-test-")  # keep session logs out of $HOME

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from ps5ffpfsc.ui import theme
from ps5ffpfsc.ui.main_window import MainWindow
from ps5ffpfsc.ui.runner import classify_backend_line
from ps5ffpfsc.ui.settings import Settings


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance() or QApplication([])
    theme.apply(instance, "dark")
    return instance


@pytest.fixture
def window(app: QApplication, tmp_path: Path):
    settings = Settings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    settings.set("output_dir", str(tmp_path / "out"))
    w = MainWindow(settings)
    w.show()
    yield w
    w.runner.cancel_all()
    w.close()


def wait_until(app: QApplication, condition, timeout: float = 120.0) -> None:
    end = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < end, "timed out"
        app.processEvents()
        time.sleep(0.02)


def test_compress_queue_end_to_end(app: QApplication, window: MainWindow, fixtures: dict[str, Path],
                                   tmp_path: Path) -> None:
    page = window.compress
    page.add_paths([fixtures["game"].parent, fixtures["exfat"]])
    wait_until(app, lambda: len(page.entries) == 2)
    page.add_paths([fixtures["exfat"]])  # duplicates are ignored
    wait_until(app, lambda: page._scanning == 0)
    assert len(page.entries) == 2

    page.start()
    wait_until(app, lambda: all(e.state == "done" for e in page.entries))
    outputs = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert outputs == ["PPSA01234 (2).ffpfsc", "PPSA01234.ffpfsc"]
    assert window.jobbar.isVisible()


def test_unpack_tree_selection(app: QApplication, window: MainWindow, fixtures: dict[str, Path],
                               tmp_path: Path) -> None:
    page = window.unpack
    page.open_paths([fixtures["ffpfsc"]])
    wait_until(app, lambda: page.images[0].summary is not None)
    detail = page.detail
    assert page.stack.currentWidget() is detail
    assert detail.title.text() == "Test Game"

    model = detail.model
    root = model.invisibleRootItem()
    data = next(root.child(r, 0) for r in range(root.rowCount()) if root.child(r, 0).text() == "data")
    data.setCheckState(Qt.CheckState.Unchecked)
    # Unchecking a folder unchecks everything below it.
    assert all(data.child(r, 0).checkState() == Qt.CheckState.Unchecked for r in range(data.rowCount()))
    # Re-checking one file makes the folder partially checked.
    readme = next(data.child(r, 0) for r in range(data.rowCount()) if data.child(r, 0).text() == "readme.txt")
    readme.setCheckState(Qt.CheckState.Checked)
    assert data.checkState() == Qt.CheckState.PartiallyChecked
    wait_until(app, lambda: sorted(detail._selected) == ["data/readme.txt", "eboot.bin", "sce_sys"])

    detail.dest.set_path(str(tmp_path), emit=True)
    detail._extract_selected()
    wait_until(app, lambda: page.images[0].jobs and page.images[0].jobs[-1].state == "done")
    extracted = tree_bytes(tmp_path / "PPSA01234")
    assert set(extracted) == {"data/readme.txt", "eboot.bin", "sce_sys/icon0.png", "sce_sys/param.json"}


def test_cancel_removes_partial_output(app: QApplication, window: MainWindow, tmp_path: Path) -> None:
    from conftest import make_game

    game = make_game(tmp_path / "big")
    (game / "data" / "huge.bin").write_bytes(os.urandom(64 * 1024 * 1024))
    page = window.compress
    window.settings.set("level", 9)
    window.settings.set("cpu", 1)
    page.add_paths([game])
    wait_until(app, lambda: len(page.entries) == 1)
    page.start()
    job = page.entries[0].job
    wait_until(app, lambda: job.state == "running" and job.stage_index >= 1)
    window.runner.cancel(job)
    wait_until(app, lambda: job.state == "cancelled")
    out = tmp_path / "out"
    assert not out.exists() or not any(out.iterdir())


def test_backend_line_classification() -> None:
    assert classify_backend_line("  Errors:  0") == "backend"
    assert classify_backend_line("Warnings: 0") == "backend"
    assert classify_backend_line("MemoryError") == "error"
    assert classify_backend_line("Traceback (most recent call last):") == "error"
    assert classify_backend_line("warning: something") == "warn"
