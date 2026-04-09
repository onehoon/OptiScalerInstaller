from __future__ import annotations

import codecs
import locale
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path


_XML_DECL_ENCODING_RE = re.compile(br"<\?xml[^>]*encoding\s*=\s*[\"']([A-Za-z0-9._:-]+)[\"']", re.IGNORECASE)
_XML_BOM_ENCODINGS = (
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF8, "utf-8"),
)
_XML_NAME_STOP_CHARS = {" ", "\t", "\r", "\n", "/", ">", "="}


@dataclass(frozen=True)
class _XmlEncodingInfo:
    encoding: str
    bom: bytes = b""


@dataclass(frozen=True)
class _XmlAttributeSpan:
    name: str
    value_start: int
    value_end: int
    quote: str


@dataclass
class _XmlElementSpan:
    tag: str
    path: tuple[str, ...]
    start_tag_start: int
    start_tag_end: int
    start_close_start: int
    attribute_insert_at: int
    content_start: int
    end_tag_start: int | None = None
    end_tag_end: int | None = None
    children: int = 0
    self_closing: bool = False
    attributes: dict[str, _XmlAttributeSpan] = field(default_factory=dict)


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


def _iter_xml_fallback_encodings():
    seen: set[str] = set()
    for encoding in ("utf-8", locale.getpreferredencoding(False), "mbcs" if os.name == "nt" else None, "cp949"):
        normalized = str(encoding or "").strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        yield str(encoding)


