from __future__ import annotations

from pathlib import Path

import pytest
from conftest import tree_bytes

from ps5ffpfsc.core.image_reader import ImageError, decode_all, extract, read_summary, verify_against

LAYOUTS = {
    "ffpfsc": "Compressed PFS (PPSA01234.exfat) → exFAT",
    "nested": "Compressed PFS (raw.ffpfs) → PFS",
    "raw": "PFS",
    "exfat": "exFAT",
}


@pytest.mark.parametrize("kind", list(LAYOUTS))
def test_summary_for_every_layout(fixtures: dict[str, Path], kind: str) -> None:
    s = read_summary(fixtures[kind])
    assert s.format_label == LAYOUTS[kind]
    assert s.game.title == "Test Game"
    assert s.game.title_id == "PPSA01234"
    assert s.game.icon_png and s.game.icon_png.startswith(b"\x89PNG")
    assert s.file_count == 6
    assert s.content_size == sum(len(b) for b in tree_bytes(fixtures["game"]).values())


@pytest.mark.parametrize("kind", list(LAYOUTS))
def test_full_extract_is_identical(fixtures: dict[str, Path], kind: str, tmp_path: Path) -> None:
    seen: list[tuple[int, int]] = []
    stats = extract(fixtures[kind], tmp_path / "out", progress=lambda d, t: seen.append((d, t)))
    assert tree_bytes(tmp_path / "out") == tree_bytes(fixtures["game"])
    assert stats.files == 6
    assert seen[-1][0] == seen[-1][1] == stats.bytes


def test_selective_extract(fixtures: dict[str, Path], tmp_path: Path) -> None:
    extract(fixtures["ffpfsc"], tmp_path, selectors=["sce_sys", "data/sub/tex.bin"])
    files = set(tree_bytes(tmp_path))
    assert files == {"sce_sys/param.json", "sce_sys/icon0.png", "data/sub/tex.bin"}


def test_selector_matching_nothing_is_an_error(fixtures: dict[str, Path], tmp_path: Path) -> None:
    with pytest.raises(ImageError):
        extract(fixtures["ffpfsc"], tmp_path, selectors=["does/not/exist"])


def test_not_an_image(tmp_path: Path) -> None:
    junk = tmp_path / "junk.ffpfsc"
    junk.write_bytes(b"\0" * 4096)
    with pytest.raises(ImageError):
        read_summary(junk)


@pytest.mark.parametrize("kind", list(LAYOUTS))
def test_verify_against_source(fixtures: dict[str, Path], kind: str) -> None:
    assert verify_against(fixtures[kind], fixtures["game"]) == []
    assert decode_all(fixtures[kind]).files == 6


def test_verify_against_image_source(fixtures: dict[str, Path]) -> None:
    assert verify_against(fixtures["ffpfsc"], fixtures["exfat"]) == []


def test_verify_against_reports_differences(fixtures: dict[str, Path], tmp_path: Path) -> None:
    import shutil

    changed = tmp_path / "game"
    shutil.copytree(fixtures["game"], changed)
    (changed / "data" / "readme.txt").write_text("HELLO")  # same size, different bytes
    (changed / "extra.bin").write_bytes(b"x")
    (changed / "eboot.bin").unlink()
    (changed / ".DS_Store").write_bytes(b"ignored by mkpfs")
    problems = verify_against(fixtures["ffpfsc"], changed)
    assert problems == [
        "Missing from image: extra.bin",
        "Unexpected file in image: eboot.bin",
        "Content differs: data/readme.txt",
    ]
