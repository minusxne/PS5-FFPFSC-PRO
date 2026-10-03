"""Unpack page: browse .ffpfsc / .ffpfs / .exfat / .ffpkg images and extract them."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QSortFilterProxyModel, Qt, QTimer, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QStackedWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ps5ffpfsc.core.games import UNPACKABLE_SUFFIXES, format_size, unique_path
from ps5ffpfsc.core.image_reader import Entry, ImageSummary, free_space, read_summary

from . import icons, theme
from .runner import Job, JobRunner
from .settings import Settings
from .tasks import run_async
from .widgets import (
    Card,
    DropZone,
    ElidedLabel,
    EmptyState,
    PathPicker,
    StatTile,
    Thumbnail,
    button,
    chip,
    icon_button,
    label,
    page_header,
    set_chip,
)

PATH_ROLE = Qt.ItemDataRole.UserRole + 1
SIZE_ROLE = Qt.ItemDataRole.UserRole + 2
DIR_ROLE = Qt.ItemDataRole.UserRole + 3

FILE_FILTER = "PS5 images (*.ffpfsc *.ffpfs *.exfat *.ffpkg);;All files (*)"


def accepts_image(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in UNPACKABLE_SUFFIXES


@dataclass
class OpenImage:
    path: Path
    summary: ImageSummary | None = None
    error: str | None = None
    jobs: list[Job] = field(default_factory=list)

    @property
    def title(self) -> str:
        if self.summary and self.summary.game.title:
            return self.summary.game.title
        return self.path.name


class ImageRow(QWidget):
    closeRequested = Signal(object)

    def __init__(self, image: OpenImage) -> None:
        super().__init__()
        self.image = image
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 6, 8)
        lay.setSpacing(10)
        self.thumb = Thumbnail(40, "disk")
        lay.addWidget(self.thumb)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.name = ElidedLabel(image.path.name, mode=Qt.TextElideMode.ElideRight)
        self.name.setStyleSheet("font-weight: 650;")
        self.sub = ElidedLabel("Reading…", "faint", Qt.TextElideMode.ElideRight)
        text.addWidget(self.name)
        text.addWidget(self.sub)
        lay.addLayout(text, 1)
        self.state = chip("", "accent")
        self.state.setVisible(False)
        lay.addWidget(self.state)
        lay.addWidget(icon_button("x", "Close image", lambda: self.closeRequested.emit(self.image)))
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.set_selected(False)

    def set_selected(self, on: bool) -> None:
        p = theme.current()
        self.setStyleSheet(
            f"ImageRow {{ background: {p.accent_soft if on else 'transparent'}; border-radius: 10px; }}"
        )

    def refresh(self) -> None:
        img = self.image
        self.name.setText(img.title)
        if img.error:
            self.sub.setText("Could not read image")
        elif img.summary:
            s = img.summary
            ident = s.game.title_id or img.path.stem
            self.sub.setText(f"{ident} · {format_size(s.file_size)}")
            self.thumb.set_png(s.game.icon_png)
        job = img.jobs[-1] if img.jobs else None
        if job and job.state in ("queued", "running"):
            verb = "Verifying" if job.kind == "verify" else "Extracting"
            set_chip(self.state, f"{verb} {job.overall * 100:.0f}%" if job.state == "running" else "Queued", "accent")
            self.state.setVisible(True)
        elif job and job.state == "done":
            set_chip(self.state, "Verified" if job.kind == "verify" else "Extracted", "success")
            self.state.setVisible(True)
        elif job and job.state == "failed":
            set_chip(self.state, "Failed", "danger")
            self.state.setVisible(True)
        else:
            self.state.setVisible(False)


class ContentFilter(QSortFilterProxyModel):
    """Keeps folders before files; filters by name recursively."""

    def lessThan(self, left, right) -> bool:
        ld, rd = left.data(DIR_ROLE), right.data(DIR_ROLE)
        if ld != rd:
            return bool(ld)
        if left.column() == 1:
            return (left.data(SIZE_ROLE) or 0) < (right.data(SIZE_ROLE) or 0)
        return str(left.data()).lower() < str(right.data()).lower()


class ImageDetail(QWidget):
    extractRequested = Signal(object, object)  # selectors (None = all), destination folder
    verifyRequested = Signal()

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.summary: ImageSummary | None = None
        self._selection_timer = QTimer(self, singleShot=True, interval=60)
        self._selection_timer.timeout.connect(self._update_selection)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        # Header
        head = Card(padding=18, spacing=14)
        top = QHBoxLayout()
        top.setSpacing(16)
        self.thumb = Thumbnail(84, "disk")
        top.addWidget(self.thumb, 0, Qt.AlignmentFlag.AlignTop)
        info = QVBoxLayout()
        info.setSpacing(6)
        self.title = label("", "heroTitle", wrap=True)
        info.addWidget(self.title)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.id_chip = chip("", "accent")
        self.ver_chip = chip("")
        self.fmt_chip = chip("")
        for c in (self.id_chip, self.ver_chip, self.fmt_chip):
            chips.addWidget(c)
        chips.addStretch(1)
        info.addLayout(chips)
        self.path_label = ElidedLabel("", "faint", Qt.TextElideMode.ElideLeft)
        info.addWidget(self.path_label)
        top.addLayout(info, 1)
        head.body.addLayout(top)

        stats = QHBoxLayout()
        stats.setSpacing(32)
        self.stat_image = StatTile("Image size")
        self.stat_content = StatTile("Game size")
        self.stat_files = StatTile("Files")
        self.stat_ratio = StatTile("Space saved")
        for t in (self.stat_image, self.stat_content, self.stat_files, self.stat_ratio):
            stats.addWidget(t)
        stats.addStretch(1)
        head.body.addLayout(stats)
        lay.addWidget(head)

        # Contents
        body = Card(padding=12, spacing=8)
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter files…")
        self.search.addAction(icons.icon("search", theme.current().faint), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        bar.addWidget(self.search, 1)
        bar.addWidget(button("All", variant="ghost", on_click=lambda: self._check_all(True)))
        bar.addWidget(button("None", variant="ghost", on_click=lambda: self._check_all(False)))
        bar.addWidget(icon_button("expand", "Expand all", lambda: self.tree.expandAll()))
        bar.addWidget(icon_button("collapse", "Collapse all", lambda: self.tree.collapseAll()))
        body.body.addLayout(bar)

        self.model = QStandardItemModel(0, 2)
        self.model.setHorizontalHeaderLabels(["Name", "Size"])
        self.model.itemChanged.connect(self._item_changed)
        self._propagating = False
        self.proxy = ContentFilter()
        self.proxy.setSourceModel(self.model)
        self.proxy.setRecursiveFilteringEnabled(True)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.tree = QTreeView()
        self.tree.setModel(self.proxy)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(1, 110)
        body.body.addWidget(self.tree, 1)
        lay.addWidget(body, 1)

        # Footer: destination + actions
        foot = Card(padding=14, spacing=10)
        dest_row = QHBoxLayout()
        dest_row.setSpacing(10)
        dest_row.addWidget(label("Extract to"))
        self.dest = PathPicker("Next to the image", "Choose where to extract", clearable=True)
        self.dest.pathChanged.connect(self._dest_changed)
        dest_row.addWidget(self.dest, 1)
        foot.body.addLayout(dest_row)
        actions = QHBoxLayout()
        self.target_label = ElidedLabel("", "faint", Qt.TextElideMode.ElideLeft)
        actions.addWidget(self.target_label, 1)
        self.verify_btn = button("Verify", "shield", on_click=self.verifyRequested.emit)
        self.verify_btn.setToolTip(
            "Checks the image structure and decodes every file, testing each compressed block's checksum.\n"
            "Finds truncated copies and damaged compressed data. Blocks stored uncompressed have no\n"
            "checksum in the format, so damage there can only be found by comparing with the original\n"
            "files (use “Verify image after building” when compressing).")
        actions.addWidget(self.verify_btn)
        self.extract_sel_btn = button("Extract selected", "check", on_click=self._extract_selected)
        actions.addWidget(self.extract_sel_btn)
        self.extract_all_btn = button("Extract all", "unpack", "primary", lambda: self._emit_extract(None))
        actions.addWidget(self.extract_all_btn)
        foot.body.addLayout(actions)
        lay.addWidget(foot)

        self._selected: list[str] = []
        self._selected_bytes = 0
        self._selected_files = 0

    # ── populate ────────────────────────────────────────────────────────────

    def show_summary(self, summary: ImageSummary) -> None:
        self.summary = summary
        g = summary.game
        self.title.setText(g.title or summary.path.stem)
        self.thumb.set_png(g.icon_png)
        set_chip(self.id_chip, g.title_id or "No title ID", "accent" if g.title_id else "neutral")
        self.ver_chip.setText(f"v{g.version}" if g.version else "")
        self.ver_chip.setVisible(bool(g.version))
        self.fmt_chip.setText(summary.format_label)
        self.path_label.setText(str(summary.path))
        self.stat_image.set(format_size(summary.file_size))
        self.stat_content.set(format_size(summary.content_size))
        self.stat_files.set(f"{summary.file_count:,}")
        saved = (1 - summary.file_size / summary.content_size) * 100 if summary.content_size else 0
        self.stat_ratio.set(f"{saved:.0f}%" if saved > 0 else "—")

        self.model.blockSignals(True)
        self.model.removeRows(0, self.model.rowCount())
        p = theme.current()
        folder_icon = icons.icon("folder", p.accent, 16)
        file_icon = icons.icon("file", p.muted, 16)

        def add(parent: QStandardItem, node: Entry) -> int:
            total = 0
            for child in node.children:
                name = QStandardItem(folder_icon if child.is_dir else file_icon, child.name)
                name.setCheckable(True)
                name.setCheckState(Qt.CheckState.Checked)
                name.setData(child.path, PATH_ROLE)
                name.setData(child.is_dir, DIR_ROLE)
                size = QStandardItem()
                size.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                size.setData(child.is_dir, DIR_ROLE)
                if child.is_dir:
                    sub_total = add(name, child)
                else:
                    sub_total = child.size
                size.setText(format_size(sub_total))
                size.setData(sub_total, SIZE_ROLE)
                name.setData(sub_total, SIZE_ROLE)
                size.setForeground(theme_color(p.muted))
                parent.appendRow([name, size])
                total += sub_total
            return total

        add(self.model.invisibleRootItem(), summary.root)
        self.model.blockSignals(False)
        self.proxy.invalidate()
        self.tree.collapseAll()
        for row in range(self.proxy.rowCount()):  # open the game root one level
            self.tree.expand(self.proxy.index(row, 0))
        self.search.clear()
        self.dest.set_path(self.settings.get("unpack_dir") or "")
        self._update_selection()

    # ── selection ───────────────────────────────────────────────────────────

    def _item_changed(self, item: QStandardItem) -> None:
        """Keep folder checkboxes consistent: push down to children, roll up to parents."""
        if self._propagating or item.column() != 0:
            return
        self._propagating = True
        try:
            state = item.checkState()
            if item.hasChildren() and state != Qt.CheckState.PartiallyChecked:
                set_subtree(item, state)
            parent = item.parent()
            while parent is not None:
                states = {parent.child(r, 0).checkState() for r in range(parent.rowCount())}
                parent.setCheckState(states.pop() if len(states) == 1 else Qt.CheckState.PartiallyChecked)
                parent = parent.parent()
        finally:
            self._propagating = False
        self._selection_timer.start()

    def _check_all(self, on: bool) -> None:
        state = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        self._propagating = True
        try:
            set_subtree(self.model.invisibleRootItem(), state)
        finally:
            self._propagating = False
        self._selection_timer.start()

    def _update_selection(self) -> None:
        selected: list[str] = []
        files = 0
        size = 0
        all_checked = True

        def walk(item: QStandardItem) -> None:
            nonlocal files, size, all_checked
            for row in range(item.rowCount()):
                child = item.child(row, 0)
                state = child.checkState()
                is_dir = bool(child.data(DIR_ROLE))
                if state == Qt.CheckState.Checked:
                    selected.append(child.data(PATH_ROLE))
                    size += child.data(SIZE_ROLE) or 0
                    files += count_files(child) if is_dir else 1
                elif state == Qt.CheckState.PartiallyChecked:
                    all_checked = False
                    walk(child)
                else:
                    all_checked = False

        walk(self.model.invisibleRootItem())
        self._selected = selected
        self._selected_files = files
        self._selected_bytes = size
        partial = bool(selected) and not all_checked
        self.extract_sel_btn.setEnabled(partial)
        self.extract_sel_btn.setText(
            f"Extract selected ({files:,} · {format_size(size)})" if partial else "Extract selected")
        self._update_target()

    def _filter(self, text: str) -> None:
        self.proxy.setFilterFixedString(text)
        if text:
            self.tree.expandAll()

    # ── destination ─────────────────────────────────────────────────────────

    def _dest_changed(self, value: str) -> None:
        self.settings.set("unpack_dir", value)
        self._update_target()

    def destination(self) -> Path | None:
        if not self.summary:
            return None
        base = Path(self.dest.path()) if self.dest.path() else self.summary.path.parent
        return unique_path(base / self.summary.path.stem)

    def _update_target(self) -> None:
        target = self.destination()
        self.target_label.setText(f"→ {target}" if target else "")

    def _extract_selected(self) -> None:
        if self._selected:
            self._emit_extract(list(self._selected))

    def _emit_extract(self, selectors: list[str] | None) -> None:
        target = self.destination()
        if target is None or self.summary is None:
            return
        need = self._selected_bytes if selectors else self.summary.content_size
        free = free_space(target)
        if free is not None and free < need:
            QMessageBox.warning(self, "Not enough space",
                                f"Extracting needs {format_size(need)}, but only {format_size(free)} "
                                f"is free at {target.parent}.")
            return
        self.extractRequested.emit(selectors, target)

    def set_busy(self, busy: bool) -> None:
        for b in (self.extract_all_btn, self.verify_btn):
            b.setEnabled(not busy)
        if busy:
            self.extract_sel_btn.setEnabled(False)
        else:
            self._update_selection()


def set_subtree(item: QStandardItem, state: Qt.CheckState) -> None:
    for row in range(item.rowCount()):
        child = item.child(row, 0)
        child.setCheckState(state)
        if child.hasChildren():
            set_subtree(child, state)


def count_files(item: QStandardItem) -> int:
    n = 0
    for row in range(item.rowCount()):
        child = item.child(row, 0)
        n += count_files(child) if child.data(DIR_ROLE) else 1
    return n


def theme_color(hex_color: str):
    from PySide6.QtGui import QBrush, QColor

    return QBrush(QColor(hex_color))


class UnpackPage(QWidget):
    statusMessage = Signal(str)

    def __init__(self, settings: Settings, runner: JobRunner) -> None:
        super().__init__()
        self.setObjectName("Page")
        self.settings = settings
        self.runner = runner
        self.images: list[OpenImage] = []
        self.rows: dict[int, ImageRow] = {}
        self.current: OpenImage | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(14)
        root.addWidget(page_header(
            "Unpack", "Browse what's inside an image and extract all of it, or just the files you need."))

        cols = QHBoxLayout()
        cols.setSpacing(18)

        left = QVBoxLayout()
        left.setSpacing(10)
        self.drop = DropZone("Open images", ".ffpfsc · .ffpfs · .exfat · .ffpkg", "unpack", accepts_image,
                             compact=False)
        self.drop.add_button(button("Open…", "folder-open", on_click=self._browse))
        self.drop.pathsDropped.connect(self.open_paths)
        left.addWidget(self.drop)
        left.addWidget(label("OPEN IMAGES", "section"))
        self.list = QListWidget()
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.currentRowChanged.connect(self._row_selected)
        left.addWidget(self.list, 1)
        left_w = QWidget()
        left_w.setLayout(left)
        left_w.setFixedWidth(300)
        cols.addWidget(left_w)

        self.stack = QStackedWidget()
        self.empty = EmptyState("unpack", "No image open",
                                "Drop a .ffpfsc file here to see the game inside it. Images made by older "
                                "versions of FFPFSC PRO open too.")
        self.loading = EmptyState("disk", "Reading image…", "Decoding the file table.")
        self.failed = EmptyState("alert", "Can't open this image", "")
        self.detail = ImageDetail(settings)
        self.detail.extractRequested.connect(self._extract)
        self.detail.verifyRequested.connect(self._verify)
        for w in (self.empty, self.loading, self.failed, self.detail):
            self.stack.addWidget(w)
        cols.addWidget(self.stack, 1)
        root.addLayout(cols, 1)

        self.setAcceptDrops(True)
        runner.jobChanged.connect(self._on_job)
        runner.jobFinished.connect(self._on_job)

    # ── opening ─────────────────────────────────────────────────────────────

    def _browse(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Open PS5 images", str(Path.home()), FILE_FILTER)
        if files:
            self.open_paths([Path(f) for f in files])

    def open_paths(self, paths: list[Path]) -> None:
        first_new: OpenImage | None = None
        for path in paths:
            existing = next((i for i in self.images if i.path.resolve() == path.resolve()), None)
            if existing:
                first_new = first_new or existing
                continue
            img = OpenImage(path)
            self.images.append(img)
            row = ImageRow(img)
            row.closeRequested.connect(self.close_image)
            item = QListWidgetItem()
            item.setSizeHint(row.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
            self.rows[id(img)] = row
            first_new = first_new or img
            run_async(lambda p=path: read_summary(p),
                      lambda s, i=img: self._loaded(i, s),
                      lambda e, i=img: self._load_failed(i, e))
        if first_new:
            self.select(first_new)

    def _loaded(self, img: OpenImage, summary: ImageSummary) -> None:
        img.summary = summary
        self._refresh_row(img)
        if img is self.current:
            self._show(img)

    def _load_failed(self, img: OpenImage, error: str) -> None:
        img.error = error
        self._refresh_row(img)
        if img is self.current:
            self._show(img)

    def close_image(self, img: OpenImage) -> None:
        idx = self.images.index(img)
        self.images.remove(img)
        self.rows.pop(id(img), None)
        self.list.takeItem(idx)
        if img is self.current:
            self.current = None
            if self.images:
                self.select(self.images[min(idx, len(self.images) - 1)])
            else:
                self.stack.setCurrentWidget(self.empty)

    def select(self, img: OpenImage) -> None:
        self.list.setCurrentRow(self.images.index(img))
        self._row_selected(self.images.index(img))

    def _row_selected(self, row: int) -> None:
        if not 0 <= row < len(self.images):
            return
        img = self.images[row]
        for other in self.images:
            r = self.rows.get(id(other))
            if r:
                r.set_selected(other is img)
        if img is not self.current:
            self.current = img
            self._show(img)

    def _show(self, img: OpenImage) -> None:
        if img.error:
            self.failed.text.setText(img.error)
            self.stack.setCurrentWidget(self.failed)
        elif img.summary is None:
            self.stack.setCurrentWidget(self.loading)
        else:
            if self.detail.summary is not img.summary:
                self.detail.show_summary(img.summary)
            self.detail.set_busy(self._busy(img))
            self.stack.setCurrentWidget(self.detail)

    def _refresh_row(self, img: OpenImage) -> None:
        row = self.rows.get(id(img))
        if row:
            row.refresh()
            for i in range(self.list.count()):
                if self.list.itemWidget(self.list.item(i)) is row:
                    self.list.item(i).setSizeHint(row.sizeHint())

    # ── jobs ────────────────────────────────────────────────────────────────

    @staticmethod
    def _busy(img: OpenImage) -> bool:
        return any(j.state in ("queued", "running") for j in img.jobs)

    def _extract(self, selectors: list[str] | None, target: Path) -> None:
        img = self.current
        if img is None or img.summary is None:
            return
        spec = {"kind": "unpack", "image": str(img.path), "dest": str(target), "selectors": selectors}
        what = "selected files" if selectors else "everything"
        job = Job("unpack", spec, title=f"Extract {img.title}", subtitle=f"{what} → {target}",
                  thumbnail=img.summary.game.icon_png)
        img.jobs.append(job)
        self.runner.submit(job)
        self._refresh_row(img)
        self.detail.set_busy(True)

    def _verify(self) -> None:
        img = self.current
        if img is None or img.summary is None:
            return
        job = Job("verify", {"kind": "verify", "image": str(img.path)}, title=f"Verify {img.title}",
                  subtitle=img.path.name, thumbnail=img.summary.game.icon_png)
        img.jobs.append(job)
        self.runner.submit(job)
        self._refresh_row(img)
        self.detail.set_busy(True)

    def _on_job(self, job: Job) -> None:
        for img in self.images:
            if job in img.jobs:
                self._refresh_row(img)
                if img is self.current and job.state not in ("running", "queued"):
                    self.detail.set_busy(self._busy(img))
                break

    def has_active_work(self) -> bool:
        return any(self._busy(i) for i in self.images)

    def dragEnterEvent(self, event) -> None:
        self.drop.dragEnterEvent(event)

    def dragLeaveEvent(self, event) -> None:
        self.drop.dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self.drop.dropEvent(event)
