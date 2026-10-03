"""Qt-free core: game detection, image reading, archive extraction, job worker."""

import sys
from pathlib import Path


def project_root() -> Path:
    """Directory holding ``backend/`` (the PyInstaller bundle dir when frozen)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[2]


def _ensure_mkpfs_importable() -> None:
    # mkpfs is vendored under backend/mkpfs (pinned: the image reader uses
    # some of its internals). In a frozen build it is bundled as a package.
    backend = project_root() / "backend"
    if (backend / "mkpfs").is_dir() and str(backend) not in sys.path:
        sys.path.insert(0, str(backend))


_ensure_mkpfs_importable()
