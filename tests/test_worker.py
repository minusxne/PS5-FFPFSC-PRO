"""Run the real worker subprocess, the same way the GUI does."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

from conftest import ROOT, make_game, tree_bytes

from ps5ffpfsc.core.image_reader import read_summary
from ps5ffpfsc.core.worker import EVENT_PREFIX


def run_worker(spec: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONUNBUFFERED": "1"}
    proc = subprocess.run([sys.executable, "-m", "ps5ffpfsc", "--worker"], input=json.dumps(spec) + "\n",
                          capture_output=True, text=True, env=env, cwd=ROOT, timeout=300)
    events = [json.loads(line[len(EVENT_PREFIX):]) for line in proc.stdout.splitlines()
              if line.startswith(EVENT_PREFIX)]
    return events, proc.stdout + proc.stderr


def result(events: list[dict[str, Any]]) -> dict[str, Any]:
    return next(e for e in events if e["t"] == "result")


def test_compress_folder_with_verify(fixtures: dict[str, Path], tmp_path: Path) -> None:
    events, log = run_worker({"kind": "compress", "source": str(fixtures["game"]), "source_kind": "folder",
                              "output_dir": str(tmp_path), "level": 6, "cpu": 2, "verify": True})
    res = result(events)
    assert res["ok"], log
    out = Path(res["outputs"][0]["path"])
    assert out.name == "PPSA01234.ffpfsc"
    assert [s["id"] for s in events[0]["stages"]] == ["prepare", "build", "verify"]
    assert any(e["t"] == "progress" for e in events)
    assert read_summary(out).game.title == "Test Game"


def test_existing_output_gets_a_new_name(fixtures: dict[str, Path], tmp_path: Path) -> None:
    (tmp_path / "PPSA01234.ffpfsc").write_bytes(b"keep me")
    events, log = run_worker({"kind": "compress", "source": str(fixtures["exfat"]), "source_kind": "image",
                              "output_dir": str(tmp_path)})
    res = result(events)
    assert res["ok"], log
    assert Path(res["outputs"][0]["path"]).name == "PPSA01234 (2).ffpfsc"
    assert (tmp_path / "PPSA01234.ffpfsc").read_bytes() == b"keep me"


def test_compress_zip_archive_cleans_temp(fixtures: dict[str, Path], tmp_path: Path) -> None:
    temp = tmp_path / "temp"
    events, log = run_worker({"kind": "compress", "source": str(fixtures["zip"]), "source_kind": "archive",
                              "output_dir": str(tmp_path / "out"), "temp_dir": str(temp)})
    res = result(events)
    assert res["ok"], log
    assert res["outputs"][0]["title"] == "Test Game"
    assert list(temp.iterdir()) == []


def test_encrypted_zip_without_password(tmp_path: Path) -> None:
    archive = tmp_path / "locked.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("a.txt", "x")
    raw = bytearray(archive.read_bytes())
    # Flip the "encrypted" flag in both headers; enough for detection.
    for sig in (b"PK\x03\x04", b"PK\x01\x02"):
        i = raw.find(sig)
        off = i + (6 if sig == b"PK\x03\x04" else 8)
        raw[off] |= 0x1
    archive.write_bytes(bytes(raw))
    events, _log = run_worker({"kind": "compress", "source": str(archive), "source_kind": "archive",
                               "output_dir": str(tmp_path / "out")})
    res = result(events)
    assert not res["ok"]
    assert res["password"]


def test_apr_injection_is_reverted(tmp_path: Path) -> None:
    game = make_game(tmp_path / "src", apr=True)
    emu = tmp_path / "emu"
    emu.mkdir()
    (emu / "libSceAmpr.sprx").write_bytes(b"ampr")
    (emu / "libScePlayGo.sprx").write_bytes(b"playgo")
    before = tree_bytes(game)
    events, log = run_worker({"kind": "compress", "source": str(game), "source_kind": "folder",
                              "output_dir": str(tmp_path / "out"), "apr": "auto", "ampr_dir": str(emu)})
    res = result(events)
    assert res["ok"], log
    assert tree_bytes(game) == before  # source folder restored
    inner = read_summary(Path(res["outputs"][0]["path"]))
    names = {e.path for e in inner.root.walk_files()}
    assert {"fakelib/libSceAmpr.sprx", "fakelib/libScePlayGo.sprx", "ampr_emu.index"} <= names


def test_unpack_selected_and_verify(fixtures: dict[str, Path], tmp_path: Path) -> None:
    dest = tmp_path / "out"
    events, log = run_worker({"kind": "unpack", "image": str(fixtures["nested"]), "dest": str(dest),
                              "selectors": ["data"]})
    assert result(events)["ok"], log
    assert set(tree_bytes(dest)) == {"data/level.bin", "data/readme.txt", "data/sub/tex.bin"}

    events, log = run_worker({"kind": "verify", "image": str(fixtures["ffpfsc"])})
    assert result(events)["ok"], log


def test_verify_detects_truncated_image(fixtures: dict[str, Path], tmp_path: Path) -> None:
    broken = tmp_path / "broken.ffpfsc"
    data = fixtures["ffpfsc"].read_bytes()
    broken.write_bytes(data[: int(len(data) * 0.7)])  # e.g. an interrupted copy
    events, _log = run_worker({"kind": "verify", "image": str(broken)})
    res = result(events)
    assert not res["ok"]
