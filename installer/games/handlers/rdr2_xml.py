from __future__ import annotations

import os
import shutil
import stat
import xml.etree.ElementTree as ET
from pathlib import Path

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


def _ensure_child(parent: ET.Element, tag: str) -> ET.Element:
    child = parent.find(tag)
    if child is None:
        child = ET.SubElement(parent, tag)
    return child


def _ensure_path(root: ET.Element, *tags: str) -> ET.Element:
    node = root
    for tag in tags:
        node = _ensure_child(node, tag)
    return node


def _set_value_attribute(root: ET.Element, path: tuple[str, ...], value: str, logger=None) -> None:
    node = _ensure_path(root, *path)
    previous = node.get("value")
    node.set("value", str(value))
    if logger and previous != str(value):
        logger.info("RDR2 XML edit %s/@value -> %s (was: %s)", "/".join(path), value, previous)


def _set_text_value(root: ET.Element, path: tuple[str, ...], value: str, logger=None) -> None:
    node = _ensure_path(root, *path)
    previous = (node.text or "").strip()
    node.text = str(value)
    if logger and previous != str(value):
        logger.info("RDR2 XML edit %s -> %s (was: %s)", "/".join(path), value, previous)


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

        tree = ET.parse(xml_path)
        root = tree.getroot()

        _set_value_attribute(root, ("graphics", "dlssIndex"), "3", logger=logger)
        _set_value_attribute(root, ("graphics", "dlssQuality"), "1", logger=logger)
        _set_value_attribute(root, ("graphics", "fsr2Index"), "0", logger=logger)

        _set_text_value(root, ("advancedGraphics", "API"), "kSettingAPI_DX12", logger=logger)
        _set_value_attribute(root, ("advancedGraphics", "locked"), "false", logger=logger)
        _set_value_attribute(root, ("advancedGraphics", "motionBlur"), "false", logger=logger)

        #_set_value_attribute(root, ("video", "windowed"), "2", logger=logger)
        _set_value_attribute(root, ("video", "tripleBuffered"), "false", logger=logger)
        _set_text_value(root, ("video", "ReflexSettings"), "kSettingReflex_On", logger=logger)

        ET.indent(tree, space="  ")
        tree.write(xml_path, encoding="UTF-8", xml_declaration=True)
        if logger:
            logger.info("Applied RDR2 graphics XML settings to %s", xml_path)
    finally:
        if original_readonly:
            os.chmod(xml_path, original_mode)
            if logger:
                logger.info("Restored read-only attribute on %s", xml_path)

    return xml_path
