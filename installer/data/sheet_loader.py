import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from ..common.cover_utils import normalize_cover_filename


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


def load_game_db_from_local_json(_gid=0):
    data_dir = Path(__file__).resolve().parents[2] / "assets" / "data"
    game_master_path = data_dir / "game_master.json"
    if not game_master_path.is_file():
        raise FileNotFoundError(f"game_master.json not found: {game_master_path}")

    with game_master_path.open("r", encoding="utf-8") as fp:
        rows = json.load(fp)

    if not isinstance(rows, list):
        raise ValueError("game_master.json must contain a list")

    db = {}
    for sheet_order, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        if not bool(row.get("enabled", True)):
            continue

        exe_path = str(row.get("match_exe", "") or "").strip()
        match_files = _split_match_files(exe_path)
        if not match_files:
            continue
        match_rule_key = "|".join(match_files)

        game_name_en = str(row.get("game_name_en", "") or "").strip()
        game_name_kr = str(row.get("game_name_kr", "") or "").strip()
        display_name = game_name_en or game_name_kr or exe_path

        supported_gpu_rule = str(row.get("supported_gpu", "") or "").strip()
        if not supported_gpu_rule:
            supported_vendors = []
            if _is_truthy_support_flag(row.get("support_intel")):
                supported_vendors.append("intel")
            if _is_truthy_support_flag(row.get("support_amd")):
                supported_vendors.append("amd")
            if _is_truthy_support_flag(row.get("support_nvidia")):
                supported_vendors.append("nvidia")

            if len(supported_vendors) == 3:
                supported_gpu_rule = "all"
            elif supported_vendors:
                supported_gpu_rule = "|".join(supported_vendors)

        db[match_rule_key] = {
            "game_id": str(row.get("game_id", "") or "").strip(),
            "sheet_order": sheet_order,
            "exe_path": exe_path,
            "match_files": match_files,
            "match_anchor": _pick_match_anchor(match_files),
            "display": display_name,
            "game_name": game_name_en or display_name,
            "game_name_kr": game_name_kr,
            "dll_name": str(row.get("dll_name", "") or "").strip(),
            "ultimate_asi_loader": bool(row.get("ultimate_asi_loader", False)),
            "ini_settings": {},
            "optipatcher": bool(row.get("optipatcher", False)),
            "specialk": bool(row.get("specialk", False)),
            "unreal5_url": str(row.get("unreal5_url", "") or "").strip(),
            "unreal5_rule": str(row.get("unreal5_rule", "") or "").strip(),
            "reframework_url": str(row.get("reframework_url", "") or "").strip(),
            "module_dl": str(row.get("module_dl", "") or "").strip().lower(),
            "engine_ini_location": str(row.get("engine_ini_location", "") or "").strip(),
            "engine_ini_type": str(row.get("engine_ini_type", "") or "").strip(),
            "information": str(row.get("information_en", "") or "").strip(),
            "information_kr": str(row.get("information_kr", "") or "").strip(),
            "cover_url": str(row.get("cover_url", "") or "").strip(),
            "filename_cover": normalize_cover_filename(str(row.get("cover_filename", "") or "")),
            "supported_gpu": supported_gpu_rule,
            "ingame_ini": str(row.get("ingame_ini", "") or "").strip(),
            "ingame_settings": {},
        }

    return db


def load_module_download_links_from_local_json():
    data_dir = Path(__file__).resolve().parents[2] / "assets" / "data"
    resource_master_path = data_dir / "resource_master.json"
    if not resource_master_path.is_file():
        raise FileNotFoundError(f"resource_master.json not found: {resource_master_path}")

    with resource_master_path.open("r", encoding="utf-8") as fp:
        rows = json.load(fp)

    if not isinstance(rows, list):
        raise ValueError("resource_master.json must contain a list")

    mapping = {}
    for row in rows:
        if not isinstance(row, dict):
            continue

        resource_key = str(row.get("resource_group") or row.get("resource_id") or "").strip().lower()
        if not resource_key:
            continue

        if resource_key == "exclude_list":
            exclude_text = str(row.get("filename", "") or "").strip()
            if exclude_text:
                mapping["__exclude_list__"] = exclude_text
            continue

        url = _normalize_download_url(str(row.get("url", "") or "").strip())
        if not url:
            continue

        version = str(row.get("version", "") or "").strip()
        display_version = str(row.get("display_version", "") or "").strip()
        filename = str(row.get("filename", "") or "").strip()
        mapping[resource_key] = {
            "url": url,
            "version": version,
            "display_version": display_version,
            "filename": filename,
        }

    return mapping


def _is_truthy_support_flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return False
    if text in {"0", "false", "no", "n", "off", "null", "none", "na", "n/a", "-"}:
        return False
    return True


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
