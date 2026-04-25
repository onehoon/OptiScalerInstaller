from __future__ import annotations

from collections.abc import Callable, Mapping
import os
import shutil
import stat
from pathlib import Path
import tempfile as tempfile_module
from typing import Any

from ..config import ini_utils, json_utils, xml_utils
from . import services as installer_services
from .payload_utils import resolve_payload_source_dir, validate_optiscaler_payload_dir
from .profile_paths import resolve_profile_path as _resolve_profile_path
from .workflow import InstallWorkflowCallbacks

import winreg


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


class _SuppressInfoLogger:
    def __init__(self, logger) -> None:
        self._logger = logger

    def info(self, *args, **kwargs) -> None:
        return None

    def __getattr__(self, name: str):
        return getattr(self._logger, name)


def resolve_ingame_ini_path(target_path: str, ingame_ini_name: str, logger=None) -> str | None:
    resolved = _resolve_profile_path(target_path, ingame_ini_name, require_existing=True, logger=logger)
    return str(resolved) if resolved is not None else None


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
    apply_callback: Callable[[], None],
    restore_original_readonly: bool,
) -> None:
    if not file_path.exists():
        return

    original_readonly = not (file_path.stat().st_mode & stat.S_IWRITE)
    try:
        if original_readonly:
            ini_utils.ensure_file_writable(file_path)
        apply_callback()
    finally:
        if original_readonly and restore_original_readonly:
            ini_utils.set_file_readonly(file_path)


def _apply_optional_existing_file_settings(
    file_path: Path,
    *,
    logger,
    profile_name: str,
    apply_callback: Callable[[], None],
) -> None:
    # File-based optional profiles (INI/Unreal INI/XML/JSON) are best-effort.
    # Log failures and keep the main install workflow running instead of
    # changing install success/failure. Registry profiles follow the same
    # policy in apply_optional_registry_settings().
    try:
        _apply_existing_file_settings(
            file_path,
            apply_callback=apply_callback,
            restore_original_readonly=True,
        )
    except Exception:
        logger.exception("Failed to apply %s settings to %s", profile_name, file_path)


def _collect_game_ini_profile_targets(
    target_path: str,
    game_data: dict[str, Any],
    *,
    logger,
) -> dict[Path, dict[str, dict[str, str]]]:
    ini_targets: dict[Path, dict[str, dict[str, str]]] = {}
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

        section_map = ini_targets.setdefault(resolved_path, {})
        section_map.setdefault(section, {})[key] = _normalize_profile_scalar(row.get("value"))
    return ini_targets


def _apply_existing_then_add_missing_ini_settings(
    file_path: Path,
    section_map: dict[str, dict[str, str]],
    *,
    logger,
) -> None:
    settings = {
        (section_name, key_name): value
        for section_name, key_values in section_map.items()
        for key_name, value in key_values.items()
    }
    ini_utils.apply_ini_settings(
        str(file_path),
        settings,
        logger=logger,
    )
    ini_utils.upsert_ini_entries(
        file_path,
        section_map,
        logger=logger,
        create_missing_file=False,
        allow_edit_existing=False,
        allow_add_key=True,
        allow_add_section=False,
    )


def _apply_game_ini_profile_settings(target_path: str, game_data: dict[str, Any], logger) -> None:
    ini_targets = _collect_game_ini_profile_targets(target_path, game_data, logger=logger)
    for file_path, section_map in ini_targets.items():
        def _apply_ini(file_path: Path = file_path, section_map: dict[str, dict[str, str]] = section_map) -> None:
            _apply_existing_then_add_missing_ini_settings(
                file_path,
                section_map,
                logger=logger,
            )
            logger.info("Applied game_ini_profile settings to %s", file_path)

        _apply_optional_existing_file_settings(
            file_path,
            logger=logger,
            profile_name="game_ini_profile",
            apply_callback=_apply_ini,
        )


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


def _apply_game_unreal_ini_profile_settings(target_path: str, game_data: dict[str, Any], logger) -> None:
    unreal_ini_targets = _collect_unreal_ini_profile_targets(target_path, game_data, logger=logger)
    for file_path, settings in unreal_ini_targets.items():
        def _apply_unreal_ini(
            file_path: Path = file_path,
            settings: dict[tuple[str, str, str], str] = settings,
        ) -> None:
            ini_utils.apply_unreal_ini_settings(
                str(file_path),
                settings,
                logger=logger,
            )
            logger.info("Applied game_unreal_ini_profile settings to %s", file_path)

        _apply_optional_existing_file_settings(
            file_path,
            logger=logger,
            profile_name="game_unreal_ini_profile",
            apply_callback=_apply_unreal_ini,
        )


def _collect_game_xml_profile_targets(
    target_path: str,
    game_data: dict[str, Any],
    *,
    logger,
) -> dict[Path, dict[str | tuple[str, ...], str]]:
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
    return xml_targets


def _apply_game_xml_profile_settings(target_path: str, game_data: dict[str, Any], logger) -> None:
    xml_targets = _collect_game_xml_profile_targets(target_path, game_data, logger=logger)
    for file_path, settings in xml_targets.items():
        def _apply_xml(
            file_path: Path = file_path,
            settings: dict[str | tuple[str, ...], str] = settings,
        ) -> None:
            xml_utils.apply_xml_settings(
                str(file_path),
                settings,
                logger=logger,
                log_label=file_path.name,
            )
            logger.info("Applied game_xml_profile settings to %s", file_path)

        _apply_optional_existing_file_settings(
            file_path,
            logger=logger,
            profile_name="game_xml_profile",
            apply_callback=_apply_xml,
        )


def apply_optional_ingame_ini_settings(target_path: str, game_data: dict[str, Any], logger) -> None:
    _apply_game_ini_profile_settings(target_path, game_data, logger)
    _apply_game_unreal_ini_profile_settings(target_path, game_data, logger)
    _apply_game_xml_profile_settings(target_path, game_data, logger)


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
            ini_utils.ensure_file_writable(engine_path)
            ini_utils.upsert_ini_entries(engine_path, section_map, logger=logger)
            logger.info("Applied engine_ini_profile settings to %s", engine_path)
        except Exception:
            logger.exception("Failed to apply engine_ini_profile settings to %s", engine_path)
        finally:
            if engine_path.exists():
                # Engine.ini profile changes are intentionally locked after
                # apply so launchers/games do not immediately overwrite them.
                ini_utils.set_file_readonly(engine_path)


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

        _apply_optional_existing_file_settings(
            file_path,
            logger=logger,
            profile_name="game_json_profile",
            apply_callback=_apply_json,
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
        # Registry profile rows are optional post-install tweaks. Log failures
        # per row and continue so they follow the same best-effort policy as
        # file-based optional profiles.
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
        extract_logger = _SuppressInfoLogger(logger) if logger is not None else None
        installer_services.extract_archive(fsr4_source_archive, tmpdir, logger=extract_logger)
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
