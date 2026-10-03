"""Read-only access to .ffpfsc / .ffpfs / .exfat / .ffpkg images.

Images can be layered. The layouts seen in practice:

* ``.ffpfsc`` (current): compressed PFS holding one exFAT image of the game.
* ``.ffpfsc`` (FFPFSC PRO <= 1.2): compressed PFS holding ``pfs_image.dat``,
  an uncompressed PFS of the game.
* ``.ffpfs``: a PFS holding the game files directly.
* ``.exfat`` / ``.ffpkg``: a raw exFAT volume.

:func:`open_image` peels single-file wrappers until it reaches the volume that
holds the game files. Everything streams through mkpfs's readers, so nothing
is decompressed to a temp file and memory use stays flat for huge games.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import struct
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from mkpfs import consts
from mkpfs.exfat import EXFAT_SIGNATURE, ExfatEntry, ExfatError, ExfatReader
from mkpfs.pfs import (
    ParsedHeader,
    ParsedInode,
    _LogicalFileView,
    build_tree_from_uroot,
    iter_inode_logical_blocks,
    parse_image_header,
    parse_image_inodes,
    parse_superroot_and_indexes,
)
from mkpfs.utils import is_ignored_name

from .games import GameInfo, parse_param_json

_MAX_LAYERS = 4


class ImageError(Exception):
    """The file is not a readable PFS/exFAT image."""


@dataclass
class Entry:
    name: str
    path: str  # POSIX path relative to the volume root ("" for the root)
    is_dir: bool
    size: int = 0
    children: list[Entry] = field(default_factory=list)
    ref: object = field(default=None, repr=False, compare=False)

    def walk_files(self) -> Iterator[Entry]:
        for child in self.children:
            if child.is_dir:
                yield from child.walk_files()
            else:
                yield child


class _Volume:
    root: Entry

    def iter_file(self, entry: Entry) -> Iterator[bytes]:
        raise NotImplementedError


class _ExfatVolume(_Volume):
    def __init__(self, fh: BinaryIO) -> None:
        self._reader = ExfatReader(fh)
        self.root = Entry("", "", True, children=[self._convert(e) for e in self._reader.root_entries()])

    def _convert(self, e: ExfatEntry) -> Entry:
        return Entry(
            name=e.name,
            path=e.rel_path,
            is_dir=e.is_dir,
            size=0 if e.is_dir else e.length,
            children=[self._convert(c) for c in e.children],
            ref=e,
        )

    def iter_file(self, entry: Entry) -> Iterator[bytes]:
        yield from self._reader.read_file(entry.ref)  # type: ignore[arg-type]


class _PfsVolume(_Volume):
    def __init__(self, fh: BinaryIO, header: ParsedHeader, inodes: list[ParsedInode],
                 files: dict[str, int], dirs: dict[str, int]) -> None:
        self._fh = fh
        self._header = header
        self._inodes = inodes
        self.root = Entry("", "", True)
        by_path: dict[str, Entry] = {"": self.root}

        def ensure_dir(rel: str) -> Entry:
            if rel in by_path:
                return by_path[rel]
            parent = ensure_dir(str(PurePosixPath(rel).parent) if "/" in rel else "")
            node = Entry(PurePosixPath(rel).name, rel, True)
            parent.children.append(node)
            by_path[rel] = node
            return node

        for rel in sorted(dirs):
            if rel:
                ensure_dir(rel)
        for rel, inode_num in sorted(files.items()):
            parent = ensure_dir(str(PurePosixPath(rel).parent) if "/" in rel else "")
            inode = inodes[inode_num]
            parent.children.append(
                Entry(PurePosixPath(rel).name, rel, False, size=max(0, inode.logical_size), ref=inode)
            )

    def iter_file(self, entry: Entry) -> Iterator[bytes]:
        yield from iter_inode_logical_blocks(self._fh, self._header, entry.ref)  # type: ignore[arg-type]


def _looks_like_exfat(head: bytes) -> bool:
    return len(head) >= 11 and head[3:11] == EXFAT_SIGNATURE


def _looks_like_pfs(head: bytes) -> bool:
    return len(head) >= 16 and struct.unpack_from("<q", head, 8)[0] == consts.PFS_MAGIC


def _read_head(fh: BinaryIO) -> bytes:
    fh.seek(0)
    return fh.read(512)


def _open_volume(fh: BinaryIO, layers: list[str], *, outer_name: str) -> _Volume:
    head = _read_head(fh)
    if _looks_like_exfat(head):
        layers.append("exFAT")
        try:
            return _ExfatVolume(fh)
        except ExfatError as exc:
            raise ImageError(f"Corrupt exFAT volume: {exc}") from exc

    if not _looks_like_pfs(head):
        raise ImageError(f"{outer_name} is not a PFS or exFAT image.")

    try:
        header = parse_image_header(fh)
        inodes = parse_image_inodes(fh, header)
        errors: list[str] = []
        uroot, _fpt, _collisions, _special = parse_superroot_and_indexes(fh, header, inodes, errors)
        if uroot < 0:
            raise ImageError("PFS image has no root directory. " + "; ".join(errors))
        files, dirs, _dirents = build_tree_from_uroot(fh, header, inodes, uroot, errors)
    except (OSError, ValueError) as exc:
        raise ImageError(f"Corrupt PFS image: {exc}") from exc

    compressed = any(inodes[n].is_compressed for n in files.values())
    layers.append("Compressed PFS" if compressed else "PFS")

    # A wrapper holds exactly one file; descend into it if it is itself an image.
    if len(files) == 1 and len(layers) < _MAX_LAYERS:
        inner_name, inode_num = next(iter(files.items()))
        inode = inodes[inode_num]
        if not inode.db_sig and not inode.ib_sig and inode.blocks > 0 and inode.logical_size >= 512:
            view = _LogicalFileView(fh, header, inode)
            inner_head = _read_head(view)  # type: ignore[arg-type]
            if _looks_like_exfat(inner_head) or _looks_like_pfs(inner_head):
                layers[-1] += f" ({inner_name})"
                return _open_volume(view, layers, outer_name=inner_name)  # type: ignore[arg-type]

    return _PfsVolume(fh, header, inodes, files, dirs)


@dataclass
class ImageSummary:
    """Everything the UI needs to show an image, with no open file handle."""

    path: Path
    file_size: int
    layers: list[str]
    root: Entry
    game: GameInfo
    game_root: str  # path of the folder holding sce_sys/ ("" = volume root)
    content_size: int
    file_count: int
    dir_count: int

    @property
    def format_label(self) -> str:
        return " → ".join(self.layers)


class OpenedImage:
    """An image opened for reading. Use as a context manager."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: BinaryIO = path.open("rb")
        self.layers: list[str] = []
        try:
            self._volume = _open_volume(self._fh, self.layers, outer_name=path.name)
        except Exception:
            self._fh.close()
            raise
        self.root = self._volume.root

    def __enter__(self) -> OpenedImage:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._fh.close()

    def find(self, rel_path: str) -> Entry | None:
        node = self.root
        for part in PurePosixPath(rel_path).parts:
            node = next((c for c in node.children if c.name == part), None)  # type: ignore[assignment]
            if node is None:
                return None
        return node

    def read_small(self, entry: Entry, limit: int = 8 * 1024 * 1024) -> bytes:
        if entry.size > limit:
            raise ImageError(f"{entry.path} is too large to read into memory")
        return b"".join(self._volume.iter_file(entry))

    def iter_file(self, entry: Entry) -> Iterator[bytes]:
        return self._volume.iter_file(entry)

    def locate_game_root(self) -> Entry | None:
        """Shallowest directory containing ``sce_sys/param.json``."""
        frontier = [self.root]
        for _depth in range(4):
            next_frontier: list[Entry] = []
            for node in frontier:
                sce = next((c for c in node.children if c.is_dir and c.name.lower() == "sce_sys"), None)
                if sce and any(c.name.lower() == "param.json" for c in sce.children):
                    return node
                next_frontier.extend(c for c in node.children if c.is_dir)
            frontier = next_frontier
        return None

    def summarize(self) -> ImageSummary:
        files = dirs = total = 0
        stack = [self.root]
        while stack:
            node = stack.pop()
            for child in node.children:
                if child.is_dir:
                    dirs += 1
                    stack.append(child)
                else:
                    files += 1
                    total += child.size

        game = GameInfo()
        game_root = self.locate_game_root()
        if game_root is not None:
            prefix = f"{game_root.path}/" if game_root.path else ""
            param = self.find(f"{prefix}sce_sys/param.json")
            if param is not None:
                try:
                    game = parse_param_json(self.read_small(param))
                except Exception:
                    pass
            icon = self.find(f"{prefix}sce_sys/icon0.png")
            if icon is not None:
                try:
                    game.icon_png = self.read_small(icon)
                except Exception:
                    pass

        return ImageSummary(
            path=self.path,
            file_size=self.path.stat().st_size,
            layers=list(self.layers),
            root=self.root,
            game=game,
            game_root=game_root.path if game_root else "",
            content_size=total,
            file_count=files,
            dir_count=dirs,
        )


