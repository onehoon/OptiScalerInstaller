from __future__ import annotations

from collections.abc import Callable, Iterable
import logging
import os
import re
from pathlib import Path
from typing import Any

if os.name == "nt":
    import winreg


GameDbEntry = dict[str, Any]
GameRecord = dict[str, Any]
GameSupportPredicate = Callable[[GameDbEntry], bool]
GameFoundCallback = Callable[[GameRecord], None]
CompletionCallback = Callable[[], None]
SchedulerCallback = Callable[[Callable[[], None]], Any]


def _append_existing_unique_path(paths: list[str], seen: set[str], candidate: Path) -> None:
    if not candidate.exists() or not candidate.is_dir():
        return

    normalized = str(candidate).lower()
    if normalized in seen:
        return

    seen.add(normalized)
    paths.append(str(candidate))


def _get_custom_auto_scan_candidates() -> tuple[Path, ...]:
    return (
        Path("D:/") / "game",
        Path("D:/") / "games",
        Path("E:/") / "game",
        Path("E:/") / "games",
    )


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
    return (
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam" / "steamapps" / "common",
        Path("D:/") / "SteamLibrary" / "steamapps" / "common",
        Path("E:/") / "SteamLibrary" / "steamapps" / "common",
    )


def get_auto_scan_paths(logger=None) -> list[str]:
    """Return existing directories to scan automatically on startup."""
    paths: list[str] = []
    seen: set[str] = set()

    for candidate in _get_custom_auto_scan_candidates():
        _append_existing_unique_path(paths, seen, candidate)

    steam_paths = _get_detected_steam_common_paths(logger=logger)
    if not any(path.exists() and path.is_dir() for path in steam_paths):
        steam_paths.extend(_get_fallback_steam_common_paths())

    for candidate in steam_paths:
        _append_existing_unique_path(paths, seen, candidate)

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


def _build_game_record(root_dir: str, matched_file: str, entry: GameDbEntry, *, use_korean: bool) -> GameRecord:
    korean_display = entry.get("game_name_kr", "") if use_korean else ""
    korean_information = entry.get("information_kr", "") if use_korean else ""
    return {
        "path": root_dir,
        "exe": matched_file,
        "display": korean_display or entry["display"],
        "game_name": entry.get("game_name", entry.get("display", "")),
        "dll_name": entry["dll_name"],
        "ultimate_asi_loader": entry.get("ultimate_asi_loader", False),
        "ini_settings": entry.get("ini_settings", {}),
        "ingame_ini": entry.get("ingame_ini", ""),
        "ingame_settings": entry.get("ingame_settings", {}),
        "engine_ini_location": entry.get("engine_ini_location", ""),
        "engine_ini_type": entry.get("engine_ini_type", ""),
        "module_dl": entry.get("module_dl", ""),
        "optipatcher": entry.get("optipatcher", False),
        "unreal5_url": entry.get("unreal5_url", ""),
        "unreal5_rule": entry.get("unreal5_rule", ""),
        "reframework_url": entry.get("reframework_url", ""),
        "information": korean_information or entry.get("information", ""),
        "cover_url": entry.get("cover_url", ""),
        "supported_gpu": entry.get("supported_gpu", ""),
        "sheet_order": int(entry.get("sheet_order", 10**9)),
        "popup_kr": entry.get("popup_kr", ""),
        "popup_en": entry.get("popup_en", ""),
        "after_popup_kr": entry.get("after_popup_kr", ""),
        "after_popup_en": entry.get("after_popup_en", ""),
        "guidepage_after_installation": entry.get("guidepage_after_installation", ""),
    }


def scan_game_folders(
    game_folders: Iterable[str],
    game_db: dict[str, GameDbEntry],
    *,
    use_korean: bool = False,
    is_game_supported: GameSupportPredicate | None = None,
    logger=None,
) -> list[GameRecord]:
    """Walk folders and return matched game entries sorted by sheet order."""
    supported_predicate = is_game_supported or (lambda _entry: True)
    found_games: list[GameRecord] = []
    seen_paths: set[tuple[str, str]] = set()
    match_index = _build_match_index(game_db)

    for game_folder in game_folders:
        try:
            folder_iter = os.walk(game_folder)
        except Exception as exc:
            if logger:
                logger.debug("Cannot walk %s: %s", game_folder, exc)
            else:
                logging.debug("Cannot walk %s: %s", game_folder, exc)
            continue

        for root_dir, _, files in folder_iter:
            if not files:
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
                found_games.append(
                    _build_game_record(
                        root_dir,
                        matched_file,
                        entry,
                        use_korean=use_korean,
                    )
                )

    found_games.sort(key=lambda game: int(game.get("sheet_order", 10**9)))
    return found_games


def _schedule_callback(
    callback: CompletionCallback | None,
    *,
    schedule: SchedulerCallback | None = None,
    logger=None,
    description: str = "scan callback",
) -> None:
    if not callable(callback):
        return

    try:
        if schedule is None:
            callback()
        else:
            schedule(callback)
    except Exception:
        if logger:
            logger.exception("Failed to schedule %s", description)
        else:
            logging.exception("Failed to schedule %s", description)


def run_scan_job(
    game_folders: Iterable[str],
    game_db: dict[str, GameDbEntry],
    *,
    use_korean: bool = False,
    is_game_supported: GameSupportPredicate | None = None,
    schedule: SchedulerCallback | None = None,
    on_game_found: GameFoundCallback | None = None,
    on_complete: CompletionCallback | None = None,
    logger=None,
) -> None:
    """Run the scan and emit completion callbacks via the provided scheduler."""
    try:
        found_games = scan_game_folders(
            game_folders,
            game_db,
            use_korean=use_korean,
            is_game_supported=is_game_supported,
            logger=logger,
        )
        for game in found_games:
            if not callable(on_game_found):
                continue
            _schedule_callback(
                lambda game_record=game: on_game_found(game_record),
                schedule=schedule,
                logger=logger,
                description="found-game callback",
            )
    except Exception:
        if logger:
            logger.exception("Scan worker error")
        else:
            logging.exception("Scan worker error")
    finally:
        _schedule_callback(
            on_complete,
            schedule=schedule,
            logger=logger,
            description="scan completion callback",
        )
