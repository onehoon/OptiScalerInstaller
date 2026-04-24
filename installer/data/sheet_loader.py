import json
import logging
import re
from urllib.parse import parse_qs, urlparse

from ..common.cover_utils import normalize_cover_filename
from ..common.flag_parser import parse_bool_token
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


def load_game_db_from_remote_json(source_url: str, *, timeout_seconds: float = 10.0):
    normalized = str(source_url or "").strip()
    if not normalized:
        raise ValueError("Game master URL is empty")
    response = _file_session.get(normalized, timeout=timeout_seconds)
    response.raise_for_status()
    rows = json.loads(response.content.decode("utf-8-sig"))
    return _build_game_db_from_rows(rows)


def _build_game_db_from_rows(rows: object) -> dict[str, dict[str, object]]:

    if not isinstance(rows, list):
        raise ValueError("game_master.json must contain a list")

    db = {}
    for sheet_order, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        enabled = _is_enabled_flag(row.get("enabled", True))
        if not enabled:
            continue

        exe_path = str(row.get("match_exe", "") or "").strip()
        match_files = _split_match_files(exe_path)
        if not match_files:
            continue
        match_rule_key = "|".join(match_files)

        game_name_en = str(row.get("game_name_en", "") or "").strip()
        game_name_kr = str(row.get("game_name_kr", "") or "").strip()
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
            "enabled": enabled,
            "sheet_order": sheet_order,
            "exe_path": exe_path,
            "match_files": match_files,
            "match_anchor": _pick_match_anchor(match_files),
            "game_name_en": game_name_en,
            "game_name_kr": game_name_kr,
            "optiscaler_dll_name": "",
            "cover_url": str(row.get("cover_url", "") or "").strip(),
            "filename_cover": normalize_cover_filename(str(row.get("cover_filename", "") or "")),
            "support_intel": row.get("support_intel", ""),
            "support_amd": row.get("support_amd", ""),
            "support_nvidia": row.get("support_nvidia", ""),
            "supported_gpu": supported_gpu_rule,
        }

    return db


def load_module_download_links_from_remote_json(source_url: str, *, timeout_seconds: float = 10.0):
    normalized = str(source_url or "").strip()
    if not normalized:
        raise ValueError("Resource master URL is empty")
    response = _file_session.get(normalized, timeout=timeout_seconds)
    response.raise_for_status()
    rows = json.loads(response.content.decode("utf-8-sig"))
    return _build_module_download_links_from_rows(rows)


def _build_module_download_links_from_rows(rows: object) -> dict[str, dict[str, str] | str]:

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
    return parse_bool_token(
        value,
        empty_default=False,
        unknown_default=True,
        extra_false_tokens=("native xefg",),
    )


def _is_enabled_flag(value: object) -> bool:
    if value is None:
        return True
    return _is_truthy_support_flag(value)


def _normalize_optional_url(value):
    raw = str(value).strip()
    if not raw:
        return ""
    if raw.lower() in {"null", "none", "na", "n/a", "-"}:
        return ""
    low = raw.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return raw
    if " " in raw or "\n" in raw:
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
