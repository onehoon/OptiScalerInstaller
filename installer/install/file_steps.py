from __future__ import annotations

from collections.abc import Callable, Mapping
import fnmatch
import logging
import os
import re
import shutil
import stat
from pathlib import Path
import tempfile as tempfile_module
from typing import Any

from ..common.windows_paths import iter_documents_dir_candidates, normalize_candidate_path
from ..config import ini_utils, json_utils, xml_utils
from . import services as installer_services
from .payload_utils import resolve_payload_source_dir, validate_optiscaler_payload_dir
from .workflow import InstallWorkflowCallbacks

import winreg


_DOCUMENTS_ENV_TOKEN = "%DOCUMENTS%"
_DOCUMENTS_PREFIXES = {"documents", "document"}
_DOCUMENTS_STYLE_PREFIXES = _DOCUMENTS_PREFIXES | {"my games"}
_REGISTRY_HIVE_MAP = {
    "hkcu": "HKEY_CURRENT_USER",
    "hkey_current_user": "HKEY_CURRENT_USER",
    "hklm": "HKEY_LOCAL_MACHINE",
    "hkey_local_machine": "HKEY_LOCAL_MACHINE",
    "hkcr": "HKEY_CLASSES_ROOT",
    "hkey_classes_root": "HKEY_CLASSES_ROOT",
    "hku": "HKEY_USERS",
    "hkey_users": "HKEY_USERS",
}
_REGISTRY_TYPE_MAP = {
    "reg_sz": "REG_SZ",
    "reg_expand_sz": "REG_EXPAND_SZ",
    "reg_multi_sz": "REG_MULTI_SZ",
    "reg_dword": "REG_DWORD",
    "reg_qword": "REG_QWORD",
}


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


def _trim_documents_prefix(relative_path: str) -> tuple[str, ...]:
    normalized = str(relative_path or "").strip()
    if not normalized:
        return ()

    if normalized[:len(_DOCUMENTS_ENV_TOKEN)].lower() == _DOCUMENTS_ENV_TOKEN.lower():
        normalized = normalized[len(_DOCUMENTS_ENV_TOKEN):].lstrip("\\/")

    parts = list(_split_relative_path_parts(normalized))
    if parts and parts[0].strip().casefold() in _DOCUMENTS_PREFIXES:
        parts = parts[1:]
    return tuple(parts)


def _resolve_documents_matches(relative_path: str) -> tuple[Path, ...]:
    relative_parts = _trim_documents_prefix(relative_path)
    if not relative_parts:
        return ()

    has_wildcard = any(_has_path_wildcard(part) for part in relative_parts)
    matches: list[Path] = []
    for documents_dir in iter_documents_dir_candidates():
        if has_wildcard:
            matches.extend(_match_documents_relative_path(documents_dir, relative_parts))
            continue

        candidate = documents_dir.joinpath(*relative_parts)
        if candidate.is_file():
            matches.append(candidate)

    return _dedupe_paths(matches)


def _resolve_documents_candidate_path(relative_path: str) -> Path | None:
    matches = _resolve_documents_matches(relative_path)
    if matches:
        return matches[0]

    relative_parts = _trim_documents_prefix(relative_path)
    if not relative_parts:
        return None

    for documents_dir in iter_documents_dir_candidates():
        return documents_dir.joinpath(*relative_parts)
    return None


def resolve_ingame_ini_path(target_path: str, ingame_ini_name: str, logger=None) -> str | None:
    resolved = _resolve_profile_path(target_path, ingame_ini_name, require_existing=True, logger=logger)
    return str(resolved) if resolved is not None else None


def _resolve_profile_path(
    target_path: str,
    configured_path: str,
    *,
    require_existing: bool,
    logger=None,
) -> Path | None:
    raw_path = str(configured_path or "").strip()
    if not raw_path:
        return None

    expanded_path = Path(os.path.expanduser(os.path.expandvars(raw_path)))
    if expanded_path.is_absolute():
        if require_existing and not expanded_path.is_file():
            return None
        return expanded_path

    first_part = next(iter(_split_relative_path_parts(raw_path)), "").strip().casefold()
    if first_part in _DOCUMENTS_STYLE_PREFIXES:
        documents_candidate = _resolve_documents_candidate_path(raw_path)
        if documents_candidate is not None and (not require_existing or documents_candidate.is_file()):
            return documents_candidate
        if logger:
            logger.info("Profile target not found under Documents: %s", raw_path)
        return None

    direct_candidate = Path(target_path) / raw_path
    if not require_existing or direct_candidate.is_file():
        return direct_candidate

    documents_candidate = _resolve_documents_candidate_path(raw_path)
    if documents_candidate is not None and documents_candidate.is_file():
        return documents_candidate

    documents_fallback = _resolve_documents_candidate_path(f"Documents\\{raw_path}")
    if documents_fallback is not None and documents_fallback.is_file():
        return documents_fallback

    return None


