"""Main window: sidebar navigation, pages and the job bar."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QCloseEvent, QDragEnterEvent, QDropEvent, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ps5ffpfsc import APP_NAME, APP_VERSION
from ps5ffpfsc.core.games import PACKED_SUFFIXES

from . import icons, theme
from .activity_page import ActivityPage
from .compress_page import CompressPage, accepts_compress_input
from .jobbar import JobBar
from .runner import Job, JobRunner
from .settings import Settings
from .settings_page import SettingsPage
from .unpack_page import UnpackPage, accepts_image
from .widgets import dropped_paths, label


class NavButton(QPushButton):
    def __init__(self, text: str, icon_name: str) -> None:
        super().__init__(text)
        self.setObjectName("NavButton")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._icon_name = icon_name
        self.setIconSize(QSize(18, 18))
        self.toggled.connect(self._tint)
        self._tint(False)

    def _tint(self, checked: bool) -> None:
        p = theme.current()
        self.setIcon(icons.icon(self._icon_name, p.accent if checked else p.muted, 18))


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setMinimumSize(1080, 700)
        self.setAcceptDrops(True)

        self.runner = JobRunner(self)
        self.compress = CompressPage(settings, self.runner)
        self.unpack = UnpackPage(settings, self.runner)
        self.activity = ActivityPage()
        self.settings_page = SettingsPage(settings)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        self.pages = QStackedWidget()
        for page in (self.compress, self.unpack, self.activity, self.settings_page):
            self.pages.addWidget(page)
        right.addWidget(self.pages, 1)
        self.jobbar = JobBar(self.runner)
        right.addWidget(self.jobbar)
        root.addLayout(right, 1)
        self.setCentralWidget(central)

        self.runner.log.connect(self.activity.append)
        self.runner.jobStarted.connect(lambda j: self.activity.append("info", f"Started: {j.title}"))
        self.runner.jobFinished.connect(self._job_finished)
        self.runner.queueDrained.connect(lambda: QApplication.alert(self))
        self.activity.errorLogged.connect(self._flag_activity)
        for page in (self.compress, self.unpack):
            page.statusMessage.connect(lambda msg: self.activity.append("info", msg))

        self.nav_buttons[0].setChecked(True)
        self.activity.append("info", f"{APP_NAME} {APP_VERSION} ready. Offline mode — no network access.")
        geometry = settings.raw().value("geometry")
        if isinstance(geometry, QByteArray):
            self.restoreGeometry(geometry)
        else:
            self.resize(1320, 840)

    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(212)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(14, 20, 14, 16)
        lay.setSpacing(4)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(34, 34))
        brand.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        title = QLabel("FFPFSC PRO")
        title.setObjectName("Brand")
        sub = QLabel("PS5 image toolkit")
        sub.setObjectName("BrandSub")
        names.addWidget(title)
        names.addWidget(sub)
        brand.addLayout(names)
        brand.addStretch(1)
        lay.addLayout(brand)
        lay.addSpacing(22)

        group = QButtonGroup(self)
        group.setExclusive(True)
        self.nav_buttons: list[NavButton] = []
        for index, (text, icon_name) in enumerate(
            (("Compress", "compress"), ("Unpack", "unpack"), ("Activity", "activity"), ("Settings", "settings"))
        ):
            btn = NavButton(text, icon_name)
            btn.toggled.connect(lambda on, i=index: on and self._go(i))
            group.addButton(btn)
            lay.addWidget(btn)
            self.nav_buttons.append(btn)

        lay.addStretch(1)
        offline = QHBoxLayout()
        offline.setSpacing(6)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background: {theme.current().success}; border-radius: 4px;")
        offline.addWidget(dot)
        offline.addWidget(label("Offline · v" + APP_VERSION, "faint"))
        offline.addStretch(1)
        lay.addLayout(offline)
        return side

    def _go(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        if index == 2:
            self.nav_buttons[2].setText("Activity")

    def _flag_activity(self) -> None:
        if self.pages.currentIndex() != 2:
            self.nav_buttons[2].setText("Activity  •")

    def _job_finished(self, job: Job) -> None:
        if job.state == "done":
            self.activity.append("ok", f"Finished: {job.title} ({job.elapsed:.0f}s)")
            if job.kind != "verify" and self.settings.get("open_when_done") and job.outputs:
                from .compress_page import reveal

                reveal(Path(job.outputs[0]["path"]))

    def show_page(self, name: str) -> None:
        index = {"compress": 0, "unpack": 1, "activity": 2, "settings": 3}[name]
        self.nav_buttons[index].setChecked(True)

    # Drops anywhere in the window are routed to the right page.
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if any(accepts_image(p) or accepts_compress_input(p) for p in dropped_paths(event)):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = dropped_paths(event)
        on_unpack = self.pages.currentWidget() is self.unpack
        images = [p for p in paths if accepts_image(p) and (on_unpack or p.suffix.lower() in PACKED_SUFFIXES)]
        others = [p for p in paths if p not in images and accepts_compress_input(p)]
        if images:
            self.unpack.open_paths(images)
            self.show_page("unpack")
        if others:
            self.compress.add_paths(others)
            if not images:
                self.show_page("compress")
        event.acceptProposedAction()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.runner.busy:
            answer = QMessageBox.question(
                self, "Jobs are running",
                "A job is still running. Quit anyway? The current job will be cancelled and its "
                "partial files removed.")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.runner.cancel_all()
        self.settings.raw().setValue("geometry", self.saveGeometry())
        event.accept()


def app_icon() -> QIcon:
    """Rounded accent square with the compress glyph."""
    from PySide6.QtGui import QColor, QPainter, QPixmap

    p = theme.current()
    size = 128
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(p.accent))
    painter.drawRoundedRect(0, 0, size, size, 30, 30)
    glyph = icons.pixmap("compress", "#ffffff", 76, ratio=1.0)
    painter.drawPixmap((size - 76) // 2, (size - 76) // 2, glyph)
    painter.end()
    return QIcon(px)
