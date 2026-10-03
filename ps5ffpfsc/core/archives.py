"""ZIP / 7z / RAR extraction with byte-level progress.

ZIP uses the standard library, 7z uses py7zr. RAR needs an external
extractor (UnRAR, 7-Zip or bsdtar); rarfile drives whichever is installed.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import zipfile
from collections.abc import Callable
from pathlib import Path

ProgressFn = Callable[[int, int], None]


class ArchiveError(Exception):
    pass


class PasswordRequired(ArchiveError):
    pass


def _check_members(names: list[str], dest: Path) -> None:
    root = dest.resolve()
    for name in names:
        target = (dest / name).resolve()
        if target != root and root not in target.parents:
            raise ArchiveError(f"Archive entry escapes the destination folder: {name}")


def _dir_size(path: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.stat(os.path.join(dirpath, f)).st_size
            except OSError:
                pass
    return total


def _run_with_polling(work: Callable[[], None], dest: Path, total: int, progress: ProgressFn | None) -> None:
    """Run ``work`` in a thread and report progress by measuring ``dest``."""
    error: list[BaseException] = []

    def target() -> None:
        try:
            work()
        except BaseException as exc:  # re-raised in the caller's thread
            error.append(exc)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    while t.is_alive():
        t.join(0.5)
        if progress:
            progress(min(_dir_size(dest), total), total)
    if error:
        raise error[0]
    if progress:
        progress(total, total)


def _zip(archive: Path, dest: Path, password: str | None, progress: ProgressFn | None) -> None:
    pwd = password.encode() if password else None
    try:
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
            _check_members([i.filename for i in infos], dest)
            if any(i.flag_bits & 0x1 for i in infos) and not pwd:
                raise PasswordRequired(f"{archive.name} is password-protected.")
            total = sum(i.file_size for i in infos)
            done = 0
            for info in infos:
                try:
                    zf.extract(info, dest, pwd=pwd)
                except RuntimeError as exc:  # wrong/missing password
                    raise PasswordRequired(f"Wrong password for {archive.name}.") from exc
                done += info.file_size
                if progress:
                    progress(done, total)
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"{archive.name} is not a valid ZIP file: {exc}") from exc


def _7z(archive: Path, dest: Path, password: str | None, progress: ProgressFn | None) -> None:
    try:
        import py7zr
    except ImportError as exc:
        raise ArchiveError("7z support needs the py7zr package (pip install py7zr).") from exc
    try:
        with py7zr.SevenZipFile(archive, password=password or None) as sz:
            if sz.needs_password() and not password:
                raise PasswordRequired(f"{archive.name} is password-protected.")
            _check_members(sz.getnames(), dest)
            total = sz.archiveinfo().uncompressed or 0
    except py7zr.exceptions.Bad7zFile as exc:
        raise ArchiveError(f"{archive.name} is not a valid 7z file: {exc}") from exc

    def work() -> None:
        with py7zr.SevenZipFile(archive, password=password or None) as sz:
            sz.extractall(path=dest)

    try:
        _run_with_polling(work, dest, total, progress)
    except py7zr.exceptions.PasswordRequired as exc:
        raise PasswordRequired(f"{archive.name} is password-protected.") from exc
    except Exception as exc:
        if "password" in str(exc).lower() or "crc" in str(exc).lower():
            raise PasswordRequired(f"Could not decrypt {archive.name} — check the password.") from exc
        raise


def find_rar_tool() -> str | None:
    """Locate an external RAR extractor."""
    here = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    candidates = [here / "unrar.exe", here / "tools" / "unrar.exe"]
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            candidates += [Path(base) / "WinRAR" / "UnRAR.exe", Path(base) / "7-Zip" / "7z.exe"]
    for c in candidates:
        if c.is_file():
            return str(c)
    for name in ("unrar", "unrar-free", "7z", "7zz", "bsdtar"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _rar(archive: Path, dest: Path, password: str | None, progress: ProgressFn | None) -> None:
    try:
        import rarfile
    except ImportError as exc:
        raise ArchiveError("RAR support needs the rarfile package (pip install rarfile).") from exc

    tool = find_rar_tool()
    if tool is None:
        raise ArchiveError(
            "No RAR extractor found. Install 7-Zip or WinRAR (or put unrar on your PATH), "
            "or extract the archive yourself and add the game folder."
        )
    name = Path(tool).name.lower()
    if "7z" in name:
        rarfile.SEVENZIP_TOOL = tool
    elif "bsdtar" in name:
        rarfile.BSDTAR_TOOL = tool
    else:
        rarfile.UNRAR_TOOL = tool

    try:
        with rarfile.RarFile(archive) as rf:
            if rf.needs_password() and not password:
                raise PasswordRequired(f"{archive.name} is password-protected.")
            infos = rf.infolist()
            _check_members([i.filename for i in infos], dest)
            total = sum(i.file_size for i in infos)
    except rarfile.Error as exc:
        raise ArchiveError(f"{archive.name} could not be read: {exc}") from exc

    def work() -> None:
        with rarfile.RarFile(archive) as rf:
            rf.extractall(path=dest, pwd=password or None)

    try:
        _run_with_polling(work, dest, total, progress)
    except rarfile.PasswordRequired as exc:
        raise PasswordRequired(f"{archive.name} is password-protected.") from exc
    except rarfile.Error as exc:
        raise ArchiveError(f"RAR extraction failed: {exc}") from exc


def extract_archive(archive: Path, dest: Path, password: str | None = None,
                    progress: ProgressFn | None = None) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    suffix = archive.suffix.lower()
    if suffix == ".zip":
        _zip(archive, dest, password, progress)
    elif suffix == ".7z":
        _7z(archive, dest, password, progress)
    elif suffix == ".rar":
        _rar(archive, dest, password, progress)
    else:
        raise ArchiveError(f"Unsupported archive type: {archive.suffix}")