def _normalize_profile_scalar(value: object, *, value_type: str = "") -> str:
    normalized_type = str(value_type or "").strip().casefold()
    try:
        if normalized_type in {"bool", "boolean"} or isinstance(value, bool):
            return "true" if bool(value) else "false"
        if normalized_type in {"int", "integer"}:
            return str(int(value))
        if normalized_type in {"float", "double"}:
            return str(float(value))
    except Exception:
        return str(value)
    if value is None:
        return ""
    return str(value)


def _apply_existing_file_settings(
    file_path: Path,
    *,
    logger,
    apply_callback: Callable[[], None],
    restore_original_readonly: bool,
) -> None:
    if not file_path.exists():
        return

    original_readonly = not (file_path.stat().st_mode & stat.S_IWRITE)
    try:
        if original_readonly:
            ini_utils._ensure_file_writable(file_path)
        apply_callback()
    finally:
        if original_readonly and restore_original_readonly:
            ini_utils._set_file_readonly(file_path)


def _collect_unreal_ini_profile_targets(
    target_path: str,
    game_data: dict[str, Any],
    *,
    logger,
) -> dict[Path, dict[tuple[str, str, str], str]]:
    unreal_targets: dict[Path, dict[tuple[str, str, str], str]] = {}
    for row in list(game_data.get("game_unreal_ini_profile") or []):
        if not isinstance(row, Mapping):
            continue
        profile_path = str(row.get("path") or "").strip()
        section = str(row.get("section") or "").strip()
        key = str(row.get("key") or "").strip()
        value_path = str(row.get("value_path") or "").strip()
        if not profile_path or not section or not key or not value_path:
            continue

        resolved_path = _resolve_profile_path(
            target_path,
            profile_path,
            require_existing=True,
            logger=logger,
        )
        if resolved_path is None:
            logger.info("Skipped game_unreal_ini_profile because target file was not found: %s", profile_path)
            continue

        unreal_targets.setdefault(resolved_path, {})[(section, key, value_path)] = _normalize_profile_scalar(
            row.get("value"),
            value_type=str(row.get("value_type") or ""),
        )
    return unreal_targets


def apply_optional_ingame_ini_settings(target_path: str, game_data: dict[str, Any], logger) -> None:
    ini_targets: dict[Path, dict[tuple[str, str], str]] = {}
    for row in list(game_data.get("game_ini_profile") or []):
        if not isinstance(row, Mapping):
            continue
        profile_path = str(row.get("path") or "").strip()
        section = str(row.get("section") or "").strip()
        key = str(row.get("key") or "").strip()
        if not profile_path or not section or not key:
            continue

        resolved_path = _resolve_profile_path(
            target_path,
            profile_path,
            require_existing=True,
            logger=logger,
        )
        if resolved_path is None:
            logger.info("Skipped game_ini_profile because target file was not found: %s", profile_path)
            continue

        ini_targets.setdefault(resolved_path, {})[(section, key)] = _normalize_profile_scalar(row.get("value"))

    for file_path, settings in ini_targets.items():
        def _apply_ini() -> None:
            ini_utils.apply_ini_settings(
                str(file_path),
                settings,
                logger=logger,
            )
            logger.info("Applied game_ini_profile settings to %s", file_path)

        _apply_existing_file_settings(
            file_path,
            logger=logger,
            apply_callback=_apply_ini,
            restore_original_readonly=True,
        )

    unreal_ini_targets = _collect_unreal_ini_profile_targets(target_path, game_data, logger=logger)
    for file_path, settings in unreal_ini_targets.items():
        def _apply_unreal_ini() -> None:
            ini_utils.apply_unreal_ini_settings(
                str(file_path),
                settings,
                logger=logger,
            )
            logger.info("Applied game_unreal_ini_profile settings to %s", file_path)

        _apply_existing_file_settings(
            file_path,
            logger=logger,
            apply_callback=_apply_unreal_ini,
            restore_original_readonly=True,
        )

    xml_targets: dict[Path, dict[str | tuple[str, ...], str]] = {}
    for row in list(game_data.get("game_xml_profile") or []):
        if not isinstance(row, Mapping):
            continue
        profile_path = str(row.get("path") or "").strip()
        xml_path = str(row.get("xml_path") or "").strip()
        if not profile_path or not xml_path:
            continue

        resolved_path = _resolve_profile_path(
            target_path,
            profile_path,
            require_existing=True,
            logger=logger,
        )
        if resolved_path is None:
            logger.info("Skipped game_xml_profile because target file was not found: %s", profile_path)
            continue

        normalized_xml_path = xml_path[2:] if xml_path.startswith("./") else xml_path
        xml_targets.setdefault(resolved_path, {})[normalized_xml_path] = _normalize_profile_scalar(row.get("value"))

    for file_path, settings in xml_targets.items():
        def _apply_xml() -> None:
            xml_utils.apply_xml_settings(
                str(file_path),
                settings,
                logger=logger,
                log_label=file_path.name,
            )
            logger.info("Applied game_xml_profile settings to %s", file_path)

        _apply_existing_file_settings(
            file_path,
            logger=logger,
            apply_callback=_apply_xml,
            restore_original_readonly=True,
        )


