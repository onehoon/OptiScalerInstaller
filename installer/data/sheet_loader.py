import csv
import io
import logging
import re
import time
import unicodedata
from typing import Optional
from urllib.parse import parse_qs, urlparse

from ..common.cover_utils import normalize_cover_filename
from ..common.network_utils import get_shared_retry_session


_file_session = get_shared_retry_session()


def _split_match_files(match_text: str) -> list[str]:
    seen = set()
    match_files = []
    for token in str(match_text or "").split("|"):
        normalized = token.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        match_files.append(normalized)
    return match_files


def _pick_match_anchor(match_files: list[str]) -> str:
    for token in match_files:
        if token.endswith(".exe"):
            return token
    return match_files[0] if match_files else ""


def _parse_ini_header_target(var_name: str) -> tuple[str, str] | None:
    raw_name = str(var_name or "").strip()
    if not raw_name:
        return None

    if "|" in raw_name:
        section, key = raw_name.split("|", 1)
        section = section.strip().strip("[]")
        key = key.strip()
        if section and key:
            return section, key
        return None

    bracket_match = re.match(r"^\s*\[([^\]]+)\]\s*(.+?)\s*$", raw_name)
    if bracket_match:
        section = bracket_match.group(1).strip()
        key = bracket_match.group(2).strip()
        if section and key:
            return section, key

    return None


