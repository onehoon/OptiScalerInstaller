import codecs
import logging
import locale
import os
import re
import stat
from pathlib import Path


_INI_BOM_ENCODINGS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def _iter_ini_fallback_encodings():
    seen = set()
    for encoding in ("utf-8", locale.getpreferredencoding(False), "mbcs" if os.name == "nt" else None, "cp949"):
        normalized = str(encoding or "").strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        yield encoding


def _read_ini_text_with_fallback(path: Path, logger=None) -> tuple[str, str]:
    raw = path.read_bytes()
    for bom, encoding in _INI_BOM_ENCODINGS:
        if raw.startswith(bom):
            text = raw.decode(encoding)
            if logger:
                logger.info("Read INI %s using BOM-detected encoding %s", path, encoding)
            elif encoding != "utf-8":
                logging.info("Read INI %s using BOM-detected encoding %s", path, encoding)
            return text, encoding

    last_error = None
    for encoding in _iter_ini_fallback_encodings():
        try:
            text = raw.decode(encoding)
            if logger and encoding != "utf-8":
                logger.info("Read INI %s using fallback encoding %s", path, encoding)
            elif encoding != "utf-8":
                logging.info("Read INI %s using fallback encoding %s", path, encoding)
            return text, encoding
        except UnicodeDecodeError as exc:
            last_error = exc

    if last_error is not None:
        raise last_error
    return raw.decode("utf-8"), "utf-8"


def _write_ini_text_with_encoding(path: Path, text: str, encoding: str) -> None:
    # Preserve the line endings we already reconstructed from the INI file.
    # The default text-mode newline translation on Windows can turn "\r\n"
    # into "\r\r\n", which shows up as blank lines between every entry.
    with path.open("w", encoding=encoding, newline="") as handle:
        handle.write(text)