def apply_optional_engine_ini_settings(target_path: str, game_data: dict[str, Any], logger) -> None:
    engine_targets: dict[Path, dict[str, dict[str, str]]] = {}
    for row in list(game_data.get("engine_ini_profile") or []):
        if not isinstance(row, Mapping):
            continue
        profile_path = str(row.get("path") or "").strip()
        section = str(row.get("section") or "").strip()
        key = str(row.get("key") or "").strip()
        if not profile_path or not section or not key:
            continue

        engine_path = _resolve_profile_path(
            target_path,
            profile_path,
            require_existing=False,
            logger=logger,
        )
        if engine_path is None:
            continue
        if not engine_path.parent.is_dir():
            logger.info("Skipped engine_ini_profile because target directory was not found: %s", engine_path.parent)
            continue

        section_map = engine_targets.setdefault(engine_path, {})
        section_map.setdefault(section, {})[key] = _normalize_profile_scalar(row.get("value"))

    for engine_path, section_map in engine_targets.items():
        try:
            if not engine_path.exists():
                engine_path.write_text("", encoding="utf-8")
            ini_utils._ensure_file_writable(engine_path)
            ini_utils._upsert_ini_entries(engine_path, section_map, logger=logger)
            logger.info("Applied engine_ini_profile settings to %s", engine_path)
        except Exception:
            logger.exception("Failed to apply engine_ini_profile settings to %s", engine_path)
        finally:
            if engine_path.exists():
                ini_utils._set_file_readonly(engine_path)


def apply_optional_json_settings(target_path: str, game_data: dict[str, Any], logger) -> None:
    json_targets: dict[Path, list[dict[str, Any]]] = {}
    for row in list(game_data.get("game_json_profile") or []):
        if not isinstance(row, Mapping):
            continue
        profile_path = str(row.get("path") or "").strip()
        json_path = str(row.get("json_path") or "").strip()
        op = str(row.get("op") or "set").strip().casefold() or "set"
        if not profile_path or not json_path:
            continue
        if op != "set":
            logger.info("Skipped game_json_profile row with unsupported op '%s': %s", op, json_path)
            continue

        resolved_path = _resolve_profile_path(
            target_path,
            profile_path,
            require_existing=True,
            logger=logger,
        )
        if resolved_path is None:
            logger.info("Skipped game_json_profile because target file was not found: %s", profile_path)
            continue

        json_targets.setdefault(resolved_path, []).append(dict(row))

    for file_path, rows in json_targets.items():
        def _apply_json() -> None:
            changed = json_utils.apply_json_settings(file_path, rows, logger=logger)
            if changed:
                logger.info("Applied game_json_profile settings to %s", Path(file_path).name)

        _apply_existing_file_settings(
            file_path,
            logger=logger,
            apply_callback=_apply_json,
            restore_original_readonly=True,
        )


def _coerce_registry_value(value: object, value_type: str) -> object:
    normalized_type = str(value_type or "").strip().casefold()
    if normalized_type == "reg_dword":
        return int(value)
    if normalized_type == "reg_qword":
        return int(value)
    if normalized_type == "reg_multi_sz":
        if isinstance(value, list):
            return [str(item) for item in value]
        return [token.strip() for token in str(value or "").split("|") if token.strip()]
    return str(value or "")


