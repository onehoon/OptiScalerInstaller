from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
import logging
import os
import re
from pathlib import Path
from typing import Any

from ..i18n import Lang

if os.name == "nt":
    import winreg


GameDbEntry = dict[str, Any]
GameRecord = dict[str, Any]
GameSupportPredicate = Callable[[GameDbEntry], bool]


def _log_debug(logger, message: str, *args: Any) -> None:
    if logger:
        logger.debug(message, *args)
    else:
        logging.debug(message, *args)


def _append_existing_unique_path(paths: list[str], seen: set[str], candidate: Path) -> None:
    if not candidate.exists() or not candidate.is_dir():
        return

    normalized = str(candidate).lower()
    if normalized in seen:
        return

    seen.add(normalized)
    paths.append(str(candidate))


def _append_existing_unique_child_dirs(paths: list[str], seen: set[str], parent: Path, *, logger=None) -> None:
    if not parent.exists() or not parent.is_dir():
        return

    try:
        child_dirs = sorted(
            (child for child in parent.iterdir() if child.is_dir()),
            key=lambda child: child.name.lower(),
        )
    except OSError as exc:
        _log_debug(logger, "Cannot list %s: %s", parent, exc)
        _append_existing_unique_path(paths, seen, parent)
        return

    for child_dir in child_dirs:
        _append_existing_unique_path(paths, seen, child_dir)


_CUSTOM_SCAN_FOLDER_NAMES: tuple[str, ...] = ("game", "games")
_DRIVE_REMOVABLE = 2
_DRIVE_FIXED = 3


def _get_drive_letters(*, include_system: bool) -> list[str]:
    """Return uppercase drive letters to auto-scan."""
    if os.name != "nt":
        return []

    try:
        import ctypes
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        drives: list[str] = []
        for i, letter in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
            if not (bitmask >> i & 1):
                continue
            if not include_system and letter == "C":
                continue
            root = f"{letter}:\\\\"
            # Include removable media like microSD cards in auto-scan candidates.
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(root)
            if drive_type in (_DRIVE_REMOVABLE, _DRIVE_FIXED):
                drives.append(letter)
        return drives
    except Exception:
        return ["C", "D", "E"] if include_system else ["D", "E"]


def _get_non_system_drive_letters() -> list[str]:
    """Return uppercase drive letters to auto-scan, excluding C:."""
    return _get_drive_letters(include_system=False)


def _get_custom_auto_scan_candidates() -> tuple[Path, ...]:
    candidates: list[Path] = []
    for letter in _get_non_system_drive_letters():
        for folder_name in _CUSTOM_SCAN_FOLDER_NAMES:
            candidates.append(Path(f"{letter}:/") / folder_name)
    return tuple(candidates)


def _get_launcher_auto_scan_candidates() -> tuple[Path, ...]:
    program_files_roots = (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(r"C:\Program Files"),
        Path(r"C:\Program Files (x86)"),
    )

    candidates: list[Path] = []
    seen_roots: set[str] = set()
    for root in program_files_roots:
        normalized_root = str(root).lower()
        if normalized_root in seen_roots:
            continue
        seen_roots.add(normalized_root)
        candidates.append(root / "GOG Galaxy" / "Games")
        candidates.append(root / "Epic Games")

    for letter in _get_drive_letters(include_system=True):
        candidates.append(Path(f"{letter}:/") / "GOG Games")
        candidates.append(Path(f"{letter}:/") / "XboxGames")

    return tuple(candidates)


def _get_detected_steam_common_paths(logger=None) -> list[Path]:
    if os.name != "nt":
        return []

    steam_paths: list[Path] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            base_steam_path_str, _ = winreg.QueryValueEx(key, "SteamPath")

        base_steam_path = Path(base_steam_path_str)
        steam_paths.append(base_steam_path / "steamapps" / "common")

        vdf_path = base_steam_path / "steamapps" / "libraryfolders.vdf"
        if vdf_path.exists():
            try:
                content = vdf_path.read_text(encoding="utf-8", errors="ignore")
                matches = re.findall(r'"path"\s+"([^"]+)"', content, re.IGNORECASE)
                for match in matches:
                    clean_path = match.replace("\\\\", "\\")
                    steam_paths.append(Path(clean_path) / "steamapps" / "common")
            except Exception as exc:
                logging.warning("Error parsing libraryfolders.vdf: %s", exc)
    except Exception as exc:
        if logger:
            logger.debug("Steam registry detection failed: %s", exc)
        else:
            logging.debug("Steam registry detection failed: %s", exc)

    return steam_paths


def _get_fallback_steam_common_paths() -> tuple[Path, ...]:
    fallback: list[Path] = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam" / "steamapps" / "common",
    ]
    for letter in _get_non_system_drive_letters():
        fallback.append(Path(f"{letter}:/") / "SteamLibrary" / "steamapps" / "common")
    return tuple(fallback)


