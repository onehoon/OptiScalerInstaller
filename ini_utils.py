import csv
import io
import logging
import os
import re
import stat
import tempfile
import unicodedata
from pathlib import Path
from typing import Optional

from network_utils import build_retry_session


_file_session = build_retry_session()


def apply_ini_settings(ini_path, settings, force_frame_generation=False, logger=None):
    if not settings:
        return

    p = Path(ini_path)
    if not p.exists():
        return

    def _norm(s):
        if s is None:
            return s
        return "".join(str(s).split()).lower()

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
            sectioned_targets.setdefault(_norm(sec), {})[_norm(key)] = str(v)
        elif isinstance(k, str) and ":" in k:
            sec, key = k.split(":", 1)
            sectioned_targets.setdefault(_norm(sec), {})[_norm(key)] = str(v)
        else:
            unsectioned_targets[_norm(k)] = str(v)

    try:
        lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
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

    def _split_line_ending(line):
        if line.endswith("\r\n"):
            return line[:-2], "\r\n"
        if line.endswith("\n"):
            return line[:-1], "\n"
        if line.endswith("\r"):
            return line[:-1], "\r"
        return line, ""

    def _split_value_and_comment(rest):
        leading_ws_len = len(rest) - len(rest.lstrip())
        leading_ws = rest[:leading_ws_len]
        body = rest[leading_ws_len:]
        comment_positions = [i for i, ch in enumerate(body) if ch in {";", "#"}]
        if not comment_positions:
            return leading_ws, ""
        comment_start = min(comment_positions)
        return leading_ws, body[comment_start:]

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
        norm_key = _norm(_strip_wrapping_quotes(key_text))

        if norm_key == "depthinverted" and current_section != xefg_section_norm:
            updated_lines.append(original_line)
            continue

        new_value = None
        if current_section and current_section in sectioned_targets:
            new_value = sectioned_targets[current_section].get(norm_key)
        if new_value is None:
            new_value = unsectioned_targets.get(norm_key)

        if new_value is None:
            updated_lines.append(original_line)
            continue

        old_value_preview = str(old_rest).strip()

        if delimiter == "=":
            leading_ws, comment = _split_value_and_comment(old_rest)
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
        if logger:
            logger.info(
                "INI edit %s -> %s (was: %s) in %s",
                applied_key,
                new_value,
                old_value_preview if old_value_preview else "<empty>",
                ini_path,
            )

    try:
        p.write_text("".join(updated_lines), encoding="utf-8")
    except Exception:
        if logger:
            logger.exception("Failed to write updated INI file")
        else:
            logging.exception("Failed to write updated INI file")
        return

    if logger:
        logger.info("INI settings applied in-place: %s", applied)
    else:
        logging.info("INI settings applied in-place: %s", applied)


def _parse_version_text_to_ini_entries(version_text: str):
    result = {}
    if not version_text:
        return result

    for raw_line in str(version_text).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|") if p.strip()]
        if not parts:
            continue

        current_section = ""

        for token in parts:
            if token.startswith("[") and token.endswith("]"):
                current_section = token[1:-1].strip()
                continue

            if "=" in token:
                k, v = token.split("=", 1)
                k = k.strip()
                v = v.strip()
                if k:
                    result.setdefault(current_section, {})[k] = v
            elif ":" in token:
                k, v = token.split(":", 1)
                k = k.strip()
                v = v.strip()
                if k:
                    result.setdefault(current_section, {})[k] = v
            else:
                logging.warning("Skipping invalid engine.ini token (no '=' or ':'): %s", token)

    return result


def _ensure_file_writable(path: Path):
    try:
        cur_mode = path.stat().st_mode
        path.chmod(cur_mode | stat.S_IWRITE)
    except Exception:
        logging.exception("Failed to make %s writable", path)


def _set_file_readonly(path: Path):
    try:
        cur_mode = path.stat().st_mode
        path.chmod(cur_mode & ~stat.S_IWRITE)
    except Exception:
        logging.exception("Failed to set %s readonly", path)


