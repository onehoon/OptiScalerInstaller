from __future__ import annotations

import fnmatch
import logging
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any

from ..common.windows_paths import iter_documents_dir_candidates, normalize_candidate_path
from ..config import ini_utils, xml_utils
from . import services as installer_services
from .workflow import InstallWorkflowCallbacks


_DOCUMENTS_ENV_TOKEN = "%DOCUMENTS%"


def _has_path_wildcard(path_part: str) -> bool:
    return any(token in path_part for token in ("*", "?", "["))


def _dedupe_paths(paths: list[Path]) -> tuple[Path, ...]:
    unique_paths: list[Path] = []
    seen_paths: set[str] = set()
    for path in paths:
        normalized = normalize_candidate_path(path)
        if normalized in seen_paths:
            continue
        seen_paths.add(normalized)
        unique_paths.append(path)
    return tuple(unique_paths)


def _split_relative_path_parts(path_text: str) -> tuple[str, ...]:
    return tuple(part for part in re.split(r"[\\/]+", path_text) if part and part != ".")


def _match_documents_relative_path(base_dir: Path, relative_parts: tuple[str, ...]) -> tuple[Path, ...]:
    if not relative_parts:
        return ()

    current_paths: tuple[Path, ...] = (base_dir,)
    for index, raw_part in enumerate(relative_parts):
        is_last_part = index == len(relative_parts) - 1
        next_paths: list[Path] = []
        pattern = raw_part.lower()
        part_has_wildcard = _has_path_wildcard(raw_part)

        for current_path in current_paths:
            if not current_path.is_dir():
                continue

            if part_has_wildcard:
                try:
                    children = tuple(current_path.iterdir())
                except OSError:
                    continue

                for child in children:
                    if not fnmatch.fnmatch(child.name.lower(), pattern):
                        continue
                    if is_last_part and child.is_file():
                        next_paths.append(child)
                    elif not is_last_part and child.is_dir():
                        next_paths.append(child)
                continue

            child_path = current_path / raw_part
            if is_last_part and child_path.is_file():
                next_paths.append(child_path)
            elif not is_last_part and child_path.is_dir():
                next_paths.append(child_path)

        current_paths = _dedupe_paths(next_paths)
        if not current_paths:
            break

    return current_paths


def _resolve_documents_ingame_ini_path(ingame_ini_name: str, logger=None) -> str | None:
    relative_text = str(ingame_ini_name or "").strip()[len(_DOCUMENTS_ENV_TOKEN):].lstrip("\\/")
    relative_parts = _split_relative_path_parts(relative_text)
    if not relative_parts:
        if logger:
            logger.warning("Skipping #ingame_ini because %%DOCUMENTS%% path is empty: %s", ingame_ini_name)
        else:
            logging.warning("Skipping #ingame_ini because %%DOCUMENTS%% path is empty: %s", ingame_ini_name)
        return None

    if any(part == ".." for part in relative_parts):
        if logger:
            logger.warning("Skipping #ingame_ini because %%DOCUMENTS%% path escapes Documents: %s", ingame_ini_name)
        else:
            logging.warning("Skipping #ingame_ini because %%DOCUMENTS%% path escapes Documents: %s", ingame_ini_name)
        return None

    matches: list[Path] = []
    for documents_dir in iter_documents_dir_candidates():
        matches.extend(_match_documents_relative_path(documents_dir, relative_parts))

    unique_matches = _dedupe_paths(matches)
    if len(unique_matches) == 1:
        resolved_path = str(unique_matches[0])
        if logger:
            logger.info("Resolved #ingame_ini via %%DOCUMENTS%%: %s -> %s", ingame_ini_name, resolved_path)
        else:
            logging.info("Resolved #ingame_ini via %%DOCUMENTS%%: %s -> %s", ingame_ini_name, resolved_path)
        return resolved_path

    if len(unique_matches) > 1:
        matched_text = ", ".join(str(path) for path in unique_matches)
        if logger:
            logger.warning(
                "Skipping #ingame_ini because %%DOCUMENTS%% path matched multiple files: %s -> %s",
                ingame_ini_name,
                matched_text,
            )
        else:
            logging.warning(
                "Skipping #ingame_ini because %%DOCUMENTS%% path matched multiple files: %s -> %s",
                ingame_ini_name,
                matched_text,
            )
        return None

    if logger:
        logger.info("No file matched #ingame_ini %%DOCUMENTS%% pattern: %s", ingame_ini_name)
    else:
        logging.info("No file matched #ingame_ini %%DOCUMENTS%% pattern: %s", ingame_ini_name)
    return None


def resolve_ingame_ini_path(target_path: str, ingame_ini_name: str, logger=None) -> str | None:
    normalized_path = str(ingame_ini_name or "").strip()
    if not normalized_path:
        return None

    if normalized_path[:len(_DOCUMENTS_ENV_TOKEN)].lower() == _DOCUMENTS_ENV_TOKEN.lower():
        return _resolve_documents_ingame_ini_path(normalized_path, logger=logger)

    if any(sep in normalized_path for sep in ("/", "\\", ":")):
        expanded_path = os.path.expandvars(normalized_path)
        return os.path.expanduser(expanded_path)
    return os.path.join(target_path, normalized_path)


