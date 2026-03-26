import os
import csv
import io
import shutil
import subprocess
import tempfile
import threading
import zipfile
import tkinter as tk
import time
import tkinter.font as tkfont
import math
import hashlib
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
from tkinter import filedialog, messagebox
import logging
import sys
from pathlib import Path
import re
import webbrowser
from typing import Optional
import unicodedata
import ctypes
import locale
import stat
if os.name == "nt":
    import winreg

try:
    import customtkinter as ctk
except ModuleNotFoundError as e:
    raise ModuleNotFoundError(
        "customtkinter is not installed in the current Python environment.\n"
        f"Interpreter: {sys.executable}\n"
        f"Install with: \"{sys.executable}\" -m pip install customtkinter"
    ) from e

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageEnhance, ImageOps
except ModuleNotFoundError as e:
    raise ModuleNotFoundError(
        "Pillow (PIL) is not installed in the current Python environment.\n"
        f"Interpreter: {sys.executable}\n"
        f"Install with: \"{sys.executable}\" -m pip install Pillow"
    ) from e

try:
    from dotenv import load_dotenv
except ModuleNotFoundError as e:
    raise ModuleNotFoundError(
        "python-dotenv is not installed in the current Python environment.\n"
        f"Interpreter: {sys.executable}\n"
        f"Install with: \"{sys.executable}\" -m pip install python-dotenv"
    ) from e

 # Application Version
APP_VERSION = "0.1.0"

 # Configure logging deterministically below (avoid calling basicConfig early)

 # Load .env file (supports both local development and PyInstaller bundle via _MEIPASS)
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    # Running as PyInstaller bundle
    _env_path = os.path.join(sys._MEIPASS, '.env')
else:
    # Running as script
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')

if os.path.exists(_env_path):
    load_dotenv(_env_path)

 # Allow overriding these values via environment variables for easier testing/config
SHEET_ID = os.environ.get("OPTISCALER_SHEET_ID", "")
SHEET_GID = int(os.environ.get("OPTISCALER_SHEET_GID", "0"))
DOWNLOAD_LINKS_SHEET_GID = int(os.environ.get("OPTISCALER_DOWNLOAD_LINKS_SHEET_GID", "0"))

if not SHEET_ID:
    logging.warning("OPTISCALER_SHEET_ID not found in environment variables or .env file.")

OPTIPATCHER_URL = os.environ.get(
    "OPTIPATCHER_URL",
    "https://github.com/optiscaler/OptiPatcher/releases/latest/download/OptiPatcher.asi",
)

# Enable GPU validation
ENFORCE_GPU_CHECK = True

import logging.handlers
def get_game_logger(game_name: str) -> logging.Logger:
    """Return a logger that writes to gamename.log in the exe/script directory."""
    safe_name = re.sub(r'[^\w\-]', '_', game_name)
    log_path = Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve().parent / f"{safe_name}.log"
    logger = logging.getLogger(f"game_{safe_name}")
    if not any(isinstance(h, logging.FileHandler) and getattr(h, 'baseFilename', None) == str(log_path) for h in logger.handlers):
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
        logger.addHandler(fh)
    logger.setLevel(logging.INFO)
    return logger

# File logging handler with fallbacks: app folder -> %LOCALAPPDATA% -> temp dir
def _init_file_logger() -> Optional[Path]:
    candidates = []
    # When frozen, the primary target is the directory containing the executable.
    # This ensures logs are created next to the .exe file.
    if getattr(sys, 'frozen', False) and hasattr(sys, 'executable'):
        try:
            exe_dir = Path(sys.executable).resolve().parent
            candidates.append(exe_dir)
        except Exception:
            try:

                if hasattr(sys, '_MEIPASS'):
                    script_dir = Path(sys.executable).resolve().parent
                else:
                    script_dir = Path(__file__).resolve().parent
                script_dir.mkdir(parents=True, exist_ok=True)
                log_path = script_dir / "installer.log"
                # ensure file is writable by opening for append
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write("")

                root_logger = logging.getLogger()
                for h in list(root_logger.handlers):
                    if isinstance(h, logging.FileHandler) and getattr(h, 'baseFilename', None) == str(log_path):
                        root_logger.removeHandler(h)
                try:
                    from logging.handlers import RotatingFileHandler
                    fh = RotatingFileHandler(
                        log_path,
                        maxBytes=5 * 1024 * 1024,
                        backupCount=3,
                        encoding="utf-8"
                    )
                except Exception:
                    fh = logging.FileHandler(log_path, encoding="utf-8")

                fh.setLevel(logging.INFO)
                fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
                root_logger.addHandler(fh)
                logging.info("File logging initialized at %s", log_path)
                return log_path
            except Exception as e:
                try:
                    print(f"Warning: failed to initialize file logging at {locals().get('script_dir', '?')}: {e}", file=sys.stderr)
                except Exception:
                    pass
                return None
def _configure_logging():
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Ensure a console StreamHandler exists
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(logging.INFO)
        sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
        root.addHandler(sh)

    # Initialize file logger (will attach FileHandler if possible)
    try:
        _init_file_logger()
    except Exception:
        logging.exception("Failed during file logger initialization")


_configure_logging()

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ModuleNotFoundError as e:
    logging.error("requests module not installed. Install: python -m pip install requests")
    raise e

# Initialize a global session with retry logic for robust file downloads
_file_session = requests.Session()
_file_retry_strategy = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=("GET", "HEAD")
)
_file_adapter = HTTPAdapter(max_retries=_file_retry_strategy)
_file_session.mount("https://", _file_adapter)
_file_session.mount("http://", _file_adapter)

def _subprocess_no_window_kwargs() -> dict:
    if os.name != "nt":
        return {}

    kwargs = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    kwargs["startupinfo"] = startupinfo
    return kwargs


def get_graphics_adapter_info():
    """Return a user-friendly GPU name string for the current Windows machine."""
    if os.name != "nt":
        return "Unknown (non-Windows OS)"

    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=8,
            **_subprocess_no_window_kwargs(),
        )
        if result.returncode != 0:
            return "Unknown"

        gpu_names = []
        for line in result.stdout.splitlines():
            name = line.strip()
            if name and name not in gpu_names:
                gpu_names.append(name)

        allowed_vendors = ("intel", "amd", "nvidia")
        filtered_names = []
        for name in gpu_names:
            lowered = name.lower()
            if "mirage driver" in lowered:
                continue
            if any(vendor in lowered for vendor in allowed_vendors):
                filtered_names.append(name)

        if filtered_names:
            return ", ".join(filtered_names)
    except Exception:
        pass

    return "Unknown"


def _is_korean_ui() -> bool:
    """Return True if the Windows UI language is Korean (ko-KR, LCID 0x0412)."""
    try:
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return (lang_id & 0xFF) == 0x12  # Primary language ID for Korean
    except Exception:
        pass
    try:
        lang = locale.getdefaultlocale()[0] or ""
        return lang.lower().startswith("ko")
    except Exception:
        pass
    return False


USE_KOREAN: bool = _is_korean_ui()


