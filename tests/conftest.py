from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(ROOT))

import ps5ffpfsc.core  # noqa: E402,F401  (puts the vendored mkpfs on sys.path)

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f8cfc0f01f0005000201a3d6d8a50000000049454e44ae426082"
)


def make_game(root: Path, title_id: str = "PPSA01234", title: str = "Test Game", apr: bool = False) -> Path:
    game = root / f"{title_id}-app"
    (game / "sce_sys").mkdir(parents=True)
    (game / "data" / "sub").mkdir(parents=True)
    (game / "eboot.bin").write_bytes(os.urandom(200_000))
    (game / "sce_sys" / "param.json").write_text(json.dumps({
        "titleId": title_id,
        "contentVersion": "01.000.000",
        "localizedParameters": {"defaultLanguage": "en-US", "en-US": {"titleName": title}},
    }))
    (game / "sce_sys" / "icon0.png").write_bytes(PNG_1PX)
    (game / "data" / "level.bin").write_bytes(b"level data\n" * 300_000)
    (game / "data" / "sub" / "tex.bin").write_bytes(os.urandom(150_000))
    (game / "data" / "readme.txt").write_text("hello")
    if apr:
        (game / "sce_sys" / "playgo-chunk.dat").write_bytes(b"\0" * 64)
    return game


def mkpfs(*args: str) -> None:
    subprocess.run([sys.executable, "-m", "mkpfs", *args], cwd=BACKEND, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def tree_bytes(folder: Path) -> dict[str, bytes]:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


@pytest.fixture(scope="session")
def fixtures(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    base = tmp_path_factory.mktemp("fx")
    game = make_game(base / "src")
    out = {"game": game}
    out["ffpfsc"] = base / "PPSA01234.ffpfsc"
    mkpfs("pack", "folder", str(game), str(out["ffpfsc"]))
    out["exfat"] = base / "PPSA01234.exfat"
    mkpfs("pack", "exfat", str(game), str(out["exfat"]))
    out["raw"] = base / "raw.ffpfs"
    mkpfs("pack", "folder", "--raw", "--no-compress", str(game), str(out["raw"]))
    out["nested"] = base / "nested.ffpfsc"  # FFPFSC PRO <= 1.2 layout
    mkpfs("pack", "file", str(out["raw"]), str(out["nested"]))
    out["zip"] = base / "game.zip"
    with zipfile.ZipFile(out["zip"], "w") as zf:
        for p in game.rglob("*"):
            zf.write(p, Path("Some Folder") / p.relative_to(game.parent))
    return out
