"""Settings page."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from ps5ffpfsc import APP_NAME, APP_VERSION

from .activity_page import log_dir
from .settings import Settings
from .widgets import Card, PathPicker, button, label, page_header


class SettingsPage(QWidget):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.setObjectName("Page")
        self.settings = settings
        s = settings

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        content = QWidget()
        content.setObjectName("Page")
        scroll.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)
        root.addWidget(page_header("Settings", "Saved automatically."))

        # Folders
        folders = Card()
        folders.body.addWidget(label("Folders", "cardTitle"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)

        def path_row(row: int, title: str, hint: str, key: str, placeholder: str) -> PathPicker:
            box = QVBoxLayout()
            box.setSpacing(1)
            box.addWidget(label(title))
            box.addWidget(label(hint, "faint", wrap=True))
            grid.addLayout(box, row, 0)
            picker = PathPicker(placeholder, title, clearable=True)
            picker.set_path(s.get(key))
            picker.pathChanged.connect(lambda v: s.set(key, v))
            s.changed.connect(lambda k, v: picker.set_path(v) if k == key else None)
            grid.addWidget(picker, row, 1)
            return picker

        path_row(0, "Output folder", "Where new .ffpfsc files are saved.", "output_dir", "Not set")
        path_row(1, "Temp folder", "Scratch space for archive extraction. A fast SSD helps.",
                 "temp_dir", "System temp folder")
        path_row(2, "Unpack folder", "Default destination for extracted games.", "unpack_dir",
                 "Next to the image")
        folders.body.addLayout(grid)
        root.addWidget(folders)

        # AMPR
        ampr = Card()
        ampr.body.addWidget(label("APR / AMPR games", "cardTitle"))
        ampr.body.addWidget(label(
            "APR titles (detected by sce_sys/playgo-chunk.dat) need the AMPR emulator. Before compressing, "
            "libSceAmpr.sprx and libScePlayGo.sprx are copied into the game's fakelib/ folder and mkpfs builds "
            "ampr_emu.index.", "muted", wrap=True))
        ampr_grid = QGridLayout()
        ampr_grid.setColumnStretch(1, 1)
        ampr_grid.addWidget(label("AMPR emu folder"), 0, 0)
        picker = PathPicker("Folder containing libSceAmpr.sprx and libScePlayGo.sprx", "AMPR emu folder",
                            clearable=True)
        picker.set_path(s.get("ampr_dir"))
        picker.pathChanged.connect(lambda v: s.set("ampr_dir", v))
        s.changed.connect(lambda k, v: picker.set_path(v) if k == "ampr_dir" else None)
        ampr_grid.addWidget(picker, 0, 1)
        ampr.body.addLayout(ampr_grid)
        ampr.body.addWidget(self._check("Remove the added files from my game folder afterwards", "ampr_cleanup",
                                        "The image keeps them; only your source folder is restored."))
        root.addWidget(ampr)

        # Behaviour
        behaviour = Card()
        behaviour.body.addWidget(label("Behaviour", "cardTitle"))
        behaviour.body.addWidget(self._check(
            "Overwrite existing .ffpfsc files", "overwrite",
            "When off, a new file is saved as “PPSA01234 (2).ffpfsc” instead."))
        behaviour.body.addWidget(self._check("Open the folder when a job finishes", "open_when_done"))
        theme_row = QHBoxLayout()
        theme_row.addWidget(label("Theme"))
        theme_combo = QComboBox()
        for value, text in (("system", "Match system"), ("dark", "Dark"), ("light", "Light")):
            theme_combo.addItem(text, value)
        theme_combo.setCurrentIndex(max(0, theme_combo.findData(s.get("theme"))))
        theme_combo.currentIndexChanged.connect(lambda _i: s.set("theme", theme_combo.currentData()))
        theme_row.addWidget(theme_combo)
        theme_row.addWidget(label("Applies the next time the app starts.", "faint"))
        theme_row.addStretch(1)
        behaviour.body.addLayout(theme_row)
        root.addWidget(behaviour)

        # About
        about = Card()
        about.body.addWidget(label("About", "cardTitle"))
        from mkpfs import __version__ as mkpfs_version

        about.body.addWidget(label(
            f"{APP_NAME} {APP_VERSION} · MkPFS {mkpfs_version}<br>"
            "Works fully offline — the app never connects to the internet.<br><br>"
            "Based on PS5 FFPFSC PRO by KINGDKAK. Image building by "
            "<a href='https://github.com/PSBrew/MkPFS'>MkPFS</a> (PSBrew); original backend by bizkut.",
            "muted", wrap=True))
        about.body.itemAt(about.body.count() - 1).widget().setOpenExternalLinks(True)
        logs = QHBoxLayout()
        logs.addWidget(button("Open log folder", "folder-open",
                              on_click=lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir())))))
        logs.addStretch(1)
        about.body.addLayout(logs)
        root.addWidget(about)
        root.addStretch(1)

    def _check(self, text: str, key: str, hint: str = "") -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        box = QCheckBox(text)
        box.setChecked(self.settings.get(key))
        box.toggled.connect(lambda v: self.settings.set(key, v))
        lay.addWidget(box)
        if hint:
            h = label(hint, "faint", wrap=True)
            h.setContentsMargins(24, 0, 0, 0)
            lay.addWidget(h)
        return w
