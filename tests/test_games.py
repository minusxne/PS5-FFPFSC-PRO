from __future__ import annotations

import json
from pathlib import Path

from conftest import make_game

from ps5ffpfsc.core.games import (
    SourceKind,
    clean_folder_name,
    find_sources,
    format_size,
    inspect_source,
    parse_param_json,
    safe_filename,
    title_id_from_name,
    unique_path,
)


def test_parse_param_json_prefers_default_language() -> None:
    doc = {
        "titleId": "ppsa04264",
        "contentVersion": "01.002.000",
        "localizedParameters": {
            "defaultLanguage": "fr-FR",
            "en-US": {"titleName": "English"},
            "fr-FR": {"titleName": "Français"},
        },
    }
    info = parse_param_json(json.dumps(doc).encode())
    assert (info.title_id, info.title, info.version) == ("PPSA04264", "Français", "01.002.000")


def test_parse_param_json_tolerates_garbage() -> None:
    assert parse_param_json(b"\xff\x00not json").title_id is None
    assert parse_param_json(b"[]").title is None


def test_title_id_and_names() -> None:
    assert title_id_from_name("Game [PPSA01411] v1.2.exfat") == "PPSA01411"
    assert title_id_from_name("nothing here") is None
    assert clean_folder_name(Path("Cool_Game-app")) == "Cool Game"
    assert safe_filename('a<b>:c?') == "a_b__c_"


def test_unique_path(tmp_path: Path) -> None:
    p = tmp_path / "X.ffpfsc"
    assert unique_path(p) == p
    p.touch()
    assert unique_path(p).name == "X (2).ffpfsc"
    (tmp_path / "X (2).ffpfsc").touch()
    assert unique_path(p).name == "X (3).ffpfsc"
    folder = tmp_path / "PPSA01234"
    folder.mkdir()
    assert unique_path(folder).name == "PPSA01234 (2)"


def test_find_sources_does_not_descend_into_games(tmp_path: Path) -> None:
    g1 = make_game(tmp_path / "dumps", "PPSA00001")
    g2 = make_game(tmp_path / "dumps" / "more", "PPSA00002")
    (g1 / "nested-image.exfat").touch()  # inside a game: must be ignored
    (tmp_path / "dumps" / "other.exfat").touch()
    (tmp_path / "dumps" / "notes.txt").touch()
    found = find_sources(tmp_path / "dumps")
    assert set(found) == {g1, g2, tmp_path / "dumps" / "other.exfat"}


def test_inspect_folder_source(tmp_path: Path) -> None:
    game = make_game(tmp_path, "PPSA07777", "Neon Drift", apr=True)
    src = inspect_source(game)
    assert src is not None
    assert src.kind is SourceKind.FOLDER
    assert src.display_name == "Neon Drift"
    assert src.title_id == "PPSA07777"
    assert src.apr_detected
    assert src.file_count == 7
    assert src.game.icon_png


def test_format_size() -> None:
    assert format_size(512) == "512 B"
    assert format_size(1536) == "1.5 KB"
    assert format_size(5 * 1024**3) == "5.0 GB"
