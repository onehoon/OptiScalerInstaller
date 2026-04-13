from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from ...common.windows_paths import iter_documents_dir_candidates
from ...config import xml_utils


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


def resolve_rdr2_system_xml_path() -> Path:
    candidates = tuple(documents_dir / RDR2_SYSTEM_XML_RELATIVE_PATH for documents_dir in iter_documents_dir_candidates())
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