def apply_optional_ingame_ini_settings(target_path: str, game_data: dict[str, Any], logger) -> None:
    ingame_ini_name = str(game_data.get("ingame_ini", "")).strip()
    ingame_settings = dict(game_data.get("ingame_settings", {}) or {})
    if not ingame_ini_name:
        return
    if not ingame_settings:
        logger.info("#ingame_ini configured but no #ingame_setting values provided: %s", ingame_ini_name)
        return

    logger.info("#ingame_ini configured: %s", ingame_ini_name)
    ingame_ini_path = resolve_ingame_ini_path(target_path, ingame_ini_name, logger=logger)
    if not ingame_ini_path:
        logger.info("#ingame_ini could not be resolved, skipped edits: %s", ingame_ini_name)
        return
    if not os.path.exists(ingame_ini_path):
        logger.info("#ingame_ini missing, skipped edits: %s", ingame_ini_path)
        return

    ini_file = Path(ingame_ini_path)
    orig_stat = ini_file.stat()
    orig_readonly = not (orig_stat.st_mode & stat.S_IWRITE)
    try:
        if orig_readonly:
            ini_utils._ensure_file_writable(ini_file)
        logger.info("#ingame_ini exists: %s", ingame_ini_path)
        if ini_file.suffix.lower() == ".xml":
            xml_utils.apply_xml_settings(ingame_ini_path, ingame_settings, logger=logger)
            logger.info("Applied in-game XML settings to %s", ingame_ini_path)
        else:
            ini_utils.apply_ini_settings(ingame_ini_path, ingame_settings, force_frame_generation=False, logger=logger)
            logger.info("Applied in-game settings to %s", ingame_ini_path)
    finally:
        # game.ini (in-game settings) is restored to its original read/write state after
        # modification. Users frequently change graphics settings in-game, so we must
        # not lock this file — leaving it writable lets the game continue to update it normally.
        if orig_readonly:
            ini_utils._set_file_readonly(ini_file)


def apply_optional_engine_ini_settings(target_path: str, game_data: dict[str, Any], logger) -> None:
    try:
        engine_loc = str(game_data.get("engine_ini_location", "")).strip()
        engine_ini_content = str(game_data.get("engine_ini_type", "")).strip()
        if not engine_loc or not engine_ini_content:
            return

        logger.info("engine.ini info for install: target=%s, engine_ini_location='%s'", target_path, engine_loc)
        ini_path = ini_utils._find_or_create_engine_ini(engine_loc, workspace_root=target_path, logger=logger)
        if not ini_path:
            return

        try:
            ini_utils._ensure_file_writable(ini_path)
            section_map = ini_utils._parse_version_text_to_ini_entries(engine_ini_content)
            if section_map:
                ini_utils._upsert_ini_entries(ini_path, section_map, logger=logger)
                logger.info("Upserted engine.ini entries to %s", ini_path)
        finally:
            # engine.ini is set to read-only after modification to prevent the game from
            # resetting it on launch. Games often overwrite engine.ini on startup, so
            # keeping it read-only ensures our settings persist across game restarts.
            ini_utils._set_file_readonly(ini_path)
    except Exception:
        logger.exception("Failed while handling engine.ini for %s", target_path)


def install_fsr4_dll(target_path: str, fsr4_source_archive: str, logger) -> Path:
    if not fsr4_source_archive:
        raise FileNotFoundError("FSR4 is not ready")

    with tempfile.TemporaryDirectory() as tmpdir:
        installer_services.extract_archive(fsr4_source_archive, tmpdir, logger=None)
        dll_candidates = [path for path in Path(tmpdir).rglob("*.dll") if path.is_file()]
        if not dll_candidates:
            raise FileNotFoundError("No DLL found inside FSR4 zip")
        if len(dll_candidates) > 1:
            raise RuntimeError("Multiple DLL files found inside FSR4 zip")

        source_dll = dll_candidates[0]
        destination_dll = Path(target_path) / source_dll.name
        try:
            os.chmod(destination_dll, 0o666)
        except OSError:
            pass
        shutil.copy2(source_dll, destination_dll)
        logger.info("Installed FSR4 DLL to %s", destination_dll)
        return destination_dll


def resolve_payload_source_dir(extract_root: str) -> str:
    contents = os.listdir(extract_root)
    if len(contents) == 1:
        single_entry_path = os.path.join(extract_root, contents[0])
        if os.path.isdir(single_entry_path):
            return single_entry_path
    return extract_root


def install_base_payload(
    source_archive: str,
    target_path: str,
    final_dll_name: str,
    exclude_patterns: list[str],
    logger,
) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        installer_services.extract_archive(source_archive, tmpdir, logger=logger)
        actual_source = resolve_payload_source_dir(tmpdir)
        installer_services.backup_existing_optiscaler_dlls(target_path, logger=logger)
        installer_services.remove_legacy_optiscaler_files(target_path, logger=logger)
        installer_services.install_from_source_folder(
            actual_source,
            target_path,
            dll_name=final_dll_name,
            exclude_patterns=exclude_patterns,
            logger=logger,
        )
        logger.info("Extracted and installed files to %s", target_path)


def create_install_workflow_callbacks() -> InstallWorkflowCallbacks:
    return InstallWorkflowCallbacks(
        install_base_payload=install_base_payload,
        apply_optional_ingame_ini_settings=apply_optional_ingame_ini_settings,
        apply_optional_engine_ini_settings=apply_optional_engine_ini_settings,
        install_fsr4_dll=install_fsr4_dll,
    )


__all__ = [
    "apply_optional_engine_ini_settings",
    "apply_optional_ingame_ini_settings",
    "create_install_workflow_callbacks",
    "install_base_payload",
    "install_fsr4_dll",
    "resolve_ingame_ini_path",
    "resolve_payload_source_dir",
]
