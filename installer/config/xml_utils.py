from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from pathlib import Path


def _normalize_xml_setting_target(target) -> tuple[tuple[str, ...], str | None]:
    if isinstance(target, (list, tuple)):
        raw_parts = [str(part or "").strip() for part in target]
        path_text = "/".join(part for part in raw_parts if part)
    else:
        path_text = str(target or "").strip()

    if not path_text:
        return (), None

    normalized_path = path_text.replace("\\", "/").strip("/")
    if not normalized_path:
        return (), None

    attribute_name = None
    if "@" in normalized_path:
        normalized_path, attribute_name = normalized_path.rsplit("@", 1)
        normalized_path = normalized_path.strip("/")
        attribute_name = str(attribute_name or "").strip() or None

    path_parts = tuple(part.strip() for part in normalized_path.split("/") if part.strip())
    return path_parts, attribute_name


def _ensure_child(parent: ET.Element, tag: str) -> ET.Element:
    child = parent.find(tag)
    if child is None:
        child = ET.SubElement(parent, tag)
    return child


def _ensure_xml_path(root: ET.Element, path_parts: tuple[str, ...]) -> ET.Element:
    node = root
    parts = path_parts
    if parts and parts[0] == root.tag:
        parts = parts[1:]
    for part in parts:
        node = _ensure_child(node, part)
    return node


def apply_xml_settings(xml_path, settings, logger=None) -> None:
    if not settings:
        return

    path = Path(xml_path)
    if not path.exists():
        return

    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except Exception:
        if logger:
            logger.exception("Failed to parse XML for in-place update: %s", path)
        else:
            logging.exception("Failed to parse XML for in-place update: %s", path)
        return

    modified = False
    for target, value in settings.items():
        path_parts, attribute_name = _normalize_xml_setting_target(target)
        if not path_parts:
            continue

        node = _ensure_xml_path(root, path_parts)
        value_text = str(value)
        label = "/".join(path_parts)

        if attribute_name:
            previous_value = node.get(attribute_name)
            if previous_value == value_text:
                continue
            node.set(attribute_name, value_text)
            modified = True
            if logger:
                logger.info("%s edit %s/@%s -> %s", path.name or "XML", label, attribute_name, value_text)
            continue

        previous_text = (node.text or "").strip()
        if previous_text == value_text:
            continue
        node.text = value_text
        modified = True
        if logger:
            logger.info("%s edit %s -> %s", path.name or "XML", label, value_text)

    if not modified:
        return

    try:
        ET.indent(tree, space="  ")
        tree.write(path, encoding="UTF-8", xml_declaration=True)
    except Exception:
        if logger:
            logger.exception("Failed to write updated XML file: %s", path)
        else:
            logging.exception("Failed to write updated XML file: %s", path)