def _get_ini_preferred_newline(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    if "\n" in text:
        return "\n"
    if "\r" in text:
        return "\r"
    return "\r\n" if os.name == "nt" else "\n"


def _get_line_ending(line: str, default: str = "") -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    if line.endswith("\r"):
        return "\r"
    return default


def _norm(s):
    if s is None:
        return s
    return "".join(str(s).split()).lower()


def _split_line_ending(line):
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    if line.endswith("\r"):
        return line[:-1], "\r"
    return line, ""


def _split_ini_value_and_comment(rest: str) -> tuple[str, str, str]:
    leading_ws_len = len(rest) - len(rest.lstrip())
    leading_ws = rest[:leading_ws_len]
    body = rest[leading_ws_len:]
    comment_positions = [i for i, ch in enumerate(body) if ch in {";", "#"}]
    if not comment_positions:
        return leading_ws, body, ""

    comment_start = min(comment_positions)
    return leading_ws, body[:comment_start].rstrip(), body[comment_start:]


def _split_top_level_comma_parts(text: str) -> list[str]:
    if not text:
        return [""]

    parts: list[str] = []
    start = 0
    depth = 0
    quote_char = ""
    escaping = False

    for index, char in enumerate(text):
        if quote_char:
            if escaping:
                escaping = False
                continue
            if char == "\\":
                escaping = True
                continue
            if char == quote_char:
                quote_char = ""
            continue

        if char in {'"', "'"}:
            quote_char = char
            continue
        if char == "(":
            depth += 1
            continue
        if char == ")":
            depth -= 1
            continue
        if char == "," and depth == 0:
            parts.append(text[start:index])
            start = index + 1

    parts.append(text[start:])
    return parts


def _unwrap_parenthesized_value(text: str) -> tuple[str, str, str] | None:
    stripped = text.strip()
    if len(stripped) < 2 or not stripped.startswith("(") or not stripped.endswith(")"):
        return None

    leading_len = len(text) - len(text.lstrip())
    trailing_len = len(text) - len(text.rstrip())
    leading_ws = text[:leading_len]
    trailing_ws = text[len(text) - trailing_len:] if trailing_len else ""
    return leading_ws, stripped[1:-1], trailing_ws


def _replace_unreal_struct_field(value_text: str, field_name: str, new_value: str) -> str | None:
    unwrapped = _unwrap_parenthesized_value(value_text)
    if unwrapped is None:
        return None

    leading_ws, body, trailing_ws = unwrapped
    parts = _split_top_level_comma_parts(body)
    field_pattern = re.compile(
        r"^(?P<prefix>\s*)(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<separator>\s*=\s*)(?P<value>.*?)(?P<suffix>\s*)$",
        re.DOTALL,
    )

    for index, part in enumerate(parts):
        match = field_pattern.match(part)
        if not match or match.group("name") != field_name:
            continue
        parts[index] = (
            f"{match.group('prefix')}{match.group('name')}"
            f"{match.group('separator')}{new_value}{match.group('suffix')}"
        )
        return f"{leading_ws}({','.join(parts)}){trailing_ws}"

    return None


def _replace_unreal_tuple_map_value(
    value_text: str,
    tuple_field: str,
    entry_name: str,
    new_value: str,
) -> str | None:
    outer = _unwrap_parenthesized_value(value_text)
    if outer is None:
        return None
    leading_ws, body, trailing_ws = outer
    top_level_parts = _split_top_level_comma_parts(body)
    field_pattern = re.compile(
        r"^(?P<prefix>\s*)(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<separator>\s*=\s*)(?P<value>.*?)(?P<suffix>\s*)$",
        re.DOTALL,
    )
    tuple_entry_pattern = re.compile(
        r"^(?P<prefix>\s*\(\s*)(?P<quote>[\"'])(?P<name>.*?)(?P=quote)"
        r"(?P<separator>\s*,\s*)(?P<value>.*?)(?P<suffix>\s*\)\s*)$",
        re.DOTALL,
    )

    for index, part in enumerate(top_level_parts):
        field_match = field_pattern.match(part)
        if not field_match or field_match.group("name") != tuple_field:
            continue

        tuple_value = field_match.group("value")
        tuple_unwrapped = _unwrap_parenthesized_value(tuple_value)
        if tuple_unwrapped is None:
            return None

        tuple_leading_ws, tuple_body, tuple_trailing_ws = tuple_unwrapped
        tuple_parts = _split_top_level_comma_parts(tuple_body)
        for tuple_index, tuple_part in enumerate(tuple_parts):
            tuple_match = tuple_entry_pattern.match(tuple_part)
            if not tuple_match or tuple_match.group("name") != entry_name:
                continue
            tuple_parts[tuple_index] = (
                f"{tuple_match.group('prefix')}{tuple_match.group('quote')}{tuple_match.group('name')}"
                f"{tuple_match.group('quote')}{tuple_match.group('separator')}{new_value}{tuple_match.group('suffix')}"
            )
            rebuilt_tuple_value = f"{tuple_leading_ws}({','.join(tuple_parts)}){tuple_trailing_ws}"
            top_level_parts[index] = (
                f"{field_match.group('prefix')}{field_match.group('name')}"
                f"{field_match.group('separator')}{rebuilt_tuple_value}{field_match.group('suffix')}"
            )
            return f"{leading_ws}({','.join(top_level_parts)}){trailing_ws}"
        return None

    return None


def _apply_unreal_value_path(value_text: str, value_path: str, new_value: str) -> str | None:
    normalized_path = str(value_path or "").strip()
    if not normalized_path:
        return None

    tuple_selector_match = re.fullmatch(
        r'(?P<field>[A-Za-z_][A-Za-z0-9_]*)\[(?P<quote>[\"\'])(?P<entry>.*?)(?P=quote)\]',
        normalized_path,
        re.DOTALL,
    )
    if tuple_selector_match:
        return _replace_unreal_tuple_map_value(
            value_text,
            tuple_selector_match.group("field"),
            tuple_selector_match.group("entry"),
            new_value,
        )

    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", normalized_path):
        return _replace_unreal_struct_field(value_text, normalized_path, new_value)

    return None


def apply_ini_settings(
    ini_path,
    settings,
    logger=None,
    *,
    allow_add_key: bool = False,
    allow_add_section: bool = False,
):
    if not settings:
        return

    p = Path(ini_path)
    if not p.exists():
        return

    def _strip_wrapping_quotes(s):
        text = str(s).strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
            return text[1:-1].strip()
        return text

    sectioned_targets = {}
    unsectioned_targets = {}
    for k, v in settings.items():
        if isinstance(k, (list, tuple)) and len(k) == 2:
            sec, key = k[0], k[1]
            sectioned_targets.setdefault(_norm(sec), {})[_norm(key)] = (
                str(sec),
                str(key),
                str(v),
            )
        elif isinstance(k, str) and ":" in k:
            sec, key = k.split(":", 1)
            sectioned_targets.setdefault(_norm(sec), {})[_norm(key)] = (
                str(sec),
                str(key),
                str(v),
            )
        else:
            unsectioned_targets[_norm(k)] = (str(k), str(v))

    try:
        ini_text, ini_encoding = _read_ini_text_with_fallback(p, logger=logger)
        lines = ini_text.splitlines(keepends=True)
    except Exception:
        if logger:
            logger.exception("Failed to read INI for in-place update")
        else:
            logging.exception("Failed to read INI for in-place update")
        return

    section_pattern = re.compile(r"^\s*\[([^\]]+)\]\s*(?:[;#].*)?$")
    key_value_pattern = re.compile(r"^(\s*)([^=;#\r\n]+?)(\s*)=(.*)$")
    key_colon_pattern = re.compile(r"^(\s*)([^:\r\n]+?)(\s*):(.*)$")
    xefg_section_norm = _norm("XeFG")

    updated_lines = []
    applied = []
    applied_sectioned = {}
    applied_unsectioned = set()
    current_section = None

    for original_line in lines:
        line_body, line_ending = _split_line_ending(original_line)
        stripped = line_body.strip()

        if not stripped or stripped.startswith(";") or stripped.startswith("#"):
            updated_lines.append(original_line)
            continue

        section_match = section_pattern.match(line_body)
        if section_match:
            current_section = _norm(section_match.group(1))
            updated_lines.append(original_line)
            continue

        kv_match = key_value_pattern.match(line_body)
        delimiter = "="
        if not kv_match:
            kv_match = key_colon_pattern.match(line_body)
            delimiter = ":"
        if not kv_match:
            updated_lines.append(original_line)
            continue

        prefix, key_text, key_space_before_delim, old_rest = kv_match.groups()
        norm_key = _norm(_strip_wrapping_quotes(key_text))

        if norm_key == "depthinverted" and current_section != xefg_section_norm:
            updated_lines.append(original_line)
            continue

        new_value = None
        matched_section = None
        matched_unsectioned = False
        if current_section and current_section in sectioned_targets:
            target = sectioned_targets[current_section].get(norm_key)
            if target is not None:
                _sec_text, _key_text, new_value = target
                matched_section = current_section
        if new_value is None:
            target = unsectioned_targets.get(norm_key)
            if target is not None:
                _key_text, new_value = target
                matched_unsectioned = True

        if new_value is None:
            updated_lines.append(original_line)
            continue

        if delimiter == "=":
            leading_ws, _old_value, comment = _split_ini_value_and_comment(old_rest)
            rebuilt_rest = f"{leading_ws}{new_value}"
            if comment:
                rebuilt_rest += f" {comment}"
        else:
            leading_ws_len = len(old_rest) - len(old_rest.lstrip())
            leading_ws = old_rest[:leading_ws_len]
            has_trailing_comma = old_rest.strip().endswith(",")
            rebuilt_rest = f"{leading_ws}{new_value}"
            if has_trailing_comma:
                rebuilt_rest += ","

        updated_lines.append(
            f"{prefix}{key_text}{key_space_before_delim}{delimiter}{rebuilt_rest}{line_ending}"
        )

        applied_key = f"{current_section}:{norm_key}" if current_section else norm_key
        applied.append(applied_key)
        if matched_section is not None:
            applied_sectioned.setdefault(matched_section, set()).add(norm_key)
        elif matched_unsectioned:
            applied_unsectioned.add(norm_key)
        if logger:
            ini_label = p.name or "INI"
            logger.info(
                "%s edit %s -> %s",
                ini_label,
                applied_key,
                new_value,
            )

    missing_section_map = {}
    if allow_add_key:
        for section_name, key_map in sectioned_targets.items():
            for norm_key, (raw_section, raw_key, raw_value) in key_map.items():
                if norm_key in applied_sectioned.get(section_name, set()):
                    continue
                missing_section_map.setdefault(raw_section, {})[raw_key] = raw_value
        for norm_key, (raw_key, raw_value) in unsectioned_targets.items():
            if norm_key in applied_unsectioned:
                continue
            missing_section_map.setdefault("", {})[raw_key] = raw_value

    if applied:
        try:
            _write_ini_text_with_encoding(p, "".join(updated_lines), ini_encoding)
        except Exception:
            if logger:
                logger.exception("Failed to write updated INI file")
            else:
                logging.exception("Failed to write updated INI file")
            return

    if missing_section_map:
        upsert_ini_entries(
            p,
            missing_section_map,
            logger=logger,
            create_missing_file=False,
            allow_edit_existing=False,
            allow_add_key=True,
            allow_add_section=allow_add_section,
        )


def apply_unreal_ini_settings(ini_path, settings, logger=None):
    if not settings:
        return

    p = Path(ini_path)
    if not p.exists():
        return

    grouped_targets: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for target, value in settings.items():
        if not isinstance(target, (list, tuple)) or len(target) != 3:
            continue
        section, key, value_path = target
        normalized_target = (_norm(section), _norm(key))
        grouped_targets.setdefault(normalized_target, []).append((str(value_path or "").strip(), str(value)))

    if not grouped_targets:
        return

    try:
        ini_text, ini_encoding = _read_ini_text_with_fallback(p, logger=logger)
        lines = ini_text.splitlines(keepends=True)
    except Exception:
        if logger:
            logger.exception("Failed to read Unreal INI for in-place update")
        else:
            logging.exception("Failed to read Unreal INI for in-place update")
        return

    section_pattern = re.compile(r"^\s*\[([^\]]+)\]\s*(?:[;#].*)?$")
    key_value_pattern = re.compile(r"^(\s*)([^=;#\r\n]+?)(\s*)=(.*)$")
    key_colon_pattern = re.compile(r"^(\s*)([^:\r\n]+?)(\s*):(.*)$")

    updated_lines = []
    applied = []
    current_section = None

    for original_line in lines:
        line_body, line_ending = _split_line_ending(original_line)
        stripped = line_body.strip()

        if not stripped or stripped.startswith(";") or stripped.startswith("#"):
            updated_lines.append(original_line)
            continue

        section_match = section_pattern.match(line_body)
        if section_match:
            current_section = _norm(section_match.group(1))
            updated_lines.append(original_line)
            continue

        kv_match = key_value_pattern.match(line_body)
        delimiter = "="
        if not kv_match:
            kv_match = key_colon_pattern.match(line_body)
            delimiter = ":"
        if not kv_match:
            updated_lines.append(original_line)
            continue

        prefix, key_text, key_space_before_delim, old_rest = kv_match.groups()
        normalized_target = (current_section, _norm(key_text))
        value_path_updates = grouped_targets.get(normalized_target)
        if not current_section or not value_path_updates or delimiter != "=":
            updated_lines.append(original_line)
            continue

        leading_ws, old_value, comment = _split_ini_value_and_comment(old_rest)
        rebuilt_value = old_value
        changed = False

        for value_path, new_value in value_path_updates:
            updated_value = _apply_unreal_value_path(rebuilt_value, value_path, new_value)
            if updated_value is None:
                if logger:
                    logger.warning(
                        "%s skip missing Unreal INI path %s in %s:%s",
                        p.name or "INI",
                        value_path,
                        current_section,
                        _norm(key_text),
                    )
                continue

            rebuilt_value = updated_value
            changed = True
            applied_key = f"{current_section}:{_norm(key_text)}:{value_path}"
            applied.append(applied_key)
            if logger:
                logger.info("%s edit %s -> %s", p.name or "INI", applied_key, new_value)

        if not changed:
            updated_lines.append(original_line)
            continue

        rebuilt_rest = f"{leading_ws}{rebuilt_value}"
        if comment:
            rebuilt_rest += f" {comment}"
        updated_lines.append(
            f"{prefix}{key_text}{key_space_before_delim}{delimiter}{rebuilt_rest}{line_ending}"
        )

    if not applied:
        return

    try:
        _write_ini_text_with_encoding(p, "".join(updated_lines), ini_encoding)
    except Exception:
        if logger:
            logger.exception("Failed to write updated Unreal INI file")
        else:
            logging.exception("Failed to write updated Unreal INI file")
        return


def ensure_file_writable(path: Path) -> None:
    try:
        cur_mode = path.stat().st_mode
        path.chmod(cur_mode | stat.S_IWRITE)
    except Exception:
        logging.exception("Failed to make %s writable", path)


def set_file_readonly(path: Path) -> None:
    try:
        cur_mode = path.stat().st_mode
        path.chmod(cur_mode & ~stat.S_IWRITE)
    except Exception:
        logging.exception("Failed to set %s readonly", path)


def upsert_ini_entries(
    ini_path: Path,
    section_map: dict,
    logger=None,
    *,
    create_missing_file: bool = True,
    allow_edit_existing: bool = True,
    allow_add_key: bool = True,
    allow_add_section: bool = True,
) -> bool:
    try:
        if not ini_path.exists():
            if not create_missing_file:
                return False
            try:
                ini_path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                logging.debug("Parent dir create skipped or failed for: %s", ini_path.parent)
            try:
                ini_path.write_text("", encoding="utf-8")
                if logger:
                    logger.info("Created missing INI file for upsert: %s", ini_path)
                else:
                    logging.info("Created missing INI file for upsert: %s", ini_path)
            except Exception:
                if logger:
                    logger.exception("Failed to create missing INI file: %s", ini_path)
                else:
                    logging.exception("Failed to create missing INI file: %s", ini_path)
                return False

        try:
            ensure_file_writable(ini_path)
        except Exception:
            logging.exception("Failed to make INI writable before upsert: %s", ini_path)

        try:
            text, ini_encoding = _read_ini_text_with_fallback(ini_path, logger=logger)
        except Exception:
            if logger:
                logger.exception("Failed to read INI for upsert: %s", ini_path)
            else:
                logging.exception("Failed to read INI for upsert: %s", ini_path)
            return False
    except Exception:
        if logger:
            logger.exception("Unexpected error preparing INI for upsert: %s", ini_path)
        else:
            logging.exception("Unexpected error preparing INI for upsert: %s", ini_path)
        return False

    lines = text.splitlines(keepends=True)
    preferred_newline = _get_ini_preferred_newline(text)
    section_pattern = re.compile(r"^\s*\[([^\]]+)\]")

    def _norm_section(s):
        return str(s or "").strip().lower()

    def _collect_sections():
        sections = {}
        current = ""
        start_idx = 0
        for i, raw in enumerate(lines):
            m = section_pattern.match(raw)
            if m:
                sec = _norm_section(m.group(1))
                if current != "":
                    sections[current] = (start_idx, i)
                current = sec
                start_idx = i
        if current != "":
            sections[current] = (start_idx, len(lines))
        return sections

    modified = False
    ini_label = ini_path.name or "INI"

    def _format_ini_log_key(sec, key):
        sec_text = str(sec or "").strip()
        return f"{sec_text}:{key}" if sec_text else str(key)

    def _norm_key_for_ini(k):
        return str(k or "").replace('"', '').replace("'", '').replace(' ', '').strip().lower()

    def _find_key_in_range(key, start, end):
        key_norm = _norm_key_for_ini(key)
        kv_re = re.compile(r"^\s*([\"']?)(.+?)\1\s*[:=]")
        for idx in range(start, end):
            m = kv_re.match(lines[idx])
            if m:
                k = _norm_key_for_ini(m.group(2))
                if k == key_norm:
                    return idx
        return None

    for sec, kvs in section_map.items():
        sections = _collect_sections()
        norm_sec = _norm_section(sec)
        if norm_sec == "":
            insert_pos = 0
            for key, value in kvs.items():
                found = _find_key_in_range(key, 0, len(lines))
                if found is not None:
                    if not allow_edit_existing:
                        continue
                    ending = _get_line_ending(lines[found], preferred_newline)
                    lines[found] = f"{key}={value}{ending}"
                    modified = True
                    if logger:
                        logger.info("%s edit %s -> %s", ini_label, _format_ini_log_key(sec, key), value)
                else:
                    if not allow_add_key:
                        if logger:
                            logger.warning("%s skip missing INI key %s", ini_label, key)
                        continue
                    lines.insert(insert_pos, f"{key}={value}{preferred_newline}")
                    insert_pos += 1
                    modified = True
                    if logger:
                        logger.info("%s add %s -> %s", ini_label, _format_ini_log_key(sec, key), value)
            continue

        if norm_sec in sections:
            start, end = sections[norm_sec]
            insert_at = end
            for key, value in kvs.items():
                found = _find_key_in_range(key, start, end)
                if found is not None:
                    if not allow_edit_existing:
                        continue
                    ending = _get_line_ending(lines[found], preferred_newline)
                    prefix = re.match(r"^(\s*)", lines[found]).group(1)
                    lines[found] = f"{prefix}{key}={value}{ending}"
                    modified = True
                    if logger:
                        logger.info("%s edit %s -> %s", ini_label, _format_ini_log_key(sec, key), value)
                else:
                    if not allow_add_key:
                        if logger:
                            logger.warning("%s skip missing INI key %s in [%s]", ini_label, key, sec)
                        continue
                    lines.insert(insert_at, f"{key}={value}{preferred_newline}")
                    insert_at += 1
                    modified = True
                    if logger:
                        logger.info("%s add %s -> %s", ini_label, _format_ini_log_key(sec, key), value)
        else:
            if not allow_add_section:
                if logger:
                    logger.warning("%s skip missing INI section [%s]", ini_label, sec)
                continue
            if lines and not _get_line_ending(lines[-1]):
                lines[-1] = lines[-1] + preferred_newline
            lines.append(f"[{sec}]{preferred_newline}")
            if logger:
                logger.info("%s add section [%s]", ini_label, sec)
            for key, value in kvs.items():
                lines.append(f"{key}={value}{preferred_newline}")
                if logger:
                    logger.info("%s add %s -> %s", ini_label, _format_ini_log_key(sec, key), value)
            modified = True

    if modified:
        try:
            _write_ini_text_with_encoding(ini_path, "".join(lines), ini_encoding)
        except Exception:
            if logger:
                logger.exception("Failed to write updated INI: %s", ini_path)
            else:
                logging.exception("Failed to write updated INI: %s", ini_path)
            return False
    return modified