def load_game_db_from_public_sheet(spreadsheet_id, gid=0):
    url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv&gid={gid}"

    max_attempts = 3
    backoff_base = 1.0
    response = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = _file_session.get(url, timeout=15)
            response.raise_for_status()
            break
        except Exception:
            if attempt < max_attempts:
                sleep_for = backoff_base * (2 ** (attempt - 1))
                try:
                    time.sleep(sleep_for)
                except Exception:
                    pass
            else:
                raise

    text = response.content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text, newline=""))
    headers = next(reader, None)
    if not headers:
        raise ValueError("DB has no header row")

    columns = [c.strip().lower() for c in headers]

    popup_kr_keys = ["popup_kr", "popup message kr", "popup message_kr", "popupkr", "popup_kr_message"]
    popup_en_keys = ["popup_en", "popup message en", "popup message_en", "popupen", "popup_en_message"]
    popup_kr_col = next((c for c in columns if c in popup_kr_keys), None)
    popup_en_col = next((c for c in columns if c in popup_en_keys), None)
    popup_kr_index = columns.index(popup_kr_col) if popup_kr_col else None
    popup_en_index = columns.index(popup_en_col) if popup_en_col else None

    after_popup_kr_keys = ["after_popup_kr", "after popup kr", "afterpopupkr"]
    after_popup_en_keys = ["after_popup_en", "after popup en", "afterpopupen"]
    after_popup_kr_col = next((c for c in columns if c in after_popup_kr_keys), None)
    after_popup_en_col = next((c for c in columns if c in after_popup_en_keys), None)
    after_popup_kr_index = columns.index(after_popup_kr_col) if after_popup_kr_col else None
    after_popup_en_index = columns.index(after_popup_en_col) if after_popup_en_col else None

    guidepage_keys = ["guidepage_after_installation", "guide_page_after_installation", "guidepage", "after_installation_guide", "guide_url", "after_install_url"]
    guidepage_col = next((c for c in columns if c in guidepage_keys), None)
    guidepage_index = columns.index(guidepage_col) if guidepage_col else None

    exe_keys = ["exe", "exe_name", "filename", "game_exe", "executable", "gamefile"]
    display_keys = ["display", "game_name", "gamename", "name", "title", "display_name"]
    dll_keys = ["dll_name", "dll", "dllname", "rename_dll", "target_dll"]
    ultimate_asi_loader_keys = [
        "ultimateasiloader",
        "ultimate_asi_loader",
        "ultimate asi loader",
        "use_ultimate_asi_loader",
    ]
    optipatcher_keys = ["optipatcher", "opti_patcher", "use_optipatcher", "opti patcher"]
    specialk_keys = ["specialk", "special_k", "special k", "use_specialk", "use_special_k"]
    unreal5_keys = ["unreal5", "unreal_5", "unreal5_url", "unreal5 patch", "unreal5_patch"]
    reframework_keys = ["reframework", "reframework_url", "re_framework", "re_framework_url"]
    information_keys = ["#information", "information", "info", "game_information"]
    cover_keys = ["cover_image", "cover", "poster", "poster_url", "image_url", "cover_url"]
    filename_cover_keys = ["filename_cover", "cover_filename", "poster_filename"]
    module_dl_keys = ["module_dl", "module", "module_name"]
    ingame_ini_keys = ["#ingame_ini", "ingame_ini", "in_game_ini"]
    ingame_setting_keys = ["#ingame_setting", "ingame_setting", "in_game_setting", "#ingame_settings", "ingame_settings"]
    engine_ini_location_keys = ["engine.ini_location", "engine_ini_location", "engine location", "engine_location", "engine_folder", "engine folder"]
    engine_ini_type_keys = ["engine.ini_type", "engine_ini_type", "engine type", "engine_type"]
    display_kr_keys = ["game_name_kr", "display_kr", "name_kr"]
    information_kr_keys = ["#information_kr", "information_kr", "info_kr"]
    supported_gpu_keys = ["supported_gpu", "supported gpu", "supported_gpus", "supported gpus", "gpu_support", "gpu support"]
    exe_col = next((c for c in columns if c in exe_keys), None)
    display_col = next((c for c in columns if c in display_keys), None)
    dll_col = next((c for c in columns if c in dll_keys), None)
    ultimate_asi_loader_col = next((c for c in columns if c in ultimate_asi_loader_keys), None)
    optipatcher_col = next((c for c in columns if c in optipatcher_keys), None)
    specialk_col = next((c for c in columns if c in specialk_keys), None)
    unreal5_col = next((c for c in columns if c in unreal5_keys), None)
    reframework_col = next((c for c in columns if c in reframework_keys), None)
    information_col = next((c for c in columns if c in information_keys), None)
    cover_col = next((c for c in columns if c in cover_keys), None)
    filename_cover_col = next((c for c in columns if c in filename_cover_keys), None)
    module_dl_col = next((c for c in columns if c in module_dl_keys), None)
    ingame_ini_col = next((c for c in columns if c in ingame_ini_keys), None)
    ingame_setting_col = next((c for c in columns if c in ingame_setting_keys), None)
    engine_ini_location_col = next((c for c in columns if c in engine_ini_location_keys), None)
    engine_ini_type_col = next((c for c in columns if c in engine_ini_type_keys), None)
    display_kr_col = next((c for c in columns if c in display_kr_keys), None)
    information_kr_col = next((c for c in columns if c in information_kr_keys), None)
    supported_gpu_col = next((c for c in columns if c in supported_gpu_keys), None)
    if exe_col is None:
        exe_col = next((c for c in columns if "exe" in c or "file" in c), None)
    if display_col is None:
        display_col = next((c for c in columns if "name" in c or "title" in c), None)
    if dll_col is None:
        dll_col = next((c for c in columns if "dll" in c), None)
    if ultimate_asi_loader_col is None:
        ultimate_asi_loader_col = next((c for c in columns if "ultimate" in c and "asi" in c), None)
    if optipatcher_col is None:
        optipatcher_col = next((c for c in columns if "opti" in c and "patcher" in c), None)
    if specialk_col is None:
        specialk_col = next((c for c in columns if "special" in c and "k" in c), None)
    if unreal5_col is None:
        unreal5_col = next((c for c in columns if "unreal5" in c or ("unreal" in c and "5" in c)), None)
    if reframework_col is None:
        reframework_col = next((c for c in columns if "reframework" in c or ("re" in c and "framework" in c)), None)
    if information_col is None:
        information_col = next((c for c in columns if "information" in c or c == "info"), None)
    if cover_col is None:
        cover_col = next((c for c in columns if "cover" in c or "poster" in c or "image" in c), None)
    if filename_cover_col is None:
        filename_cover_col = next((c for c in columns if "filename" in c and ("cover" in c or "poster" in c)), None)
    if module_dl_col is None:
        module_dl_col = next((c for c in columns if "module" in c and "dl" in c), None)
    if ingame_ini_col is None:
        ingame_ini_col = next((c for c in columns if "ingame" in c and "ini" in c), None)
    if ingame_setting_col is None:
        ingame_setting_col = next((c for c in columns if "ingame" in c and "setting" in c), None)
    if supported_gpu_col is None:
        supported_gpu_col = next((c for c in columns if "supported" in c and "gpu" in c), None)
    if exe_col is None or display_col is None:
        raise ValueError(
            f"DB header does not include required columns: "
            f"exe keys {exe_keys} and display keys {display_keys}. Actual headers: {columns}"
        )

    exe_index = columns.index(exe_col)
    display_index = columns.index(display_col)
    dll_index = columns.index(dll_col) if dll_col else None
    ultimate_asi_loader_index = columns.index(ultimate_asi_loader_col) if ultimate_asi_loader_col else None
    optipatcher_index = columns.index(optipatcher_col) if optipatcher_col else None
    specialk_index = columns.index(specialk_col) if specialk_col else None
    unreal5_index = columns.index(unreal5_col) if unreal5_col else None
    reframework_index = columns.index(reframework_col) if reframework_col else None
    information_index = columns.index(information_col) if information_col else None
    cover_index = columns.index(cover_col) if cover_col else None
    filename_cover_index = columns.index(filename_cover_col) if filename_cover_col else None
    module_dl_index = columns.index(module_dl_col) if module_dl_col else None
    ingame_ini_index = columns.index(ingame_ini_col) if ingame_ini_col else None
    ingame_setting_index = columns.index(ingame_setting_col) if ingame_setting_col else None
    engine_ini_location_index = columns.index(engine_ini_location_col) if engine_ini_location_col else None
    engine_ini_type_index = columns.index(engine_ini_type_col) if engine_ini_type_col else None
    display_kr_index = columns.index(display_kr_col) if display_kr_col else None
    information_kr_index = columns.index(information_kr_col) if information_kr_col else None
    supported_gpu_index = columns.index(supported_gpu_col) if supported_gpu_col else None
    ini_marker_index = next((i for i, c in enumerate(columns) if c == "#ini"), None)
    ini_var_indices = {}
    if ini_marker_index is not None:
        raw_headers = [h.strip() for h in headers]
        for i in range(ini_marker_index + 1, len(columns)):
            if columns[i].startswith("#"):
                continue
            ini_var_indices[i] = raw_headers[i]

    db = {}
    for sheet_order, row in enumerate(reader):
        if not row:
            continue
        if len(row) < len(columns):
            row = list(row) + [""] * (len(columns) - len(row))
        if len(row) <= max(exe_index, display_index):
            continue

        exe_path = row[exe_index].strip()
        match_files = _split_match_files(exe_path)
        match_rule_key = "|".join(match_files)
        game_name = row[display_index].strip()
        display_name = game_name or exe_path
        dll_name = ""
        ultimate_asi_loader_enabled = False
        optipatcher_enabled = False
        specialk_enabled = False
        unreal5_url = ""
        unreal5_rule = ""
        reframework_url = ""
        module_dl = ""
        information = ""
        ingame_ini_name = ""
        ingame_settings = {}
        game_name_kr = ""
        information_kr = ""
        cover_url = ""
        filename_cover = ""
        supported_gpu = ""
        if dll_index is not None and len(row) > dll_index:
            dll_name = row[dll_index].strip()
        if ultimate_asi_loader_index is not None and len(row) > ultimate_asi_loader_index:
            ultimate_asi_loader_enabled = _is_true_value(row[ultimate_asi_loader_index])
        if optipatcher_index is not None and len(row) > optipatcher_index:
            optipatcher_enabled = _is_true_value(row[optipatcher_index])
        if specialk_index is not None and len(row) > specialk_index:
            specialk_enabled = _is_true_value(row[specialk_index])
        if unreal5_index is not None and len(row) > unreal5_index:
            raw_unreal5 = str(row[unreal5_index]).strip()
            val = raw_unreal5.lower()
            if val in {"null", "none"}:
                unreal5_rule = ""
            elif raw_unreal5:
                unreal5_rule = raw_unreal5
        if reframework_index is not None and len(row) > reframework_index:
            reframework_url = _normalize_optional_url(row[reframework_index])
        if information_index is not None and len(row) > information_index:
            information = row[information_index].replace("\r\n", "\n").replace("\r", "\n").strip()
        if cover_index is not None and len(row) > cover_index:
            cover_url = _normalize_optional_url(row[cover_index])
        if filename_cover_index is not None and len(row) > filename_cover_index:
            filename_cover = normalize_cover_filename(row[filename_cover_index])
        if module_dl_index is not None and len(row) > module_dl_index:
            module_dl = str(row[module_dl_index]).strip().lower()
        if ingame_ini_index is not None and len(row) > ingame_ini_index:
            ingame_ini_name = row[ingame_ini_index].strip()

        engine_ini_location = ""
        engine_ini_type = ""
        if engine_ini_location_index is not None and len(row) > engine_ini_location_index:
            engine_ini_location = row[engine_ini_location_index].strip()
        if engine_ini_type_index is not None and len(row) > engine_ini_type_index:
            engine_ini_type = row[engine_ini_type_index].strip()
        if ingame_setting_index is not None and len(row) > ingame_setting_index:
            ingame_settings = _parse_pipe_ini_settings(row[ingame_setting_index])
        if display_kr_index is not None and len(row) > display_kr_index:
            game_name_kr = row[display_kr_index].strip()
        if information_kr_index is not None and len(row) > information_kr_index:
            information_kr = row[information_kr_index].replace("\r\n", "\n").replace("\r", "\n").strip()
        if supported_gpu_index is not None and len(row) > supported_gpu_index:
            supported_gpu = str(row[supported_gpu_index]).strip()
        popup_kr = ""
        popup_en = ""
        if popup_kr_index is not None and len(row) > popup_kr_index:
            popup_kr = row[popup_kr_index].replace("\r\n", "\n").replace("\r", "\n").strip()
        if popup_en_index is not None and len(row) > popup_en_index:
            popup_en = row[popup_en_index].replace("\r\n", "\n").replace("\r", "\n").strip()

        after_popup_kr = ""
        after_popup_en = ""
        guidepage_after_installation = ""
        if after_popup_kr_index is not None and len(row) > after_popup_kr_index:
            after_popup_kr = row[after_popup_kr_index].replace("\r\n", "\n").replace("\r", "\n").strip()
        if after_popup_en_index is not None and len(row) > after_popup_en_index:
            after_popup_en = row[after_popup_en_index].replace("\r\n", "\n").replace("\r", "\n").strip()
        if guidepage_index is not None and len(row) > guidepage_index:
            raw_guide = str(row[guidepage_index]).strip()
            guidepage_after_installation = _normalize_optional_url(raw_guide)

        ini_settings = {}
        for col_i, var_name in ini_var_indices.items():
            if len(row) > col_i:
                val = row[col_i].strip()
                if not val:
                    continue
                parsed_target = _parse_ini_header_target(var_name)
                if parsed_target:
                    ini_settings[parsed_target] = val
                else:
                    ini_settings[var_name] = val

        if match_rule_key:
            db[match_rule_key] = {
                "sheet_order": sheet_order,
                "exe_path": exe_path,
                "match_files": match_files,
                "match_anchor": _pick_match_anchor(match_files),
                "display": display_name,
                "game_name": game_name,
                "game_name_kr": game_name_kr,
                "dll_name": dll_name,
                "ultimate_asi_loader": ultimate_asi_loader_enabled,
                "ini_settings": ini_settings,
                "optipatcher": optipatcher_enabled,
                "specialk": specialk_enabled,
                "unreal5_url": unreal5_url,
                "unreal5_rule": unreal5_rule,
                "reframework_url": reframework_url,
                "module_dl": module_dl,
                "engine_ini_location": engine_ini_location,
                "engine_ini_type": engine_ini_type,
                "information": information,
                "information_kr": information_kr,
                "cover_url": cover_url,
                "filename_cover": filename_cover,
                "supported_gpu": supported_gpu,
                "ingame_ini": ingame_ini_name,
                "ingame_settings": ingame_settings,
                "popup_kr": popup_kr,
                "popup_en": popup_en,
                "after_popup_kr": after_popup_kr,
                "after_popup_en": after_popup_en,
                "guidepage_after_installation": guidepage_after_installation,
            }

    return db