def open_image(path: Path) -> OpenedImage:
    return OpenedImage(path)


def read_summary(path: Path) -> ImageSummary:
    with open_image(path) as img:
        return img.summarize()


# ── Extraction ──────────────────────────────────────────────────────────────


@dataclass
class ExtractStats:
    files: int = 0
    dirs: int = 0
    bytes: int = 0


def _select(root: Entry, selectors: list[str] | None) -> tuple[list[Entry], list[Entry]]:
    """Return ``(dirs, files)`` covered by ``selectors`` (None = everything)."""
    norm = None
    if selectors:
        norm = [s.strip().replace("\\", "/").strip("/") for s in selectors]
        norm = [s for s in norm if s] or None

    def wanted(path: str) -> bool:
        return norm is None or any(path == s or path.startswith(s + "/") for s in norm)

    dirs: list[Entry] = []
    files: list[Entry] = []
    stack = [root]
    while stack:
        node = stack.pop()
        for child in node.children:
            if child.is_dir:
                if wanted(child.path):
                    dirs.append(child)
                stack.append(child)
            elif wanted(child.path):
                files.append(child)
    files.sort(key=lambda e: e.path.lower())
    return dirs, files


def _safe_target(dest: Path, rel: str) -> Path:
    parts = [p for p in PurePosixPath(rel).parts if p not in ("", ".", "/")]
    if any(p == ".." for p in parts):
        raise ImageError(f"Refusing to extract unsafe path: {rel}")
    return dest.joinpath(*parts)


