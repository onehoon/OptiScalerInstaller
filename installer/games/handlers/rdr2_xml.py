from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from ...config import xml_utils

try:
    import winreg
except ImportError:  # pragma: no cover - Windows-only dependency
    winreg = None


RDR2_SYSTEM_XML_RELATIVE_PATH = (
    Path("Rockstar Games")
    / "Red Dead Redemption 2"
    / "Settings"
    / "system.xml"
)
RDR2_SYSTEM_XML_SETTINGS = {
    "graphics/dlssIndex@value": "3",
    "graphics/dlssQuality@value": "1",
    "graphics/fsr2Index@value": "0",
    "advancedGraphics/API": "kSettingAPI_DX12",
    "advancedGraphics/locked@value": "false",
    "advancedGraphics/motionBlur@value": "false",
    "video/tripleBuffered@value": "false",
    "video/ReflexSettings": "kSettingReflex_On",
}


def _normalize_candidate_path(path: Path) -> str:
    return str(path.expanduser().resolve(strict=False)).lower()


def _get_windows_documents_dir() -> Path | None:
    if os.name != "nt" or winreg is None:
        return None

    registry_targets = (
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"),
    )
    for root_key, sub_key in registry_targets:
        try:
            with winreg.OpenKey(root_key, sub_key) as key:
                value, _ = winreg.QueryValueEx(key, "Personal")
        except OSError:
            continue

        expanded = os.path.expandvars(str(value or "").strip())
        if expanded:
            return Path(expanded)
    return None


def _iter_documents_dir_candidates() -> tuple[Path, ...]:
    candidates: list[Path] = []

    documents_dir = _get_windows_documents_dir()
    if documents_dir is not None:
        candidates.append(documents_dir)

    for env_name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        env_value = str(os.environ.get(env_name, "") or "").strip()
        if env_value:
            candidates.append(Path(env_value) / "Documents")

    userprofile = str(os.environ.get("USERPROFILE", "") or "").strip()
    if userprofile:
        candidates.append(Path(userprofile) / "Documents")

    candidates.append(Path.home() / "Documents")

    unique_candidates: list[Path] = []
    seen_candidates: set[str] = set()
    for candidate in candidates:
        normalized = _normalize_candidate_path(candidate)
        if normalized in seen_candidates:
            continue
        seen_candidates.add(normalized)
        unique_candidates.append(candidate)
    return tuple(unique_candidates)


def resolve_rdr2_system_xml_path() -> Path:
    candidates = tuple(documents_dir / RDR2_SYSTEM_XML_RELATIVE_PATH for documents_dir in _iter_documents_dir_candidates())
    for candidate in candidates:
        if candidate.is_file():
            return candidate

    if candidates:
        return candidates[0]
    return Path(os.environ.get("USERPROFILE") or str(Path.home())) / "Documents" / RDR2_SYSTEM_XML_RELATIVE_PATH


def _system_xml_backup_path(xml_path: Path) -> Path:
    return xml_path.with_name(f"{xml_path.name}.bak")


def _ensure_system_xml_backup(xml_path: Path, logger=None) -> Path:
    backup_path = _system_xml_backup_path(xml_path)
    if backup_path.exists():
        if not backup_path.is_file():
            raise RuntimeError(f"RDR2 system.xml backup path is not a file: {backup_path}")
        if logger:
            logger.info("Reusing existing RDR2 system.xml backup: %s", backup_path)
        return backup_path

    shutil.copy2(xml_path, backup_path)
    if logger:
        logger.info("Created RDR2 system.xml backup: %s", backup_path)
    return backup_path


def apply_rdr2_system_xml_settings(system_xml_path: str | Path | None = None, logger=None) -> Path:
    xml_path = Path(system_xml_path) if system_xml_path else resolve_rdr2_system_xml_path()
    if not xml_path.is_file():
        raise FileNotFoundError(f"RDR2 system.xml not found: {xml_path}")

    original_mode = xml_path.stat().st_mode
    original_readonly = not bool(original_mode & stat.S_IWRITE)
    _ensure_system_xml_backup(xml_path, logger=logger)

    try:
        if original_readonly:
            os.chmod(xml_path, original_mode | stat.S_IWRITE)
            if logger:
                logger.info("Temporarily removed read-only attribute from %s", xml_path)

        xml_utils.apply_xml_settings(
            xml_path,
            RDR2_SYSTEM_XML_SETTINGS,
            logger=logger,
            log_label="RDR2 XML",
            raise_on_error=True,
        )
        if logger:
            logger.info("Applied RDR2 graphics XML settings to %s", xml_path)
    finally:
        if original_readonly:
            os.chmod(xml_path, original_mode)
            if logger:
                logger.info("Restored read-only attribute on %s", xml_path)

    return xml_path