def _dedupe_registry_rows(rows: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    deduped: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for row in rows:
        hive_name = str(row.get("hive") or "").strip().casefold()
        key_path = str(row.get("key_path") or "").strip()
        value_name = str(row.get("value_name") or "").strip()
        if not hive_name or not key_path or not value_name:
            continue
        deduped[(hive_name, key_path, value_name)] = row
    return list(deduped.values())


def apply_optional_registry_settings(game_data: dict[str, Any], logger) -> None:
    rows = [row for row in list(game_data.get("registry_profile") or []) if isinstance(row, Mapping)]
    if not rows:
        return
    rows = _dedupe_registry_rows(rows)

    for row in rows:
        hive_name = str(row.get("hive") or "").strip().casefold()
        key_path = str(row.get("key_path") or "").strip()
        value_name = str(row.get("value_name") or "").strip()
        value_type_name = str(row.get("value_type") or "").strip().casefold()
        if not hive_name or not key_path or not value_name or not value_type_name:
            continue

        resolved_hive_name = _REGISTRY_HIVE_MAP.get(hive_name)
        resolved_type_name = _REGISTRY_TYPE_MAP.get(value_type_name)
        if not resolved_hive_name or not resolved_type_name:
            logger.info(
                "Skipped registry_profile because hive or type is unsupported: hive=%s type=%s",
                hive_name,
                value_type_name,
            )
            continue

        try:
            root_key = getattr(winreg, resolved_hive_name)
            registry_type = getattr(winreg, resolved_type_name)
            registry_value = _coerce_registry_value(row.get("value"), value_type_name)
            with winreg.CreateKeyEx(root_key, key_path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(
                    key,
                    value_name,
                    0,
                    registry_type,
                    registry_value,
                )
            logger.info("Applied registry_profile value %s\\%s", key_path, value_name)
        except Exception:
            logger.exception("Failed to apply registry_profile row: %s", key_path)


def install_fsr4_dll(target_path: str, fsr4_source_archive: str, logger) -> Path:
    if not fsr4_source_archive:
        raise FileNotFoundError("FSR4 is not ready")

    with tempfile_module.TemporaryDirectory() as tmpdir:
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
        logger.info("Installed FSR4 DLL")
        return destination_dll


def install_base_payload_from_folder(
    source_folder: str,
    target_path: str,
    final_dll_name: str,
    exclude_patterns: list[str],
    logger,
) -> None:
    source_path = validate_optiscaler_payload_dir(source_folder)
    installer_services.backup_existing_optiscaler_dlls(target_path, logger=logger)
    installer_services.remove_legacy_optiscaler_files(target_path, logger=logger)
    installer_services.install_from_source_folder(
        str(source_path),
        target_path,
        dll_name=final_dll_name,
        exclude_patterns=exclude_patterns,
        logger=logger,
    )
    logger.info("Installed OptiScaler files from prepared payload folder")


def install_base_payload_from_archive(
    source_archive: str,
    target_path: str,
    final_dll_name: str,
    exclude_patterns: list[str],
    logger,
) -> None:
    with tempfile_module.TemporaryDirectory() as tmpdir:
        installer_services.extract_archive(source_archive, tmpdir, logger=logger)
        actual_source = resolve_payload_source_dir(tmpdir)
        install_base_payload_from_folder(
            str(actual_source),
            target_path,
            final_dll_name,
            exclude_patterns,
            logger,
        )
        logger.info("Extracted and installed OptiScaler files from archive")


def install_base_payload(
    source_archive: str,
    target_path: str,
    final_dll_name: str,
    exclude_patterns: list[str],
    logger,
) -> None:
    source_path = Path(str(source_archive or "").strip())
    if source_path.is_dir():
        install_base_payload_from_folder(
            str(source_path),
            target_path,
            final_dll_name,
            exclude_patterns,
            logger,
        )
        return
    install_base_payload_from_archive(
        str(source_path),
        target_path,
        final_dll_name,
        exclude_patterns,
        logger,
    )


def create_install_workflow_callbacks() -> InstallWorkflowCallbacks:
    return InstallWorkflowCallbacks(
        install_base_payload=install_base_payload,
        apply_optional_ingame_ini_settings=apply_optional_ingame_ini_settings,
        apply_optional_json_settings=apply_optional_json_settings,
        apply_optional_engine_ini_settings=apply_optional_engine_ini_settings,
        apply_optional_registry_settings=apply_optional_registry_settings,
        install_fsr4_dll=install_fsr4_dll,
    )


__all__ = [
    "apply_optional_engine_ini_settings",
    "apply_optional_ingame_ini_settings",
    "apply_optional_json_settings",
    "apply_optional_registry_settings",
    "create_install_workflow_callbacks",
    "install_base_payload",
    "install_base_payload_from_archive",
    "install_base_payload_from_folder",
    "install_fsr4_dll",
    "resolve_ingame_ini_path",
    "resolve_payload_source_dir",
]