def _get_engine_ini_path(raw_path: Optional[str], workspace_root: Optional[str] = None, logger=None) -> Optional[Path]:
    if not raw_path:
        return None

    raw = str(raw_path).strip()
    if not raw:
        return None

    try:
        raw = os.path.expandvars(raw)
    except Exception:
        if logger:
            logger.exception(f"Failed to expand env vars in engine.ini path: {raw}")
        else:
            logging.exception("Failed to expand env vars in engine.ini path: %s", raw)

    try:
        raw = os.path.expanduser(raw)
    except Exception:
        if logger:
            logger.exception(f"Failed to expand user in engine.ini path: {raw}")
        else:
            logging.exception("Failed to expand user in engine.ini path: %s", raw)

    p = Path(raw)
    if not p.is_absolute():
        base = Path(workspace_root) if workspace_root else Path.cwd()
        p = base.joinpath(p)

    try:
        p = p.resolve(strict=False)
    except Exception:
        p = Path(str(p))

    if p.suffix.lower() == ".ini" or p.name.lower() == "engine.ini":
        target = p
    else:
        target = p / "Engine.ini"

    parent = target.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        return target
    except PermissionError as e:
        msg = f"Permission denied creating {parent} (requested {raw_path}): {e}"
        if logger:
            logger.error(msg)
        else:
            logging.error("Permission denied creating %s (requested %s): %s", parent, raw_path, e)
    except OSError as e:
        msg = f"OS error creating {parent} (requested {raw_path}): {e}"
        if logger:
            logger.error(msg)
        else:
            logging.error("OS error creating %s (requested %s): %s", parent, raw_path, e)
    except Exception as exc:
        msg = f"Unexpected error creating {parent} for {raw_path}: {exc}"
        if logger:
            logger.exception(msg)
        else:
            logging.exception("Unexpected error creating %s for %s", parent, raw_path)

    try:
        la = os.environ.get("LOCALAPPDATA")
        if la:
            fallback = Path(la) / "OptiScalerInstaller" / target.name
            fallback.parent.mkdir(parents=True, exist_ok=True)
            if logger:
                logger.info(f"Falling back to LOCALAPPDATA for engine.ini: {fallback}")
            else:
                logging.info("Falling back to LOCALAPPDATA for engine.ini: %s", fallback)
            return fallback
    except Exception as exc:
        if logger:
            logger.exception(f"Failed to fall back to LOCALAPPDATA for engine.ini: {exc}")
        else:
            logging.exception("Failed to fall back to LOCALAPPDATA for engine.ini")

    try:
        fallback = Path(tempfile.gettempdir()) / "OptiScalerInstaller" / target.name
        fallback.parent.mkdir(parents=True, exist_ok=True)
        logging.info("Falling back to temp dir for engine.ini: %s", fallback)
        return fallback
    except Exception:
        logging.exception("Failed to fall back to temp dir for engine.ini")

    return None