def load_game_db_from_public_sheet(spreadsheet_id, gid=0):
    # Fetch with retry/backoff; do NOT use or write any local cache.
    url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv&gid={gid}"

    max_attempts = 3
    backoff_base = 1.0
    response = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = _file_session.get(url, timeout=15)
            response.raise_for_status()
            break
        except Exception as e:
            if attempt < max_attempts:
                sleep_for = backoff_base * (2 ** (attempt - 1))
                try:
                    time.sleep(sleep_for)
                except Exception:
                    pass
            else:
                # All attempts failed — propagate the exception (no cache fallback)
                raise

    text = response.content.decode("utf-8-sig")

    # Use StringIO so quoted multi-line cells (e.g., #information with line breaks)
    # are parsed correctly instead of being flattened.
    reader = csv.reader(io.StringIO(text, newline=""))
    headers = next(reader, None)
    # (No debug logging of parsed headers)
    if not headers:
        raise ValueError("Google Sheet has no header row")

    columns = [c.strip().lower() for c in headers]


    # One fetch + header parse only; per-row extraction happens in the single main loop below.

    popup_kr_keys = ["popup_kr", "popup message kr", "popup message_kr", "popupkr", "popup_kr_message"]
    popup_en_keys = ["popup_en", "popup message en", "popup message_en", "popupen", "popup_en_message"]
    popup_kr_col = next((c for c in columns if c in popup_kr_keys), None)
    popup_en_col = next((c for c in columns if c in popup_en_keys), None)
    popup_kr_index = columns.index(popup_kr_col) if popup_kr_col else None
    popup_en_index = columns.index(popup_en_col) if popup_en_col else None
    # after_popup columns: enable per-row extraction below
    after_popup_kr_keys = ["after_popup_kr", "after popup kr", "afterpopupkr"]
    after_popup_en_keys = ["after_popup_en", "after popup en", "afterpopupen"]
    after_popup_kr_col = next((c for c in columns if c in after_popup_kr_keys), None)
    after_popup_en_col = next((c for c in columns if c in after_popup_en_keys), None)
    after_popup_kr_index = columns.index(after_popup_kr_col) if after_popup_kr_col else None
    after_popup_en_index = columns.index(after_popup_en_col) if after_popup_en_col else None
    # Guide page URL to open after installation (optional)
    guidepage_keys = ["guidepage_after_installation", "guide_page_after_installation", "guidepage", "after_installation_guide", "guide_url", "after_install_url"]
    guidepage_col = next((c for c in columns if c in guidepage_keys), None)
    guidepage_index = columns.index(guidepage_col) if guidepage_col else None

    exe_keys = ["exe", "exe_name", "filename", "game_exe", "executable", "gamefile"]
    display_keys = ["display", "game_name", "gamename", "name", "title", "display_name"]
    dll_keys = ["dll_name", "dll", "dllname", "rename_dll", "target_dll"]
    optipatcher_keys = ["optipatcher", "opti_patcher", "use_optipatcher", "opti patcher"]
    unreal5_keys = ["unreal5", "unreal_5", "unreal5_url", "unreal5 patch", "unreal5_patch"]
    reframework_keys = ["reframework", "reframework_url", "re_framework", "re_framework_url"]
    information_keys = ["#information", "information", "info", "game_information"]
    cover_keys = ["cover_image", "cover", "poster", "poster_url", "image_url", "cover_url"]
    module_dl_keys = ["module_dl", "module", "module_name"]
    ingame_ini_keys = ["#ingame_ini", "ingame_ini", "in_game_ini"]
    ingame_setting_keys = ["#ingame_setting", "ingame_setting", "in_game_setting", "#ingame_settings", "ingame_settings"]
    engine_ini_location_keys = ["engine.ini_location", "engine_ini_location", "engine location", "engine_location", "engine_folder", "engine folder"]
    engine_ini_type_keys = ["engine.ini_type", "engine_ini_type", "engine type", "engine_type"]
    display_kr_keys = ["game_name_kr", "display_kr", "name_kr"]
    information_kr_keys = ["#information_kr", "information_kr", "info_kr"]

    exe_col = next((c for c in columns if c in exe_keys), None)
    display_col = next((c for c in columns if c in display_keys), None)
    dll_col = next((c for c in columns if c in dll_keys), None)
    optipatcher_col = next((c for c in columns if c in optipatcher_keys), None)
    unreal5_col = next((c for c in columns if c in unreal5_keys), None)
    reframework_col = next((c for c in columns if c in reframework_keys), None)
    information_col = next((c for c in columns if c in information_keys), None)
    cover_col = next((c for c in columns if c in cover_keys), None)
    module_dl_col = next((c for c in columns if c in module_dl_keys), None)
    ingame_ini_col = next((c for c in columns if c in ingame_ini_keys), None)
    ingame_setting_col = next((c for c in columns if c in ingame_setting_keys), None)
    engine_ini_location_col = next((c for c in columns if c in engine_ini_location_keys), None)
    engine_ini_type_col = next((c for c in columns if c in engine_ini_type_keys), None)
    display_kr_col = next((c for c in columns if c in display_kr_keys), None)
    information_kr_col = next((c for c in columns if c in information_kr_keys), None)

    if exe_col is None:
        exe_col = next((c for c in columns if "exe" in c or "file" in c), None)
    if display_col is None:
        display_col = next((c for c in columns if "name" in c or "title" in c), None)
    if dll_col is None:
        dll_col = next((c for c in columns if "dll" in c), None)
    if optipatcher_col is None:
        optipatcher_col = next((c for c in columns if "opti" in c and "patcher" in c), None)
    if unreal5_col is None:
        unreal5_col = next((c for c in columns if "unreal5" in c or ("unreal" in c and "5" in c)), None)
    if reframework_col is None:
        reframework_col = next((c for c in columns if "reframework" in c or ("re" in c and "framework" in c)), None)
    if information_col is None:
        information_col = next((c for c in columns if "information" in c or c == "info"), None)
    if cover_col is None:
        cover_col = next((c for c in columns if "cover" in c or "poster" in c or "image" in c), None)
    if module_dl_col is None:
        module_dl_col = next((c for c in columns if "module" in c and "dl" in c), None)
    if ingame_ini_col is None:
        ingame_ini_col = next((c for c in columns if "ingame" in c and "ini" in c), None)
    if ingame_setting_col is None:
        ingame_setting_col = next((c for c in columns if "ingame" in c and "setting" in c), None)

    if exe_col is None or display_col is None:
        raise ValueError(
            f"Google Sheet header does not include required columns: "
            f"exe keys {exe_keys} and display keys {display_keys}. Actual headers: {columns}"
        )

    exe_index = columns.index(exe_col)
    display_index = columns.index(display_col)
    dll_index = columns.index(dll_col) if dll_col else None
    optipatcher_index = columns.index(optipatcher_col) if optipatcher_col else None
    unreal5_index = columns.index(unreal5_col) if unreal5_col else None
    reframework_index = columns.index(reframework_col) if reframework_col else None
    information_index = columns.index(information_col) if information_col else None
    cover_index = columns.index(cover_col) if cover_col else None
    module_dl_index = columns.index(module_dl_col) if module_dl_col else None
    ingame_ini_index = columns.index(ingame_ini_col) if ingame_ini_col else None
    ingame_setting_index = columns.index(ingame_setting_col) if ingame_setting_col else None
    engine_ini_location_index = columns.index(engine_ini_location_col) if engine_ini_location_col else None
    engine_ini_type_index = columns.index(engine_ini_type_col) if engine_ini_type_col else None
    display_kr_index = columns.index(display_kr_col) if display_kr_col else None
    information_kr_index = columns.index(information_kr_col) if information_kr_col else None

    # Columns after a header named '#ini' are INI variable names
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
        # Pad row to match header columns so trailing empty cells are addressable
        if len(row) < len(columns):
            row = list(row) + [""] * (len(columns) - len(row))
        if len(row) <= max(exe_index, display_index):
            continue

        exe_path = row[exe_index].strip()
        game_name = row[display_index].strip()
        display_name = game_name or exe_path
        dll_name = ""
        optipatcher_enabled = False
        unreal5_url = ""
        unreal5_flag = False
        reframework_url = ""
        module_dl = ""
        information = ""
        ingame_ini_name = ""
        ingame_settings = {}
        game_name_kr = ""
        information_kr = ""

        cover_url = ""
        if dll_index is not None and len(row) > dll_index:
            dll_name = row[dll_index].strip()
        if optipatcher_index is not None and len(row) > optipatcher_index:
            optipatcher_enabled = _is_true_value(row[optipatcher_index])
        if unreal5_index is not None and len(row) > unreal5_index:

            val = row[unreal5_index].strip().lower()
            if val in ("true", "1", "yes", "y", "on"): 
                unreal5_flag = True
            unreal5_url = _normalize_optional_url(row[unreal5_index]) if "," not in val and (val.startswith("http") or val.endswith(('.zip', '.7z'))) else ""
        if reframework_index is not None and len(row) > reframework_index:
            reframework_url = _normalize_optional_url(row[reframework_index])
        if information_index is not None and len(row) > information_index:
            information = row[information_index].replace("\r\n", "\n").replace("\r", "\n").strip()
        if cover_index is not None and len(row) > cover_index:
            cover_url = _normalize_optional_url(row[cover_index])
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

        popup_kr = ""
        popup_en = ""
        if popup_kr_index is not None and len(row) > popup_kr_index:
            popup_kr = row[popup_kr_index].replace("\r\n", "\n").replace("\r", "\n").strip()
        if popup_en_index is not None and len(row) > popup_en_index:
            popup_en = row[popup_en_index].replace("\r\n", "\n").replace("\r", "\n").strip()

        # after popup messages (may contain multi-line content)
        after_popup_kr = ""
        after_popup_en = ""
        guidepage_after_installation = ""
        if after_popup_kr_index is not None and len(row) > after_popup_kr_index:
            after_popup_kr = row[after_popup_kr_index].replace("\r\n", "\n").replace("\r", "\n").strip()
        if after_popup_en_index is not None and len(row) > after_popup_en_index:
            after_popup_en = row[after_popup_en_index].replace("\r\n", "\n").replace("\r", "\n").strip()
        if guidepage_index is not None and len(row) > guidepage_index:
            raw_guide = str(row[guidepage_index]).strip()
            norm_guide = _normalize_optional_url(raw_guide)
            guidepage_after_installation = norm_guide

        ini_settings = {}
        for col_i, var_name in ini_var_indices.items():
            if len(row) > col_i:
                val = row[col_i].strip()
                if val:
                    # [Section]|Key 형태면 Section/Key로 분리, 아니면 Key만 사용
                    if "|" in var_name:
                        section, key = var_name.split("|", 1)
                        section = section.strip().strip("[]")
                        key = key.strip()
                        if section and key:
                            ini_settings[(section, key)] = val
                        else:
                            ini_settings[var_name] = val
                    else:
                        ini_settings[var_name] = val

        if exe_path:
            db[exe_path.lower()] = {
                "sheet_order": sheet_order,
                "display": display_name,
                "game_name": game_name,
                "game_name_kr": game_name_kr,
                "dll_name": dll_name,
                "ini_settings": ini_settings,
                "optipatcher": optipatcher_enabled,
                "unreal5_url": unreal5_url,
                "unreal5": unreal5_flag,
                "reframework_url": reframework_url,
                "module_dl": module_dl,
                "engine_ini_location": engine_ini_location,
                "engine_ini_type": engine_ini_type,
                "information": information,
                "information_kr": information_kr,
                "cover_url": cover_url,
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
    """Load module download links from sheet columns: module_dl, version, download link, gpu rule."""
    url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv&gid={gid}"
    response = _file_session.get(url, timeout=15)
    response.raise_for_status()

    reader = csv.reader(io.StringIO(response.content.decode("utf-8-sig"), newline=""))
    headers = next(reader, None)
    if not headers:
        return {}

    cols = [str(h).strip().lower() for h in headers]
    module_idx = next((i for i, c in enumerate(cols) if c in {"module_dl", "module", "module_name"}), None)
    version_idx = next((i for i, c in enumerate(cols) if c in {"version", "ver", "버전", "버전 정보"}), None)
    link_idx = next((i for i, c in enumerate(cols) if c in {"download", "download_link", "url", "downloadurl", "다운로드링크", "다운로드 링크", "c"}), None)
    gpu_vendor_idx = next((i for i, c in enumerate(cols) if c in {"gpu vendor", "gpu_vendor", "vendor", "gpu"}), None)

    # Fallback to A/B/C columns when explicit headers are missing.
    if module_idx is None:
        module_idx = 0 if len(cols) > 0 else None
    if version_idx is None:
        version_idx = 1 if len(cols) > 1 else None
    if link_idx is None:
        link_idx = 2 if len(cols) > 2 else None
    if gpu_vendor_idx is None:
        gpu_vendor_idx = 3 if len(cols) > 3 else None

    if module_idx is None or link_idx is None:
        return {}

    mapping = {}

    for row in reader:
        if not row:
            continue
        if len(row) <= module_idx:
            continue

        module_key = _norm_key(row[module_idx])
        if not module_key:
            continue

        # Optional startup warning rows: A="warning_kr" or "warning_en", B="message text"
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

        # Allow global key/value row such as: A="GPU Vendor", B="All"|"Intel".
        if module_key in {"gpu vendor", "gpu_vendor"}:
            value = ""
            if version_idx is not None and len(row) > version_idx:
                value = str(row[version_idx]).strip().lower()
            elif len(row) > module_idx + 1:
                value = str(row[module_idx + 1]).strip().lower()
            if value:
                mapping["__gpu_vendor__"] = value
            continue

        if len(row) <= max(module_idx, link_idx):
            continue

        raw_link = str(row[link_idx]).strip()
        download_url = _normalize_optional_url(raw_link)
        if not download_url:
            continue

        version = ""
        if version_idx is not None and len(row) > version_idx:
            version = str(row[version_idx]).strip()

        gpu_vendor = ""
        if gpu_vendor_idx is not None and len(row) > gpu_vendor_idx:
            gpu_vendor = str(row[gpu_vendor_idx]).strip().lower()

        mapping[module_key] = {
            "url": download_url,
            "version": version,
            "gpu_vendor": gpu_vendor,
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

    # Accept common bare forms like "example.com" or "www.example.com" by
    # prepending https://. Reject values with spaces or that look like placeholders.
    if " " in raw or "\n" in raw or low in {"null", "none", "na", "n/a", "-"}:
        return ""

    # Simple heuristic: contains a dot and no scheme -> assume it's a hostname/path
    if "." in raw:
        candidate = raw
        if candidate.startswith("//"):
            candidate = "https:" + candidate
        elif not candidate.lower().startswith("http"):
            candidate = "https://" + candidate
        return candidate

    return ""


def _norm_key(s: Optional[str]) -> str:
    """Normalize keys used for matching module names and engine types.

    This trims, applies Unicode NFKC normalization, removes BOM/non-breaking
    spaces and lowercases the result so lookups are stable across sheets.
    """
    if s is None:
        return ""
    t = str(s).strip()
    t = unicodedata.normalize("NFKC", t)
    t = t.replace("\u00A0", " ").replace("\uFEFF", "")
    return t.lower()


def _parse_pipe_ini_settings(raw_value):
    """Parse `key=value|Section:key=value|"key": value` into settings dict."""
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
            # Supports JSON-like sheet value: "m_bFilmGrain": true
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

        # Support both plain key and explicit section:key syntax.
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


# ---------------------------------------------------------------------------
# Installation logic
# ---------------------------------------------------------------------------

OPTISCALER_DLL = "OptiScaler.dll"


def install_from_source_folder(source_folder, target_path, dll_name=""):
    """Copy all files from source_folder into target_path, preserving structure."""
    if not os.path.isdir(source_folder):
        raise ValueError(f"Invalid source folder: {source_folder}")

    for dirpath, _, filenames in os.walk(source_folder):
        rel_dir = os.path.relpath(dirpath, source_folder)
        dest_dir = target_path if rel_dir == "." else os.path.join(target_path, rel_dir)
        os.makedirs(dest_dir, exist_ok=True)
        for fname in filenames:
            src = os.path.join(dirpath, fname)
            dst = os.path.join(dest_dir, fname)
            shutil.copy2(src, dst)

    _rename_optiscaler_dll(target_path, dll_name)


def extract_archive(archive_path, target_path, logger=None):
    """Extract .zip or .7z into target_path, preserving folder structure."""
    ext = os.path.splitext(archive_path)[1].lower()
    try:
        # 1. Prefer Python's built-in zipfile for .zip (Safest for Korean/Unicode paths)
        if ext == ".zip":
            try:
                with zipfile.ZipFile(archive_path, "r") as z:
                    z.extractall(target_path)
                if logger:
                    logger.info(f"Extracted .zip archive {archive_path} to {target_path}")
                return
            except Exception as e:
                if logger:
                    logger.warning(f"Python zipfile extraction failed, trying tar fallback: {e}")
                else:
                    logging.warning("Python zipfile extraction failed, trying tar fallback: %s", e)

        tar_exe = shutil.which("tar")
        if tar_exe:
            try:
                subprocess.run(
                    [tar_exe, "-xf", archive_path, "-C", target_path],
                    check=True,
                    **_subprocess_no_window_kwargs(),
                )
                if logger:
                    logger.info(f"Extracted archive {archive_path} to {target_path} using tar.exe")
                return
            except subprocess.CalledProcessError as e:
                if logger:
                    logger.warning(f"tar.exe extraction failed ({archive_path}), falling back: {e}")
                else:
                    logging.warning("tar.exe extraction failed (%s), falling back: %s", archive_path, e)

        # If zipfile failed above (and tar also failed or wasn't found), try zipfile one last time
        # strictly to raise the error if it was the only option.
        if ext == ".zip":
            raise RuntimeError(f"Failed to extract .zip file: {archive_path}")

        if ext == ".7z":
            raise RuntimeError(
                "Cannot extract .7z archive: no suitable extractor found. "
                "On Windows 11, ensure 'tar.exe' is available (built-in)."
            )

        raise ValueError(f"Unsupported archive format: {ext}")
    except Exception as e:
        if logger:
            logger.error(f"Failed to extract archive {archive_path} to {target_path}: {e}")
        raise


def _rename_optiscaler_dll(target_path, dll_name):
    """Rename OptiScaler.dll to dll_name if dll_name is specified."""
    if not dll_name:
        return
    src = os.path.join(target_path, OPTISCALER_DLL)
    dst = os.path.join(target_path, dll_name)
    if not os.path.exists(src):
        logging.warning("%s not found in %s, skipping rename.", OPTISCALER_DLL, target_path)
        return
    if os.path.exists(dst):
        os.remove(dst)
    os.rename(src, dst)
    logging.info("Renamed %s -> %s", OPTISCALER_DLL, dll_name)


def apply_ini_settings(ini_path, settings, force_frame_generation=False):
    """Update only existing INI keys in place while preserving comments and layout."""
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

        # DepthInverted exists in multiple sections; only update the XeFG one.
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

        if current_section:
            applied.append(f"{current_section}:{norm_key}")
        else:
            applied.append(norm_key)

    try:
        p.write_text("".join(updated_lines), encoding="utf-8")
    except Exception:
        logging.exception("Failed to write updated INI file")
        return

    logging.info("INI settings applied in-place: %s", applied)


def download_to_file(url, dest_path, timeout=60, logger=None):
    """Download a file with streaming writes to avoid high memory usage."""
    try:
        response = _file_session.get(url, timeout=timeout, stream=True)
        response.raise_for_status()
        p = Path(dest_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
        if logger:
            logger.info(f"Downloaded file from {url} to {dest_path}")
    except Exception as e:
        if logger:
            logger.error(f"Failed to download {url} to {dest_path}: {e}")
        raise


def _parse_version_text_to_ini_entries(version_text: str):
    """Parse version text into a mapping: {section: {key: value, ...}, ...}.
    Each line can contain pipe-separated tokens where the first token is a section
    like [Section] and subsequent tokens are key=value pairs.
    """
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

        # Default to global section if none specified at start
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
        # Add write permission for owner
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
    """Resolve and prepare a Path for an Engine.ini file.

    This will expand env vars and user home, resolve relative paths against
    `workspace_root` (or CWD), and ensure the parent directory exists. If the
    parent cannot be created due to permissions, the function will fall back to
    `%LOCALAPPDATA%/OptiScalerInstaller` and then to the system temp directory.

    Returns a Path (possibly non-existent file) or None on fatal error.
    """
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

    # If a file was provided explicitly, use it; otherwise target Engine.ini
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

    # Fallback: %LOCALAPPDATA%/OptiScalerInstaller
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

    # Final fallback: temp directory
    try:
        fallback = Path(tempfile.gettempdir()) / "OptiScalerInstaller" / target.name
        fallback.parent.mkdir(parents=True, exist_ok=True)
        logging.info("Falling back to temp dir for engine.ini: %s", fallback)
        return fallback
    except Exception:
        logging.exception("Failed to fall back to temp dir for engine.ini")

    return None



def _find_or_create_engine_ini(folder_name: str, workspace_root: Optional[str] = None) -> Optional[Path]:
    """Use the exact path specified in `folder_name` to find or create Engine.ini.

    Behavior:
    - If `folder_name` is an absolute path, use it directly as the target folder.
    - If `folder_name` contains any path separators but is not absolute, resolve it
      relative to `workspace_root` (or CWD if not provided).
    - If `folder_name` is a simple folder name (no separators), treat it as a
      folder name directly under `workspace_root` (or CWD). Do NOT search the
      entire workspace for matching folder names.
    - In the target folder, if a file named `engine.ini` (case-insensitive)
      already exists, return its Path. Otherwise create `Engine.ini` and return it.
    """
    if workspace_root is None:
        workspace_root = os.getcwd()

    # Normalize input path and handle cases where a full file path (e.g. Engine.ini)
    # was provided instead of a folder. Accept absolute paths, workspace-root
    # relative paths, or simple folder names.
    folder_raw = str(folder_name or "").strip()
    # Normalize unicode and remove common invisible characters and outer quotes
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
        logging.info("Empty engine.ini_location provided")
        return None

    # Expand environment variables (~, %VAR%) and user, then normalize separators
    try:
        folder_raw = os.path.expandvars(folder_raw)
    except Exception:
        pass
    # If %VAR% tokens remain, replace them using os.environ (case-insensitive)
    try:
        if "%" in folder_raw:
            def _replace_env(match):
                name = match.group(1).strip()
                val = os.environ.get(name) or os.environ.get(name.upper()) or os.environ.get(name.lower())
                
                # Fallback: If LOCALAPPDATA is missing in env, try to construct it from home
                if not val and name.upper() == "LOCALAPPDATA":
                    try:
                        val = str(Path.home() / "AppData" / "Local")
                    except Exception:
                        pass
                
                if val is None:
                    logging.warning("Environment variable %s not found when expanding engine.ini path", name)
                    # Return the original string so we don't break the path structure (e.g. avoid '\SHf\...')
                    return match.group(0)
                return val

            folder_raw_new = re.sub(r"%([^%]+)%", _replace_env, folder_raw)
            if folder_raw_new != folder_raw:
                logging.info("Expanded env vars in engine.ini path: %s -> %s", folder_raw, folder_raw_new)
                folder_raw = folder_raw_new
    except Exception:
        logging.exception("Failed while replacing %VAR% tokens in engine.ini path: %s", folder_raw)
    try:
        from pathlib import Path

        folder_raw = os.path.expanduser(folder_raw)
        p_in = Path(folder_raw)
    except Exception:
        p_in = None

    # If the input begins with a Windows-style environment variable like
    # %LOCALAPPDATA%\..., expand the leading variable in a case-insensitive
    # way and replace folder_raw with the expanded absolute path. This makes
    # paths that rely on Windows env vars resolve correctly rather than
    # falling back to workspace-relative resolution.
    try:
        m_var = re.match(r"^%([^%]+)%(?:[\\/](.*))?$", folder_raw)
        if m_var:
            var = m_var.group(1)
            rest = m_var.group(2) or ""
            val = os.environ.get(var) or os.environ.get(var.upper()) or os.environ.get(var.lower())
            if val:
                expanded = os.path.normpath(os.path.join(val, rest)) if rest else os.path.normpath(val)
                logging.info("Expanded leading env var in engine.ini path: %s -> %s", folder_raw, expanded)
                folder_raw = expanded
                try:
                    p_in = Path(folder_raw)
                except Exception:
                    p_in = None
            else:
                logging.warning("Environment variable %s not set for engine.ini path", var)
    except Exception:
        logging.exception("Error while expanding leading env var in engine.ini path: %s", folder_raw)

    # If the input looks like a file (ends with .ini or named Engine.ini),
    # treat its parent directory as the target folder.
    if p_in is not None and (p_in.suffix.lower() == ".ini" or p_in.name.lower() == "engine.ini"):
        target_dir = str(p_in.parent)
    else:
        # Determine target directory without searching other locations
        if os.path.isabs(folder_raw):
            target_dir = folder_raw
        elif os.path.sep in folder_raw or (os.path.altsep and os.path.altsep in folder_raw):
            # relative path with separators -> resolve against workspace_root
            target_dir = os.path.normpath(os.path.join(workspace_root, folder_raw))
        else:
            # simple folder name -> treat as direct child of workspace_root
            target_dir = os.path.normpath(os.path.join(workspace_root, folder_raw))

    logging.info("Resolved engine.ini target_dir: %s from input: %s", target_dir, folder_raw)

    try:
        Path(target_dir).mkdir(parents=True, exist_ok=True)
    except Exception:
        logging.exception("Failed to ensure target directory for engine.ini: %s", target_dir)
        return None

    # Look for any file named engine.ini (case-insensitive) inside this folder only
    try:
        for fname in os.listdir(target_dir):
            if fname.lower() == "engine.ini":
                p_existing = Path(os.path.join(target_dir, fname))
                logging.info("Found existing Engine.ini: %s", p_existing)
                return p_existing
    except Exception:
        logging.exception("Failed to list directory for engine.ini: %s", target_dir)

    # Not found -> create Engine.ini in the exact folder
    p = Path(os.path.join(target_dir, "Engine.ini"))
    try:
        p.write_text("", encoding="utf-8")
        logging.info("Created new INI: %s", p)
        return p
    except Exception:
        logging.exception("Failed to create Engine.ini at %s", p)
        return None


def _upsert_ini_entries(ini_path: Path, section_map: dict):
    """Insert or update keys in the ini file according to section_map.
    section_map: {section_name: {key: value, ...}, ...} where section_name=="" means global.
    """
    # Ensure the INI file exists and is writable. Some machines may have the
    # folder present but no Engine.ini file; attempting to read in that case
    # raises FileNotFoundError which used to cause an early return and skip
    # creating the file. Create an empty file if missing and clear readonly.
    try:
        if not ini_path.exists():
            try:
                ini_path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                logging.debug("Parent dir create skipped or failed for: %s", ini_path.parent)
            try:
                ini_path.write_text("", encoding="utf-8")
                logging.info("Created missing INI file for upsert: %s", ini_path)
            except Exception:
                logging.exception("Failed to create missing INI file: %s", ini_path)
                return

        # Ensure writable before reading/writing
        try:
            _ensure_file_writable(ini_path)
        except Exception:
            logging.exception("Failed to make INI writable before upsert: %s", ini_path)

        try:
            text = ini_path.read_text(encoding="utf-8")
        except Exception:
            logging.exception("Failed to read INI for upsert (will proceed with empty content): %s", ini_path)
            text = ""
    except Exception:
        logging.exception("Unexpected error preparing INI for upsert: %s", ini_path)
        return

    lines = text.splitlines(keepends=True)
    section_pattern = re.compile(r"^\s*\[([^\]]+)\]")

    # Build section positions (case-insensitive, normalized)
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

    # Helper to find key within a range (case-insensitive, normalize, allow spaces/quotes)
    def _norm_key_for_ini(k):
        return str(k or "").replace('"', '').replace("'", '').replace(' ', '').strip().lower()

    def _find_key_in_range(key, start, end):
        key_norm = _norm_key_for_ini(key)
        # Allow keys with spaces, quotes, = or :
        kv_re = re.compile(r"^\s*([\"']?)(.+?)\1\s*[:=]")
        for idx in range(start, end):
            m = kv_re.match(lines[idx])
            if m:
                k = _norm_key_for_ini(m.group(2))
                if k == key_norm:
                    return idx
        return None

    # Process each section
    for sec, kvs in section_map.items():
        norm_sec = _norm_section(sec)
        if norm_sec == "":
            # global keys: place at top of file before first section
            insert_pos = 0
            for key, value in kvs.items():
                found = _find_key_in_range(key, 0, len(lines))
                if found is not None:
                    ending = "\n" if lines[found].endswith("\n") else ""
                    lines[found] = f"{key}={value}{ending}"
                    modified = True
                else:
                    lines.insert(insert_pos, f"{key}={value}\n")
                    insert_pos += 1
                    modified = True
            continue

        # Find section by normalized name
        if norm_sec in sections:
            start, end = sections[norm_sec]
            insert_at = end
            for key, value in kvs.items():
                found = _find_key_in_range(key, start, end)
                if found is not None:
                    ending = "\n" if lines[found].endswith("\n") else ""
                    prefix = re.match(r"^(\s*)", lines[found]).group(1)
                    lines[found] = f"{prefix}{key}={value}{ending}"
                    modified = True
                else:
                    lines.insert(insert_at, f"{key}={value}\n")
                    insert_at += 1
                    modified = True
        else:
            # create new section at end
            if lines and not lines[-1].endswith("\n"):
                lines[-1] = lines[-1] + "\n"
            lines.append(f"[{sec}]\n")
            for key, value in kvs.items():
                lines.append(f"{key}={value}\n")
            modified = True

    if modified:
        try:
            ini_path.write_text("".join(lines), encoding="utf-8")
            logging.info("Upserted INI entries into %s", ini_path)
        except Exception:
            logging.exception("Failed to write updated INI: %s", ini_path)


def process_engine_ini_edits(spreadsheet_id: str, gid: int = 0, workspace_root: Optional[str] = None):
    """Main entry: read sheet gid (default 0), find rows with engine.ini_location,
    and apply engine.ini_type content (direct ini entries) to ini files.
    """
    # Fetch game sheet CSV
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

        # Ensure writable, edit, then set readonly
        try:
            _ensure_file_writable(ini_path)

            if content:
                section_map = _parse_version_text_to_ini_entries(content)
                if section_map:
                    _upsert_ini_entries(ini_path, section_map)
            else:
                logging.info("Engine.ini type content is empty; nothing to write")
        finally:
            # Always set to readonly per requirement
            _set_file_readonly(ini_path)



def install_optipatcher(target_path, url=OPTIPATCHER_URL, logger=None):
    plugins_dir = os.path.join(target_path, "plugins")
    os.makedirs(plugins_dir, exist_ok=True)

    asi_path = os.path.join(plugins_dir, "OptiPatcher.asi")
    download_to_file(url, asi_path, timeout=30, logger=logger)
    if logger:
        logger.info(f"OptiPatcher downloaded to {asi_path}")


def install_unreal5_from_url(url, target_path, logger=None):
    parsed = urlparse(url)
    file_name = os.path.basename(parsed.path)
    ext = os.path.splitext(file_name)[1].lower()
    if ext not in {".zip", ".7z"}:
        msg = f"Unreal5 URL must point to .zip or .7z archive: {url}"
        if logger:
            logger.error(msg)
        raise ValueError(msg)

    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = str(Path(tmpdir) / (file_name or f"unreal5_patch{ext}"))
        download_to_file(url, archive_path, timeout=60, logger=logger)
        extract_archive(archive_path, target_path, logger=logger)
        if logger:
            logger.info(f"Unreal5 patch installed from URL: {url}")


def install_reframework_dinput8_from_url(url, target_path, logger=None):
    """Download REFramework zip and install only dinput8.dll into target_path."""
    parsed = urlparse(url)
    file_name = os.path.basename(parsed.path) or "reframework.zip"

    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = str(Path(tmpdir) / file_name)
        download_to_file(url, archive_path, timeout=60, logger=logger)

        with zipfile.ZipFile(archive_path, "r") as z:
            dll_member = next(
                (
                    m for m in z.namelist()
                    if not m.endswith("/") and os.path.basename(m).lower() == "dinput8.dll"
                ),
                None,
            )

            if not dll_member:
                msg = "dinput8.dll not found inside reframework zip"
                if logger:
                    logger.error(msg)
                raise FileNotFoundError(msg)

            dst = os.path.join(target_path, "dinput8.dll")
            with z.open(dll_member, "r") as src_fp, open(dst, "wb") as dst_fp:
                shutil.copyfileobj(src_fp, dst_fp)
        if logger:
            logger.info(f"REFramework dinput8.dll installed from URL: {url}")


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

CARD_W = 120
CARD_H = 180
GRID_COLS = 4
GRID_ROWS_VISIBLE = 2
CARD_H_SPACING = 2
CARD_V_SPACING = 2
GRID_SIDE_PADDING = 12
RESIZE_REFLOW_THRESHOLD_PX = 18
SCROLLBAR_GUTTER = 18
GRID_W = (CARD_W * GRID_COLS) + (CARD_H_SPACING * GRID_COLS) + (GRID_SIDE_PADDING * 2)
GRID_H = CARD_H * GRID_ROWS_VISIBLE
WINDOW_W = GRID_W
WINDOW_H = 710
PLACEHOLDER_BG = "#3a414c"
PLACEHOLDER_FG = "#9fb0c5"
# Store poster cache in system temp to avoid writing beside the executable.
POSTER_CACHE_DIR = Path(tempfile.gettempdir()) / "OptiScalerInstaller" / "posters"
APP_BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ASSETS_DIR = APP_BASE_DIR / "assets"
DEFAULT_POSTER_CANDIDATES = [
    ASSETS_DIR / "default_poster.webp",
    ASSETS_DIR / "default_poster.jpg",
    ASSETS_DIR / "default_poster.png",
]
DEFAULT_POSTER_PATH = next((p for p in DEFAULT_POSTER_CANDIDATES if p.exists()), DEFAULT_POSTER_CANDIDATES[0])
IMAGE_TIMEOUT_SECONDS = 10
IMAGE_MAX_RETRIES = 3
IMAGE_MAX_WORKERS = 4
HI_DPI_SCALE = 2
TARGET_POSTER_W = CARD_W * HI_DPI_SCALE
TARGET_POSTER_H = CARD_H * HI_DPI_SCALE
INFO_TEXT_OFFSET_PX = 10
ENABLE_POSTER_CACHE = os.environ.get("OPTISCALER_ENABLE_POSTER_CACHE", "0").strip().lower() in {"1", "true", "yes", "on"}
IMAGE_CACHE_MAX = int(os.environ.get("OPTISCALER_IMAGE_CACHE_MAX", "100"))


def _make_default_poster_base(width: int, height: int) -> Image.Image:
    """Build a polished fallback base poster when no bundled asset exists."""
    img = Image.new("RGB", (width, height), "#12161d")
    draw = ImageDraw.Draw(img)

    # Vertical gradient background
    top = (32, 42, 56)
    bottom = (15, 19, 25)
    for y in range(height):
        t = y / max(1, height - 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    # Accent stripes to keep fallback visually intentional.
    stripe = (76, 201, 240)
    draw.polygon([(0, height * 0.18), (width * 0.55, 0), (width * 0.9, 0), (0, height * 0.5)], fill=(stripe[0], stripe[1], stripe[2]))
    draw.polygon([(width * 0.2, height), (width, height * 0.58), (width, height * 0.92), (width * 0.55, height)], fill=(53, 81, 110))

    # Dark vignette for readability.
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    o = ImageDraw.Draw(overlay)
    o.rectangle([0, height * 0.62, width, height], fill=(0, 0, 0, 140))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    return img


def _load_default_poster_base(width: int, height: int) -> Image.Image:
    """Load the default poster from disk, creating it if it's missing or corrupt."""
    if DEFAULT_POSTER_PATH.exists():
        try:
            return Image.open(DEFAULT_POSTER_PATH).convert("RGB").resize((width, height), Image.LANCZOS)
        except Exception:
            logging.warning("Failed to read/decode default poster at %s, will recreate it.", DEFAULT_POSTER_PATH)

    # If file doesn't exist or was corrupt, try to create it.
    try:
        DEFAULT_POSTER_PATH.parent.mkdir(parents=True, exist_ok=True)
        img = _make_default_poster_base(width, height)
        suffix = DEFAULT_POSTER_PATH.suffix.lower()
        if suffix == ".webp":
            img.save(DEFAULT_POSTER_PATH, format="WEBP", quality=92)
        elif suffix in {".jpg", ".jpeg"}:
            img.save(DEFAULT_POSTER_PATH, format="JPEG", quality=92)
        else:
            img.save(DEFAULT_POSTER_PATH, format="PNG")
        return img
    except Exception as e:
        logging.warning("Failed to create default poster asset at %s: %s", DEFAULT_POSTER_PATH, e)
        # Final fallback: return an in-memory version without saving.
        return _make_default_poster_base(width, height)


def _prepare_cover_image(img: Image.Image, target_w: int = TARGET_POSTER_W, target_h: int = TARGET_POSTER_H) -> Image.Image:
    """Convert, fit/crop, and lightly sharpen cover art for crisp UI rendering."""
    # Normalize color profile first to reduce conversion artifacts and banding.
    if img.mode not in {"RGB", "RGBA"}:
        img = img.convert("RGBA")
    else:
        img = img.copy()

    # Downscale very large sources first to reduce peak memory and resize cost.
    prefit_limit = (max(1, target_w * 2), max(1, target_h * 2))
    if img.width > prefit_limit[0] or img.height > prefit_limit[1]:
        img.thumbnail(prefit_limit, Image.Resampling.LANCZOS)

    # Preserve aspect ratio while filling the target frame (no stretching).
    img = ImageOps.fit(
        img,
        (target_w, target_h),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )

    # Moderate edge restoration after resize to avoid over-sharpened artifacts.
    img = img.filter(ImageFilter.UnsharpMask(radius=0.6, percent=60, threshold=4))
    img = ImageEnhance.Color(img).enhance(1.1)
    img = ImageEnhance.Contrast(img).enhance(1.05)
    return img.convert("RGBA")


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Accent colours
_ACCENT = "#4CC9F0"
_ACCENT_HOVER = "#35B6E0"
_LINK_ACTIVE = "#7DD3FC"
_LINK_HOVER = "#38BDF8"
_SELECTED_BORDER = "#4CC9F0"
_CARD_BG = "#181B21"
_CARD_BG_SEL = "#33506B"
_SURFACE = "#2A2E35"
_PANEL = "#1E2128"
_ACCENT_DISABLED = "#3A414C"
FONT_HEADING = "Malgun Gothic" if USE_KOREAN else "Segoe UI"
FONT_UI = "Malgun Gothic" if USE_KOREAN else "Segoe UI"


class OptiManagerApp:
    def __init__(self, root: ctk.CTk):
        self.root = root
        self.root.title(f"OptiScaler Installer for Intel LunarLake (v{APP_VERSION})")
        screen_w = max(1, int(self.root.winfo_screenwidth() or WINDOW_W))
        screen_h = max(1, int(self.root.winfo_screenheight() or WINDOW_H))
        target_w = min(WINDOW_W, max(360, screen_w - 40))
        target_h = min(WINDOW_H, max(420, screen_h - 80))

        self.root.geometry(f"{target_w}x{target_h}")
        self.root.minsize(target_w, target_h)
        self.root.update_idletasks()
        self.root.state("normal")
        self.root.overrideredirect(False)
        self.root.resizable(True, True)

        self.game_folder = ""
        self.opti_source_archive = ""
        self.found_exe_list = []
        self.game_db = {}
        self.module_download_links = {}
        self._supported_games_popup_shown = False
        self.sheet_status = False
        self.sheet_loading = True
        self.gpu_info = "Checking GPU..."
        self.install_in_progress = False
        self.selected_game_index = None
        self.card_frames: list = []
        self.card_items: list = []
        self._hovered_card_index = None
        self._grid_cols_current = GRID_COLS
        self._resize_after_id = None
        self._resize_visual_after_id = None
        self._resize_in_progress = False
        self._last_reflow_width = 0
        self._base_root_width = None
        self._ctk_images: list = []   # keep refs alive
        self._image_cache: dict = {}  # cache_key -> PIL.Image
        self._poster_cache_dir = POSTER_CACHE_DIR
        if ENABLE_POSTER_CACHE:
            self._poster_cache_dir.mkdir(parents=True, exist_ok=True)
        self._default_poster_base = _load_default_poster_base(TARGET_POSTER_W, TARGET_POSTER_H)
        self._image_session = self._build_retry_session()
        self._image_executor = ThreadPoolExecutor(max_workers=IMAGE_MAX_WORKERS, thread_name_prefix="cover-loader")
        self._task_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="general-task")
        self._pending_image_jobs: dict = {}
        self._inflight_image_futures: dict = {}
        self._failed_image_jobs: dict = {}
        self._render_generation = 0
        self._image_queue_after_id = None
        self._games_scrollregion_after_id = None
        self._overflow_fit_after_id = None
        self._initial_image_pass = True
        self._scan_in_progress = False
        self._auto_scan_active = False
        self._retry_attempted = False
        self.setup_ui()
        # Fetch GPU info asynchronously to avoid blocking startup on slow PowerShell
        try:
            self._task_executor.submit(self._fetch_gpu_info_async)
        except Exception:
            logging.exception("Failed to submit GPU info fetch task")
        self.root.bind("<Configure>", self._on_root_resize)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(250, self._capture_startup_width)
        self._start_game_db_load_async()

    def _show_game_selection_popup(self, message_text: str, on_confirm: callable = None, is_after_popup: bool = False):
        popup = ctk.CTkToplevel(self.root)
        popup.title("Installer Notice")
        popup.transient(self.root)
        popup.grab_set()
        popup.resizable(False, False)
        popup.configure(fg_color=_SURFACE)
        popup.withdraw()

        container = ctk.CTkFrame(popup, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=22, pady=(18, 12))

        text_widget = tk.Text(
            container,
            wrap="word",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            bg=_SURFACE,
            fg="#E3EAF3",
            width=58,
        )
        normal_font = tkfont.Font(family=FONT_UI, size=13)
        text_widget.configure(font=normal_font)

        def insert_with_red(text):
            idx = 0
            while idx < len(text):
                start = text.find("[RED]", idx)
                if start == -1:
                    text_widget.insert("end", text[idx:])
                    break
                text_widget.insert("end", text[idx:start])
                end = text.find("[END]", start)
                if end == -1:
                    text_widget.insert("end", text[start+5:], "red")
                    break
                text_widget.insert("end", text[start+5:end], "red")
                idx = end + 5

        if is_after_popup and ("[RED]" in message_text and "[END]" in message_text):
            text_widget.tag_configure("red", foreground="#FF4444")
            insert_with_red(message_text)
        else:
            text_widget.insert("end", message_text)

        line_count = max(1, min(16, message_text.count("\n") + 1))
        text_widget.configure(height=line_count)
        text_widget.configure(state="disabled")
        text_widget.pack(anchor="w", fill="x")

        def _confirm():
            try:
                popup.grab_release()
            except Exception:
                pass
            popup.destroy()
            if on_confirm:
                on_confirm()

        ctk.CTkButton(
            popup,
            text="확인" if USE_KOREAN else "OK",
            width=100,
            height=34,
            corner_radius=8,
            fg_color=_ACCENT,
            hover_color=_ACCENT_HOVER,
            text_color="#000000",
            font=ctk.CTkFont(family=FONT_UI, size=12, weight="bold"),
            command=_confirm,
        ).pack(pady=(0, 14))

        popup.protocol("WM_DELETE_WINDOW", lambda: None)  # Block closing without confirm
        self._center_popup_on_root(popup, use_requested_size=True)
        popup.deiconify()
        popup.after(0, lambda p=popup: self._center_popup_on_root(p))
        popup.after(80, lambda p=popup: self._center_popup_on_root(p))

    def _fetch_gpu_info_async(self):
        try:
            info = get_graphics_adapter_info()
        except Exception:
            logging.exception("Error fetching GPU info")
            info = "Unknown"
        try:
            self.root.after(0, lambda: self._update_gpu_ui(info))
        except Exception:
            logging.exception("Failed to schedule GPU UI update")

    def _update_gpu_ui(self, info: str):
        try:
            self.gpu_info = info
            if hasattr(self, 'gpu_lbl') and self.gpu_lbl:
                self.gpu_lbl.configure(text=f"GPU : {self.gpu_info}")
        except Exception:
            logging.exception("Failed to update GPU UI")
    def _show_after_install_popup(self, game: dict):
        # Determine language
        import locale
        lang, _ = locale.getdefaultlocale()
        is_kr = lang and lang.lower().startswith("ko")
        msg = ""
        if is_kr:
            msg = game.get("after_popup_kr", "").strip()
        else:
            msg = game.get("after_popup_en", "").strip()
        if not msg:
            msg = "설치가 완료되었습니다." if is_kr else "Installation Success"
        # If a guide URL is provided in the sheet, open it after the user confirms the popup.
        guide_url = (game.get("guidepage_after_installation") or "").strip()

        def _on_confirm_open_guide():
            try:
                if guide_url:
                    webbrowser.open(guide_url)
                else:
                    logging.debug("No guide URL provided for after-install popup for game: %s", game.get("display", "<unknown>"))
            except Exception:
                logging.exception("Failed to open guide URL: %s", guide_url)

        self._show_game_selection_popup(msg, on_confirm=_on_confirm_open_guide, is_after_popup=True)

    def _get_rtss_install_path(self) -> Path:
        roots = [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]
        subkeys = [r"SOFTWARE\WOW6432Node\Unwinder\RTSS", r"SOFTWARE\Unwinder\RTSS"]

        for root in roots:
            for subkey in subkeys:
                try:
                    with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ) as key:
                        val, _ = winreg.QueryValueEx(key, "InstallPath")
                        if val:
                            p = Path(val)
                            # If the registry value points to RTSS.exe, use its parent folder
                            if p.is_file() and p.name.lower() == "rtss.exe":
                                p = p.parent
                            if p.exists():
                                return p
                except Exception:
                    continue
        return Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "RivaTuner Statistics Server"

    def _check_and_show_rtss_popup(self, logger=None):

        try:
            install_path = self._get_rtss_install_path()
            profiles_dir = install_path / "Profiles"
            global_path = profiles_dir / "Global"

            # Do not show the popup if the RTSS folder or Global file does not exist
            if not (profiles_dir.exists() and global_path.exists()):
                if logger:
                    logger.info(f"RTSS not installed or Global file missing at {global_path}")
                return
            lines = global_path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
            ref_val, detours_val = None, None
            for line in lines:
                line = line.strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() == "ReflexSetLatencyMarker":
                        ref_val = v.strip()
                    elif k.strip() == "UseDetours":
                        detours_val = v.strip()
            if logger:
                logger.info(f"RTSS Global: UseDetours={detours_val}, ReflexSetLatencyMarker={ref_val}")

            if ref_val == "0" and detours_val == "1":
                if logger:
                    logger.info("RTSS settings OK: UseDetours=1, ReflexSetLatencyMarker=0")
                return

            key = "rtss_kr" if USE_KOREAN else "rtss_en"
            val = self.module_download_links.get(key, None)
            msg = str(val or "").strip()
            if not msg:
                if USE_KOREAN:
                    msg = "RTSS 설정 확인이 필요합니다.\n\n[Global]\nUseDetours=1\nReflexSetLatencyMarker=0\n\n위 설정이 적용되어 있는지 확인해주세요."
                else:
                    msg = "RTSS Configuration Check:\n\nPlease ensure the following settings in your Global profile:\nUseDetours=1\nReflexSetLatencyMarker=0"
            self._show_rtss_popup(msg)
        except Exception as e:
            if logger:
                logger.warning(f"Error during RTSS popup check: {e}")
            else:
                logging.warning(f"Error during RTSS popup check: {e}")

    def _show_rtss_popup(self, message_text: str):

        popup = ctk.CTkToplevel(self.root)
        popup.title("RTSS Notice")
        popup.transient(self.root)
        popup.grab_set()
        popup.resizable(False, False)
        popup.configure(fg_color=_SURFACE)
        popup.withdraw()

        container = ctk.CTkFrame(popup, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=22, pady=(18, 12))


        text = message_text or "(No message)"


        # --- Create and fill message_widget before using it ---
        pattern = re.compile(r"\[\s*RED\s*\](.*?)\[\s*END\s*\]", re.IGNORECASE | re.DOTALL)
        last = 0
        message_widget = tk.Text(
            container,
            wrap="word",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            bg=_SURFACE,
            fg="#E3EAF3",
            width=58,
        )
        normal_font = tkfont.Font(family=FONT_UI, size=13)
        red_font = tkfont.Font(family=FONT_UI, size=14, weight="bold")
        message_widget.configure(font=normal_font)
        message_widget.tag_configure("warning_red", foreground="#FF4D4F", font=red_font)

        full_plain_text = ""
        for m in pattern.finditer(text):
            if m.start() > last:
                normal = text[last:m.start()]
                message_widget.insert("end", normal)
                full_plain_text += normal
            red_text = m.group(1)
            if red_text:
                message_widget.insert("end", red_text, ("warning_red",))
                full_plain_text += red_text
            last = m.end()
        if last < len(text):
            tail = text[last:]
            message_widget.insert("end", tail)
            full_plain_text += tail

        line_count = max(1, min(16, full_plain_text.count("\n") + 1))
        message_widget.configure(height=line_count)
        message_widget.configure(state="disabled")
        message_widget.pack(anchor="w", fill="x")

        # --- Now show RTSS.webp image below the text ---
        try:
            img_path = Path(__file__).resolve().parent / "assets" / "RTSS.webp"
            if img_path.exists():
                pil_img = Image.open(img_path)
                orig_w, orig_h = pil_img.size
                new_w = int(orig_w * 0.75)
                new_h = int(orig_h * 0.75)
                ctk_image = ctk.CTkImage(light_image=pil_img, size=(new_w, new_h))
                img_label = ctk.CTkLabel(container, image=ctk_image, text="")
                img_label.image = ctk_image  # Prevent garbage collection
                img_label.pack(pady=(12, 0))
        except Exception:
            pass


        def _close_popup():
            try:
                popup.grab_release()
            except Exception:
                pass
            popup.destroy()

        ctk.CTkButton(
            popup,
            text="OK",
            width=100,
            height=34,
            corner_radius=8,
            fg_color=_ACCENT,
            hover_color=_ACCENT_HOVER,
            text_color="#000000",
            font=ctk.CTkFont(family=FONT_UI, size=12, weight="bold"),
            command=_close_popup,
        ).pack(pady=(0, 14))

        popup.protocol("WM_DELETE_WINDOW", _close_popup)
        self._center_popup_on_root(popup, use_requested_size=True)
        popup.deiconify()
        popup.after(0, lambda p=popup: self._center_popup_on_root(p))
        popup.after(80, lambda p=popup: self._center_popup_on_root(p))

    # ------------------------------------------------------------------
    # Async DB load
    # ------------------------------------------------------------------

    def _on_close(self):
        if self.install_in_progress:
            msg = "설치가 진행 중입니다. 완료 후 종료해주세요." if USE_KOREAN else "Installation is in progress. Please wait."
            messagebox.showwarning("Warning", msg)
            return

        try:
            if self._image_queue_after_id is not None:
                self.root.after_cancel(self._image_queue_after_id)
                self._image_queue_after_id = None
        except Exception:
            pass
        try:
            if self._games_scrollregion_after_id is not None:
                self.root.after_cancel(self._games_scrollregion_after_id)
                self._games_scrollregion_after_id = None
        except Exception:
            pass
        try:
            if self._overflow_fit_after_id is not None:
                self.root.after_cancel(self._overflow_fit_after_id)
                self._overflow_fit_after_id = None
        except Exception:
            pass
        try:
            self._image_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        try:
            self._task_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        self.root.destroy()

    def _build_retry_session(self) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=IMAGE_MAX_RETRIES,
            connect=IMAGE_MAX_RETRIES,
            read=IMAGE_MAX_RETRIES,
            backoff_factor=0.6,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "HEAD"),
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _start_game_db_load_async(self):
        self._task_executor.submit(self._load_game_db_worker)

    def _load_game_db_worker(self):
        try:
            db = load_game_db_from_public_sheet(SHEET_ID, SHEET_GID)
            if not db:
                raise ValueError("Sheet has no data.")

            module_links = {}
            try:
                module_links = load_module_download_links_from_public_sheet(SHEET_ID, DOWNLOAD_LINKS_SHEET_GID)
            except Exception as link_err:
                logging.warning("Failed to load download-link sheet (gid=%s): %s", DOWNLOAD_LINKS_SHEET_GID, link_err)

            self.root.after(0, lambda db=db, links=module_links: self._on_game_db_loaded(db, links, True, None))
        except Exception as e:
            self.root.after(0, lambda err=e: self._on_game_db_loaded({}, {}, False, err))

    def _on_game_db_loaded(self, db, module_links, ok, err):
        self.sheet_loading = False
        self.game_db = db if ok else {}
        self.module_download_links = module_links if ok else {}

        self.sheet_status = ok
        self._refresh_optiscaler_download_link_ui()
        self.apply_btn.configure(state="normal", fg_color=_ACCENT)
        self._update_sheet_status()
        # --- 최신 버전 확인 및 업그레이드 유도 ---
        self.check_app_update()

        # --- 게임 자동 스캔 트리거 ---
        if ok:
            warning_key = "__warning_kr__" if USE_KOREAN else "__warning_en__"
            warning_text = str(self.module_download_links.get(warning_key, "")).strip()
            if not self._supported_games_popup_shown:
                self._supported_games_popup_shown = True
                if warning_text:
                    self._show_startup_warning_popup(
                        warning_text,
                        on_close=self._show_supported_games_popup,
                    )
                else:
                    self._show_supported_games_popup()
            self._start_auto_scan()

    def check_app_update(self):
        """Check for app update using loaded module_download_links. Runs on UI thread after sheet load."""
        try:
            latest_info = None
            for k, v in (self.module_download_links or {}).items():
                if k == "latest_installer_version" and isinstance(v, dict):
                    latest_info = v
                    break
            if latest_info:
                def parse_version(verstr):
                    return tuple(int(x) for x in str(verstr).strip().split(".") if x.isdigit())
                app_ver = parse_version(APP_VERSION)
                sheet_ver = parse_version(latest_info.get("version", ""))
                if sheet_ver and app_ver and app_ver < sheet_ver:
                    upgrade_url = latest_info.get("url") or latest_info.get("link")
                    if upgrade_url:
                        webbrowser.open_new(upgrade_url)
        except Exception as e:
            logging.warning(f"Version check failed: {e}")

        # 시트 로드 성공 여부와 무관하게 RTSS 설정 체크 진행 (기본 메시지 Fallback)
        logger = None
        if getattr(self, "found_exe_list", None) and self.selected_game_index is not None:
            logger = get_game_logger(self.found_exe_list[self.selected_game_index].get("game_name", "unknown"))
        self._check_and_show_rtss_popup(logger=logger)

    def _show_startup_warning_popup(self, warning_text: str, on_close=None):
        text = str(warning_text or "").strip()
        if not text:
            if callable(on_close):
                on_close()
            return

        popup = ctk.CTkToplevel(self.root)
        popup.title("Warning")
        popup.transient(self.root)
        popup.grab_set()
        popup.resizable(False, False)
        popup.configure(fg_color=_SURFACE)
        popup.withdraw()

        container = ctk.CTkFrame(popup, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=22, pady=(18, 12))

        # Parse [RED]... [END] and color only the text between
        pattern = re.compile(r"\[\s*RED\s*\](.*?)\[\s*END\s*\]", re.IGNORECASE | re.DOTALL)
        last = 0
        message_text = tk.Text(
            container,
            wrap="word",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            bg=_SURFACE,
            fg="#E3EAF3",
            width=58,
        )
        normal_font = tkfont.Font(family=FONT_UI, size=13)
        red_font = tkfont.Font(family=FONT_UI, size=14, weight="bold")
        message_text.configure(font=normal_font)
        message_text.tag_configure("warning_red", foreground="#FF4D4F", font=red_font)

        full_plain_text = ""
        for m in pattern.finditer(text):
            if m.start() > last:
                normal = text[last:m.start()]
                message_text.insert("end", normal)
                full_plain_text += normal
            red_text = m.group(1)
            if red_text:
                message_text.insert("end", red_text, ("warning_red",))
                full_plain_text += red_text
            last = m.end()
        if last < len(text):
            tail = text[last:]
            message_text.insert("end", tail)
            full_plain_text += tail

        line_count = max(1, min(16, full_plain_text.count("\n") + 1))
        message_text.configure(height=line_count)
        message_text.configure(state="disabled")
        message_text.pack(anchor="w", fill="x")

        def _close_popup():
            try:
                popup.grab_release()
            except Exception:
                pass
            popup.destroy()
            if callable(on_close):
                on_close()

        ctk.CTkButton(
            popup,
            text="OK",
            width=100,
            height=34,
            corner_radius=8,
            fg_color=_ACCENT,
            hover_color=_ACCENT_HOVER,
            text_color="#000000",
            font=ctk.CTkFont(family=FONT_UI, size=12, weight="bold"),
            command=_close_popup,
        ).pack(pady=(0, 14))

        popup.protocol("WM_DELETE_WINDOW", _close_popup)
        self._center_popup_on_root(popup, use_requested_size=True)
        popup.deiconify()
        popup.after(0, lambda p=popup: self._center_popup_on_root(p))
        popup.after(80, lambda p=popup: self._center_popup_on_root(p))

    def _get_auto_scan_paths(self) -> list:
        """Return existing directories to scan automatically on startup."""
        paths = []
        seen = set()

        # 1. Custom paths that should always be scanned if they exist
        custom_candidates = [
            Path("D:/") / "game",
            Path("D:/") / "games",
            Path("E:/") / "game",
            Path("E:/") / "games",
        ]

        for p in custom_candidates:
            if p.exists() and p.is_dir():
                resolved = str(p).lower()
                if resolved not in seen:
                    seen.add(resolved)
                    paths.append(str(p))

        # 2. Steam Library Detection (Registry + VDF)
        steam_paths = []
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                base_steam_path_str, _ = winreg.QueryValueEx(key, "SteamPath")
            
            base_steam_path = Path(base_steam_path_str)
            steam_paths.append(base_steam_path / "steamapps" / "common")

            vdf_path = base_steam_path / "steamapps" / "libraryfolders.vdf"
            if vdf_path.exists():
                try:
                    with open(vdf_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    matches = re.findall(r'"path"\s+"([^"]+)"', content, re.IGNORECASE)
                    for match in matches:
                        clean_path = match.replace("\\\\", "\\")
                        steam_paths.append(Path(clean_path) / "steamapps" / "common")
                except Exception as e:
                    logging.warning("Error parsing libraryfolders.vdf: %s", e)
        except Exception as e:
            logging.debug("Steam registry detection failed: %s", e)

        # 3. Fallback Steam paths (only if auto-detection found nothing valid)
        if not any(p.exists() and p.is_dir() for p in steam_paths):
            steam_paths.append(Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam" / "steamapps" / "common")
            steam_paths.append(Path("D:/") / "SteamLibrary" / "steamapps" / "common")
            steam_paths.append(Path("E:/") / "SteamLibrary" / "steamapps" / "common")

        for p in steam_paths:
            if p.exists() and p.is_dir():
                resolved = str(p).lower()
                if resolved not in seen:
                    seen.add(resolved)
                    paths.append(str(p))

        return paths

    def _start_auto_scan(self):
        """Kick off a silent auto-scan of known Steam/game directories."""
        scan_paths = self._get_auto_scan_paths()
        if not scan_paths:
            return

        self.lbl_game_path.configure(text="Scanning...", text_color="#F1F5F9")
        self.found_exe_list = []
        self._clear_cards()
        self._configure_card_columns(self._get_dynamic_column_count())
        self._scan_in_progress = True
        self._auto_scan_active = True
        self.btn_select_folder.configure(state="disabled")

        self._task_executor.submit(self._scan_worker, scan_paths)

    def _show_supported_games_popup(self):
        names = []
        seen = set()
        for idx, entry in enumerate(self.game_db.values()):
            # Inclusion rule: A-column(exe) exists. Display text comes from B-column(game_name).
            # Rows without exe are already excluded when game_db is built.
            name = (str(entry.get("game_name_kr") or entry.get("game_name", "")).strip()
                    if USE_KOREAN else str(entry.get("game_name", "")).strip())
            dedupe_key = name.lower() if name else f"__blank__{idx}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            names.append(name)

        if not names:
            return

        popup = ctk.CTkToplevel(self.root)
        popup.title("Supported Game List")
        popup.transient(self.root)
        popup.grab_set()
        popup.configure(fg_color=_SURFACE)
        popup.withdraw()
        popup.geometry("364x420")
        popup.minsize(336, 360)

        ctk.CTkLabel(
            popup,
            text="Supported Game List",
            font=ctk.CTkFont(family=FONT_HEADING, size=16, weight="bold"),
            text_color="#F1F5F9",
        ).pack(anchor="w", padx=18, pady=(14, 8))

        list_frame = ctk.CTkScrollableFrame(
            popup,
            corner_radius=8,
            fg_color="#2A303A",
            border_width=0,
        )
        list_frame.pack(fill="both", expand=True, padx=18, pady=(0, 12))
        list_frame.grid_columnconfigure(0, weight=1)

        for i, name in enumerate(names):
            ctk.CTkLabel(
                list_frame,
                text=f"· {name}",
                font=ctk.CTkFont(family=FONT_UI, size=12),
                text_color="#E3EAF3",
                anchor="w",
                justify="left",
                height=16,
            ).grid(row=i, column=0, sticky="ew", padx=10, pady=0)

        ctk.CTkButton(
            popup,
            text="OK",
            width=100,
            height=34,
            corner_radius=8,
            fg_color=_ACCENT,
            hover_color=_ACCENT_HOVER,
            text_color="#000000",
            font=ctk.CTkFont(family=FONT_UI, size=12, weight="bold"),
            command=popup.destroy,
        ).pack(pady=(0, 14))

        self._center_popup_on_root(popup, use_requested_size=True)
        popup.deiconify()
        popup.after(0, lambda p=popup: self._center_popup_on_root(p))
        popup.after(80, lambda p=popup: self._center_popup_on_root(p))

    def _center_popup_on_root(self, popup: ctk.CTkToplevel, use_requested_size: bool = False):
        try:
            self.root.update_idletasks()
            popup.update_idletasks()

            root_x = self.root.winfo_x()
            root_y = self.root.winfo_y()
            root_w = self.root.winfo_width()
            root_h = self.root.winfo_height()

            popup_w = popup.winfo_reqwidth() if use_requested_size else popup.winfo_width()
            popup_h = popup.winfo_reqheight() if use_requested_size else popup.winfo_height()

            x = root_x + (root_w // 2) - (popup_w // 2)
            y = root_y + (root_h // 2) - (popup_h // 2)
            popup.geometry(f"+{x}+{y}")
        except Exception:
            logging.debug("Failed to center popup on root window")

    # ------------------------------------------------------------------
    # UI builder
    # ------------------------------------------------------------------

    def setup_ui(self):
        self.root.configure(fg_color=_PANEL)
        self.root.grid_rowconfigure(2, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        self._build_header()       # row 0
        self._build_scan_row()     # row 1
        self._build_grid_area()    # row 2, expands
        self._build_bottom_bar()   # row 3

    # -- Header -----------------------------------------------------------

    def _build_header(self):
        hdr = ctk.CTkFrame(self.root, fg_color=_PANEL, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        hdr.grid_columnconfigure(0, weight=1)

        title_lbl = ctk.CTkLabel(
            hdr,
            text=f"OptiScaler Installer",
            font=ctk.CTkFont(family=FONT_HEADING, size=20, weight="bold"),
            text_color=_ACCENT,
        )
        title_lbl.grid(row=0, column=0, padx=24, pady=(18, 2), sticky="w")

        sub_frame = ctk.CTkFrame(hdr, fg_color=_PANEL, corner_radius=0)
        sub_frame.grid(row=1, column=0, padx=24, pady=(0, 14), sticky="ew")
        sub_frame.grid_columnconfigure(0, weight=1)

        self.gpu_lbl = ctk.CTkLabel(
            sub_frame,
            text=f"GPU : {self.gpu_info}",
            font=ctk.CTkFont(family=FONT_UI, size=11),
            text_color="#C5CFDB",
            anchor="w",
        )
        self.gpu_lbl.grid(row=0, column=0, sticky="w")

        # Badge-style status indicator
        self.status_badge = ctk.CTkLabel(
            sub_frame,
            text="  ● Game DB: Loading…  ",
            font=ctk.CTkFont(family=FONT_UI, size=11, weight="bold"),
            text_color="#FFCB62",
            fg_color="#4B4330",
            corner_radius=8,
        )
        self.status_badge.grid(row=0, column=1, sticky="e", padx=(8, 0))

        # Separator line
        sep = ctk.CTkFrame(hdr, height=1, fg_color="#4A5361", corner_radius=0)
        sep.grid(row=2, column=0, sticky="ew")

    # -- Scan row ----------------------------------------------------------

    def _build_scan_row(self):
        row = ctk.CTkFrame(self.root, fg_color=_SURFACE, corner_radius=0)
        row.grid(row=1, column=0, sticky="ew", padx=0, pady=0)
        row.grid_columnconfigure(1, weight=1)

        sec_lbl = ctk.CTkLabel(
            row,
            text="1. Scan Game Folder",
            font=ctk.CTkFont(family=FONT_HEADING, size=12, weight="bold"),
            text_color="#F1F5F9",
        )
        sec_lbl.grid(row=0, column=0, padx=(20, 10), pady=12, sticky="w")

        self.btn_select_folder = ctk.CTkButton(
            row,
            text="Browse…",
            width=110,
            height=32,
            corner_radius=8,
            fg_color=_ACCENT,
            hover_color=_ACCENT_HOVER,
            text_color="#0B121A",
            font=ctk.CTkFont(family=FONT_UI, size=11, weight="bold"),
            command=self.select_game_folder,
        )
        self.btn_select_folder.grid(row=0, column=1, padx=4, pady=12, sticky="w")

        self.lbl_game_path = ctk.CTkLabel(
            row,
            text="",
            font=ctk.CTkFont(family=FONT_UI, size=11),
            text_color="#AEB9C8",
            anchor="w",
        )
        self.lbl_game_path.grid(row=0, column=2, padx=(6, 20), pady=12, sticky="ew")

    # -- Grid area (poster cards) -----------------------------------------

    def _build_grid_area(self):
        wrapper = ctk.CTkFrame(self.root, fg_color=_PANEL, corner_radius=0)
        wrapper.grid(row=2, column=0, sticky="nsew", padx=0, pady=0)
        wrapper.grid_rowconfigure(1, weight=1)
        wrapper.grid_columnconfigure(0, weight=1)

        sec_lbl = ctk.CTkLabel(
            wrapper,
            text="2. Supported Games",
            font=ctk.CTkFont(family=FONT_HEADING, size=12, weight="bold"),
            text_color="#F1F5F9",
        )
        sec_lbl.grid(row=0, column=0, padx=20, pady=(12, 6), sticky="w")

        self.games_scroll = ctk.CTkScrollableFrame(
            wrapper,
            width=GRID_W,
            height=GRID_H,
            fg_color=_PANEL,
            scrollbar_button_color="#566171",
            scrollbar_button_hover_color="#6A7587",
            corner_radius=0,
        )
        self.games_scroll.grid(row=1, column=0, sticky="nsew", padx=0, pady=(0, 8))
        self._configure_card_columns(self._grid_cols_current)
        self.games_scroll.bind("<Configure>", self._on_games_area_resize)
        try:
            canvas = getattr(self.games_scroll, "_parent_canvas", None)
            if canvas is not None:
                canvas.bind("<MouseWheel>", self._on_games_scroll, add="+")
                canvas.bind("<Button-4>", self._on_games_scroll, add="+")
                canvas.bind("<Button-5>", self._on_games_scroll, add="+")
                canvas.bind("<ButtonRelease-1>", self._on_games_scroll, add="+")
                # Also reflow columns when the inner canvas itself resizes.
                canvas.bind("<Configure>", self._on_games_area_resize, add="+")
        except Exception:
            logging.debug("Failed to bind scroll events for image priority updates")

        # Empty-state placeholder (kept hidden intentionally)
        self.empty_label = ctk.CTkLabel(
            self.games_scroll,
            text="",
            font=ctk.CTkFont(family=FONT_UI, size=13),
            text_color="#9AA8BC",
        )


    # -- Bottom bar --------------------------------------------------------

    def _build_bottom_bar(self):
        bar = ctk.CTkFrame(self.root, fg_color=_SURFACE, corner_radius=0, height=168)
        bar.grid(row=3, column=0, sticky="ew", padx=0, pady=0)
        bar.grid_propagate(False)
        bar.grid_columnconfigure(0, weight=1)

        # Section label + latest version info on the same line
        title_line = ctk.CTkFrame(bar, fg_color="transparent", corner_radius=0)
        title_line.grid(row=0, column=0, padx=20, pady=(10, 2), sticky="ew")
        title_line.grid_columnconfigure(1, weight=1)

        sec_lbl = ctk.CTkLabel(
            title_line,
            text="3. Select OptiScaler Archive",
            font=ctk.CTkFont(family=FONT_HEADING, size=12, weight="bold"),
            text_color="#F1F5F9",
        )
        sec_lbl.grid(row=0, column=0, sticky="w")

        self.lbl_optiscaler_version_line = ctk.CTkLabel(
            title_line,
            text="",
            font=ctk.CTkFont(family=FONT_UI, size=11),
            text_color="#AEB9C8",
            anchor="e",
            justify="right",
            wraplength=520,
        )
        self.lbl_optiscaler_version_line.grid(row=0, column=1, padx=(10, 0), sticky="e")

        mid_top = ctk.CTkFrame(bar, fg_color=_SURFACE, corner_radius=0)
        mid_top.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 2))
        mid_top.grid_columnconfigure(2, weight=1)

        mid_bottom = ctk.CTkFrame(bar, fg_color=_SURFACE, corner_radius=0)
        mid_bottom.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 10))
        mid_bottom.grid_columnconfigure(0, weight=1)

        btn_font = ctk.CTkFont(family=FONT_UI, size=11, weight="bold")
        btn_text_width = tkfont.Font(family=FONT_UI, size=11, weight="bold").measure("Select Archive")

        self.btn_select_archive = ctk.CTkButton(
            mid_top,
            text="Select Archive",
            width=btn_text_width + 4,
            height=32,
            corner_radius=8,
            fg_color="#465160",
            hover_color="#596576",
            text_color=_ACCENT,
            font=btn_font,
            command=self.select_opti_source_archive,
        )
        self.btn_select_archive.grid(row=0, column=0, padx=(0, 10), pady=4)

        self.optiscaler_link_block = ctk.CTkFrame(mid_top, fg_color="transparent", corner_radius=0)
        self.optiscaler_link_block.grid(row=0, column=1, padx=(30, 10), pady=(0, 0), sticky="w")
        self.optiscaler_link_block.grid_columnconfigure(0, weight=1)

        self.lbl_optiscaler_link_title = ctk.CTkLabel(
            self.optiscaler_link_block,
            text="",
            font=ctk.CTkFont(family=FONT_UI, size=11, weight="bold"),
            text_color="#7FA3C9",
            cursor="hand2",
            anchor="w",
            justify="left",
        )
        self.lbl_optiscaler_link_title.grid(row=0, column=0, padx=0, pady=(0, 0), sticky="w")
        self.lbl_optiscaler_link_title.bind("<Button-1>", self._open_optiscaler_download_link)
        self.lbl_optiscaler_link_title.bind("<Enter>", self._on_optiscaler_link_enter)
        self.lbl_optiscaler_link_title.bind("<Leave>", self._on_optiscaler_link_leave)

        self.lbl_opti_path = ctk.CTkLabel(
            mid_top,
            text="",
            font=ctk.CTkFont(family=FONT_UI, size=11),
            text_color="#AEB9C8",
            anchor="w",
        )
        self.lbl_opti_path.grid(row=0, column=2, sticky="ew", padx=(0, 10))

        self.apply_btn = ctk.CTkButton(
            mid_bottom,
            text="Install",
            width=130,
            height=72,
            corner_radius=10,
            fg_color=_ACCENT_DISABLED,
            hover_color=_ACCENT_HOVER,
            text_color="#000000",
            font=ctk.CTkFont(family=FONT_UI, size=14, weight="bold"),
            state="disabled",
            command=self.apply_optiscaler,
        )
        self.apply_btn.grid(row=0, column=1, padx=(10, 0), pady=(0, 0))

        self.info_text = ctk.CTkTextbox(
            mid_bottom,
            height=78,
            corner_radius=8,
            fg_color="#2A303A",
            text_color="#E3EAF3",
            font=ctk.CTkFont(family=FONT_UI, size=12),
            state="disabled",
            wrap="word",
            border_width=0,
        )
        self.info_text.grid(row=0, column=0, sticky="ew", pady=(0, 0))
        self._apply_information_text_shift()

        self._refresh_optiscaler_download_link_ui()
        self._set_information_text("Select a game to view information.")

    def _refresh_optiscaler_download_link_ui(self):
        if not hasattr(self, "lbl_optiscaler_link_title"):
            return

        # Do not show placeholder version text before sheet load completes.
        if getattr(self, "sheet_loading", False):
            self.lbl_optiscaler_link_title.configure(text="", cursor="arrow")
            if hasattr(self, "lbl_optiscaler_version_line"):
                self.lbl_optiscaler_version_line.configure(text="")
            return

        entry = self.module_download_links.get("optiscaler", {}) if hasattr(self, "module_download_links") else {}
        self._optiscaler_download_url = ""

        if isinstance(entry, dict):
            self._optiscaler_download_url = str(entry.get("url", "")).strip()
            raw_version = str(entry.get("version", "")).replace("\r", " ").replace("\n", " ").strip()
            version = re.sub(r"\s+", " ", raw_version)
        else:
            version = ""

        version_text = f"Latest Version : {version}" if version else "Latest Version: -"

        if self._optiscaler_download_url:
            self.lbl_optiscaler_link_title.configure(
                text="OptiScaler Download Link",
                text_color=_LINK_ACTIVE,
                cursor="hand2",
            )
            if hasattr(self, "lbl_optiscaler_version_line"):
                self.lbl_optiscaler_version_line.configure(text=version_text, text_color="#AEB9C8")
        else:
            self.lbl_optiscaler_link_title.configure(
                text="OptiScaler Download Link",
                text_color="#7FA3C9",
                cursor="arrow",
            )
            if hasattr(self, "lbl_optiscaler_version_line"):
                self.lbl_optiscaler_version_line.configure(text=version_text, text_color="#7F8B99")

    def _open_optiscaler_download_link(self, _event=None):
        url = getattr(self, "_optiscaler_download_url", "")
        if url:
            webbrowser.open(url)

    def _on_optiscaler_link_enter(self, _event=None):
        if getattr(self, "_optiscaler_download_url", ""):
            self.lbl_optiscaler_link_title.configure(text_color=_LINK_HOVER)

    def _on_optiscaler_link_leave(self, _event=None):
        if getattr(self, "_optiscaler_download_url", ""):
            self.lbl_optiscaler_link_title.configure(text_color=_LINK_ACTIVE)
        else:
            self.lbl_optiscaler_link_title.configure(text_color="#7FA3C9")

    def _apply_information_text_shift(self):
        # Keep inner spacing tight; avoid moving the inner widget itself to preserve border/glow rendering.
        try:
            text_widget = getattr(self.info_text, "_textbox", None)
            if text_widget is None:
                return
            text_widget.configure(spacing1=0, spacing2=0, spacing3=0, pady=0)

            # Shift text area upward by reducing top outer padding and giving the same room at bottom.
            manager = text_widget.winfo_manager()
            if manager == "pack":
                text_widget.pack_configure(pady=(0, INFO_TEXT_OFFSET_PX))
            elif manager == "grid":
                text_widget.grid_configure(pady=(0, INFO_TEXT_OFFSET_PX))
        except Exception as exc:
            logging.debug("Could not adjust information textbox position: %s", exc)

    # ------------------------------------------------------------------
    # Status badge
    # ------------------------------------------------------------------

    def _update_sheet_status(self):
        if self.sheet_loading:
            self.status_badge.configure(
                text="  ● Game DB: Loading…  ",
                text_color="#FFCB62",
                fg_color="#4B4330",
            )
            return
        if self.sheet_status:
            self.status_badge.configure(
                text="  ● Game DB: Online  ",
                text_color="#7EE1AA",
                fg_color="#244336",
            )
        else:
            self.status_badge.configure(
                text="  ● Game DB: Offline  ",
                text_color="#FF8A8A",
                fg_color="#4A2F34",
            )

    # ------------------------------------------------------------------
    # Information text
    # ------------------------------------------------------------------

    def _set_information_text(self, text=""):
        info_text = (text or "").strip() or "No information available."
        text_widget = getattr(self.info_text, "_textbox", self.info_text)
        self._apply_information_text_shift()
        self.info_text.configure(state="normal")
        try:
            text_widget.delete("1.0", "end")
            self._insert_information_with_markup(info_text)
        except Exception as exc:
            logging.warning("Failed to render information markup, falling back to plain text: %s", exc)
            text_widget.delete("1.0", "end")
            fallback_text = re.sub(r"\[\s*/?\s*(RED|END)\s*\]", "", info_text, flags=re.IGNORECASE)
            text_widget.insert("1.0", fallback_text)
        finally:
            self.info_text.configure(state="disabled")

    def _insert_information_with_markup(self, raw_text: str):
        # Supports [RED]... [END] segments from sheet information text.
        text_widget = getattr(self.info_text, "_textbox", self.info_text)

        font_spec = text_widget.cget("font")
        try:
            base_font = tkfont.nametofont(font_spec)
        except Exception:
            # Some environments return an inline font spec, not a named Tk font.
            base_font = tkfont.Font(font=font_spec)

        red_font = tkfont.Font(font=base_font)
        base_size = int(base_font.cget("size") or 12)
        red_size = base_size + 1 if base_size >= 0 else base_size - 1
        red_font.configure(size=red_size, weight="bold")
        text_widget.tag_configure("info_red_emphasis", foreground="#FF4D4F", font=red_font)
        self._info_red_font = red_font

        pattern = re.compile(r"\[\s*RED\s*\](.*?)\[\s*END\s*\]", re.IGNORECASE | re.DOTALL)
        cursor = 0
        for match in pattern.finditer(raw_text):
            start, end = match.span()
            if start > cursor:
                text_widget.insert("end", raw_text[cursor:start])
            emphasized_text = match.group(1).strip()
            text_widget.insert("end", emphasized_text, ("info_red_emphasis",))
            cursor = end

        if cursor < len(raw_text):
            text_widget.insert("end", raw_text[cursor:])

    # ------------------------------------------------------------------
    # Poster card grid
    # ------------------------------------------------------------------

    def _clear_cards(self, keep_selection=False):
        self._render_generation += 1
        self._pending_image_jobs.clear()
        self._inflight_image_futures.clear()
        self._failed_image_jobs.clear()
        self._initial_image_pass = True
        self._scan_in_progress = False
        self._retry_attempted = False
        for frame in self.card_frames:
            frame.destroy()
        self.card_frames.clear()
        self.card_items.clear()
        self._ctk_images.clear()  # Release stale PhotoImage refs to prevent accumulation.
        if not keep_selection:
            self.selected_game_index = None
            self._set_information_text("")
        self._hovered_card_index = None

    def _get_effective_widget_scale(self) -> float:
        try:
            if hasattr(ctk, "get_widget_scaling"):
                scale = float(ctk.get_widget_scaling())
                if scale > 0:
                    return scale
        except Exception:
            pass
        return 1.0

    def _get_forced_card_area_width(self) -> int:
        canvas = getattr(self.games_scroll, "_parent_canvas", None)
        if canvas is not None:
            width = int(canvas.winfo_width() or 0)
            if width > 1:
                return width

        try:
            scroll_w = int(self.games_scroll.winfo_width() or 0)
            if scroll_w > 1:
                return scroll_w
        except Exception:
            pass

        window_w = max(1, int(self.root.winfo_width() or 0))
        scale = self._get_effective_widget_scale()
        # Startup fallback is intentionally conservative to avoid right overflow before widgets settle.
        safe_margin = int(round(240 * scale))
        return max(1, window_w - safe_margin)

    def _apply_forced_games_canvas_width(self) -> int:
        return self._get_forced_card_area_width()

    def _get_dynamic_column_count(self) -> int:
        usable_w = self._get_forced_card_area_width()
        if usable_w <= 1:
            return 1
        return self._max_safe_columns_for_width(usable_w)

    def _max_safe_columns_for_width(self, usable_w: int) -> int:
        # Use measured card slot width so fullscreen/high-DPI wraps correctly.
        card_unit_w = self._get_card_slot_width()
        # Small right gutter prevents partial card visibility at boundary values.
        safe_w = max(1, int(usable_w) - 6)
        cols = max(1, safe_w // card_unit_w)
        return cols

    def _get_card_slot_width(self) -> int:
        fallback = max(1, CARD_W + CARD_H_SPACING)
        if not self.card_frames:
            return fallback

        card = self.card_frames[0]
        try:
            card_w = max(int(card.winfo_width() or 0), int(card.winfo_reqwidth() or 0), CARD_W)
            grid_info = card.grid_info()
            padx = grid_info.get("padx", (CARD_H_SPACING // 2, CARD_H_SPACING // 2))

            left = 0
            right = 0
            if isinstance(padx, (tuple, list)):
                if len(padx) >= 2:
                    left = int(padx[0])
                    right = int(padx[1])
                elif len(padx) == 1:
                    left = right = int(padx[0])
            elif isinstance(padx, str):
                parts = [p for p in padx.replace("{", " ").replace("}", " ").split() if p]
                if len(parts) >= 2:
                    left = int(float(parts[0]))
                    right = int(float(parts[1]))
                elif len(parts) == 1:
                    left = right = int(float(parts[0]))
            else:
                left = right = int(padx)

            return max(1, card_w + left + right)
        except Exception:
            return fallback

    def _capture_startup_width(self):
        self._base_root_width = max(1, self.root.winfo_width())
        self._last_reflow_width = self.root.winfo_width()
        self._apply_forced_games_canvas_width()
        self._schedule_overflow_fit_check()
        if self.found_exe_list:
            self._render_cards(keep_selection=True)

    def _get_games_container_width(self) -> int:
        try:
            canvas = getattr(self.games_scroll, "_parent_canvas", None)
            if canvas is not None:
                return max(1, canvas.winfo_width())
            return max(1, self.games_scroll.winfo_width())
        except Exception:
            return max(1, self.root.winfo_width())

    def _schedule_reflow_for_resize(self):
        # Trailing debounce only: avoid repeated re-layout while the user is dragging.
        current_w = self.root.winfo_width()
        self._resize_in_progress = True

        next_cols = self._get_dynamic_column_count()
        if next_cols != self._grid_cols_current:
            delay_ms = 120
        else:
            # 열 개수 변화가 없더라도 너비 차이가 크면 재정렬 (안전장치)
            if abs(current_w - self._last_reflow_width) < 20:
                self._resize_in_progress = False
                self._schedule_overflow_fit_check()
                return
            self._last_reflow_width = current_w
            delay_ms = 160

        if self._resize_after_id is not None:
            self.root.after_cancel(self._resize_after_id)
        self._resize_after_id = self.root.after(delay_ms, self._finish_resize_reflow)

        if self._resize_visual_after_id is not None:
            self.root.after_cancel(self._resize_visual_after_id)
        self._resize_visual_after_id = self.root.after(delay_ms + 80, self._end_resize_visual_suppression)

    def _finish_resize_reflow(self):
        self._resize_after_id = None
        self._rerender_cards_for_resize()

    def _end_resize_visual_suppression(self):
        self._resize_visual_after_id = None
        self._resize_in_progress = False
        self._pump_image_jobs()

    def _on_root_resize(self, _event=None):
        self._schedule_reflow_for_resize()

    def _configure_card_columns(self, cols: int):
        max_cols = max(self._grid_cols_current, cols)
        for col in range(max_cols):
            self.games_scroll.grid_columnconfigure(col, weight=0, minsize=0)

        for col in range(cols):
            self.games_scroll.grid_columnconfigure(col, weight=0, minsize=CARD_W)
        self._grid_cols_current = cols

    def _layout_existing_cards(self, cols: int):
        self._configure_card_columns(cols)
        for i, card in enumerate(self.card_frames):
            row_idx = i // cols
            col_idx = i % cols
            card.grid(
                row=row_idx,
                column=col_idx,
                padx=(CARD_H_SPACING // 2, CARD_H_SPACING // 2),
                pady=(CARD_V_SPACING // 2, CARD_V_SPACING // 2),
                sticky="n",
            )

    def _cards_overflow_visible_width(self) -> bool:
        if not self.card_frames:
            return False

        canvas = getattr(self.games_scroll, "_parent_canvas", None)
        if canvas is None:
            return False

        viewport_w = max(1, int(canvas.winfo_width() or 0))
        max_right = 0
        for card in self.card_frames:
            try:
                right = int(card.winfo_x() + card.winfo_width())
                if right > max_right:
                    max_right = right
            except Exception:
                continue
        return max_right > viewport_w

    def _schedule_games_scrollregion_refresh(self):
        if self._games_scrollregion_after_id is not None:
            return
        self._games_scrollregion_after_id = self.root.after_idle(self._refresh_games_scrollregion)

    def _refresh_games_scrollregion(self):
        self._games_scrollregion_after_id = None
        try:
            canvas = getattr(self.games_scroll, "_parent_canvas", None)
            if canvas is not None:
                bbox = canvas.bbox("all")
                if bbox:
                    canvas.configure(scrollregion=bbox)
        except Exception:
            pass

    def _schedule_overflow_fit_check(self):
        if self._overflow_fit_after_id is not None:
            return
        try:
            if not self.root.winfo_exists() or not self.games_scroll.winfo_exists():
                return
        except tk.TclError:
            return
        self._overflow_fit_after_id = self.root.after_idle(self._run_overflow_fit_check)

    def _run_overflow_fit_check(self):
        self._overflow_fit_after_id = None
        try:
            if not self.root.winfo_exists() or not self.games_scroll.winfo_exists() or not self.card_frames:
                return

            canvas = getattr(self.games_scroll, "_parent_canvas", None)
            viewport_w = int(canvas.winfo_width() or 0) if canvas is not None else 0
            if viewport_w <= 1:
                self._overflow_fit_after_id = self.root.after(30, self._run_overflow_fit_check)
                return

            cols = max(1, int(self._grid_cols_current))
            max_cols = self._max_safe_columns_for_width(self._get_forced_card_area_width())
            target_cols = min(cols, max_cols)

            if cols < max_cols and not self._cards_overflow_visible_width():
                target_cols = max_cols

            if target_cols != cols:
                self._layout_existing_cards(target_cols)
                self._schedule_games_scrollregion_refresh()
                if target_cols < cols:
                    self._schedule_overflow_fit_check()
                return

            if cols > 1 and self._cards_overflow_visible_width():
                self._layout_existing_cards(cols - 1)
                self._schedule_games_scrollregion_refresh()
                self._schedule_overflow_fit_check()
        except tk.TclError:
            logging.debug("Skipped overflow fit check because widgets are no longer available")

    def _fit_cards_to_visible_width(self, preferred_cols: int | None = None):
        if not self.card_frames:
            if preferred_cols is not None:
                self._configure_card_columns(max(1, preferred_cols))
            return

        requested_cols = max(1, preferred_cols if preferred_cols is not None else self._grid_cols_current)
        max_cols = self._max_safe_columns_for_width(self._get_forced_card_area_width())
        cols = min(requested_cols, max_cols)
        self._layout_existing_cards(cols)
        self._schedule_games_scrollregion_refresh()
        self._schedule_overflow_fit_check()

    def _on_games_area_resize(self, _event=None):
        self._schedule_reflow_for_resize()
        self._schedule_overflow_fit_check()
        if not self._resize_in_progress:
            self._pump_image_jobs()

    def _on_games_scroll(self, _event=None):
        # Handle wheel scrolling explicitly so rows beyond 2 are reachable.
        event = _event
        canvas = getattr(self.games_scroll, "_parent_canvas", None)
        if canvas is not None and event is not None:
            step = 0
            if hasattr(event, "delta") and event.delta:
                step = -1 if event.delta > 0 else 1
            elif getattr(event, "num", None) == 4:
                step = -1
            elif getattr(event, "num", None) == 5:
                step = 1

            if step != 0:
                canvas.yview_scroll(step, "units")

        # Re-evaluate queue ordering when viewport changes.
        self.root.after(30, self._pump_image_jobs)

    def _rerender_cards_for_resize(self):
        self._resize_after_id = None
        cols = self._get_dynamic_column_count()
        self._fit_cards_to_visible_width(cols)

    def _make_card_view_image(self, base_pil: Image.Image, selected: bool) -> Image.Image:
        img = base_pil.convert("RGBA")

        # When no game is selected, keep all covers crisp.
        if self.selected_game_index is None:
            return img

        if selected:
            zoom = 1.06
            zw = max(1, int(TARGET_POSTER_W * zoom))
            zh = max(1, int(TARGET_POSTER_H * zoom))
            zoomed = img.resize((zw, zh), Image.LANCZOS)
            left = max(0, (zw - TARGET_POSTER_W) // 2)
            top = max(0, (zh - TARGET_POSTER_H) // 2)
            img = zoomed.crop((left, top, left + TARGET_POSTER_W, top + TARGET_POSTER_H))
        else:
            # Keep focus effect but avoid making non-selected cards look overly blurry.
            img = img.filter(ImageFilter.GaussianBlur(0.35))
            img = ImageEnhance.Brightness(img).enhance(0.9)

        return img

    def _ensure_card_image_cache(self, item: dict):
        base_revision = int(item.get("base_revision", 0))
        if item.get("ctk_img_cache_revision") == base_revision and item.get("ctk_img_cache"):
            return

        base_pil = item["base_pil"]
        normal_img = base_pil.convert("RGBA")
        selected_img = self._make_card_view_image(base_pil, selected=True)
        dimmed_img = self._make_card_view_image(base_pil, selected=False)

        ctk_cache = {
            "normal": ctk.CTkImage(light_image=normal_img, dark_image=normal_img, size=(CARD_W, CARD_H)),
            "selected": ctk.CTkImage(light_image=selected_img, dark_image=selected_img, size=(CARD_W, CARD_H)),
            "dimmed": ctk.CTkImage(light_image=dimmed_img, dark_image=dimmed_img, size=(CARD_W, CARD_H)),
        }
        # Keep explicit refs to prevent Tk image GC.
        self._ctk_images.extend(ctk_cache.values())
        item["ctk_img_cache"] = ctk_cache
        item["ctk_img_cache_revision"] = base_revision
        item["current_image_state"] = None

    def _refresh_card_visual(self, index: int):
        if index < 0 or index >= len(self.card_items):
            return

        item = self.card_items[index]
        selected = self.selected_game_index == index
        hovered = self._hovered_card_index == index
        title_overlay = item["hover_title"]

        if selected:
            item["card"].configure(border_color=_SELECTED_BORDER, fg_color=_CARD_BG_SEL, border_width=3)
        elif hovered:
            item["card"].configure(border_color=_ACCENT, fg_color=_CARD_BG_SEL, border_width=2)
        else:
            item["card"].configure(border_color=_CARD_BG, fg_color=_CARD_BG, border_width=2)

        if selected or hovered:
            title_overlay.place(x=0, y=CARD_H - 34)
            title_overlay.lift()
        else:
            title_overlay.place_forget()

        if self.selected_game_index is None:
            image_state = "normal"
        elif selected:
            image_state = "selected"
        else:
            image_state = "dimmed"

        self._ensure_card_image_cache(item)
        if item.get("current_image_state") == image_state:
            return

        item["img_label"].configure(image=item["ctk_img_cache"][image_state])
        item["current_image_state"] = image_state

    def _refresh_all_card_visuals(self):
        for i in range(len(self.card_items)):
            self._refresh_card_visual(i)

    def _set_card_base_image(self, index: int, label: ctk.CTkLabel, pil_img: Image.Image):
        if index < 0 or index >= len(self.card_items):
            return
        item = self.card_items[index]
        if item["img_label"] is not label:
            return
        item["base_pil"] = pil_img.convert("RGBA")
        item["base_revision"] = int(item.get("base_revision", 0)) + 1
        item["ctk_img_cache"] = {}
        item["ctk_img_cache_revision"] = -1
        item["current_image_state"] = None
        self._refresh_card_visual(index)

    def _render_cards(self, keep_selection=False):
        prev_selected = self.selected_game_index if keep_selection else None
        self._clear_cards(keep_selection=keep_selection)

        if self.empty_label.winfo_exists():
            self.empty_label.grid_remove()

        cols = self._get_dynamic_column_count()
        self._configure_card_columns(cols)
        for i, game in enumerate(self.found_exe_list):
            row_idx = i // cols
            col_idx = i % cols
            card = self._make_card(i, game)
            card.grid(
                row=row_idx,
                column=col_idx,
                padx=(CARD_H_SPACING // 2, CARD_H_SPACING // 2),
                pady=(CARD_V_SPACING // 2, CARD_V_SPACING // 2),
                sticky="nsew",
            )
            self.card_frames.append(card)

        self._fit_cards_to_visible_width(cols)

        if keep_selection and prev_selected is not None and 0 <= prev_selected < len(self.found_exe_list):
            self.selected_game_index = prev_selected
            self._refresh_all_card_visuals()
            self._set_information_text(self.found_exe_list[prev_selected].get("information", ""))

        if not self.found_exe_list:
            self.empty_label.grid_remove()

        self._schedule_games_scrollregion_refresh()

        self._pump_image_jobs()

    def _make_card(self, index: int, game: dict) -> ctk.CTkFrame:
        card = ctk.CTkFrame(
            self.games_scroll,
            width=CARD_W,
            fg_color=_CARD_BG,
            corner_radius=0,
            border_width=2,
            border_color=_CARD_BG,
        )
        card.grid_propagate(False)
        card.configure(height=CARD_H)

        # Poster image area
        img_label = ctk.CTkLabel(card, text="", width=CARD_W, height=CARD_H)
        img_label.grid(row=0, column=0, padx=0, pady=0)

        # Hover overlay title (hidden by default)
        hover_title = ctk.CTkLabel(
            card,
            text=game["display"],
            font=ctk.CTkFont(family=FONT_UI, size=11, weight="bold"),
            text_color="#F1F5F9",
            fg_color="#2D3B4C",
            corner_radius=0,
            wraplength=CARD_W - 10,
            justify="center",
            width=CARD_W,
            height=34,
        )
        hover_title.place_forget()

        self.card_items.append(
            {
                "card": card,
                "img_label": img_label,
                "hover_title": hover_title,
                "base_pil": self._default_poster_base.copy().convert("RGBA"),
                "base_revision": 0,
                "ctk_img_cache": {},
                "ctk_img_cache_revision": -1,
                "current_image_state": None,
                "is_default_poster": True,
            }
        )

        self._refresh_card_visual(index)

        def _on_enter(_event=None, idx=index):
            prev = self._hovered_card_index
            self._hovered_card_index = idx
            if prev is not None and prev != idx:
                self._refresh_card_visual(prev)
            self._refresh_card_visual(idx)

        def _on_leave(_event=None, idx=index):
            if self._hovered_card_index == idx:
                self._hovered_card_index = None
            self._refresh_card_visual(idx)

        # Bind clicks
        for widget in (card, img_label, hover_title):
            widget.bind("<Button-1>", lambda _e, idx=index: self._set_selected_game(idx))
            widget.bind("<Double-Button-1>", lambda _e, idx=index: (self._set_selected_game(idx), self.apply_optiscaler()))
            widget.bind("<Enter>", _on_enter)
            widget.bind("<Leave>", _on_leave)

        # Load fallback first, then fetch real image asynchronously via queue.
        self._set_card_placeholder(index, img_label, game["display"])
        cover_url = game.get("cover_url", "")
        self._queue_card_image_fetch(
            index,
            img_label,
            game["display"],
            game.get("game_name", game.get("display", "")),
            cover_url,
        )

        return card

    def _set_card_placeholder(self, index: int, label: ctk.CTkLabel, title: str):
        pil_img = self._default_poster_base.copy().convert("RGBA")
        self.root.after(0, lambda idx=index, l=label, img=pil_img: self._set_card_base_image(idx, l, img))

    def _find_local_cover_asset(self, game_name: str) -> Optional[Path]:
        name = str(game_name or "").strip()
        if not name:
            return None

        search_names = [name]
        # Windows file names cannot contain ':', so also try a colon-stripped variant.
        colon_stripped = name.replace(":", "").strip()
        if colon_stripped and colon_stripped not in search_names:
            search_names.append(colon_stripped)

        # Priority order requested: webp -> jpg -> png.
        for lookup_name in search_names:
            for ext in (".webp", ".jpg", ".png"):
                candidate = ASSETS_DIR / f"{lookup_name}{ext}"
                if candidate.exists() and candidate.is_file():
                    return candidate

        return None

    def _poster_cache_key(self, title: str, url: str) -> str:
        normalized_url = ""
        if url:
            try:
                parsed = urlparse(url.strip())
                # Ignore query/fragment so cache survives signed URL changes.
                normalized_url = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}".strip().lower()
            except Exception:
                normalized_url = str(url).strip().lower()
        source = normalized_url or (title or "").strip().lower()
        if not source:
            source = "unknown"
        return hashlib.sha256(source.encode("utf-8")).hexdigest()

    def _poster_cache_path(self, title: str, url: str) -> Path:
        key = self._poster_cache_key(title, url)
        # Store processed poster in a stable format/path regardless of source URL extension.
        return self._poster_cache_dir / f"{key}.png"

    def _queue_card_image_fetch(self, index: int, label: ctk.CTkLabel, title: str, game_name: str, url: str):
        self._pending_image_jobs[index] = {
            "index": index,
            "label": label,
            "title": title,
            "game_name": game_name,
            "url": url,
            "generation": self._render_generation,
            "cache_key": self._poster_cache_key(title, url),
        }
        self._pump_image_jobs()

    def _visible_game_indices(self) -> set:
        total = len(self.found_exe_list)
        if total == 0:
            return set()

        cols = max(1, self._grid_cols_current)
        total_rows = max(1, math.ceil(total / cols))
        start_row = 0
        end_row = min(total_rows - 1, GRID_ROWS_VISIBLE)

        try:
            canvas = getattr(self.games_scroll, "_parent_canvas", None)
            if canvas is not None:
                y0, y1 = canvas.yview()
                start_row = max(0, int(y0 * total_rows))
                end_row = min(total_rows - 1, int(math.ceil(y1 * total_rows)))
        except Exception:
            pass

        visible = set()
        for r in range(start_row, end_row + 1):
            for c in range(cols):
                idx = r * cols + c
                if idx < total:
                    visible.add(idx)
        return visible

    def _image_priority_key(self, index: int, visible: set) -> tuple:
        if self._initial_image_pass:
            return (index,)

        is_visible = 0 if index in visible else 1
        if visible:
            nearest = min(abs(index - i) for i in visible)
        else:
            nearest = index
        return (is_visible, nearest, index)

    def _pump_image_jobs(self):
        self._collect_completed_image_jobs()

        if not self._pending_image_jobs and not self._inflight_image_futures and not self._scan_in_progress:
            self._initial_image_pass = False

        # Once all downloads finish and scan is done, retry failures exactly once.
        if (
            not self._pending_image_jobs
            and not self._inflight_image_futures
            and not self._scan_in_progress
            and self._failed_image_jobs
            and not self._retry_attempted
        ):
            self._retry_attempted = True
            logging.info("Retrying %d failed poster download(s)...", len(self._failed_image_jobs))
            for job in self._failed_image_jobs.values():
                job["generation"] = self._render_generation
            self._pending_image_jobs.update(self._failed_image_jobs)
            self._failed_image_jobs.clear()

        visible = self._visible_game_indices()

        while self._pending_image_jobs and len(self._inflight_image_futures) < IMAGE_MAX_WORKERS:
            next_index = min(self._pending_image_jobs.keys(), key=lambda idx: self._image_priority_key(idx, visible))
            job = self._pending_image_jobs.pop(next_index)

            # Skip stale jobs from previous renders.
            if job["generation"] != self._render_generation:
                continue

            future = self._image_executor.submit(
                self._load_poster_image_worker,
                job["title"],
                job.get("game_name", ""),
                job["url"],
            )
            self._inflight_image_futures[future] = job

        if (self._pending_image_jobs or self._inflight_image_futures) and self._image_queue_after_id is None:
            self._image_queue_after_id = self.root.after(120, self._image_queue_tick)

    def _collect_completed_image_jobs(self):
        completed = []
        for future, job in list(self._inflight_image_futures.items()):
            if future.done():
                completed.append((future, job))
                self._inflight_image_futures.pop(future, None)

        for future, job in completed:
            try:
                pil_img, _ = future.result()
                self._apply_loaded_poster(job["index"], job["label"], job["generation"], pil_img)
            except Exception as exc:
                logging.warning("Poster download failed (will retry): %s", exc)
                # Store for one automatic retry after all jobs are done.
                self._failed_image_jobs[job["index"]] = job

    def _image_queue_tick(self):
        self._image_queue_after_id = None
        self._pump_image_jobs()

    def _load_poster_image_worker(self, title: str, game_name: str, url: str) -> tuple[Image.Image, bool]:
        local_cover_path = self._find_local_cover_asset(game_name)
        if local_cover_path is not None:
            local_cache_key = f"local::{str(local_cover_path).lower()}"
            if ENABLE_POSTER_CACHE and local_cache_key in self._image_cache:
                return self._image_cache[local_cache_key], False
            try:
                pil_img = _prepare_cover_image(Image.open(local_cover_path), TARGET_POSTER_W, TARGET_POSTER_H)
                if ENABLE_POSTER_CACHE:
                    self._image_cache_put(local_cache_key, pil_img)
                return pil_img, False
            except Exception as exc:
                logging.warning("Failed to load local cover image from %s: %s", local_cover_path, exc)

        cache_key = self._poster_cache_key(title, url)
        if ENABLE_POSTER_CACHE and cache_key in self._image_cache:
            return self._image_cache[cache_key], False

        cache_path = self._poster_cache_path(title, url)
        if ENABLE_POSTER_CACHE and cache_path.exists():
            try:
                pil_img = Image.open(cache_path).convert("RGBA")
                self._image_cache_put(cache_key, pil_img)
                return pil_img, False
            except Exception:
                logging.warning("Failed to decode cached poster: %s", cache_path)

        if not url:
            fallback = self._default_poster_base.copy().convert("RGBA")
            return fallback, True

        try:
            with self._image_session.get(url, timeout=IMAGE_TIMEOUT_SECONDS, stream=True) as response:
                response.raise_for_status()
                data = b"".join(response.iter_content(chunk_size=65536))
            pil_img = _prepare_cover_image(Image.open(io.BytesIO(data)), TARGET_POSTER_W, TARGET_POSTER_H)

            if ENABLE_POSTER_CACHE:
                try:
                    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
                    pil_img.save(tmp_path, format="PNG")
                    tmp_path.replace(cache_path)
                except Exception:
                    logging.debug("Failed to write poster cache file: %s", cache_path)

                self._image_cache_put(cache_key, pil_img)
            return pil_img, False
        except Exception as exc:
            logging.warning("Failed to load cover image from %s: %s", url, exc)
            fallback = self._default_poster_base.copy().convert("RGBA")
            return fallback, True

    def _apply_loaded_poster(self, index: int, label: ctk.CTkLabel, generation: int, pil_img: Image.Image):
        if generation != self._render_generation:
            return
        self._set_card_base_image(index, label, pil_img)

    def _image_cache_put(self, key: str, pil_img: Image.Image):
        try:
            self._image_cache[key] = pil_img
            # Evict oldest entries when exceeding cap. Use dict insertion order (Py3.7+).
            if len(self._image_cache) > IMAGE_CACHE_MAX:
                try:
                    first_key = next(iter(self._image_cache))
                    del self._image_cache[first_key]
                except Exception:
                    # On any failure, fall back to clearing a single arbitrary item.
                    try:
                        self._image_cache.popitem(last=False)
                    except Exception:
                        pass
        except Exception:
            logging.exception("Failed to put image into cache for key=%s", key)

    def _set_selected_game(self, index: int):
        self.selected_game_index = index
        self._refresh_all_card_visuals()

        # Popup confirmation logic
        self._game_popup_confirmed = False
        if 0 <= index < len(self.found_exe_list):
            game = self.found_exe_list[index]
            self._set_information_text(game.get("information", ""))
            popup_msg = ""
            if USE_KOREAN:
                popup_msg = game.get("popup_kr", "").strip()
            else:
                popup_msg = game.get("popup_en", "").strip()
            if popup_msg:
                self.apply_btn.configure(state="disabled", fg_color=_ACCENT_DISABLED)
                def _on_confirm():
                    self._game_popup_confirmed = True
                    self.apply_btn.configure(state="normal", fg_color=_ACCENT)
                self._show_game_selection_popup(popup_msg, on_confirm=_on_confirm)
            else:
                self._game_popup_confirmed = True
                self.apply_btn.configure(state="normal", fg_color=_ACCENT)

    # ------------------------------------------------------------------
    # File dialogs
    # ------------------------------------------------------------------

    def select_opti_source_archive(self):
        path = filedialog.askopenfilename(filetypes=[("Archives", "*.zip *.7z"), ("All files", "*")])
        if not path:
            return
        self.opti_source_archive = path
        self.lbl_opti_path.configure(text="OptiScaler Selected", text_color="#F1F5F9")

    def select_game_folder(self):
        if self.sheet_loading:
            messagebox.showinfo("Game DB Loading", "Game DB is still loading. Please wait a moment.")
            return
        if not self.sheet_status:
            messagebox.showerror(
                "Game DB Error",
                "Failed to connect to Google Sheet. Please check network or sheet permissions.",
            )
            return

        self.game_folder = filedialog.askdirectory()
        if not self.game_folder:
            return

        self.lbl_game_path.configure(text="Scanning...", text_color="#F1F5F9")
        self.found_exe_list = []
        self._clear_cards()
        self._configure_card_columns(self._get_dynamic_column_count())
        self._scan_in_progress = True
        self._auto_scan_active = False
        self.btn_select_folder.configure(state="disabled")

        self._task_executor.submit(self._scan_worker, [self.game_folder])

    def _scan_worker(self, game_folders: list):
        """Background thread: walk one or more folders, post each found game to the main thread."""
        try:
            found_games = []
            seen_paths = set()  # deduplicate by (exe_key, normalised_dir)
            for game_folder in game_folders:
                try:
                    folder_iter = os.walk(game_folder)
                except Exception as walk_err:
                    logging.debug("Cannot walk %s: %s", game_folder, walk_err)
                    continue
                for root_dir, _, files in folder_iter:
                    for file in files:
                        key = file.lower()
                        if key in self.game_db:
                            dedup_key = (key, os.path.normcase(root_dir))
                            if dedup_key in seen_paths:
                                continue
                            seen_paths.add(dedup_key)
                            entry = self.game_db[key]
                            _kr_display = entry.get("game_name_kr", "") if USE_KOREAN else ""
                            _kr_info = entry.get("information_kr", "") if USE_KOREAN else ""
                            game = {
                                "path": root_dir,
                                "exe": file,
                                "display": _kr_display or entry["display"],
                                "game_name": entry.get("game_name", entry.get("display", "")),
                                "dll_name": entry["dll_name"],
                                "ini_settings": entry.get("ini_settings", {}),
                                "ingame_ini": entry.get("ingame_ini", ""),
                                "ingame_settings": entry.get("ingame_settings", {}),
                                "engine_ini_location": entry.get("engine_ini_location", ""),
                                "engine_ini_type": entry.get("engine_ini_type", ""),
                                "module_dl": entry.get("module_dl", ""),
                                "optipatcher": entry.get("optipatcher", False),
                                "unreal5_url": entry.get("unreal5_url", ""),
                                "unreal5": entry.get("unreal5", False),
                                "reframework_url": entry.get("reframework_url", ""),
                                "information": _kr_info or entry.get("information", ""),
                                "cover_url": entry.get("cover_url", ""),
                                "sheet_order": int(entry.get("sheet_order", 10**9)),
                                "popup_kr": entry.get("popup_kr", ""),
                                "popup_en": entry.get("popup_en", ""),
                                "after_popup_kr": entry.get("after_popup_kr", ""),
                                "after_popup_en": entry.get("after_popup_en", ""),
                                "guidepage_after_installation": entry.get("guidepage_after_installation", ""),
                            }
                            found_games.append(game)

            found_games.sort(key=lambda g: int(g.get("sheet_order", 10**9)))
            for game in found_games:
                self.root.after(0, lambda g=game: self._on_game_found(g))
        except Exception as exc:
            logging.error("Scan worker error: %s", exc)
        finally:
            self.root.after(0, self._on_scan_complete)

    def _on_game_found(self, game: dict):
        """Main-thread callback: add one game card immediately as it is discovered."""
        self._add_game_card_incremental(game)

    def _on_scan_complete(self):
        """Main-thread callback: finalize scan UI and kick any pending retry."""
        self._scan_in_progress = False
        is_auto = self._auto_scan_active
        self._auto_scan_active = False
        self.btn_select_folder.configure(state="normal")
        count = len(self.found_exe_list)
        self.lbl_game_path.configure(
            text=f"Supported Games : {count}",
            text_color="#F1F5F9",
        )
        if count > 0:
            self._set_information_text("Select a game to view information.")
        elif not is_auto:
            messagebox.showinfo("Scan Result", "No supported games found in selected folder.")
        # Trigger retry check now that scan is done.
        self._pump_image_jobs()

    def _add_game_card_incremental(self, game: dict):
        """Append one game to the list and immediately render + queue its cover download."""
        index = len(self.found_exe_list)
        self.found_exe_list.append(game)

        cols = max(1, self._grid_cols_current)
        row_idx = index // cols
        col_idx = index % cols

        card = self._make_card(index, game)
        card.grid(
            row=row_idx,
            column=col_idx,
            padx=(CARD_H_SPACING // 2, CARD_H_SPACING // 2),
            pady=(CARD_V_SPACING // 2, CARD_V_SPACING // 2),
            sticky="nsew",
        )
        self.card_frames.append(card)

        target_cols = self._max_safe_columns_for_width(self._get_forced_card_area_width())
        if target_cols < cols:
            self._fit_cards_to_visible_width(target_cols)

        # Expand scroll region so newly added row is reachable.
        self._schedule_games_scrollregion_refresh()

        self.lbl_game_path.configure(
            text=f"Supported Games : {len(self.found_exe_list)}",
            text_color="#F1F5F9",
        )

    # ------------------------------------------------------------------
    # Install
    # ------------------------------------------------------------------

    def _resolve_install_gpu_vendor(self, game_data):
        # Priority: global rule from link sheet -> module-specific rule -> optiscaler rule.
        # Rule format supports OR keyword matching via pipe, e.g. "140V|780M|890M".
        global_vendor = str(self.module_download_links.get("__gpu_vendor__", "")).strip().lower()
        if global_vendor:
            return global_vendor

        module_key = str(game_data.get("module_dl", "") or "").strip().lower()
        entry = None
        if module_key:
            entry = self.module_download_links.get(module_key)
        if not isinstance(entry, dict):
            entry = self.module_download_links.get("optiscaler")
        if isinstance(entry, dict):
            return str(entry.get("gpu_vendor", "")).strip().lower()
        return ""

    def _parse_gpu_rule_keywords(self, required_vendor: str) -> list[str]:
        text = str(required_vendor or "").strip().lower()
        if not text:
            return []

        # Backward compatible aliases.
        if text in {"all", "any", "*"}:
            return []

        # Primary delimiter is '|'. Also accept commas/semicolons for resilience.
        normalized = text.replace(";", "|").replace(",", "|")
        keywords = [token.strip() for token in normalized.split("|") if token.strip()]
        return keywords

    def _is_gpu_supported_for_install(self, required_vendor):
        gpu_text = self.gpu_info.lower()
        keywords = self._parse_gpu_rule_keywords(str(required_vendor or ""))
        if not keywords:
            return True

        # OR condition: at least one keyword must appear in current GPU model text.
        return any(keyword in gpu_text for keyword in keywords)

    def apply_optiscaler(self):
        if self.install_in_progress:
            messagebox.showinfo("Installing", "Installation is already in progress. Please wait.")
            return


        if self.selected_game_index is None:
            messagebox.showwarning("Warning", "Please select a game card to install.")
            return

        if not getattr(self, "opti_source_archive", None):
            messagebox.showwarning("Warning", "Please select the OptiScaler archive (.zip/.7z).")
            return

        if self.selected_game_index < 0 or self.selected_game_index >= len(self.found_exe_list):
            messagebox.showwarning("Warning", "Please select a valid game item.")
            return

        # Block install if popup not confirmed
        if not getattr(self, "_game_popup_confirmed", True):
            messagebox.showwarning("Notice", "Please confirm the popup before installing.")
            return

        game_data = dict(self.found_exe_list[self.selected_game_index])
        required_vendor = self._resolve_install_gpu_vendor(game_data)
        if not self._is_gpu_supported_for_install(required_vendor):
            messagebox.showerror(
                "Unsupported GPU",
                "Current GPU not supported.",
            )
            return

        source_archive = self.opti_source_archive

        self.install_in_progress = True
        self.apply_btn.configure(state="disabled", text="Installing…", fg_color=_ACCENT_DISABLED)

        self._task_executor.submit(self._apply_optiscaler_worker, game_data, source_archive)

    def _apply_optiscaler_worker(self, game_data, source_archive):
        target_path = game_data["path"]
        logger = get_game_logger(game_data.get("game_name", "unknown"))
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                extract_archive(source_archive, tmpdir)
                contents = os.listdir(tmpdir)
                if len(contents) == 1 and os.path.isdir(os.path.join(tmpdir, contents[0])):
                    actual_source = os.path.join(tmpdir, contents[0])
                else:
                    actual_source = tmpdir
                install_from_source_folder(actual_source, target_path, dll_name=game_data.get("dll_name", ""))
                logger.info(f"Extracted and installed files to {target_path}")

            module_key = str(game_data.get("module_dl", "")).strip().lower()

            unreal_link_entry = self.module_download_links.get(module_key) or self.module_download_links.get("unreal5")
            unreal_url = str(game_data.get("unreal5_url", "")).strip()
            if isinstance(unreal_link_entry, dict) and unreal_link_entry.get("url"):
                unreal_url = unreal_link_entry["url"]

            if game_data.get("unreal5") and unreal_url:
                install_unreal5_from_url(unreal_url, target_path)
                logger.info(f"Installed Unreal5 patch from {unreal_url} to {target_path}")

            if game_data.get("reframework_url"):
                install_reframework_dinput8_from_url(game_data["reframework_url"], target_path)
                logger.info(f"Installed REFramework dinput8.dll from {game_data['reframework_url']} to {target_path}")

            ini_path = os.path.join(target_path, "OptiScaler.ini")
            if not os.path.exists(ini_path):
                raise FileNotFoundError("OptiScaler.ini not found after installation")

            merged_ini_settings = dict(game_data.get("ini_settings", {}))
            if game_data.get("optipatcher"):
                opti_key = module_key or "optipatcher"
                opti_link_entry = self.module_download_links.get(opti_key) or self.module_download_links.get("optipatcher")
                opti_url = OPTIPATCHER_URL
                if isinstance(opti_link_entry, dict):
                    opti_url = opti_link_entry.get("url", OPTIPATCHER_URL)
                install_optipatcher(target_path, url=opti_url)
                merged_ini_settings["LoadAsiPlugins"] = "True"
                logger.info(f"Installed OptiPatcher from {opti_url} to {target_path}")

            apply_ini_settings(ini_path, merged_ini_settings, force_frame_generation=True)
            logger.info(f"Applied ini settings to {ini_path}")

            # Optional in-game ini patching from sheet columns:
            # - only when #ingame_ini is provided
            # - only when that file already exists in target folder
            # - update only keys present in #ingame_setting (no key/file creation)
            ingame_ini_name = str(game_data.get("ingame_ini", "")).strip()
            ingame_settings = dict(game_data.get("ingame_settings", {}) or {})
            if ingame_ini_name and ingame_settings:
                # Determine if ingame_ini_name is a full path (contains folder) or just a filename
                if any(sep in ingame_ini_name for sep in ("/", "\\", ":")):
                    # Treat as path, expand env vars
                    expanded_path = os.path.expandvars(ingame_ini_name)
                    expanded_path = os.path.expanduser(expanded_path)
                    ingame_ini_path = expanded_path
                else:
                    # Just a filename, use game exe folder
                    ingame_ini_path = os.path.join(target_path, ingame_ini_name)

                if os.path.exists(ingame_ini_path):
                    ini_file = Path(ingame_ini_path)
                    # Check original read-only state
                    orig_stat = ini_file.stat()
                    orig_readonly = not (orig_stat.st_mode & stat.S_IWRITE)
                    try:
                        if orig_readonly:
                            _ensure_file_writable(ini_file)
                        apply_ini_settings(ingame_ini_path, ingame_settings, force_frame_generation=False)
                        logger.info(f"Applied in-game settings to {ingame_ini_path}")
                    finally:
                        # Restore original read-only state
                        if orig_readonly:
                            _set_file_readonly(ini_file)
                else:
                    logger.info(f"In-game ini not found, skipped: {ingame_ini_path}")

            try:
                engine_loc = str(game_data.get("engine_ini_location", "")).strip()
                engine_ini_content = str(game_data.get("engine_ini_type", "")).strip()
                logger.info(f"engine.ini info for install: target={target_path}, engine_ini_location='{engine_loc}'")
                
                if engine_loc and engine_ini_content:
                    ini_path = _find_or_create_engine_ini(engine_loc, workspace_root=target_path)
                    
                    if ini_path:
                        try:
                            _ensure_file_writable(ini_path)
                            section_map = _parse_version_text_to_ini_entries(engine_ini_content)
                            
                            if section_map:
                                _upsert_ini_entries(ini_path, section_map)
                                logger.info(f"Upserted engine.ini entries to {ini_path}")
                        finally:
                            _set_file_readonly(ini_path)
            except Exception:
                logging.exception("Failed while handling engine.ini for %s", target_path)

            self.root.after(0, lambda: self._on_install_finished(True, "Install Completed"))
        except Exception as e:
            self.root.after(0, lambda err=e: self._on_install_finished(False, str(err)))

    def _on_install_finished(self, success, message):
        self.install_in_progress = False
        self.apply_btn.configure(state="normal", text="Install", fg_color=_ACCENT)

        if success:
            # Use after_popup_kr/en if present, else fallback
            game = self.found_exe_list[self.selected_game_index] if hasattr(self, 'selected_game_index') and self.selected_game_index is not None and self.selected_game_index < len(self.found_exe_list) else {}
            # Debug logging to help diagnose missing guide URL / popup behavior
            pass
            self._show_after_install_popup(game)
        else:
            messagebox.showerror("Error", f"An error occurred during installation: {message}")

    def _show_success_popup(self, message):
        popup = ctk.CTkToplevel(self.root)
        popup.title("Success")
        popup.transient(self.root)
        popup.grab_set()
        popup.resizable(False, False)
        popup.configure(fg_color=_SURFACE)

        ctk.CTkLabel(
            popup,
            text="✓  " + message,
            font=ctk.CTkFont(family=FONT_HEADING, size=14, weight="bold"),
            text_color="#2CC826",
            padx=30,
            pady=24,
        ).pack()

        ctk.CTkButton(
            popup,
            text="OK",
            width=100,
            height=34,
            corner_radius=8,
            fg_color=_ACCENT,
            hover_color=_ACCENT_HOVER,
            text_color="#000000",
            font=ctk.CTkFont(family=FONT_UI, size=12, weight="bold"),
            command=popup.destroy,
        ).pack(pady=(0, 20))

        popup.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() // 2) - (popup.winfo_width() // 2)
        y = self.root.winfo_rooty() + (self.root.winfo_height() // 2) - (popup.winfo_height() // 2)
        popup.geometry(f"+{x}+{y}")


if __name__ == "__main__":
    if "--edit-engine-ini" in sys.argv:
        logging.info("Running engine.ini edits from Google Sheet (gid=%s)", SHEET_GID)
        try:
            process_engine_ini_edits(SHEET_ID, gid=SHEET_GID)
        except Exception:
            logging.exception("engine.ini edit run failed")
        sys.exit(0)

    root = ctk.CTk()
    app = OptiManagerApp(root)
    root.mainloop()
