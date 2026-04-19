import os
from pathlib import Path
from unittest.mock import patch

from installer.games.scan_hints import (
    is_manual_scan_hint_path_allowed,
    load_manual_scan_hint_paths,
    save_manual_scan_hint,
)


def test_save_manual_scan_hint_persists_valid_directory(tmp_path):
    hints_file = tmp_path / "manual_scan_hints.json"
    game_dir = tmp_path / "MyGame"
    game_dir.mkdir()

    with patch("installer.games.scan_hints._get_manual_scan_hints_path", return_value=hints_file):
        assert save_manual_scan_hint(game_dir) is True
        assert load_manual_scan_hint_paths() == [str(game_dir.resolve(strict=False))]


def test_save_manual_scan_hint_deduplicates_same_directory(tmp_path):
    hints_file = tmp_path / "manual_scan_hints.json"
    game_dir = tmp_path / "MyGame"
    game_dir.mkdir()

    with patch("installer.games.scan_hints._get_manual_scan_hints_path", return_value=hints_file):
        assert save_manual_scan_hint(game_dir) is True
        assert save_manual_scan_hint(game_dir) is True
        assert load_manual_scan_hint_paths() == [str(game_dir.resolve(strict=False))]


def test_exact_excluded_path_is_not_saved(tmp_path):
    hints_file = tmp_path / "manual_scan_hints.json"
    userprofile = tmp_path / "UserProfile"
    userprofile.mkdir()

    with patch.dict(os.environ, {"USERPROFILE": str(userprofile)}, clear=False):
        with patch("installer.games.scan_hints._get_manual_scan_hints_path", return_value=hints_file):
            assert is_manual_scan_hint_path_allowed(userprofile) is False
            assert save_manual_scan_hint(userprofile) is False
            assert load_manual_scan_hint_paths() == []


def test_child_of_excluded_path_is_saved(tmp_path):
    hints_file = tmp_path / "manual_scan_hints.json"
    userprofile = tmp_path / "UserProfile"
    child_dir = userprofile / "Games" / "MyGame"
    child_dir.mkdir(parents=True)

    with patch.dict(os.environ, {"USERPROFILE": str(userprofile)}, clear=False):
        with patch("installer.games.scan_hints._get_manual_scan_hints_path", return_value=hints_file):
            assert is_manual_scan_hint_path_allowed(child_dir) is True
            assert save_manual_scan_hint(child_dir) is True
            assert load_manual_scan_hint_paths() == [str(child_dir.resolve(strict=False))]