def load_module_download_links_from_public_sheet(spreadsheet_id, gid=518993268):
    url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv&gid={gid}"
    max_attempts = 3
    backoff_base = 1.0
    response = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = _file_session.get(url, timeout=15)
            response.raise_for_status()
            break
        except Exception:
            if attempt < max_attempts:
                sleep_for = backoff_base * (2 ** (attempt - 1))
                try:
                    time.sleep(sleep_for)
                except Exception:
                    pass
            else:
                raise

    reader = csv.reader(io.StringIO(response.content.decode("utf-8-sig"), newline=""))
    headers = next(reader, None)
    if not headers:
        return {}

    cols = [str(h).strip().lower() for h in headers]
    module_idx = next((i for i, c in enumerate(cols) if c in {"module_dl", "module", "module_name"}), None)
    version_idx = next((i for i, c in enumerate(cols) if c in {"version", "ver", "filename", "file_name", "archive_name", "file"}), None)
    link_idx = next((i for i, c in enumerate(cols) if c in {"download", "download_link", "url", "downloadurl", "c"}), None)

    if module_idx is None:
        module_idx = 0 if len(cols) > 0 else None
    if version_idx is None:
        version_idx = 1 if len(cols) > 1 else None
    if link_idx is None:
        link_idx = 2 if len(cols) > 2 else None

    if module_idx is None or link_idx is None:
        return {}

    mapping = {}
    for row in reader:
        if not row or len(row) <= module_idx:
            continue

        module_key = _norm_key(row[module_idx])
        if not module_key:
            continue

        if module_key in {"warning_kr", "warning_en"}:
            warning_text = ""
            if version_idx is not None and len(row) > version_idx:
                warning_text = str(row[version_idx]).strip()
            elif len(row) > module_idx + 1:
                warning_text = str(row[module_idx + 1]).strip()
            if warning_text:
                mapping[f"__{module_key}__"] = warning_text
            continue

        if module_key in {"rtss_kr", "rtss_en"}:
            rtss_text = ""
            if version_idx is not None and len(row) > version_idx:
                rtss_text = str(row[version_idx]).strip()
            elif len(row) > module_idx + 1:
                rtss_text = str(row[module_idx + 1]).strip()
            if rtss_text:
                mapping[module_key] = rtss_text
            continue

        if module_key == "exclude_list":
            exclude_text = ""
            if version_idx is not None and len(row) > version_idx:
                exclude_text = str(row[version_idx]).strip()
            elif len(row) > module_idx + 1:
                exclude_text = str(row[module_idx + 1]).strip()
            if exclude_text:
                mapping["__exclude_list__"] = exclude_text
            continue

        if len(row) <= max(module_idx, link_idx):
            continue

        raw_link = str(row[link_idx]).strip()
        download_url = _normalize_download_url(raw_link)
        if not download_url:
            continue

        version = ""
        if version_idx is not None and len(row) > version_idx:
            version = str(row[version_idx]).strip()

        mapping[module_key] = {
            "url": download_url,
            "version": version,
            "filename": version,
        }

    return mapping