def extract(
    image: Path,
    dest: Path,
    selectors: list[str] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> ExtractStats:
    """Extract files from ``image`` into ``dest``, keeping their relative paths.

    Args:
        image: Image to read.
        dest: Destination directory (created if missing).
        selectors: Optional file or directory paths to extract; directories
            include everything below them. ``None`` extracts everything.
        progress: Called as ``progress(bytes_done, bytes_total)``.
    """
    stats = ExtractStats()
    with open_image(image) as img:
        dirs, files = _select(img.root, selectors)
        if selectors and not dirs and not files:
            raise ImageError("None of the selected paths exist in the image.")
        total = sum(f.size for f in files)
        dest.mkdir(parents=True, exist_ok=True)
        for d in dirs:
            _safe_target(dest, d.path).mkdir(parents=True, exist_ok=True)
            stats.dirs += 1

        report_every = 8 * 1024 * 1024
        last = 0
        if progress:
            progress(0, total)
        for entry in files:
            target = _safe_target(dest, entry.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as out:
                for chunk in img.iter_file(entry):
                    out.write(chunk)
                    stats.bytes += len(chunk)
                    if progress and stats.bytes - last >= report_every:
                        last = stats.bytes
                        progress(stats.bytes, total)
            stats.files += 1
        if progress:
            progress(total, total)
    return stats


# ── Verification ────────────────────────────────────────────────────────────


def _hash_stream(chunks: Iterator[bytes], on_bytes: Callable[[int], None]) -> str:
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk)
        on_bytes(len(chunk))
    return digest.hexdigest()


def _read_path(path: Path, chunk: int = 4 * 1024 * 1024) -> Iterator[bytes]:
    with path.open("rb") as fh:
        while data := fh.read(chunk):
            yield data


def _folder_files(root: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not is_ignored_name(d)]
        for name in filenames:
            if not is_ignored_name(name):
                full = Path(dirpath) / name
                files[full.relative_to(root).as_posix()] = full
    return files


class _Progress:
    def __init__(self, total: int, report: Callable[[int, int], None] | None) -> None:
        self.done, self.total, self._report, self._last = 0, total, report, 0

    def add(self, n: int) -> None:
        self.done += n
        if self._report and self.done - self._last >= 8 * 1024 * 1024:
            self._last = self.done
            self._report(self.done, self.total)

    def finish(self) -> None:
        if self._report:
            self._report(self.total, self.total)


def decode_all(image: Path, progress: Callable[[int, int], None] | None = None) -> ExtractStats:
    """Read and decode every file in the image, raising ImageError on damage."""
    stats = ExtractStats()
    with open_image(image) as img:
        files = list(img.root.walk_files())
        tracker = _Progress(sum(f.size for f in files), progress)
        for entry in files:
            got = 0
            try:
                for chunk in img.iter_file(entry):
                    got += len(chunk)
                    tracker.add(len(chunk))
            except Exception as exc:
                raise ImageError(f"Damaged data in {entry.path}: {exc}") from exc
            if got != entry.size:
                raise ImageError(f"{entry.path} is truncated ({got:,} of {entry.size:,} bytes).")
            stats.files += 1
            stats.bytes += got
        tracker.finish()
    return stats


def verify_against(image: Path, source: Path,
                   progress: Callable[[int, int], None] | None = None) -> list[str]:
    """Compare every file in ``image`` with ``source`` (a folder or another image).

    Returns a list of human-readable problems; empty means identical content.
    """
    problems: list[str] = []
    with open_image(image) as img:
        packed = {e.path: e for e in img.root.walk_files()}
        src_img: OpenedImage | None = None
        try:
            if source.is_dir():
                src_paths = _folder_files(source)
                src_sizes = {rel: p.stat().st_size for rel, p in src_paths.items()}

                def src_chunks(rel: str) -> Iterator[bytes]:
                    return _read_path(src_paths[rel])
            else:
                src_img = open_image(source)
                src_entries = {e.path: e for e in src_img.root.walk_files()}
                src_sizes = {rel: e.size for rel, e in src_entries.items()}

                def src_chunks(rel: str) -> Iterator[bytes]:
                    return src_img.iter_file(src_entries[rel])  # type: ignore[union-attr]

            for rel in sorted(set(src_sizes) - set(packed)):
                problems.append(f"Missing from image: {rel}")
            for rel in sorted(set(packed) - set(src_sizes)):
                problems.append(f"Unexpected file in image: {rel}")
            common = sorted(set(src_sizes) & set(packed))
            tracker = _Progress(2 * sum(src_sizes[r] for r in common), progress)
            for rel in common:
                if packed[rel].size != src_sizes[rel]:
                    problems.append(f"Size differs: {rel} ({packed[rel].size:,} vs {src_sizes[rel]:,} bytes)")
                    continue
                try:
                    a = _hash_stream(img.iter_file(packed[rel]), tracker.add)
                except Exception as exc:
                    problems.append(f"Damaged data in {rel}: {exc}")
                    continue
                if a != _hash_stream(src_chunks(rel), tracker.add):
                    problems.append(f"Content differs: {rel}")
                if len(problems) >= 25:
                    problems.append("…stopping after 25 problems.")
                    break
            tracker.finish()
        finally:
            if src_img is not None:
                src_img.close()
    return problems


def free_space(path: Path) -> int | None:
    """Free bytes on the volume holding ``path`` (or its nearest existing parent)."""
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None


__all__ = [
    "Entry",
    "ExtractStats",
    "ImageError",
    "ImageSummary",
    "OpenedImage",
    "decode_all",
    "extract",
    "free_space",
    "open_image",
    "read_summary",
    "verify_against",
]