def get_auto_scan_paths(logger=None) -> list[str]:
    """Return existing directories to scan automatically on startup."""
    paths: list[str] = []
    seen: set[str] = set()

    for candidate in _get_custom_auto_scan_candidates():
        _append_existing_unique_path(paths, seen, candidate)

    for candidate in _get_launcher_auto_scan_candidates():
        _append_existing_unique_path(paths, seen, candidate)

    steam_paths = _get_detected_steam_common_paths(logger=logger)
    if not any(path.exists() and path.is_dir() for path in steam_paths):
        steam_paths.extend(_get_fallback_steam_common_paths())

    for candidate in steam_paths:
        _append_existing_unique_child_dirs(paths, seen, candidate, logger=logger)

    return paths


def _build_match_index(game_db: dict[str, GameDbEntry]) -> dict[str, list[tuple[str, GameDbEntry]]]:
    match_index: dict[str, list[tuple[str, GameDbEntry]]] = {}
    for entry_key, entry in game_db.items():
        required_files = tuple(entry.get("match_files") or [entry_key])
        for token in required_files:
            match_index.setdefault(token, []).append((entry_key, entry))
    return match_index


def _resolve_matched_file(file_lookup: dict[str, str], required_files: list[str] | tuple[str, ...], entry: GameDbEntry) -> str:
    anchor_key = str(entry.get("match_anchor", "")).strip().lower()
    matched_file = file_lookup.get(anchor_key)
    if matched_file:
        return matched_file

    matched_file = next(
        (file_lookup[token] for token in required_files if token.endswith(".exe") and token in file_lookup),
        "",
    )
    if matched_file:
        return matched_file

    if required_files:
        return file_lookup.get(required_files[0], required_files[0])
    return ""


def _build_game_record(root_dir: str, matched_file: str, entry: GameDbEntry, *, lang: Lang) -> GameRecord:
    english_name = str(entry.get("game_name_en", "") or "").strip()
    korean_name = str(entry.get("game_name_kr", "") or "").strip()
    default_display = english_name or korean_name or str(entry.get("display", "") or matched_file or "").strip()
    game_record = dict(entry)

    match_files = entry.get("match_files")
    if isinstance(match_files, (list, tuple)):
        game_record["match_files"] = list(match_files)

    ini_settings = entry.get("ini_settings")
    if isinstance(ini_settings, dict):
        game_record["ini_settings"] = dict(ini_settings)

    ingame_settings = entry.get("ingame_settings")
    if isinstance(ingame_settings, dict):
        game_record["ingame_settings"] = dict(ingame_settings)

    game_record["path"] = root_dir
    game_record["exe"] = matched_file
    game_record["display"] = korean_name if lang == "ko" and korean_name else default_display

    return game_record


def iter_scan_game_folders(
    game_folders: Iterable[str],
    game_db: dict[str, GameDbEntry],
    *,
    lang: Lang = "en",
    is_game_supported: GameSupportPredicate | None = None,
    logger=None,
) -> Iterator[GameRecord]:
    """Walk folders and yield matched game entries as they are discovered."""
    supported_predicate = is_game_supported or (lambda _entry: True)
    seen_paths: set[tuple[str, str]] = set()
    match_index = _build_match_index(game_db)
    match_tokens: frozenset[str] = frozenset(match_index.keys())

    for game_folder in game_folders:
        normalized_folder = str(game_folder or "").strip()
        if not normalized_folder:
            continue

        def _walk_error(exc: OSError, source_path: str = normalized_folder) -> None:
            _log_debug(logger, "Cannot walk %s: %s", source_path, exc)

        try:
            folder_iter = os.walk(normalized_folder, onerror=_walk_error)
        except Exception as exc:
            _log_debug(logger, "Cannot walk %s: %s", normalized_folder, exc)
            continue

        for root_dir, dirs, files in folder_iter:
            if not files:
                continue

            files_lower = {f.lower() for f in files}
            if files_lower.isdisjoint(match_tokens):
                continue

            file_lookup: dict[str, str] = {}
            for file_name in files:
                key = file_name.lower()
                if key not in file_lookup:
                    file_lookup[key] = file_name

            candidate_entries: dict[str, GameDbEntry] = {}
            for key in file_lookup:
                for entry_key, entry in match_index.get(key, ()):
                    candidate_entries[entry_key] = entry

            if not candidate_entries:
                continue

            normalized_root = os.path.normcase(root_dir)
            matched_in_root = False
            for entry_key, entry in candidate_entries.items():
                required_files = entry.get("match_files") or [entry_key]
                if not all(token in file_lookup for token in required_files):
                    continue

                dedup_key = (entry_key, normalized_root)
                if dedup_key in seen_paths:
                    continue
                seen_paths.add(dedup_key)

                if not supported_predicate(entry):
                    continue

                matched_file = _resolve_matched_file(file_lookup, required_files, entry)
                matched_in_root = True
                yield _build_game_record(
                    root_dir,
                    matched_file,
                    entry,
                    lang=lang,
                )

            if matched_in_root:
                dirs[:] = []


def scan_game_folders(
    game_folders: Iterable[str],
    game_db: dict[str, GameDbEntry],
    *,
    lang: Lang = "en",
    is_game_supported: GameSupportPredicate | None = None,
    logger=None,
) -> list[GameRecord]:
    """Walk folders and return matched game entries in discovery order."""
    return list(
        iter_scan_game_folders(
            game_folders,
            game_db,
            lang=lang,
            is_game_supported=is_game_supported,
            logger=logger,
        )
    )
