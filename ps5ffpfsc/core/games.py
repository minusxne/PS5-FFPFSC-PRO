"""PS5 game/source detection and metadata (param.json, icon, title ID)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

TITLE_ID_RE = re.compile(r"\b([A-Z]{4}\d{5})\b", re.IGNORECASE)

IMAGE_SUFFIXES = {".exfat", ".ffpkg"}
ARCHIVE_SUFFIXES = {".zip", ".rar", ".7z"}
PACKED_SUFFIXES = {".ffpfsc", ".ffpfs"}
#: Anything the Unpack page can open.
UNPACKABLE_SUFFIXES = PACKED_SUFFIXES | IMAGE_SUFFIXES

AMPR_SPRX_FILES = ("libSceAmpr.sprx", "libScePlayGo.sprx")
AMPR_INDEX_NAME = "ampr_emu.index"


class SourceKind(str, Enum):
    FOLDER = "folder"
    IMAGE = "image"
    ARCHIVE = "archive"


@dataclass
class GameInfo:
    title_id: str | None = None
    title: str | None = None
    version: str | None = None
    icon_png: bytes | None = None


@dataclass
class Source:
    """One compress-queue entry, as detected when the user adds a path."""

    path: Path
    kind: SourceKind
    size: int = 0
    file_count: int = 0
    game: GameInfo = field(default_factory=GameInfo)
    apr_detected: bool = False
    encrypted: bool = False

    @property
    def display_name(self) -> str:
        return self.game.title or clean_folder_name(self.path)

    @property
    def title_id(self) -> str | None:
        return self.game.title_id or title_id_from_name(self.path.name)


def title_id_from_name(name: str) -> str | None:
    m = TITLE_ID_RE.search(name)
    return m.group(1).upper() if m else None


def clean_folder_name(path: Path) -> str:
    """Turn ``PPSA01234-app`` / ``Game_Name [v1.02]`` into something readable."""
    name = path.stem if path.is_file() else path.name
    name = re.sub(r"[-_](app|patch)\d*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*\[.*?\]\s*", " ", name)
    return name.replace("_", " ").strip(" -") or path.name


def parse_param_json(data: bytes) -> GameInfo:
    """Extract title ID, localized title and version from ``sce_sys/param.json``."""
    try:
        doc = json.loads(data.decode("utf-8-sig", errors="replace"))
    except (ValueError, UnicodeDecodeError):
        return GameInfo()
    if not isinstance(doc, dict):
        return GameInfo()

    title_id = doc.get("titleId") or doc.get("title_id")
    title = None
    loc = doc.get("localizedParameters")
    if isinstance(loc, dict):
        default_lang = loc.get("defaultLanguage", "")
        for lang in (default_lang, "en-US", "en-GB"):
            entry = loc.get(lang)
            if isinstance(entry, dict) and entry.get("titleName"):
                title = entry["titleName"]
                break
        else:
            title = next(
                (v["titleName"] for v in loc.values() if isinstance(v, dict) and v.get("titleName")),
                None,
            )
    version = doc.get("contentVersion") or doc.get("masterVersion")
    return GameInfo(
        title_id=str(title_id).upper() if title_id else None,
        title=str(title).strip() if title else None,
        version=str(version) if version else None,
    )


def read_folder_game_info(folder: Path) -> GameInfo:
    sce_sys = folder / "sce_sys"
    info = GameInfo()
    try:
        info = parse_param_json((sce_sys / "param.json").read_bytes())
    except OSError:
        pass
    try:
        info.icon_png = (sce_sys / "icon0.png").read_bytes()
    except OSError:
        pass
    return info


def is_game_folder(path: Path) -> bool:
    return path.is_dir() and (path / "eboot.bin").is_file() and (path / "sce_sys").is_dir()


def is_apr_game(folder: Path) -> bool:
    """APR titles ship a PlayGo chunk file and need the AMPR emulator libs."""
    sce_sys = folder / "sce_sys"
    return (sce_sys / "playgo-chunk.dat").exists() or (sce_sys / "playgo_chunk.dat").exists()


def folder_stats(folder: Path) -> tuple[int, int]:
    """Return ``(total_bytes, file_count)`` for a directory tree."""
    total = count = 0
    for dirpath, _dirs, files in os.walk(folder):
        for name in files:
            try:
                total += os.stat(os.path.join(dirpath, name)).st_size
                count += 1
            except OSError:
                pass
    return total, count


def classify(path: Path) -> SourceKind | None:
    if path.is_dir():
        return SourceKind.FOLDER if is_game_folder(path) else None
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return SourceKind.IMAGE
    if suffix in ARCHIVE_SUFFIXES:
        return SourceKind.ARCHIVE
    return None


def find_sources(root: Path, max_depth: int = 4) -> list[Path]:
    """Find game folders, images and archives at or below ``root``.

    Game folders are not descended into, so their own contents never show up
    as separate sources.
    """
    if classify(root) is not None:
        return [root]
    if not root.is_dir():
        return []

    found: list[Path] = []

    def walk(directory: Path, depth: int) -> None:
        try:
            children = sorted(directory.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return
        for child in children:
            if child.name.startswith("."):
                continue
            kind = classify(child)
            if kind is not None:
                found.append(child)
            elif child.is_dir() and depth < max_depth:
                walk(child, depth + 1)

    walk(root, 1)
    return found


def archive_is_encrypted(path: Path) -> bool:
    """Best-effort check for password-protected archives (False when unsure)."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".zip":
            import zipfile

            with zipfile.ZipFile(path) as zf:
                return any(info.flag_bits & 0x1 for info in zf.infolist())
        if suffix == ".7z":
            import py7zr

            with py7zr.SevenZipFile(path) as sz:
                return bool(sz.needs_password())
        if suffix == ".rar":
            import rarfile

            with rarfile.RarFile(path) as rf:
                return bool(rf.needs_password())
    except Exception:
        return False
    return False


def inspect_source(path: Path) -> Source | None:
    """Build a :class:`Source` for a path accepted by :func:`classify`."""
    kind = classify(path)
    if kind is None:
        return None
    src = Source(path=path, kind=kind)
    if kind is SourceKind.FOLDER:
        src.size, src.file_count = folder_stats(path)
        src.game = read_folder_game_info(path)
        src.apr_detected = is_apr_game(path)
    else:
        try:
            src.size = path.stat().st_size
        except OSError:
            pass
        src.file_count = 1
        src.game = GameInfo(title_id=title_id_from_name(path.name))
        if kind is SourceKind.ARCHIVE:
            src.encrypted = archive_is_encrypted(path)
    return src


_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str, fallback: str = "game") -> str:
    cleaned = _UNSAFE_CHARS.sub("_", name).strip(" .")
    return cleaned[:120] or fallback


def unique_path(path: Path) -> Path:
    """Return ``path``, or ``name (2).ext`` / ``name (3).ext``… if it exists."""
    if not path.exists():
        return path
    stem, suffix = (path.stem, path.suffix) if path.suffix else (path.name, "")
    n = 2
    while True:
        candidate = path.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def format_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.2f} TB"


def format_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"