def _find_or_create_engine_ini(folder_name: str, workspace_root: Optional[str] = None, logger=None) -> Optional[Path]:
    if workspace_root is None:
        workspace_root = os.getcwd()

    folder_raw = str(folder_name or "").strip()
    try:
        folder_raw = unicodedata.normalize("NFKC", folder_raw)
    except Exception:
        pass
    folder_raw = folder_raw.replace("\u00A0", " ").replace("\uFEFF", "").strip()
    if (folder_raw.startswith('"') and folder_raw.endswith('"')) or (
        folder_raw.startswith("'") and folder_raw.endswith("'")
    ):
        folder_raw = folder_raw[1:-1].strip()
    if not folder_raw:
        if logger:
            logger.info("Empty engine.ini_location provided")
        else:
            logging.info("Empty engine.ini_location provided")
        return None

    try:
        folder_raw = os.path.expandvars(folder_raw)
    except Exception:
        pass
    try:
        if "%" in folder_raw:
            def _replace_env(match):
                name = match.group(1).strip()
                val = os.environ.get(name) or os.environ.get(name.upper()) or os.environ.get(name.lower())
                if not val and name.upper() == "LOCALAPPDATA":
                    try:
                        val = str(Path.home() / "AppData" / "Local")
                    except Exception:
                        pass
                if val is None:
                    if logger:
                        logger.warning("Environment variable %s not found when expanding engine.ini path", name)
                    else:
                        logging.warning("Environment variable %s not found when expanding engine.ini path", name)
                    return match.group(0)
                return val

            folder_raw_new = re.sub(r"%([^%]+)%", _replace_env, folder_raw)
            if folder_raw_new != folder_raw:
                if logger:
                    logger.info("Expanded env vars in engine.ini path: %s -> %s", folder_raw, folder_raw_new)
                else:
                    logging.info("Expanded env vars in engine.ini path: %s -> %s", folder_raw, folder_raw_new)
                folder_raw = folder_raw_new
    except Exception:
        if logger:
            logger.exception("Failed while replacing %%VAR%% tokens in engine.ini path: %s", folder_raw)
        else:
            logging.exception("Failed while replacing %%VAR%% tokens in engine.ini path: %s", folder_raw)
    try:
        folder_raw = os.path.expanduser(folder_raw)
        p_in = Path(folder_raw)
    except Exception:
        p_in = None

    try:
        m_var = re.match(r"^%([^%]+)%(?:[\\/](.*))?$", folder_raw)
        if m_var:
            var = m_var.group(1)
            rest = m_var.group(2) or ""
            val = os.environ.get(var) or os.environ.get(var.upper()) or os.environ.get(var.lower())
            if val:
                expanded = os.path.normpath(os.path.join(val, rest)) if rest else os.path.normpath(val)
                if logger:
                    logger.info("Expanded leading env var in engine.ini path: %s -> %s", folder_raw, expanded)
                else:
                    logging.info("Expanded leading env var in engine.ini path: %s -> %s", folder_raw, expanded)
                folder_raw = expanded
                try:
                    p_in = Path(folder_raw)
                except Exception:
                    p_in = None
            else:
                if logger:
                    logger.warning("Environment variable %s not set for engine.ini path", var)
                else:
                    logging.warning("Environment variable %s not set for engine.ini path", var)
    except Exception:
        if logger:
            logger.exception("Error while expanding leading env var in engine.ini path: %s", folder_raw)
        else:
            logging.exception("Error while expanding leading env var in engine.ini path: %s", folder_raw)

    if p_in is not None and (p_in.suffix.lower() == ".ini" or p_in.name.lower() == "engine.ini"):
        target_dir = str(p_in.parent)
    else:
        if os.path.isabs(folder_raw):
            target_dir = folder_raw
        elif os.path.sep in folder_raw or (os.path.altsep and os.path.altsep in folder_raw):
            target_dir = os.path.normpath(os.path.join(workspace_root, folder_raw))
        else:
            target_dir = os.path.normpath(os.path.join(workspace_root, folder_raw))

    if logger:
        logger.info("Resolved engine.ini target_dir: %s from input: %s", target_dir, folder_raw)
    else:
        logging.info("Resolved engine.ini target_dir: %s from input: %s", target_dir, folder_raw)

    try:
        Path(target_dir).mkdir(parents=True, exist_ok=True)
    except Exception:
        if logger:
            logger.exception("Failed to ensure target directory for engine.ini: %s", target_dir)
        else:
            logging.exception("Failed to ensure target directory for engine.ini: %s", target_dir)
        return None

    try:
        for fname in os.listdir(target_dir):
            if fname.lower() == "engine.ini":
                p_existing = Path(os.path.join(target_dir, fname))
                if logger:
                    logger.info("Engine.ini already existed: %s", p_existing)
                else:
                    logging.info("Found existing Engine.ini: %s", p_existing)
                return p_existing
    except Exception:
        if logger:
            logger.exception("Failed to list directory for engine.ini: %s", target_dir)
        else:
            logging.exception("Failed to list directory for engine.ini: %s", target_dir)

    p = Path(os.path.join(target_dir, "Engine.ini"))
    try:
        p.write_text("", encoding="utf-8")
        if logger:
            logger.info("Engine.ini did not exist, created new file: %s", p)
        else:
            logging.info("Created new INI: %s", p)
        return p
    except Exception:
        if logger:
            logger.exception("Failed to create Engine.ini at %s", p)
        else:
            logging.exception("Failed to create Engine.ini at %s", p)
        return None