def _read_xml_text_with_encoding(path: Path, logger=None) -> tuple[str, _XmlEncodingInfo]:
    raw = path.read_bytes()

    for bom, encoding in _XML_BOM_ENCODINGS:
        if raw.startswith(bom):
            text = raw[len(bom):].decode(encoding)
            if logger and encoding != "utf-8":
                logger.info("Read XML %s using BOM-detected encoding %s", path, encoding)
            elif encoding != "utf-8":
                logging.info("Read XML %s using BOM-detected encoding %s", path, encoding)
            return text, _XmlEncodingInfo(encoding=encoding, bom=bom)

    encoding_candidates: list[str] = []
    declaration_match = _XML_DECL_ENCODING_RE.search(raw[:512])
    if declaration_match:
        declared_encoding = declaration_match.group(1).decode("ascii", errors="ignore").strip()
        if declared_encoding:
            encoding_candidates.append(declared_encoding)

    encoding_candidates.extend(_iter_xml_fallback_encodings())

    seen: set[str] = set()
    last_error = None
    for encoding in encoding_candidates:
        normalized = str(encoding or "").strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        try:
            text = raw.decode(encoding)
            if logger and normalized != "utf-8":
                logger.info("Read XML %s using encoding %s", path, encoding)
            elif normalized != "utf-8":
                logging.info("Read XML %s using encoding %s", path, encoding)
            return text, _XmlEncodingInfo(encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc

    if last_error is not None:
        raise last_error
    return raw.decode("utf-8"), _XmlEncodingInfo(encoding="utf-8")


def _write_xml_text_with_encoding(path: Path, text: str, encoding_info: _XmlEncodingInfo) -> None:
    path.write_bytes(encoding_info.bom + text.encode(encoding_info.encoding))


def _log_xml_message(logger, level: str, message: str, *args) -> None:
    if logger:
        log_method = getattr(logger, level, None) or getattr(logger, "info", None)
        if log_method is not None:
            log_method(message, *args)
            return

    getattr(logging, level, logging.info)(message, *args)


def _unescape_xml_value(value: str) -> str:
    return (
        value.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&amp;", "&")
    )


def _escape_xml_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_xml_attribute(value: str, quote: str) -> str:
    escaped = _escape_xml_text(value)
    if quote == "'":
        return escaped.replace("'", "&apos;")
    return escaped.replace('"', "&quot;")


def _replace_text_range(text: str, start: int, end: int, replacement: str) -> str:
    return f"{text[:start]}{replacement}{text[end:]}"


def _find_tag_end(text: str, start_index: int) -> int:
    quote = None
    for index in range(start_index + 1, len(text)):
        char = text[index]
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char == ">":
            return index + 1
    raise ValueError("Unterminated XML tag")


def _find_markup_end(text: str, start_index: int) -> int:
    quote = None
    bracket_depth = 0
    for index in range(start_index + 2, len(text)):
        char = text[index]
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char == "[":
            bracket_depth += 1
            continue
        if char == "]" and bracket_depth:
            bracket_depth -= 1
            continue
        if char == ">" and bracket_depth == 0:
            return index + 1
    raise ValueError("Unterminated XML markup declaration")


def _parse_start_tag(text: str, start_index: int, tag_end: int) -> tuple[str, dict[str, _XmlAttributeSpan], int, int, bool]:
    cursor = start_index + 1
    while cursor < tag_end and text[cursor].isspace():
        cursor += 1

    name_start = cursor
    while cursor < tag_end and text[cursor] not in _XML_NAME_STOP_CHARS:
        cursor += 1
    tag_name = text[name_start:cursor]
    if not tag_name:
        raise ValueError("Invalid XML start tag")

    close_scan = tag_end - 2
    while close_scan >= start_index and text[close_scan].isspace():
        close_scan -= 1
    self_closing = close_scan >= start_index and text[close_scan] == "/"
    start_close_start = close_scan if self_closing else tag_end - 1

    insert_scan = (start_close_start - 1) if self_closing else (tag_end - 2)
    while insert_scan >= start_index and text[insert_scan].isspace():
        insert_scan -= 1
    attribute_insert_at = insert_scan + 1

    attributes: dict[str, _XmlAttributeSpan] = {}
    limit = start_close_start if self_closing else (tag_end - 1)
    while cursor < limit:
        while cursor < limit and text[cursor].isspace():
            cursor += 1
        if cursor >= limit:
            break

        attr_name_start = cursor
        while cursor < limit and text[cursor] not in _XML_NAME_STOP_CHARS:
            cursor += 1
        attr_name = text[attr_name_start:cursor]
        if not attr_name:
            break

        while cursor < limit and text[cursor].isspace():
            cursor += 1
        if cursor >= limit or text[cursor] != "=":
            while cursor < limit and not text[cursor].isspace():
                cursor += 1
            continue

        cursor += 1
        while cursor < limit and text[cursor].isspace():
            cursor += 1
        if cursor >= limit:
            break

        quote = text[cursor]
        if quote not in {'"', "'"}:
            value_start = cursor
            while cursor < limit and not text[cursor].isspace():
                cursor += 1
            attributes[attr_name] = _XmlAttributeSpan(
                name=attr_name,
                value_start=value_start,
                value_end=cursor,
                quote='"',
            )
            continue

        value_start = cursor + 1
        value_end = value_start
        while value_end < limit and text[value_end] != quote:
            value_end += 1
        if value_end >= limit:
            raise ValueError(f"Unterminated XML attribute value for {attr_name}")

        attributes[attr_name] = _XmlAttributeSpan(
            name=attr_name,
            value_start=value_start,
            value_end=value_end,
            quote=quote,
        )
        cursor = value_end + 1

    return tag_name, attributes, start_close_start, attribute_insert_at, self_closing


def _parse_xml_elements(text: str) -> list[_XmlElementSpan]:
    elements: list[_XmlElementSpan] = []
    stack: list[int] = []
    cursor = 0

    while cursor < len(text):
        start_index = text.find("<", cursor)
        if start_index < 0:
            break

        if text.startswith("<!--", start_index):
            end_index = text.find("-->", start_index + 4)
            if end_index < 0:
                raise ValueError("Unterminated XML comment")
            cursor = end_index + 3
            continue

        if text.startswith("<![CDATA[", start_index):
            end_index = text.find("]]>", start_index + 9)
            if end_index < 0:
                raise ValueError("Unterminated XML CDATA section")
            cursor = end_index + 3
            continue

        if text.startswith("<?", start_index):
            end_index = text.find("?>", start_index + 2)
            if end_index < 0:
                raise ValueError("Unterminated XML processing instruction")
            cursor = end_index + 2
            continue

        if text.startswith("<!", start_index):
            cursor = _find_markup_end(text, start_index)
            continue

        tag_end = _find_tag_end(text, start_index)
        if text.startswith("</", start_index):
            close_cursor = start_index + 2
            while close_cursor < tag_end and text[close_cursor].isspace():
                close_cursor += 1

            name_start = close_cursor
            while close_cursor < tag_end and text[close_cursor] not in _XML_NAME_STOP_CHARS:
                close_cursor += 1
            closing_tag = text[name_start:close_cursor]

            if not stack:
                raise ValueError(f"Unexpected XML closing tag: {closing_tag}")

            element = elements[stack.pop()]
            if element.tag != closing_tag:
                raise ValueError(f"Mismatched XML closing tag: expected {element.tag}, got {closing_tag}")

            element.end_tag_start = start_index
            element.end_tag_end = tag_end
            cursor = tag_end
            continue

        tag_name, attributes, start_close_start, attribute_insert_at, self_closing = _parse_start_tag(text, start_index, tag_end)
        path = (tag_name,)
        if stack:
            parent = elements[stack[-1]]
            parent.children += 1
            path = parent.path + (tag_name,)

        element = _XmlElementSpan(
            tag=tag_name,
            path=path,
            start_tag_start=start_index,
            start_tag_end=tag_end,
            start_close_start=start_close_start,
            attribute_insert_at=attribute_insert_at,
            content_start=tag_end,
            self_closing=self_closing,
            attributes=attributes,
        )
        elements.append(element)
        element_index = len(elements) - 1

        if self_closing:
            element.end_tag_start = tag_end
            element.end_tag_end = tag_end
        else:
            stack.append(element_index)

        cursor = tag_end

    if stack:
        unclosed = elements[stack[-1]].tag
        raise ValueError(f"Unclosed XML tag: {unclosed}")

    return elements


def _find_matching_element(elements: list[_XmlElementSpan], path_parts: tuple[str, ...]) -> _XmlElementSpan | None:
    if not elements:
        return None

    root_tag = elements[0].tag
    normalized_parts = path_parts
    if normalized_parts and normalized_parts[0] == root_tag:
        normalized_parts = normalized_parts[1:]
        if not normalized_parts:
            return elements[0]

    for element in elements:
        candidate_path = element.path[1:] if element.path and element.path[0] == root_tag else element.path
        if candidate_path == normalized_parts:
            return element
    return None


def _choose_attribute_quote(element: _XmlElementSpan) -> str:
    for attribute in element.attributes.values():
        if attribute.quote in {'"', "'"}:
            return attribute.quote
    return '"'


def _update_xml_attribute(text: str, element: _XmlElementSpan, attribute_name: str, value_text: str) -> tuple[str, bool]:
    attribute = element.attributes.get(attribute_name)
    if attribute is None:
        quote = _choose_attribute_quote(element)
        escaped_value = _escape_xml_attribute(value_text, quote)
        insertion = f" {attribute_name}={quote}{escaped_value}{quote}"
        return _replace_text_range(text, element.attribute_insert_at, element.attribute_insert_at, insertion), True

    previous_value = _unescape_xml_value(text[attribute.value_start:attribute.value_end])
    if previous_value == value_text:
        return text, False

    escaped_value = _escape_xml_attribute(value_text, attribute.quote)
    return _replace_text_range(text, attribute.value_start, attribute.value_end, escaped_value), True


def _update_xml_text(text: str, element: _XmlElementSpan, value_text: str) -> tuple[str, bool]:
    if element.self_closing:
        if not value_text:
            return text, False

        start_tag_body = text[element.start_tag_start:element.start_close_start].rstrip()
        escaped_value = _escape_xml_text(value_text)
        replacement = f"{start_tag_body}>{escaped_value}</{element.tag}>"
        return _replace_text_range(text, element.start_tag_start, element.start_tag_end, replacement), True

    if element.end_tag_start is None:
        raise ValueError(f"Missing XML closing tag for {'/'.join(element.path)}")

    current_inner = text[element.content_start:element.end_tag_start]
    if element.children or "<" in current_inner:
        raise ValueError(f"XML path {'/'.join(element.path)} does not map to a simple text node")

    previous_value = _unescape_xml_value(current_inner).strip()
    if previous_value == value_text:
        return text, False

    escaped_value = _escape_xml_text(value_text)
    return _replace_text_range(text, element.content_start, element.end_tag_start, escaped_value), True


def _apply_xml_settings_to_text(text: str, settings, logger=None, log_label: str | None = None) -> tuple[str, bool]:
    modified = False
    label = str(log_label or "XML")
    updated_text = text

    for target, value in settings.items():
        path_parts, attribute_name = _normalize_xml_setting_target(target)
        if not path_parts:
            continue

        elements = _parse_xml_elements(updated_text)
        element = _find_matching_element(elements, path_parts)
        joined_path = "/".join(path_parts)
        if element is None:
            _log_xml_message(logger, "warning", "%s skip missing XML path %s", label, joined_path)
            continue

        value_text = str(value)
        try:
            if attribute_name:
                updated_text, changed = _update_xml_attribute(updated_text, element, attribute_name, value_text)
                if changed:
                    modified = True
                    _log_xml_message(logger, "info", "%s edit %s/@%s -> %s", label, joined_path, attribute_name, value_text)
                continue

            updated_text, changed = _update_xml_text(updated_text, element, value_text)
            if changed:
                modified = True
                _log_xml_message(logger, "info", "%s edit %s -> %s", label, joined_path, value_text)
        except ValueError as exc:
            _log_xml_message(logger, "warning", "%s skip %s: %s", label, joined_path, exc)

    return updated_text, modified


def apply_xml_settings(xml_path, settings, logger=None, log_label: str | None = None, raise_on_error: bool = False) -> bool:
    if not settings:
        return False

    path = Path(xml_path)
    if not path.exists():
        return False

    label = str(log_label or path.name or "XML")
    try:
        xml_text, encoding_info = _read_xml_text_with_encoding(path, logger=logger)
        updated_text, modified = _apply_xml_settings_to_text(xml_text, settings, logger=logger, log_label=label)
        if not modified:
            return False

        _write_xml_text_with_encoding(path, updated_text, encoding_info)
        return True
    except Exception:
        if logger:
            logger.exception("Failed to apply XML settings in-place: %s", path)
        else:
            logging.exception("Failed to apply XML settings in-place: %s", path)
        if raise_on_error:
            raise
        return False