def _is_true_value(value):
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_optional_url(value):
    raw = str(value).strip()
    if not raw:
        return ""
    if raw.lower() in {"null", "none", "na", "n/a", "-"}:
        return ""
    low = raw.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return raw
    if " " in raw or "\n" in raw or low in {"null", "none", "na", "n/a", "-"}:
        return ""
    if "." in raw:
        candidate = raw
        if candidate.startswith("//"):
            candidate = "https:" + candidate
        elif not candidate.lower().startswith("http"):
            candidate = "https://" + candidate
        return candidate
    return ""


def _normalize_download_url(value):
    normalized = _normalize_optional_url(value)
    if not normalized:
        return ""

    try:
        parsed = urlparse(normalized)
        host = str(parsed.netloc or "").strip().lower()
        if host in {"drive.google.com", "www.drive.google.com"}:
            file_id = ""
            match = re.search(r"/file/d/([^/]+)", parsed.path)
            if match:
                file_id = str(match.group(1) or "").strip()
            else:
                file_id = str((parse_qs(parsed.query).get("id") or [""])[0]).strip()
            if file_id:
                return f"https://drive.google.com/uc?export=download&id={file_id}"
    except Exception:
        logging.debug("Failed to normalize download URL: %s", normalized, exc_info=True)

    return normalized


def _norm_key(s: Optional[str]) -> str:
    if s is None:
        return ""
    t = str(s).strip()
    t = unicodedata.normalize("NFKC", t)
    t = t.replace("\u00A0", " ").replace("\uFEFF", "")
    return t.lower()


def _parse_pipe_ini_settings(raw_value):
    text = str(raw_value or "").strip()
    if not text:
        return {}

    parsed = {}
    for token in text.split("|"):
        token = token.strip()
        if not token:
            continue
        if "=" in token:
            key, value = token.split("=", 1)
        elif ":" in token:
            key, value = token.split(":", 1)
        else:
            logging.warning("Skipping invalid #ingame_setting token (missing '=' or ':'): %s", token)
            continue

        key = key.strip()
        value = value.strip().rstrip(",")
        if not key:
            continue

        if len(key) >= 2 and key[0] == key[-1] and key[0] in {'"', "'"}:
            key = key[1:-1].strip()

        if ":" in key:
            section, section_key = key.split(":", 1)
            section = section.strip()
            section_key = section_key.strip()
            if section and section_key:
                parsed[(section, section_key)] = value
            else:
                logging.warning("Skipping invalid #ingame_setting token (invalid section:key): %s", token)
        else:
            parsed[key] = value

    return parsed