def _upsert_ini_entries(ini_path: Path, section_map: dict, logger=None):
    try:
        if not ini_path.exists():
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
                return

        try:
            _ensure_file_writable(ini_path)
        except Exception:
            logging.exception("Failed to make INI writable before upsert: %s", ini_path)

        try:
            text = ini_path.read_text(encoding="utf-8")
        except Exception:
            if logger:
                logger.exception("Failed to read INI for upsert (will proceed with empty content): %s", ini_path)
            else:
                logging.exception("Failed to read INI for upsert (will proceed with empty content): %s", ini_path)
            text = ""
    except Exception:
        if logger:
            logger.exception("Unexpected error preparing INI for upsert: %s", ini_path)
        else:
            logging.exception("Unexpected error preparing INI for upsert: %s", ini_path)
        return

    lines = text.splitlines(keepends=True)
    section_pattern = re.compile(r"^\s*\[([^\]]+)\]")

    def _norm_section(s):
        return str(s or "").strip().lower()

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

    modified = False

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
        norm_sec = _norm_section(sec)
        if norm_sec == "":
            insert_pos = 0
            for key, value in kvs.items():
                found = _find_key_in_range(key, 0, len(lines))
                if found is not None:
                    ending = "\n" if lines[found].endswith("\n") else ""
                    previous_value = lines[found].strip()
                    lines[found] = f"{key}={value}{ending}"
                    modified = True
                    if logger:
                        logger.info(
                            "Engine.ini edit %s=%s (was: %s) in %s",
                            key,
                            value,
                            previous_value if previous_value else "<empty>",
                            ini_path,
                        )
                else:
                    lines.insert(insert_pos, f"{key}={value}\n")
                    insert_pos += 1
                    modified = True
                    if logger:
                        logger.info("Engine.ini add %s=%s in %s", key, value, ini_path)
            continue

        if norm_sec in sections:
            start, end = sections[norm_sec]
            insert_at = end
            for key, value in kvs.items():
                found = _find_key_in_range(key, start, end)
                if found is not None:
                    ending = "\n" if lines[found].endswith("\n") else ""
                    prefix = re.match(r"^(\s*)", lines[found]).group(1)
                    previous_value = lines[found].strip()
                    lines[found] = f"{prefix}{key}={value}{ending}"
                    modified = True
                    if logger:
                        logger.info(
                            "Engine.ini edit [%s] %s=%s (was: %s) in %s",
                            sec,
                            key,
                            value,
                            previous_value if previous_value else "<empty>",
                            ini_path,
                        )
                else:
                    lines.insert(insert_at, f"{key}={value}\n")
                    insert_at += 1
                    modified = True
                    if logger:
                        logger.info("Engine.ini add [%s] %s=%s in %s", sec, key, value, ini_path)
        else:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] = lines[-1] + "\n"
            lines.append(f"[{sec}]\n")
            if logger:
                logger.info("Engine.ini add section [%s] in %s", sec, ini_path)
            for key, value in kvs.items():
                lines.append(f"{key}={value}\n")
                if logger:
                    logger.info("Engine.ini add [%s] %s=%s in %s", sec, key, value, ini_path)
            modified = True

    if modified:
        try:
            ini_path.write_text("".join(lines), encoding="utf-8")
            if logger:
                logger.info("Upserted INI entries into %s", ini_path)
            else:
                logging.info("Upserted INI entries into %s", ini_path)
        except Exception:
            if logger:
                logger.exception("Failed to write updated INI: %s", ini_path)
            else:
                logging.exception("Failed to write updated INI: %s", ini_path)


def process_engine_ini_edits(spreadsheet_id: str, gid: int = 0, workspace_root: Optional[str] = None):
    url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv&gid={gid}"
    resp = _file_session.get(url, timeout=15)
    resp.raise_for_status()
    text = resp.content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text, newline=""))
    headers = next(reader, None)
    if not headers:
        logging.warning("Sheet has no headers for engine.ini processing")
        return

    cols = [h.strip().lower() for h in headers]
    loc_idx = next((i for i, c in enumerate(cols) if c in {"engine.ini_location", "engine_ini_location", "engine location", "engine_location"}), None)
    type_idx = next((i for i, c in enumerate(cols) if c in {"engine.ini_type", "engine_ini_type", "engine type", "engine_type"}), None)

    if loc_idx is None or type_idx is None:
        logging.info("No engine.ini_location or engine.ini_type column found; skipping")
        return

    for row in reader:
        if not row or len(row) <= max(loc_idx, type_idx):
            continue
        loc = str(row[loc_idx]).strip()
        content = str(row[type_idx]).strip()
        if not loc:
            continue

        ini_path = _find_or_create_engine_ini(loc, workspace_root=workspace_root)
        if ini_path is None:
            continue

        try:
            _ensure_file_writable(ini_path)
            if content:
                section_map = _parse_version_text_to_ini_entries(content)
                if section_map:
                    _upsert_ini_entries(ini_path, section_map)
            else:
                logging.info("Engine.ini type content is empty; nothing to write")
        finally:
            _set_file_readonly(ini_path)
