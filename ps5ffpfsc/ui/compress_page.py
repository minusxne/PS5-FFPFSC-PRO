"""Compress page: queue of games → .ffpfsc."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QStorageInfo, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ps5ffpfsc.core.games import (
    ARCHIVE_SUFFIXES,
    IMAGE_SUFFIXES,
    Source,
    SourceKind,
    find_sources,
    format_duration,
    format_size,
    inspect_source,
)

from .runner import Job, JobRunner
from .settings import BLOCK_SIZES, PRESET_HINTS, PRESETS, Settings
from .tasks import run_async
from .widgets import (
    Card,
    DropZone,
    ElidedLabel,
    EmptyState,
    PathPicker,
    Segmented,
    Thumbnail,
    button,
    chip,
    icon_button,
    label,
    page_header,
    restyle,
    set_chip,
)

KIND_LABEL = {SourceKind.FOLDER: "Game folder", SourceKind.IMAGE: "exFAT image", SourceKind.ARCHIVE: "Archive"}


def accepts_compress_input(path: Path) -> bool:
    return path.is_dir() or path.suffix.lower() in IMAGE_SUFFIXES | ARCHIVE_SUFFIXES


@dataclass
class QueueEntry:
    source: Source
    apr: bool
    password: str | None = None
    job: Job | None = None

    @property
    def state(self) -> str:
        return self.job.state if self.job else "ready"


class QueueRow(QWidget):
    removeRequested = Signal(object)
    changed = Signal()

    def __init__(self, entry: QueueEntry) -> None:
        super().__init__()
        self.entry = entry
        src = entry.source

        card = QWidget()
        card.setObjectName("Card")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 6, 0)
        outer.addWidget(card)

        lay = QHBoxLayout(card)
        lay.setContentsMargins(12, 10, 10, 10)
        lay.setSpacing(12)

        thumb = Thumbnail(46, {"folder": "gamepad", "image": "disk", "archive": "archive"}[src.kind.value])
        thumb.set_png(src.game.icon_png)
        lay.addWidget(thumb, 0, Qt.AlignmentFlag.AlignTop)

        mid = QVBoxLayout()
        mid.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(6)
        name = ElidedLabel(src.display_name, "cardTitle", Qt.TextElideMode.ElideRight)
        top.addWidget(name)
        if src.title_id:
            top.addWidget(chip(src.title_id, "accent"))
        top.addWidget(chip(KIND_LABEL[src.kind] if src.kind is not SourceKind.ARCHIVE
                           else src.path.suffix.lstrip(".").upper() + " archive"))
        if src.game.version:
            top.addWidget(chip("v" + src.game.version))
        if src.encrypted:
            top.addWidget(chip("Password", "warning"))
        top.addStretch(1)
        mid.addLayout(top)

        files = f" · {src.file_count:,} files" if src.kind is SourceKind.FOLDER else ""
        meta = QHBoxLayout()
        meta.setSpacing(6)
        meta.addWidget(label(f"{format_size(src.size)}{files} ·", "faint"))
        meta.addWidget(ElidedLabel(str(src.path), "faint", Qt.TextElideMode.ElideLeft), 1)
        mid.addLayout(meta)

        self.bar = QProgressBar()
        self.bar.setProperty("thin", "true")
        self.bar.setRange(0, 1000)
        self.bar.setVisible(False)
        mid.addWidget(self.bar)
        lay.addLayout(mid, 1)

        right = QVBoxLayout()
        right.setSpacing(6)
        right.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        status_row = QHBoxLayout()
        status_row.setSpacing(4)
        self.status = chip("Ready")
        status_row.addWidget(self.status)
        self.menu_btn = icon_button("more", "More actions", self._show_menu)
        status_row.addWidget(self.menu_btn)
        self.remove_btn = icon_button("x", "Remove from queue", lambda: self.removeRequested.emit(self.entry))
        status_row.addWidget(self.remove_btn)
        right.addLayout(status_row)

        self.apr = QCheckBox("APR / AMPR")
        self.apr.setToolTip("Adds the AMPR emulator libs (fakelib/) for APR titles.\n"
                            "Ticked automatically when sce_sys/playgo-chunk.dat is found.")
        self.apr.setChecked(entry.apr)
        self.apr.setVisible(src.kind is SourceKind.FOLDER)
        self.apr.toggled.connect(self._apr_toggled)
        right.addWidget(self.apr, 0, Qt.AlignmentFlag.AlignRight)
        self.result = label("", "muted")
        self.result.setAlignment(Qt.AlignmentFlag.AlignRight)
        right.addWidget(self.result)
        lay.addLayout(right)
        self.refresh()

    def _apr_toggled(self, on: bool) -> None:
        self.entry.apr = on
        self.changed.emit()

    def _show_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("Show source in folder", lambda: reveal(self.entry.source.path))
        if self.entry.job and self.entry.job.outputs:
            out = Path(self.entry.job.outputs[0]["path"])
            menu.addAction("Show output in folder", lambda: reveal(out))
        if self.entry.source.kind is SourceKind.ARCHIVE:
            menu.addAction("Set archive password…", self._ask_password)
        if self.entry.state in ("failed", "cancelled", "done"):
            menu.addAction("Reset to Ready", self._reset)
        menu.exec(self.menu_btn.mapToGlobal(self.menu_btn.rect().bottomLeft()))

    def _ask_password(self) -> None:
        pw, ok = QInputDialog.getText(self, "Archive password", f"Password for {self.entry.source.path.name}:",
                                      QLineEdit.EchoMode.Password, self.entry.password or "")
        if ok:
            self.entry.password = pw or None
            if self.entry.state in ("failed", "cancelled"):
                self._reset()

    def _reset(self) -> None:
        self.entry.job = None
        self.refresh()
        self.changed.emit()

    def refresh(self) -> None:
        job = self.entry.job
        state = self.entry.state
        running = state == "running"
        self.bar.setVisible(running)
        self.apr.setEnabled(state in ("ready", "failed", "cancelled"))
        self.remove_btn.setEnabled(not running)
        if state == "ready":
            set_chip(self.status, "Ready", "neutral")
            self.result.setText("")
        elif state == "queued":
            set_chip(self.status, "Queued", "neutral")
            self.result.setText("Waiting…")
        elif running and job:
            set_chip(self.status, f"{job.overall * 100:.0f}%", "accent")
            self.bar.setValue(int(job.overall * 1000))
            self.result.setText(job.phase or "Starting…")
        elif state == "done" and job:
            set_chip(self.status, "Done", "success")
            out = sum(o.get("size", 0) for o in job.outputs)
            src = sum(o.get("source_size", 0) for o in job.outputs) or self.entry.source.size
            saved = (1 - out / src) * 100 if src else 0
            self.result.setText(f"{format_size(src)} → {format_size(out)} (−{saved:.0f}%)")
            self.result.setToolTip(f"Took {format_duration(job.elapsed)}")
        elif state == "failed" and job:
            set_chip(self.status, "Failed", "danger")
            self.result.setText("Password needed" if job.password_required else "See Activity for details")
            self.result.setToolTip(job.error or "")
        elif state == "cancelled":
            set_chip(self.status, "Cancelled", "warning")
            self.result.setText("")


def reveal(path: Path) -> None:
    target = path if path.is_dir() else path.parent
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))


class CompressPage(QWidget):
    statusMessage = Signal(str)

    def __init__(self, settings: Settings, runner: JobRunner) -> None:
        super().__init__()
        self.setObjectName("Page")
        self.settings = settings
        self.runner = runner
        self.entries: list[QueueEntry] = []
        self.rows: dict[int, QueueRow] = {}
        self._scanning = 0

        root = QHBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(20)

        # ── Left: header, drop zone, queue ──────────────────────────────────
        left = QVBoxLayout()
        left.setSpacing(14)
        left.addWidget(page_header("Compress", "Turn PS5 game dumps into compact .ffpfsc images for ShadowMount."))

        self.drop = DropZone(
            "Drop games here",
            "Game folders, .exfat / .ffpkg images, or .zip / .7z / .rar archives",
            "compress",
            accepts_compress_input,
            compact=True,
        )
        self.drop.add_button(button("Add folder", "folder", on_click=self._browse_folder))
        self.drop.add_button(button("Add files", "file", on_click=self._browse_files))
        self.drop.pathsDropped.connect(self.add_paths)
        left.addWidget(self.drop)

        head = QHBoxLayout()
        head.addWidget(label("QUEUE", "section"))
        self.summary = label("", "faint")
        head.addWidget(self.summary)
        head.addStretch(1)
        self.clear_done_btn = button("Clear finished", variant="ghost", on_click=self.clear_finished)
        self.clear_all_btn = button("Clear all", "trash", variant="ghost", on_click=self.clear_all)
        head.addWidget(self.clear_done_btn)
        head.addWidget(self.clear_all_btn)
        left.addLayout(head)

        self.stack = QStackedWidget()
        self.empty = EmptyState("gamepad", "Your queue is empty",
                                "Drop a game folder anywhere on this page, or use Add folder. "
                                "Folders containing several games are scanned automatically.")
        self.list = QScrollArea()
        self.list.setWidgetResizable(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rows_host = QWidget()
        self.rows_layout = QVBoxLayout(rows_host)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(8)
        self.rows_layout.addStretch(1)
        self.list.setWidget(rows_host)
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.list)
        left.addWidget(self.stack, 1)
        root.addLayout(left, 1)

        # ── Right: options ──────────────────────────────────────────────────
        root.addWidget(self._build_options())

        self.setAcceptDrops(True)
        runner.jobChanged.connect(self._on_job_changed)
        runner.jobFinished.connect(self._on_job_changed)
        self._refresh()

    # ── options panel ───────────────────────────────────────────────────────

    def _build_options(self) -> QWidget:
        s = self.settings
        card = Card(padding=18, spacing=10)

        card.body.addWidget(label("OUTPUT FOLDER", "section"))
        self.output = PathPicker("Choose where .ffpfsc files go", "Choose output folder")
        self.output.set_path(s.get("output_dir"))
        self.output.pathChanged.connect(lambda v: s.set("output_dir", v))
        s.changed.connect(lambda k, v: self.output.set_path(v) if k == "output_dir" else None)
        card.body.addWidget(self.output)
        self.output_note = label("", "faint", wrap=True)
        card.body.addWidget(self.output_note)

        card.body.addSpacing(6)
        card.body.addWidget(label("PRESET", "section"))
        self.presets = Segmented(list(PRESETS))
        for name, hint in PRESET_HINTS.items():
            self.presets.set_tooltip(name, hint)
        self.presets.selected.connect(self._apply_preset)
        card.body.addWidget(self.presets)
        self.preset_hint = label("", "faint", wrap=True)
        card.body.addWidget(self.preset_hint)

        card.body.addSpacing(6)
        level_row = QHBoxLayout()
        level_row.addWidget(label("Compression level"))
        level_row.addStretch(1)
        self.level_value = label("", "value")
        level_row.addWidget(self.level_value)
        card.body.addLayout(level_row)
        self.level = QSlider(Qt.Orientation.Horizontal)
        self.level.setRange(1, 9)
        self.level.setPageStep(1)
        self.level.setValue(s.get("level"))
        self.level.valueChanged.connect(lambda v: self._set_option("level", v))
        card.body.addWidget(self.level)

        grid = QHBoxLayout()
        grid.setSpacing(10)
        cpu_box = QVBoxLayout()
        cpu_box.setSpacing(4)
        cpu_box.addWidget(label("CPU cores"))
        self.cpu = QSpinBox()
        self.cpu.setRange(0, os.cpu_count() or 64)
        self.cpu.setSpecialValueText(f"Auto ({max(1, min(16, (os.cpu_count() or 2) - 1))})")
        self.cpu.setValue(s.get("cpu"))
        self.cpu.valueChanged.connect(lambda v: self._set_option("cpu", v))
        cpu_box.addWidget(self.cpu)
        grid.addLayout(cpu_box, 1)
        block_box = QVBoxLayout()
        block_box.setSpacing(4)
        block_box.addWidget(label("Block size"))
        self.block = QComboBox()
        for value, text in BLOCK_SIZES:
            self.block.addItem(text.split(" (")[0], value)
            self.block.setItemData(self.block.count() - 1, text, Qt.ItemDataRole.ToolTipRole)
        self.block.setCurrentIndex(max(0, self.block.findData(s.get("block_size"))))
        self.block.currentIndexChanged.connect(lambda _i: self._set_option("block_size", self.block.currentData()))
        block_box.addWidget(self.block)
        grid.addLayout(block_box, 1)
        card.body.addLayout(grid)

        self.verify = QCheckBox("Verify image after building")
        self.verify.setToolTip("Reads every file back out of the finished image and compares it with the source.\n"
                               "Catches any packing problem; adds roughly the time of one more pass.")
        self.verify.setChecked(s.get("verify"))
        self.verify.toggled.connect(lambda v: s.set("verify", v))
        card.body.addWidget(self.verify)

        card.body.addStretch(1)
        self.start_btn = button("Start", "play", "primary", self.start, size="large")
        card.body.addWidget(self.start_btn)
        self.start_hint = label("", "faint", wrap=True)
        self.start_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card.body.addWidget(self.start_hint)

        self._sync_preset()
        scroll = QScrollArea()
        scroll.setWidget(card)
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(340)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return scroll

    def _set_option(self, key: str, value: object) -> None:
        self.settings.set(key, value)
        self._sync_preset()

    def _apply_preset(self, name: str) -> None:
        vals = PRESETS[name]
        for w in (self.level, self.cpu, self.block):
            w.blockSignals(True)
        self.level.setValue(vals["level"])
        self.cpu.setValue(vals["cpu"])
        self.block.setCurrentIndex(max(0, self.block.findData(vals["block_size"])))
        for w in (self.level, self.cpu, self.block):
            w.blockSignals(False)
        for k, v in vals.items():
            self.settings.set(k, v)
        self._sync_preset()

    def _sync_preset(self) -> None:
        self.level_value.setText(str(self.level.value()))
        name = self.settings.matching_preset()
        self.presets.set_value(name)
        self.preset_hint.setText(PRESET_HINTS[name] if name else "Custom settings")

    # ── adding sources ──────────────────────────────────────────────────────

    def _browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add game folder (or a folder of games)", str(Path.home()))
        if folder:
            self.add_paths([Path(folder)])

    def _browse_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add images or archives", str(Path.home()),
            "Images & archives (*.exfat *.ffpkg *.zip *.7z *.rar);;All files (*)")
        if files:
            self.add_paths([Path(f) for f in files])

    def add_paths(self, paths: list[Path]) -> None:
        known = {e.source.path.resolve() for e in self.entries}

        def scan() -> tuple[list[Source], list[Path]]:
            sources: list[Source] = []
            ignored: list[Path] = []
            for p in paths:
                found = find_sources(p)
                if not found:
                    ignored.append(p)
                for f in found:
                    if f.resolve() in known:
                        continue
                    known.add(f.resolve())
                    src = inspect_source(f)
                    if src:
                        sources.append(src)
            return sources, ignored

        self._scanning += 1
        self._refresh()
        run_async(scan, self._on_scanned, self._on_scan_failed)

    def _on_scan_failed(self, error: str) -> None:
        self._scanning -= 1
        self._refresh()
        QMessageBox.warning(self, "Could not add", error)

    def _on_scanned(self, result: tuple[list[Source], list[Path]]) -> None:
        self._scanning -= 1
        sources, ignored = result
        for src in sources:
            self._add_entry(QueueEntry(src, apr=src.apr_detected))
        if ignored and not sources:
            names = "\n".join(f"• {p.name}" for p in ignored[:6])
            QMessageBox.information(
                self, "No games found",
                f"Nothing to compress was found in:\n{names}\n\n"
                "A PS5 game folder contains eboot.bin and an sce_sys folder.")
        elif sources:
            self.statusMessage.emit(f"Added {len(sources)} item(s) to the queue.")
        self._refresh()

    def _add_entry(self, entry: QueueEntry) -> None:
        self.entries.append(entry)
        row = QueueRow(entry)
        row.removeRequested.connect(self.remove_entry)
        row.changed.connect(self._refresh)
        self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
        self.rows[id(entry)] = row

    def remove_entry(self, entry: QueueEntry) -> None:
        if entry.job and entry.job.state in ("queued", "running"):
            self.runner.cancel(entry.job)
            if entry.job.state == "running":
                return
        row = self.rows.pop(id(entry), None)
        if row is not None:
            row.setParent(None)
            row.deleteLater()
        self.entries.remove(entry)
        self._refresh()

    def clear_finished(self) -> None:
        for e in [e for e in self.entries if e.state in ("done", "cancelled")]:
            self.remove_entry(e)

    def clear_all(self) -> None:
        active = [e for e in self.entries if e.state in ("queued", "running")]
        if active and QMessageBox.question(
                self, "Clear queue", "Some games are still being compressed. Cancel them and clear the queue?"
        ) != QMessageBox.StandardButton.Yes:
            return
        for e in list(self.entries):
            if e.state != "running":
                self.remove_entry(e)
        for e in active:
            if e.job:
                self.runner.cancel(e.job)

    # ── running ─────────────────────────────────────────────────────────────

    def _startable(self) -> list[QueueEntry]:
        return [e for e in self.entries if e.state in ("ready", "failed", "cancelled")]

    def start(self) -> None:
        todo = self._startable()
        if not todo:
            return
        out = self.settings.get("output_dir")
        if not out:
            self.output._browse()
            out = self.settings.get("output_dir")
            if not out:
                return
        if not self._preflight(todo, Path(out)):
            return
        common = {
            "output_dir": out,
            "temp_dir": self.settings.get("temp_dir") or None,
            "level": self.settings.get("level"),
            "cpu": self.settings.get("cpu"),
            "block_size": self.settings.get("block_size"),
            "verify": self.settings.get("verify"),
            "overwrite": self.settings.get("overwrite"),
            "ampr_dir": self.settings.get("ampr_dir") or None,
            "ampr_cleanup": self.settings.get("ampr_cleanup"),
        }
        for entry in todo:
            src = entry.source
            spec = {
                **common,
                "kind": "compress",
                "source": str(src.path),
                "source_kind": src.kind.value,
                "password": entry.password,
                "apr": ("on" if entry.apr else "off") if src.kind is SourceKind.FOLDER else "auto",
            }
            job = Job("compress", spec, title=src.display_name,
                      subtitle=src.title_id or src.path.name, thumbnail=src.game.icon_png)
            entry.job = job
            self.runner.submit(job)
        self._refresh()

    def _preflight(self, todo: list[QueueEntry], out: Path) -> bool:
        # APR titles need the AMPR emulator folder.
        if any(e.apr for e in todo) and not Path(self.settings.get("ampr_dir") or "/nonexistent").is_dir():
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle("AMPR emulator files")
            box.setText("Some games are marked APR / AMPR.")
            box.setInformativeText(
                "They need libSceAmpr.sprx and libScePlayGo.sprx copied into a fakelib/ folder. "
                "Choose the folder that contains those two files.")
            choose = box.addButton("Choose folder…", QMessageBox.ButtonRole.AcceptRole)
            skip = box.addButton("Continue without", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            if box.clickedButton() is choose:
                folder = QFileDialog.getExistingDirectory(self, "Folder with libSceAmpr.sprx", str(Path.home()))
                if not folder:
                    return False
                self.settings.set("ampr_dir", folder)
            elif box.clickedButton() is not skip:
                return False

        # Passwords for encrypted archives.
        for e in todo:
            if e.source.encrypted and not e.password:
                pw, ok = QInputDialog.getText(self, "Archive password",
                                              f"{e.source.path.name} is password-protected.\nPassword:",
                                              QLineEdit.EchoMode.Password)
                if not ok:
                    return False
                e.password = pw or None

        # FAT32 cannot hold files over 4 GB.
        probe = out if out.exists() else out.parent
        storage = QStorageInfo(str(probe))
        fs = bytes(storage.fileSystemType().data()).decode(errors="ignore").lower()
        if fs in ("vfat", "fat32", "fat", "msdos") and any(e.source.size > 3.5 * 1024**3 for e in todo):
            QMessageBox.warning(self, "Output drive is FAT32",
                                "The output folder is on a FAT32 drive, which cannot store files larger "
                                "than 4 GB. Choose a folder on an NTFS, exFAT, APFS or ext4 drive.")
            return False

        need = sum(e.source.size for e in todo)
        free = storage.bytesAvailable()
        if storage.isValid() and 0 < free < need * 0.9:
            answer = QMessageBox.warning(
                self, "Low disk space",
                f"The output drive has {format_size(free)} free, and the selected games total "
                f"{format_size(need)}. Compressed images are usually smaller, but they may not fit.\n\n"
                "Start anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return False
        return True

    def _on_job_changed(self, job: Job) -> None:
        for entry in self.entries:
            if entry.job is job:
                row = self.rows.get(id(entry))
                if row:
                    row.refresh()
                break
        if job.state not in ("running",):
            self._refresh()

    def _refresh(self) -> None:
        has = bool(self.entries)
        self.stack.setCurrentWidget(self.list if has else self.empty)
        total = sum(e.source.size for e in self.entries)
        done = sum(1 for e in self.entries if e.state == "done")
        parts = [f"{len(self.entries)} item(s) · {format_size(total)}"] if has else []
        if done:
            parts.append(f"{done} done")
        if self._scanning:
            parts.append("scanning…")
        self.summary.setText("  ·  ".join(parts))
        self.clear_done_btn.setVisible(any(e.state in ("done", "cancelled") for e in self.entries))
        self.clear_all_btn.setVisible(has)

        todo = self._startable()
        self.start_btn.setEnabled(bool(todo))
        if todo:
            size = format_size(sum(e.source.size for e in todo))
            self.start_btn.setText(f"Compress {len(todo)} game{'s' if len(todo) != 1 else ''}")
            busy = self.runner.busy
            self.start_hint.setText(f"{size} total" + (" · will be added after the current job" if busy else ""))
        else:
            running = any(e.state in ("queued", "running") for e in self.entries)
            self.start_btn.setText("Compressing…" if running else "Start")
            self.start_hint.setText("" if running or has else "Add a game to get started")
        restyle(self.start_btn)
        out = self.settings.get("output_dir")
        self.output_note.setText("" if out else "Required — pick a drive with plenty of free space.")

    def has_active_work(self) -> bool:
        return any(e.state in ("queued", "running") for e in self.entries)

    # Page-wide drop target (the drop zone handles its own area).
    def dragEnterEvent(self, event) -> None:
        self.drop.dragEnterEvent(event)

    def dragLeaveEvent(self, event) -> None:
        self.drop.dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self.drop.dropEvent(event)
