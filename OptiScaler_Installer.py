import os
import io
import shutil
import tempfile
import zipfile
import tkinter as tk
import time
import tkinter.font as tkfont
import math
import hashlib
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlparse
from tkinter import filedialog, messagebox
import logging
import sys
from pathlib import Path
import re
import webbrowser
from typing import Callable, Optional
import ctypes
import stat
from installer import app_update
from installer.app import (
    gpu_notice,
    message_popup,
    render_markup_to_text_widget,
    rtss_notice,
    strip_markup_text,
)
from installer.config import ini_utils
from installer.data import sheet_loader
from installer.games import scanner as game_scanner
from installer.i18n import (
    detect_ui_language,
    get_app_strings,
    is_korean,
    pick_module_message,
    pick_sheet_text,
    translate_default_precheck_error,
)
from installer.install import (
    OPTISCALER_ASI_NAME,
    install_optipatcher,
    install_reframework_dinput8,
    install_ultimate_asi_loader,
    install_unreal5_patch,
    services as installer_services,
)
from installer.system import gpu_service

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


def _iter_env_file_candidates() -> tuple[Path, ...]:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        # Keep the bundled .env as a fallback, but let a sidecar .env next to the
        # built executable override it so config changes do not require a rebuild.
        candidates.append(Path(sys._MEIPASS) / ".env")
        candidates.append(Path(sys.executable).resolve().parent / ".env")
    else:
        candidates.append(Path(__file__).resolve().parent / ".env")

    unique_candidates: list[Path] = []
    seen_candidates = set()
    for candidate in candidates:
        normalized = str(candidate.resolve(strict=False)).lower()
        if normalized in seen_candidates:
            continue
        seen_candidates.add(normalized)
        unique_candidates.append(candidate)
    return tuple(unique_candidates)


 # Application Version
APP_VERSION = "0.2.4"
# Install flow supports up to two detected GPUs. Dual-GPU requires explicit user selection.
MAX_SUPPORTED_GPU_COUNT = 2

 # Configure logging deterministically below (avoid calling basicConfig early)

 # Load .env file(s) deterministically and let the most local file win.
for _env_path in _iter_env_file_candidates():
    if _env_path.exists():
        # Override inherited env vars so VS Code terminals or parent processes
        # cannot keep stale values after .env changes.
        load_dotenv(_env_path, override=True)


def _get_int_env(name: str, default: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        logging.warning("[APP] Invalid integer env %s=%r, using %s", name, raw, default)
        return default


 # Allow overriding these values via environment variables for easier testing/config
SHEET_ID = os.environ.get("OPTISCALER_SHEET_ID", "")
SHEET_GID = _get_int_env("OPTISCALER_SHEET_GID", 0)
DOWNLOAD_LINKS_SHEET_GID = _get_int_env("OPTISCALER_DOWNLOAD_LINKS_SHEET_GID", 0)
SUPPORTED_GAMES_WIKI_URL = str(os.environ.get("SUPPORTED_GAMES_WIKI_URL", "") or "").strip()
GPU_VENDOR_DB_GIDS = {
    "intel": _get_int_env("DB_INTEL_GID", SHEET_GID),
    "amd": _get_int_env("DB_AMD_GID", SHEET_GID),
    "nvidia": _get_int_env("DB_NVIDIA_GID", SHEET_GID),
}

if not SHEET_ID:
    logging.warning("[APP] OPTISCALER_SHEET_ID not found in environment variables or .env file.")

OPTIPATCHER_URL = os.environ.get(
    "OPTIPATCHER_URL",
    "https://github.com/optiscaler/OptiPatcher/releases/latest/download/OptiPatcher.asi",
)

import logging.handlers


class PrefixedLoggerAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        prefix = self.extra.get("prefix", "APP")
        return f"[{prefix}] {msg}", kwargs


def get_prefixed_logger(prefix: str = "APP") -> PrefixedLoggerAdapter:
    return PrefixedLoggerAdapter(logging.getLogger(), {"prefix": prefix})

# File logging handler with fallbacks: app folder -> %LOCALAPPDATA% -> temp dir
def _init_file_logger() -> Optional[Path]:
    candidates: list[Path] = []

    try:
        if getattr(sys, 'frozen', False) and hasattr(sys, 'executable'):
            candidates.append(Path(sys.executable).resolve().parent)
        else:
            candidates.append(Path(__file__).resolve().parent)
    except Exception:
        pass

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "OptiScalerInstaller")

    candidates.append(Path(tempfile.gettempdir()) / "OptiScalerInstaller")

    root_logger = logging.getLogger()
    formatter = logging.Formatter("%(asctime)s %(levelname)s: %(message)s")

    for directory in candidates:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            log_path = directory / f"installer_{time.strftime('%Y-%m-%d_%H-%M-%S')}.log"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("")

            for h in list(root_logger.handlers):
                if isinstance(h, logging.FileHandler):
                    root_logger.removeHandler(h)
                    try:
                        h.close()
                    except Exception:
                        pass

            fh = logging.FileHandler(log_path, encoding="utf-8")

            fh.setLevel(logging.INFO)
            fh.setFormatter(formatter)
            root_logger.addHandler(fh)
            get_prefixed_logger("APP").info("OptiScaler Installer version %s", APP_VERSION)
            get_prefixed_logger("APP").info("File logging initialized")
            return log_path
        except Exception as e:
            try:
                print(f"Warning: failed to initialize file logging at {directory}: {e}", file=sys.stderr)
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
        logging.exception("[APP] Failed during file logger initialization")


_configure_logging()

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ModuleNotFoundError as e:
    logging.error("[APP] requests module not installed. Install: python -m pip install requests")
    raise e
APP_LANG = detect_ui_language()
APP_STRINGS = get_app_strings(APP_LANG)
USE_KOREAN: bool = is_korean(APP_LANG)
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
GRID_W = (CARD_W * GRID_COLS) + (CARD_H_SPACING * GRID_COLS) + (GRID_SIDE_PADDING * 2)
GRID_H = CARD_H * GRID_ROWS_VISIBLE
WINDOW_W = GRID_W
WINDOW_H = 710
WINDOW_MIN_W = 360
WINDOW_MIN_H = 420
_SM_CONVERTIBLESLATEMODE = 0x2003
LOCAL_APPDATA_DIR = Path(os.environ.get("LOCALAPPDATA") or Path(tempfile.gettempdir()))
APP_CACHE_DIR = LOCAL_APPDATA_DIR / "OptiScalerInstaller"
OPTISCALER_CACHE_DIR = APP_CACHE_DIR / "cache" / "optiscaler"
FSR4_CACHE_DIR = APP_CACHE_DIR / "cache" / "fsr4"
COVER_CACHE_DIR = APP_CACHE_DIR / "cache" / "covers"
COVERS_REPO_RAW_BASE_URL = str(
    os.environ.get(
        "OPTISCALER_COVERS_RAW_BASE_URL",
        "https://raw.githubusercontent.com/onehoon/OptiScalerInstaller/covers/assets",
    )
    or ""
).strip().rstrip("/")
FSR4_SKIP_GPU_RULE = "*rx 90*"
APP_BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ASSETS_DIR = APP_BASE_DIR / "assets"
DEFAULT_POSTER_CANDIDATES = [
    ASSETS_DIR / "default_poster.webp",
    ASSETS_DIR / "default_poster.jpg",
    ASSETS_DIR / "default_poster.png",
]
ALLOWED_COVER_IMAGE_EXTENSIONS = {".webp", ".png", ".jpg"}
BUNDLED_COVER_FILENAME_MAP = {
    "rtss.webp": "RTSS.webp",
}


def _is_windows_slate_mode() -> bool:
    if os.name != "nt":
        return False
    try:
        return int(ctypes.windll.user32.GetSystemMetrics(_SM_CONVERTIBLESLATEMODE)) == 0
    except Exception:
        logging.debug("[APP] Failed to read SM_CONVERTIBLESLATEMODE", exc_info=True)
        return False


def _build_centered_window_geometry(screen_w: int, screen_h: int, width: int, height: int) -> str:
    x = max(0, (max(1, int(screen_w)) - max(1, int(width))) // 2)
    y = max(0, (max(1, int(screen_h)) - max(1, int(height))) // 2)
    return f"{max(1, int(width))}x{max(1, int(height))}+{x}+{y}"


def _should_apply_umpc_window_workaround(screen_w: int, screen_h: int, target_w: int, target_h: int) -> bool:
    if not _is_windows_slate_mode():
        return False

    width_ratio = max(1, int(target_w)) / max(1, int(screen_w))
    height_ratio = max(1, int(target_h)) / max(1, int(screen_h))
    return width_ratio >= 0.90 or height_ratio >= 0.84 or max(1, int(screen_h)) <= WINDOW_H + 140


def _get_umpc_startup_window_size(screen_w: int, screen_h: int, target_w: int, target_h: int) -> tuple[int, int]:
    compact_w = min(int(target_w), max(WINDOW_MIN_W, int(screen_w) - max(96, int(screen_w) // 10)))
    compact_h = min(int(target_h), max(WINDOW_MIN_H, int(screen_h) - max(140, int(screen_h) // 6)))
    return max(WINDOW_MIN_W, compact_w), max(WINDOW_MIN_H, compact_h)
DEFAULT_POSTER_PATH = next((p for p in DEFAULT_POSTER_CANDIDATES if p.exists()), DEFAULT_POSTER_CANDIDATES[0])
IMAGE_TIMEOUT_SECONDS = 10
IMAGE_MAX_RETRIES = 3
IMAGE_MAX_WORKERS = 4
IMAGE_RETRY_DELAY_MS = int(os.environ.get("OPTISCALER_IMAGE_RETRY_DELAY_MS", "1500"))
HI_DPI_SCALE = 2
TARGET_POSTER_W = CARD_W * HI_DPI_SCALE
TARGET_POSTER_H = CARD_H * HI_DPI_SCALE
INFO_TEXT_OFFSET_PX = 10
POSTER_CACHE_VERSION = 2
ENABLE_POSTER_CACHE = os.environ.get("OPTISCALER_ENABLE_POSTER_CACHE", "1").strip().lower() in {"1", "true", "yes", "on"}
IMAGE_CACHE_MAX = int(os.environ.get("OPTISCALER_IMAGE_CACHE_MAX", "100"))


def _format_optiscaler_version_display_name(raw_name: str) -> str:
    name = Path(str(raw_name or "").strip()).name
    if not name:
        return ""

    name = re.sub(r"(?i)\.(zip|7z)$", "", name).strip()
    name = re.sub(r"(?i)^optiscaler", "", name).lstrip()
    name = re.sub(r"^[-_]+", "", name).lstrip()
    return re.sub(r"\s+", " ", name).strip()


def _normalize_cover_filename(value: str) -> str:
    raw_name = str(value or "").strip()
    if not raw_name:
        return ""
    if raw_name.lower() in {"null", "none", "na", "n/a", "-"}:
        return ""
    if any(sep in raw_name for sep in ("/", "\\", ":")):
        return ""
    if Path(raw_name).name != raw_name:
        return ""

    suffix = Path(raw_name).suffix.lower()
    if suffix not in ALLOWED_COVER_IMAGE_EXTENSIONS:
        return ""
    return raw_name


@contextmanager
def _temporary_logger_level(logger_names: tuple[str, ...], level: int):
    previous_levels: list[tuple[logging.Logger, int]] = []
    try:
        for logger_name in logger_names:
            logger = logging.getLogger(logger_name)
            previous_levels.append((logger, logger.level))
            logger.setLevel(level)
        yield
    finally:
        for logger, previous_level in reversed(previous_levels):
            logger.setLevel(previous_level)


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
_ACCENT_SUCCESS = "#7EE1AA"
_TITLE_TEXT = "#D6DCE5"
_BROWSE_BUTTON = "#5B6574"
_BROWSE_BUTTON_HOVER = "#6A7587"
_POPUP_OK_BUTTON = "#8A95A3"
_POPUP_OK_BUTTON_HOVER = "#99A4B1"
_INSTALL_BUTTON = "#D6AA43"
_INSTALL_BUTTON_HOVER = "#E2BA58"
_INSTALL_BUTTON_BORDER = "#F0D082"
_INSTALL_BUTTON_DISABLED = "#4B4338"
_INSTALL_BUTTON_BORDER_DISABLED = "#5B5246"
_INSTALL_BUTTON_TEXT = "#0B121A"
_STATUS_TEXT = "#C5CFDB"
_SECTION_LABEL_TEXT = "#C5CFDB"
_SELECTED_GAME_HIGHLIGHT = "#FFCB62"
_SCAN_STATUS_TEXT = "#AEB9C8"
_STATUS_INDICATOR_LOADING = "#7EE1AA"
_STATUS_INDICATOR_LOADING_DIM = "#415C4D"
_STATUS_INDICATOR_ONLINE = "#7EE1AA"
_STATUS_INDICATOR_WARNING = "#FFCB62"
_STATUS_INDICATOR_OFFLINE = "#FF8A8A"
_STATUS_INDICATOR_SIZE = 10
_STATUS_INDICATOR_Y_OFFSET = 2
_STATUS_INDICATOR_PULSE_MS = 620
_CONTENT_SIDE_PAD = 20
_META_RIGHT_PAD = 5
_SCAN_META_RIGHT_INSET = _CONTENT_SIDE_PAD + _META_RIGHT_PAD
_META_VALUE_GAP = 8
_LINK_ACTIVE = _SELECTED_GAME_HIGHLIGHT
_LINK_HOVER = "#FFE08F"
_CARD_BG = "#181B21"
_CARD_TITLE_OVERLAY_BG = "#243447"
_CARD_TITLE_OVERLAY_TEXT = "#FFFFFF"
_SURFACE = "#2A2E35"
_PANEL = "#1E2128"
_ACCENT_DISABLED = "#3A414C"
FONT_HEADING = APP_STRINGS.main.heading_font_family
FONT_UI = APP_STRINGS.main.ui_font_family
RTSS_NOTICE_THEME = rtss_notice.RtssNoticeTheme(
    surface_color=_SURFACE,
    accent_color=_ACCENT,
    accent_hover_color=_ACCENT_HOVER,
    font_ui=FONT_UI,
)
GPU_NOTICE_THEME = gpu_notice.GpuNoticeTheme(
    surface_color=_SURFACE,
    accent_color=_ACCENT,
    accent_hover_color=_ACCENT_HOVER,
    font_ui=FONT_UI,
)
MESSAGE_POPUP_THEME = message_popup.MessagePopupTheme(
    surface_color=_SURFACE,
    accent_color=_POPUP_OK_BUTTON,
    accent_hover_color=_POPUP_OK_BUTTON_HOVER,
    font_ui=FONT_UI,
)


class OptiManagerApp:
    def __init__(self, root: ctk.CTk):
        self.root = root
        self.lang = APP_LANG
        self.txt = APP_STRINGS
        self.root.title(self.txt.main.window_title_template.format(version=APP_VERSION))
        screen_w = max(1, int(self.root.winfo_screenwidth() or WINDOW_W))
        screen_h = max(1, int(self.root.winfo_screenheight() or WINDOW_H))
        target_w = min(WINDOW_W, max(WINDOW_MIN_W, screen_w - 40))
        target_h = min(WINDOW_H, max(WINDOW_MIN_H, screen_h - 80))
        self._startup_window_workaround_active = _should_apply_umpc_window_workaround(
            screen_w,
            screen_h,
            target_w,
            target_h,
        )
        if self._startup_window_workaround_active:
            target_w, target_h = _get_umpc_startup_window_size(screen_w, screen_h, target_w, target_h)
        self._startup_window_width = target_w
        self._startup_window_height = target_h

        if self._startup_window_workaround_active:
            self.root.geometry(_build_centered_window_geometry(screen_w, screen_h, target_w, target_h))
        else:
            self.root.geometry(f"{target_w}x{target_h}")
        self.root.minsize(target_w, target_h)
        self.root.update_idletasks()
        self.root.state("normal")
        self.root.overrideredirect(False)
        self.root.resizable(True, True)
        if self._startup_window_workaround_active:
            logging.info(
                "[APP] Enabling UMPC startup window workaround (screen=%sx%s, target=%sx%s)",
                screen_w,
                screen_h,
                target_w,
                target_h,
            )

        self.game_folder = ""
        self.opti_source_archive = ""
        self.fsr4_source_archive = ""
        self.optiscaler_cache_dir = OPTISCALER_CACHE_DIR
        self.optiscaler_cache_dir.mkdir(parents=True, exist_ok=True)
        self.fsr4_cache_dir = FSR4_CACHE_DIR
        self.fsr4_cache_dir.mkdir(parents=True, exist_ok=True)
        self.cover_cache_dir = COVER_CACHE_DIR
        self.cover_cache_dir.mkdir(parents=True, exist_ok=True)
        self.optiscaler_archive_ready = False
        self.optiscaler_archive_downloading = False
        self.optiscaler_archive_error = ""
        self.optiscaler_archive_filename = ""
        self.fsr4_archive_ready = False
        self.fsr4_archive_downloading = False
        self.fsr4_archive_error = ""
        self.fsr4_archive_filename = ""
        self._post_sheet_startup_done = False
        self._startup_popup_queue: list[dict[str, object]] = []
        self._startup_popup_active = False
        self._startup_popup_order = 0
        self._initial_auto_scan_empty_popup_shown = False
        self.found_exe_list = []
        self.game_db = {}
        self.module_download_links = {}
        self._game_db_load_started = False
        self.active_game_db_vendor = "default"
        self.active_game_db_gid = SHEET_GID
        self.gpu_names: list[str] = []
        self.gpu_count = 0
        self.is_multi_gpu = False
        self.multi_gpu_blocked = False
        self._multi_gpu_popup_shown = False
        self._gpu_selection_pending = False
        self._gpu_context: Optional[gpu_service.GpuContext] = None
        self._selected_gpu_adapter: Optional[gpu_service.GpuAdapterChoice] = None
        self.sheet_status = False
        self.sheet_loading = True
        self.gpu_info = self.txt.main.checking_gpu
        self.install_in_progress = False
        self.selected_game_index = None
        self._game_popup_confirmed = False
        self.install_precheck_running = False
        self.install_precheck_ok = False
        self.install_precheck_error = ""
        self.install_precheck_dll_name = ""
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
        self._default_poster_base = _load_default_poster_base(TARGET_POSTER_W, TARGET_POSTER_H)
        self._image_session = self._build_retry_session()
        self._image_executor = ThreadPoolExecutor(max_workers=IMAGE_MAX_WORKERS, thread_name_prefix="cover-loader")
        self._task_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="general-task")
        self._download_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="archive-download")
        self._app_update_manager = app_update.InstallerUpdateManager(
            self.root,
            current_version=APP_VERSION,
            use_korean=USE_KOREAN,
            on_busy_state_changed=self._update_install_button_state,
            on_update_failed=lambda: self._run_post_sheet_startup(True),
            on_exit_requested=self._on_close,
        )
        self._pending_image_jobs: dict = {}
        self._inflight_image_futures: dict = {}
        self._failed_image_jobs: dict = {}
        self._delayed_image_retry_after_ids: dict[int, str] = {}
        self._render_generation = 0
        self._image_queue_after_id = None
        self._games_scrollregion_after_id = None
        self._games_viewport_after_id = None
        self._overflow_fit_after_id = None
        self._initial_image_pass = True
        self._scan_in_progress = False
        self._auto_scan_active = False
        self._retry_attempted = False
        self._status_indicator_after_id = None
        self._status_indicator_pulse_visible = True
        self._status_indicator_pulse_colors = (_STATUS_INDICATOR_LOADING, _STATUS_INDICATOR_LOADING_DIM)
        self._scan_meta_label_width = self._measure_meta_label_width(self._get_supported_games_meta_label_text())
        self.setup_ui()
        # Fetch GPU info asynchronously to avoid blocking startup on slow PowerShell
        try:
            self._task_executor.submit(self._fetch_gpu_info_async)
        except Exception:
            logging.exception("Failed to submit GPU info fetch task")
        self.root.bind("<Configure>", self._on_root_resize)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if self._startup_window_workaround_active:
            self.root.after_idle(self._apply_startup_window_workaround)
            self.root.after(220, self._apply_startup_window_workaround)
        self.root.after(250, self._capture_startup_width)

    def _apply_startup_window_workaround(self):
        if not getattr(self, "_startup_window_workaround_active", False):
            return

        try:
            screen_w = max(1, int(self.root.winfo_screenwidth() or self._startup_window_width))
            screen_h = max(1, int(self.root.winfo_screenheight() or self._startup_window_height))
            current_w = max(1, int(self.root.winfo_width() or self._startup_window_width))
            current_h = max(1, int(self.root.winfo_height() or self._startup_window_height))
            state = str(self.root.state() or "").strip().lower()
            is_effectively_maximized = (
                state == "zoomed"
                or current_w >= screen_w - 24
                or current_h >= screen_h - 24
            )
            if not is_effectively_maximized:
                return

            self.root.overrideredirect(False)
            self.root.state("normal")
            self.root.deiconify()
            self.root.geometry(
                _build_centered_window_geometry(
                    screen_w,
                    screen_h,
                    self._startup_window_width,
                    self._startup_window_height,
                )
            )
            self.root.update_idletasks()
            logging.info(
                "[APP] Restored startup window from maximized state to %sx%s",
                self._startup_window_width,
                self._startup_window_height,
            )
        except Exception:
            logging.debug("[APP] Failed to apply UMPC startup window workaround", exc_info=True)

    def _normalize_gpu_info_text(self, value: object) -> str:
        text = str(value or "").strip()
        if not text or text.lower().startswith("unknown"):
            return self.txt.main.unknown_gpu
        return text

    def _format_gpu_label_text(self, gpu_info: str) -> str:
        return self.txt.main.gpu_label_template.format(gpu=self._normalize_gpu_info_text(gpu_info))

    def _show_game_selection_popup(
        self,
        message_text: str,
        on_confirm: Optional[Callable[[], None]] = None,
    ) -> None:
        message_popup.show_message_popup(
            root=self.root,
            message_text=message_text,
            theme=MESSAGE_POPUP_THEME,
            title=self.txt.dialogs.installer_notice_title,
            confirm_text=self.txt.common.ok,
            on_close=(lambda: self.root.after_idle(on_confirm)) if callable(on_confirm) else None,
            allow_window_close=False,
            scrollable=False,
            debug_name="selection popup",
            preferred_text_chars=72,
            min_text_chars=58,
            max_text_chars=110,
            emphasis_font_size=13,
            emphasis_weight="normal",
            root_width_fallback=WINDOW_W,
            root_height_fallback=WINDOW_H,
        )

    def _fetch_gpu_info_async(self):
        try:
            gpu_context = gpu_service.detect_gpu_context(GPU_VENDOR_DB_GIDS, SHEET_GID)
        except Exception:
            logging.exception("Error fetching GPU info")
            gpu_context = gpu_service.GpuContext(
                gpu_names=[],
                gpu_count=0,
                gpu_info=self.txt.main.unknown_gpu,
                selected_vendor="default",
                selected_gid=SHEET_GID,
                adapters=(),
                selected_model_name="",
            )
        try:
            self.root.after(
                0,
                lambda: self._update_gpu_ui(gpu_context),
            )
        except Exception:
            logging.exception("Failed to schedule GPU UI update")

    def _is_multi_gpu_block_active(self) -> bool:
        return self.gpu_count > MAX_SUPPORTED_GPU_COUNT

    def _apply_multi_gpu_block_state(self):
        self.multi_gpu_blocked = self._is_multi_gpu_block_active()
        if not self.multi_gpu_blocked:
            return

        self._gpu_selection_pending = False
        self._selected_gpu_adapter = None
        self._post_sheet_startup_done = True
        self.sheet_loading = False
        self.sheet_status = False
        self.game_db = {}
        self.module_download_links = {}
        self.found_exe_list = []
        self.selected_game_index = None
        self.install_precheck_running = False
        self.install_precheck_ok = False
        self.install_precheck_error = ""
        self.install_precheck_dll_name = ""

        if hasattr(self, "btn_select_folder") and self.btn_select_folder:
            self.btn_select_folder.configure(state="disabled")
        text = self.txt.gpu.unsupported_message
        self._set_supported_games_value(None)
        self._set_scan_status_message(text, "#FF8A8A")
        self._clear_cards()
        if hasattr(self, "info_text") and self.info_text:
            self._set_information_text(gpu_notice.get_unsupported_gpu_message(USE_KOREAN))
        self._update_selected_game_header()
        self._update_sheet_status()
        self._update_install_button_state()
        if not self._multi_gpu_popup_shown:
            self._multi_gpu_popup_shown = True
            gpu_notice.show_unsupported_gpu_notice(self.root, USE_KOREAN, GPU_NOTICE_THEME)

    def _apply_selected_gpu(
        self,
        gpu_context: gpu_service.GpuContext,
        selected_adapter: Optional[gpu_service.GpuAdapterChoice] = None,
    ):
        self._gpu_context = gpu_context
        self.gpu_names = list(gpu_context.gpu_names or [])
        self.gpu_count = max(0, int(gpu_context.gpu_count or 0))
        self.is_multi_gpu = gpu_context.is_multi_gpu
        self.multi_gpu_blocked = False
        self._gpu_selection_pending = False
        self._selected_gpu_adapter = selected_adapter

        if selected_adapter is not None:
            self.active_game_db_vendor = str(selected_adapter.vendor or "default")
            self.active_game_db_gid = int(selected_adapter.selected_gid or SHEET_GID)
            self.gpu_info = self._normalize_gpu_info_text(
                selected_adapter.model_name or gpu_context.selected_model_name or gpu_context.gpu_info
            )
        else:
            self.active_game_db_vendor = str(gpu_context.selected_vendor or "default")
            self.active_game_db_gid = int(gpu_context.selected_gid or SHEET_GID)
            self.gpu_info = self._normalize_gpu_info_text(gpu_context.selected_model_name or gpu_context.gpu_info)

        if hasattr(self, "gpu_lbl") and self.gpu_lbl:
            self.gpu_lbl.configure(text=self._format_gpu_label_text(self.gpu_info))

        self._set_supported_games_value(None)
        self._set_scan_status_message("")
        self._update_sheet_status()
        self._update_install_button_state()

    def _update_gpu_ui(self, gpu_context: gpu_service.GpuContext):
        try:
            self._gpu_context = gpu_context
            self.gpu_names = list(gpu_context.gpu_names or [])
            self.gpu_count = max(0, int(gpu_context.gpu_count or 0))
            self.is_multi_gpu = gpu_context.is_multi_gpu
            self.multi_gpu_blocked = self._is_multi_gpu_block_active()
            if self.multi_gpu_blocked:
                self.gpu_info = self._normalize_gpu_info_text(gpu_context.gpu_info)
                if hasattr(self, "gpu_lbl") and self.gpu_lbl:
                    self.gpu_lbl.configure(text=self._format_gpu_label_text(self.gpu_info))
                self._apply_multi_gpu_block_state()
                return
            if self.gpu_count == 2 and len(gpu_context.adapters or ()) >= 2:
                self._gpu_selection_pending = True
                self._selected_gpu_adapter = None
                self.gpu_info = self.txt.main.waiting_for_gpu_selection
                if hasattr(self, "gpu_lbl") and self.gpu_lbl:
                    self.gpu_lbl.configure(text=self._format_gpu_label_text(self.gpu_info))
                self._set_supported_games_value(None)
                self._set_scan_status_message("")
                self._update_sheet_status()
                self._update_install_button_state()
                selected_adapter = gpu_notice.select_dual_gpu_adapter(
                    root=self.root,
                    adapters=tuple(gpu_context.adapters[:2]),
                    use_korean=USE_KOREAN,
                    theme=GPU_NOTICE_THEME,
                )
                if selected_adapter is None:
                    logging.warning("[GPU] Dual-GPU selection popup closed without a selection")
                    return
                self._apply_selected_gpu(gpu_context, selected_adapter)
                self._start_game_db_load_async()
                return
            self._apply_selected_gpu(gpu_context)
            self._start_game_db_load_async()
            self._update_install_button_state()
        except Exception:
            logging.exception("Failed to update GPU UI")

    def _is_game_supported_for_current_gpu(self, game_data: dict) -> bool:
        return gpu_service.matches_gpu_rule(str(game_data.get("supported_gpu", "") or ""), self.gpu_info)

    def _matches_fsr4_skip_rule(self, rule_text: str) -> bool:
        return gpu_service.matches_gpu_rule(FSR4_SKIP_GPU_RULE, rule_text)

    def _should_apply_fsr4_for_game(self, game_data: Optional[dict] = None) -> bool:
        if self._matches_fsr4_skip_rule(self.gpu_info):
            return False

        if isinstance(game_data, dict):
            supported_gpu_rule = str(game_data.get("supported_gpu", "") or "").strip()
            if supported_gpu_rule and self._matches_fsr4_skip_rule(supported_gpu_rule):
                return False

        return True

    def _get_supported_games_meta_label_text(self) -> str:
        return self.txt.main.supported_games_label

    def _measure_meta_label_width(self, *candidate_texts: str) -> int:
        try:
            meta_font = tkfont.Font(family=FONT_UI, size=12, weight="bold")
            candidates = tuple(text for text in candidate_texts if text)
            if not candidates:
                candidates = (self._get_supported_games_meta_label_text(),)
            return max(meta_font.measure(text) for text in candidates) + 2
        except Exception:
            return 120

    def _align_supported_games_count_label(self):
        return

    def _set_supported_games_value(self, value: Optional[object], text_color: str = _SECTION_LABEL_TEXT):
        if not hasattr(self, "lbl_supported_games_value") or not self.lbl_supported_games_value.winfo_exists():
            return
        display_value = "" if value is None else str(value)
        self.lbl_supported_games_value.configure(text=display_value, text_color=text_color)

    def _set_supported_games_wiki_link_hover(self, hovered: bool) -> None:
        if not hasattr(self, "lbl_supported_games_wiki_link") or not self.lbl_supported_games_wiki_link.winfo_exists():
            return
        if not SUPPORTED_GAMES_WIKI_URL:
            self.lbl_supported_games_wiki_link.configure(text_color=_STATUS_TEXT)
            return
        self.lbl_supported_games_wiki_link.configure(text_color=_LINK_HOVER if hovered else _LINK_ACTIVE)

    def _open_supported_games_wiki(self, _event=None) -> None:
        wiki_url = SUPPORTED_GAMES_WIKI_URL
        if not wiki_url:
            messagebox.showinfo(self.txt.common.notice, self.txt.dialogs.wiki_not_configured_detail)
            return

        try:
            if not webbrowser.open(wiki_url):
                raise RuntimeError("webbrowser.open returned False")
        except Exception:
            logging.exception("Failed to open supported games wiki URL: %s", wiki_url)
            messagebox.showerror(self.txt.common.error, self.txt.dialogs.wiki_open_failed_detail)

    def _set_scan_status_message(self, text: str = "", text_color: str = _SCAN_STATUS_TEXT):
        if not hasattr(self, "lbl_scan_status") or not self.lbl_scan_status.winfo_exists():
            return
        message = str(text or "").strip()
        if not message:
            self.lbl_scan_status.configure(text="")
            self.lbl_scan_status.grid_remove()
            return

        self.lbl_scan_status.configure(text=message, text_color=text_color)
        self.lbl_scan_status.grid()

    def _set_status_badge_state(self, label_text: str, indicator_color: str, pulse: bool = False):
        if not hasattr(self, "status_badge_label") or not hasattr(self, "status_badge_dot"):
            return
        if not self.status_badge_label.winfo_exists() or not self.status_badge_dot.winfo_exists():
            return

        self.status_badge_label.configure(text=label_text, text_color=_STATUS_TEXT)
        if pulse:
            self._start_status_badge_pulse(indicator_color, _STATUS_INDICATOR_LOADING_DIM)
            return

        self._stop_status_badge_pulse()
        self.status_badge_dot.configure(fg_color=indicator_color)

    def _start_status_badge_pulse(self, active_color: str, dim_color: str):
        self._stop_status_badge_pulse()
        self._status_indicator_pulse_colors = (active_color, dim_color)
        self._status_indicator_pulse_visible = True
        self.status_badge_dot.configure(fg_color=active_color)
        self._status_indicator_after_id = self.root.after(
            _STATUS_INDICATOR_PULSE_MS,
            self._tick_status_badge_pulse,
        )

    def _stop_status_badge_pulse(self):
        try:
            if self._status_indicator_after_id is not None:
                self.root.after_cancel(self._status_indicator_after_id)
        except Exception:
            pass
        self._status_indicator_after_id = None
        self._status_indicator_pulse_visible = True

    def _tick_status_badge_pulse(self):
        self._status_indicator_after_id = None
        if not hasattr(self, "status_badge_dot") or not self.root.winfo_exists() or not self.status_badge_dot.winfo_exists():
            return

        active_color, dim_color = self._status_indicator_pulse_colors
        next_visible = not self._status_indicator_pulse_visible
        self._status_indicator_pulse_visible = next_visible
        self.status_badge_dot.configure(fg_color=active_color if next_visible else dim_color)
        self._status_indicator_after_id = self.root.after(
            _STATUS_INDICATOR_PULSE_MS,
            self._tick_status_badge_pulse,
        )

    def _get_selected_game_header_text(self) -> str:
        if self.selected_game_index is None or not (0 <= self.selected_game_index < len(self.found_exe_list)):
            return ""

        game = self.found_exe_list[self.selected_game_index]
        if USE_KOREAN:
            game_name = str(game.get("display", "") or game.get("game_name_kr", "") or game.get("game_name", "")).strip()
        else:
            game_name = str(game.get("game_name", "") or game.get("display", "")).strip()
        return game_name

    def _update_selected_game_header(self):
        try:
            game_name = self._get_selected_game_header_text()
            if hasattr(self, "lbl_selected_game_header") and self.lbl_selected_game_header.winfo_exists():
                self.lbl_selected_game_header.configure(text=game_name)
        except Exception:
            logging.debug("Failed to update selected game header", exc_info=True)

    def _show_after_install_popup(self, game: dict):
        msg = pick_sheet_text(game, "after_popup", self.lang)
        if not msg:
            msg = self.txt.dialogs.installation_completed
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

        self._show_game_selection_popup(msg, on_confirm=_on_confirm_open_guide)

    def _update_install_button_state(self):
        if not hasattr(self, "apply_btn"):
            return

        has_valid_game = (
            self.selected_game_index is not None
            and 0 <= self.selected_game_index < len(self.found_exe_list)
        )
        has_supported_gpu = (
            self._is_game_supported_for_current_gpu(self.found_exe_list[self.selected_game_index])
            if has_valid_game else True
        )
        fsr4_required = (
            self._should_apply_fsr4_for_game(self.found_exe_list[self.selected_game_index])
            if has_valid_game else False
        )
        fsr4_ready = (
            not fsr4_required
            or (
                self.fsr4_archive_ready
                and not self.fsr4_archive_downloading
            )
        )
        can_install = (
            not self.multi_gpu_blocked
            and not self._gpu_selection_pending
            and self.sheet_status
            and not self.sheet_loading
            and not self.install_in_progress
            and not self._app_update_manager.in_progress
            and has_valid_game
            and not self.install_precheck_running
            and self.install_precheck_ok
            and self.optiscaler_archive_ready
            and not self.optiscaler_archive_downloading
            and fsr4_ready
            and has_supported_gpu
            and getattr(self, "_game_popup_confirmed", False)
        )

        self.apply_btn.configure(
            state="normal" if can_install else "disabled",
            text=self.txt.main.install_button if not self.install_in_progress else self.txt.main.installing_button,
            fg_color=_INSTALL_BUTTON if can_install else _INSTALL_BUTTON_DISABLED,
            hover_color=_INSTALL_BUTTON_HOVER if can_install else _INSTALL_BUTTON_DISABLED,
            border_color=_INSTALL_BUTTON_BORDER if can_install else _INSTALL_BUTTON_BORDER_DISABLED,
        )

    # ------------------------------------------------------------------
    # Async DB load
    # ------------------------------------------------------------------

    def _on_close(self):
        if self.install_in_progress:
            messagebox.showwarning(self.txt.common.warning, self.txt.dialogs.close_while_installing_body)
            return

        try:
            if self._image_queue_after_id is not None:
                self.root.after_cancel(self._image_queue_after_id)
                self._image_queue_after_id = None
        except Exception:
            pass
        for index in list(self._delayed_image_retry_after_ids.keys()):
            self._cancel_delayed_image_retry(index)
        try:
            if self._games_scrollregion_after_id is not None:
                self.root.after_cancel(self._games_scrollregion_after_id)
                self._games_scrollregion_after_id = None
        except Exception:
            pass
        try:
            if self._games_viewport_after_id is not None:
                self.root.after_cancel(self._games_viewport_after_id)
                self._games_viewport_after_id = None
        except Exception:
            pass
        try:
            if self._overflow_fit_after_id is not None:
                self.root.after_cancel(self._overflow_fit_after_id)
                self._overflow_fit_after_id = None
        except Exception:
            pass
        try:
            if self._status_indicator_after_id is not None:
                self.root.after_cancel(self._status_indicator_after_id)
                self._status_indicator_after_id = None
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
        try:
            self._download_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        try:
            self._app_update_manager.shutdown()
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
        if self._game_db_load_started:
            return
        self._game_db_load_started = True

        game_db_gid = int(getattr(self, "active_game_db_gid", SHEET_GID) or SHEET_GID)
        game_db_vendor = str(getattr(self, "active_game_db_vendor", "default") or "default")
        logging.info(
            "[APP] Starting Game DB load for vendor=%s gpu=%s",
            game_db_vendor,
            self.gpu_info,
        )
        self._task_executor.submit(self._load_game_db_worker, game_db_gid, game_db_vendor)

    def _load_game_db_worker(self, game_db_gid: int, game_db_vendor: str):
        try:
            db = sheet_loader.load_game_db_from_public_sheet(SHEET_ID, game_db_gid)
            if not db:
                raise ValueError("Sheet has no data.")

            module_links = {}
            try:
                module_links = sheet_loader.load_module_download_links_from_public_sheet(SHEET_ID, DOWNLOAD_LINKS_SHEET_GID)
            except Exception as link_err:
                logging.warning("Failed to load download-link sheet (gid=%s): %s", DOWNLOAD_LINKS_SHEET_GID, link_err)

            self.root.after(
                0,
                lambda db=db, links=module_links, gid=game_db_gid, vendor=game_db_vendor:
                    self._on_game_db_loaded(db, links, True, None, gid, vendor),
            )
        except Exception as e:
            self.root.after(
                0,
                lambda err=e, gid=game_db_gid, vendor=game_db_vendor:
                    self._on_game_db_loaded({}, {}, False, err, gid, vendor),
            )

    def _on_game_db_loaded(self, db, module_links, ok, err, game_db_gid=None, game_db_vendor="default"):
        self.sheet_loading = False
        if game_db_gid is not None:
            self.active_game_db_gid = int(game_db_gid)
        if game_db_vendor:
            self.active_game_db_vendor = str(game_db_vendor)
        self.game_db = db if ok else {}
        self.module_download_links = module_links if ok else {}

        self.sheet_status = ok
        if ok:
            logging.info(
                "[APP] Game DB loaded successfully: vendor=%s, games=%d, module_links=%d",
                self.active_game_db_vendor,
                len(self.game_db),
                len(self.module_download_links),
            )
        else:
            logging.error(
                "[APP] Failed to load Game DB for vendor=%s: %s",
                self.active_game_db_vendor,
                err,
            )
        if self.multi_gpu_blocked:
            self._update_install_button_state()
            self._update_sheet_status()
            return
        self._refresh_optiscaler_archive_info_ui()
        self._update_install_button_state()
        self._update_sheet_status()
        update_started = self.check_app_update() if ok else False
        if not update_started:
            self._run_post_sheet_startup(ok)

    def _get_optiscaler_archive_entry(self) -> dict:
        entry = self.module_download_links.get("optiscaler", {}) if hasattr(self, "module_download_links") else {}
        return entry if isinstance(entry, dict) else {}

    def _get_expected_optiscaler_archive_name(self) -> str:
        entry = self._get_optiscaler_archive_entry()
        filename = str(entry.get("filename", "") or entry.get("version", "")).strip()
        if filename:
            return Path(filename).name

        url = str(entry.get("url", "")).strip()
        if not url:
            return ""
        parsed = urlparse(url)
        return Path(parsed.path).name

    def _get_fsr4_archive_entry(self) -> dict:
        entry = self.module_download_links.get("fsr4int8", {}) if hasattr(self, "module_download_links") else {}
        return entry if isinstance(entry, dict) else {}

    def _get_expected_fsr4_archive_name(self) -> str:
        entry = self._get_fsr4_archive_entry()
        filename = str(entry.get("filename", "") or entry.get("version", "")).strip()
        if filename:
            return Path(filename).name

        url = str(entry.get("url", "")).strip()
        if not url:
            return ""
        parsed = urlparse(url)
        return Path(parsed.path).name

    def _list_stale_optiscaler_archive_paths(self, keep_filename: str) -> list[Path]:
        cache_dir = Path(getattr(self, "optiscaler_cache_dir", OPTISCALER_CACHE_DIR))
        if not cache_dir.exists():
            return []

        keep_name = Path(str(keep_filename or "")).name.casefold()
        archive_suffixes = {".7z", ".zip", ".rar", ".tar", ".gz", ".xz", ".bz2"}
        stale_paths: list[Path] = []
        for cache_path in cache_dir.iterdir():
            if not cache_path.is_file():
                continue
            if keep_name and cache_path.name.casefold() == keep_name:
                continue
            if cache_path.suffix.lower() not in archive_suffixes:
                continue
            stale_paths.append(cache_path)
        return sorted(stale_paths)

    def _cleanup_stale_optiscaler_archives(self, keep_filename: str):
        for stale_path in self._list_stale_optiscaler_archive_paths(keep_filename):
            try:
                stale_path.unlink()
                logging.info("[APP] Removed stale OptiScaler archive cache: %s", stale_path)
            except OSError:
                logging.warning("[APP] Failed to remove stale OptiScaler archive cache: %s", stale_path, exc_info=True)

    def _start_optiscaler_archive_prepare(self):
        entry = self._get_optiscaler_archive_entry()
        url = str(entry.get("url", "")).strip()
        filename = self._get_expected_optiscaler_archive_name()
        self.optiscaler_archive_filename = filename

        if not url or not filename:
            self.optiscaler_archive_ready = False
            self.optiscaler_archive_downloading = False
            self.optiscaler_archive_error = "Missing archive metadata in sheet."
            self.opti_source_archive = ""
            logging.warning(
                "[APP] OptiScaler archive preparation skipped: missing metadata (url=%r, filename=%r, entry=%r)",
                url,
                filename,
                entry,
            )
            self._start_fsr4_archive_prepare()
            self._update_install_button_state()
            return

        cache_path = self.optiscaler_cache_dir / filename
        self.opti_source_archive = str(cache_path)
        if cache_path.exists():
            self.optiscaler_archive_ready = True
            self.optiscaler_archive_downloading = False
            self.optiscaler_archive_error = ""
            logging.info("[APP] OptiScaler archive already cached: %s", cache_path)
            self._cleanup_stale_optiscaler_archives(filename)
            self._start_fsr4_archive_prepare()
            self._update_install_button_state()
            return

        self.optiscaler_archive_ready = False
        self.optiscaler_archive_downloading = True
        self.optiscaler_archive_error = ""
        logging.info("[APP] Starting OptiScaler archive download: %s -> %s", url, cache_path)
        self._update_install_button_state()
        self._download_executor.submit(self._download_optiscaler_archive_worker, url, str(cache_path), filename)

    def _download_optiscaler_archive_worker(self, url: str, dest_path: str, archive_name: str):
        try:
            installer_services.download_to_file(url, dest_path, timeout=300)
            logging.info("[APP] OptiScaler archive download completed: %s", dest_path)
            self.root.after(
                0,
                lambda path=dest_path, name=archive_name: self._on_optiscaler_archive_ready(path, name, None),
            )
        except Exception as exc:
            logging.error("[APP] OptiScaler archive download failed: %s", exc)
            self.root.after(
                0,
                lambda err=str(exc): self._on_optiscaler_archive_ready("", archive_name, err),
            )

    def _on_optiscaler_archive_ready(self, archive_path: str, archive_name: str, error_message: Optional[str]):
        self.optiscaler_archive_filename = archive_name
        self.optiscaler_archive_downloading = False
        if error_message:
            self.optiscaler_archive_ready = False
            self.optiscaler_archive_error = error_message
            self.opti_source_archive = ""
            logging.warning("[APP] OptiScaler archive is not ready: %s", error_message)
        else:
            self.optiscaler_archive_ready = True
            self.optiscaler_archive_error = ""
            self.opti_source_archive = archive_path
            logging.info("[APP] OptiScaler archive is ready: %s", archive_path)
            self._cleanup_stale_optiscaler_archives(archive_name)

        self._start_fsr4_archive_prepare()
        self._update_install_button_state()

    def _start_fsr4_archive_prepare(self):
        if not self._should_apply_fsr4_for_game():
            self.fsr4_archive_ready = False
            self.fsr4_archive_downloading = False
            self.fsr4_archive_error = ""
            self.fsr4_archive_filename = ""
            self.fsr4_source_archive = ""
            logging.info("[APP] Skipping FSR4 preparation for GPU: %s", self.gpu_info)
            self._update_install_button_state()
            return

        entry = self._get_fsr4_archive_entry()
        url = str(entry.get("url", "")).strip()
        filename = self._get_expected_fsr4_archive_name()
        self.fsr4_archive_filename = filename

        if not url or not filename:
            self.fsr4_archive_ready = False
            self.fsr4_archive_downloading = False
            self.fsr4_archive_error = "Missing FSR4 download metadata in sheet."
            self.fsr4_source_archive = ""
            logging.warning(
                "[APP] FSR4 preparation skipped: missing metadata (filename=%r, entry=%r)",
                filename,
                entry,
            )
            self._update_install_button_state()
            return

        cache_path = self.fsr4_cache_dir / filename
        self.fsr4_source_archive = str(cache_path)
        if cache_path.exists():
            if cache_path.suffix.lower() == ".zip" and not zipfile.is_zipfile(cache_path):
                logging.warning("[APP] Cached FSR4 file is invalid, removing and downloading again: %s", cache_path)
                try:
                    cache_path.unlink()
                except OSError as exc:
                    self.fsr4_archive_ready = False
                    self.fsr4_archive_downloading = False
                    self.fsr4_archive_error = f"Failed to remove invalid FSR4 cache: {exc}"
                    self.fsr4_source_archive = ""
                    self._update_install_button_state()
                    return
            else:
                self.fsr4_archive_ready = True
                self.fsr4_archive_downloading = False
                self.fsr4_archive_error = ""
                logging.info("[APP] FSR4 already cached: %s", cache_path)
                self._update_install_button_state()
                return

        self.fsr4_archive_ready = False
        self.fsr4_archive_downloading = True
        self.fsr4_archive_error = ""
        logging.info("[APP] Starting FSR4 download: %s", filename)
        self._update_install_button_state()
        self._download_executor.submit(self._download_fsr4_archive_worker, url, str(cache_path), filename)

    def _download_fsr4_archive_worker(self, url: str, dest_path: str, archive_name: str):
        try:
            installer_services.download_to_file(url, dest_path, timeout=300)
            if Path(dest_path).suffix.lower() == ".zip" and not zipfile.is_zipfile(dest_path):
                Path(dest_path).unlink(missing_ok=True)
                raise RuntimeError(f"Downloaded FSR4 file is not a valid zip file: {dest_path}")
            logging.info("[APP] FSR4 download completed: %s", dest_path)
            self.root.after(
                0,
                lambda path=dest_path, name=archive_name: self._on_fsr4_archive_ready(path, name, None),
            )
        except Exception as exc:
            logging.error("[APP] FSR4 download failed: %s", exc)
            self.root.after(
                0,
                lambda err=str(exc): self._on_fsr4_archive_ready("", archive_name, err),
            )

    def _on_fsr4_archive_ready(self, archive_path: str, archive_name: str, error_message: Optional[str]):
        self.fsr4_archive_filename = archive_name
        self.fsr4_archive_downloading = False
        if error_message:
            self.fsr4_archive_ready = False
            self.fsr4_archive_error = error_message
            self.fsr4_source_archive = ""
            logging.warning("[APP] FSR4 is not ready: %s", error_message)
        else:
            self.fsr4_archive_ready = True
            self.fsr4_archive_error = ""
            self.fsr4_source_archive = archive_path
            logging.info("[APP] FSR4 is ready: %s", archive_path)

        self._update_install_button_state()

    def _enqueue_startup_popup(self, popup_id: str, priority: int, show_callback, blocking: bool = False) -> None:
        self._startup_popup_order += 1
        self._startup_popup_queue.append(
            {
                "id": popup_id,
                "priority": int(priority),
                "order": int(self._startup_popup_order),
                "blocking": bool(blocking),
                "show": show_callback,
            }
        )

    def _run_next_startup_popup(self) -> None:
        if self._startup_popup_active:
            return
        if not self._startup_popup_queue:
            return

        self._startup_popup_queue.sort(key=lambda item: (-int(item["priority"]), int(item["order"])))
        popup_item = self._startup_popup_queue.pop(0)
        popup_id = str(popup_item.get("id", "unknown"))
        show_callback = popup_item.get("show")
        is_blocking = bool(popup_item.get("blocking", False))
        if not callable(show_callback):
            logging.warning("[APP] Startup popup %s has no callable show callback", popup_id)
            self.root.after_idle(self._run_next_startup_popup)
            return

        self._startup_popup_active = True
        finished = False

        def _finish_popup() -> None:
            nonlocal finished
            if finished:
                return
            finished = True
            self._startup_popup_active = False
            self.root.after_idle(self._run_next_startup_popup)

        try:
            if is_blocking:
                show_callback()
                _finish_popup()
            else:
                show_callback(_finish_popup)
        except Exception:
            logging.exception("[APP] Failed to show startup popup: %s", popup_id)
            _finish_popup()

    def _run_post_sheet_startup(self, ok: bool):
        if self._post_sheet_startup_done:
            return

        if self.multi_gpu_blocked:
            self._post_sheet_startup_done = True
            return

        self._post_sheet_startup_done = True
        self._startup_popup_queue.clear()
        self._startup_popup_active = False

        logger = None
        if getattr(self, "found_exe_list", None) and self.selected_game_index is not None:
            logger = get_prefixed_logger(self.found_exe_list[self.selected_game_index].get("game_name", "unknown"))

        self._enqueue_startup_popup(
            "rtss_notice",
            priority=100,
            blocking=True,
            show_callback=lambda: rtss_notice.check_and_show_rtss_notice(
                root=self.root,
                module_download_links=self.module_download_links,
                use_korean=USE_KOREAN,
                assets_dir=ASSETS_DIR,
                theme=RTSS_NOTICE_THEME,
                logger=logger,
            ),
        )

        if not ok:
            self._run_next_startup_popup()
            return

        self._start_optiscaler_archive_prepare()
        self._start_auto_scan()

        warning_text = pick_module_message(self.module_download_links, "warning", self.lang)
        if warning_text:
            self._enqueue_startup_popup(
                "startup_warning",
                priority=80,
                blocking=False,
                show_callback=lambda done_callback, warning=warning_text: self._show_startup_warning_popup(
                    warning,
                    on_close=done_callback,
                ),
            )
        self._run_next_startup_popup()

    def check_app_update(self) -> bool:
        return self._app_update_manager.check_for_update(
            self.module_download_links,
            blocked=self.multi_gpu_blocked,
        )

    def _show_startup_warning_popup(
        self,
        warning_text: str,
        on_close: Optional[Callable[[], None]] = None,
    ) -> None:
        message_popup.show_message_popup(
            root=self.root,
            message_text=warning_text,
            theme=MESSAGE_POPUP_THEME,
            title=self.txt.common.notice,
            confirm_text=self.txt.common.ok,
            on_close=on_close,
            allow_window_close=True,
            scrollable=True,
            debug_name="startup warning popup",
            max_text_chars=110,
            emphasis_font_size=14,
            root_width_fallback=WINDOW_W,
            root_height_fallback=WINDOW_H,
        )

    def _show_scan_result_popup(
        self,
        message_text: str,
        on_close: Optional[Callable[[], None]] = None,
    ) -> None:
        message_popup.show_message_popup(
            root=self.root,
            message_text=message_text,
            theme=MESSAGE_POPUP_THEME,
            title=self.txt.main.scan_result_title,
            confirm_text=self.txt.common.ok,
            on_close=on_close,
            allow_window_close=True,
            scrollable=True,
            debug_name="scan result popup",
            preferred_text_chars=42,
            max_text_chars=72,
            emphasis_font_size=14,
            root_width_fallback=WINDOW_W,
            root_height_fallback=WINDOW_H,
        )

    def _enqueue_initial_auto_scan_empty_popup(self) -> None:
        if self._initial_auto_scan_empty_popup_shown:
            return
        self._initial_auto_scan_empty_popup_shown = True
        detail = self.txt.main.auto_scan_no_results
        self._enqueue_startup_popup(
            "auto_scan_no_results",
            priority=60,
            blocking=False,
            show_callback=lambda done_callback, text=detail: self._show_scan_result_popup(
                text,
                on_close=done_callback,
            ),
        )
        self._run_next_startup_popup()

    def _start_auto_scan(self):
        """Kick off a silent auto-scan of known Steam/game directories."""
        if self.multi_gpu_blocked:
            return
        scan_paths = game_scanner.get_auto_scan_paths(logger=logging.getLogger())
        if not scan_paths:
            self._enqueue_initial_auto_scan_empty_popup()
            return

        self._begin_scan(scan_paths, is_auto=True)

    def _begin_scan(self, scan_paths: list[str], *, is_auto: bool) -> None:
        self._set_supported_games_value(0)
        self._set_scan_status_message(self.txt.main.scanning, "#F1F5F9")
        self.found_exe_list = []
        self._clear_cards()
        self._configure_card_columns(self._get_dynamic_column_count())
        self._scan_in_progress = True
        self._auto_scan_active = bool(is_auto)
        self.btn_select_folder.configure(state="disabled")

        self._task_executor.submit(
            game_scanner.run_scan_job,
            scan_paths,
            self.game_db,
            use_korean=USE_KOREAN,
            is_game_supported=self._is_game_supported_for_current_gpu,
            schedule=lambda callback: self.root.after(0, callback),
            on_game_found=self._on_game_found,
            on_complete=self._on_scan_complete,
            logger=logging.getLogger(),
        )

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
            text=self.txt.main.app_title,
            font=ctk.CTkFont(family=FONT_HEADING, size=20, weight="bold"),
            text_color=_TITLE_TEXT,
        )
        title_lbl.grid(row=0, column=0, padx=24, pady=(18, 2), sticky="w")

        sub_frame = ctk.CTkFrame(hdr, fg_color=_PANEL, corner_radius=0)
        sub_frame.grid(row=1, column=0, padx=24, pady=(0, 14), sticky="ew")
        sub_frame.grid_columnconfigure(0, weight=1)

        self.gpu_lbl = ctk.CTkLabel(
            sub_frame,
            text=self._format_gpu_label_text(self.gpu_info),
            font=ctk.CTkFont(family=FONT_UI, size=11),
            text_color="#C5CFDB",
            anchor="w",
        )
        self.gpu_lbl.grid(row=0, column=0, padx=(1, 0), sticky="w")

        # Compact header status indicator
        self.status_badge = ctk.CTkFrame(
            sub_frame,
            fg_color="transparent",
            corner_radius=0,
        )
        self.status_badge.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self.status_badge.grid_columnconfigure(1, weight=1)

        self.status_badge_dot = ctk.CTkFrame(
            self.status_badge,
            width=_STATUS_INDICATOR_SIZE,
            height=_STATUS_INDICATOR_SIZE,
            fg_color=_STATUS_INDICATOR_LOADING,
            corner_radius=_STATUS_INDICATOR_SIZE // 2,
        )
        self.status_badge_dot.grid(
            row=0,
            column=0,
            padx=(0, 8),
            pady=(_STATUS_INDICATOR_Y_OFFSET, 0),
            sticky="w",
        )

        self.status_badge_label = ctk.CTkLabel(
            self.status_badge,
            text=self.txt.main.status_game_db,
            font=ctk.CTkFont(family=FONT_UI, size=12, weight="bold"),
            text_color=_STATUS_TEXT,
            anchor="w",
        )
        self.status_badge_label.grid(row=0, column=1, sticky="w")
        self._set_status_badge_state(self.txt.main.status_game_db, _STATUS_INDICATOR_LOADING, pulse=True)

        # Separator line
        sep = ctk.CTkFrame(hdr, height=1, fg_color="#4A5361", corner_radius=0)
        sep.grid(row=2, column=0, sticky="ew")

    # -- Scan row ----------------------------------------------------------

    def _build_scan_row(self):
        row = ctk.CTkFrame(self.root, fg_color=_SURFACE, corner_radius=0)
        row.grid(row=1, column=0, sticky="ew", padx=0, pady=0)
        row.grid_columnconfigure(2, weight=1)
        self.scan_row = row

        sec_lbl = ctk.CTkLabel(
            row,
            text=self.txt.main.scan_section_title,
            font=ctk.CTkFont(family=FONT_HEADING, size=12, weight="bold"),
            text_color="#F1F5F9",
        )
        sec_lbl.grid(row=0, column=0, padx=(_CONTENT_SIDE_PAD, 10), pady=(8, 8), sticky="w")

        self.btn_select_folder = ctk.CTkButton(
            row,
            text=self.txt.main.browse_button,
            width=110,
            height=32,
            corner_radius=8,
            fg_color=_BROWSE_BUTTON,
            hover_color=_BROWSE_BUTTON_HOVER,
            text_color="#F1F5F9",
            font=ctk.CTkFont(family=FONT_UI, size=11, weight="bold"),
            command=self.select_game_folder,
        )
        self.btn_select_folder.grid(row=0, column=1, padx=4, pady=(8, 8), sticky="w")

        supported_games_meta = ctk.CTkFrame(row, fg_color="transparent", corner_radius=0)
        supported_games_meta.grid(row=0, column=2, padx=(8, _SCAN_META_RIGHT_INSET), pady=(8, 8), sticky="ew")
        supported_games_meta.grid_columnconfigure(0, weight=1)

        self.lbl_supported_games_label = ctk.CTkLabel(
            supported_games_meta,
            text=self._get_supported_games_meta_label_text(),
            width=self._scan_meta_label_width,
            font=ctk.CTkFont(family=FONT_UI, size=12, weight="bold"),
            text_color=_SECTION_LABEL_TEXT,
            anchor="e",
            justify="right",
        )
        self.lbl_supported_games_label.grid(row=0, column=1, padx=(0, _META_VALUE_GAP), sticky="e")

        self.lbl_supported_games_value = ctk.CTkLabel(
            supported_games_meta,
            text="",
            font=ctk.CTkFont(family=FONT_UI, size=12, weight="bold"),
            text_color=_SECTION_LABEL_TEXT,
            anchor="e",
            justify="right",
        )
        self.lbl_supported_games_value.grid(row=0, column=2, sticky="e")

        self.lbl_scan_status = ctk.CTkLabel(
            row,
            text="",
            font=ctk.CTkFont(family=FONT_UI, size=11),
            text_color=_SCAN_STATUS_TEXT,
            anchor="w",
            justify="left",
        )
        self.lbl_scan_status.grid(
            row=1,
            column=0,
            columnspan=4,
            padx=(_CONTENT_SIDE_PAD, _SCAN_META_RIGHT_INSET),
            pady=(0, 10),
            sticky="w",
        )
        self.lbl_scan_status.grid_remove()

    # -- Grid area (poster cards) -----------------------------------------

    def _build_grid_area(self):
        wrapper = ctk.CTkFrame(self.root, fg_color=_PANEL, corner_radius=0)
        wrapper.grid(row=2, column=0, sticky="nsew", padx=0, pady=0)
        wrapper.grid_rowconfigure(1, weight=1)
        wrapper.grid_columnconfigure(0, weight=1)

        header_row = ctk.CTkFrame(wrapper, fg_color="transparent", corner_radius=0)
        header_row.grid(row=0, column=0, padx=(_CONTENT_SIDE_PAD, _CONTENT_SIDE_PAD), pady=(6, 6), sticky="ew")
        header_row.grid_columnconfigure(1, weight=1)

        self.lbl_supported_games_wiki_link = ctk.CTkLabel(
            header_row,
            text=self.txt.main.supported_games_link,
            font=ctk.CTkFont(family=FONT_UI, size=12, weight="bold", underline=True),
            text_color=_LINK_ACTIVE if SUPPORTED_GAMES_WIKI_URL else _STATUS_TEXT,
            anchor="w",
            justify="left",
            cursor="hand2" if SUPPORTED_GAMES_WIKI_URL else "arrow",
        )
        self.lbl_supported_games_wiki_link.grid(row=0, column=0, padx=(14, 12), pady=(1, 0), sticky="w")
        if SUPPORTED_GAMES_WIKI_URL:
            self.lbl_supported_games_wiki_link.bind("<Enter>", lambda _event: self._set_supported_games_wiki_link_hover(True))
            self.lbl_supported_games_wiki_link.bind("<Leave>", lambda _event: self._set_supported_games_wiki_link_hover(False))
            self.lbl_supported_games_wiki_link.bind("<Button-1>", self._open_supported_games_wiki)

        selected_header_row = ctk.CTkFrame(header_row, fg_color="transparent", corner_radius=0)
        selected_header_row.grid(row=0, column=1, padx=(8, _META_RIGHT_PAD), pady=(1, 0), sticky="ew")
        selected_header_row.grid_columnconfigure(0, weight=1)

        game_name = self._get_selected_game_header_text()

        self.lbl_selected_game_header = ctk.CTkLabel(
            selected_header_row,
            text=game_name,
            font=ctk.CTkFont(family=FONT_UI, size=12, weight="bold"),
            text_color=_SELECTED_GAME_HIGHLIGHT,
            anchor="e",
            justify="right",
        )
        self.lbl_selected_game_header.grid(row=0, column=1, sticky="e")

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
            scrollbar = getattr(self.games_scroll, "_scrollbar", None)
            if canvas is not None:
                canvas.bind("<MouseWheel>", self._on_games_scroll, add="+")
                canvas.bind("<Button-4>", self._on_games_scroll, add="+")
                canvas.bind("<Button-5>", self._on_games_scroll, add="+")
                canvas.bind("<ButtonRelease-1>", self._on_games_scroll, add="+")
                # Also reflow columns when the inner canvas itself resizes.
                canvas.bind("<Configure>", self._on_games_area_resize, add="+")
            if canvas is not None and scrollbar is not None:
                scrollbar.configure(command=self._on_games_scrollbar_command)
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
        bar = ctk.CTkFrame(self.root, fg_color=_SURFACE, corner_radius=0, height=142)
        bar.grid(row=3, column=0, sticky="ew", padx=0, pady=0)
        bar.grid_propagate(False)
        bar.grid_columnconfigure(0, weight=1)

        # Section label + latest version info on the same line
        title_line = ctk.CTkFrame(bar, fg_color="transparent", corner_radius=0)
        title_line.grid(row=0, column=0, padx=20, pady=(7, 2), sticky="ew")
        title_line.grid_columnconfigure(1, weight=1)

        sec_lbl = ctk.CTkLabel(
            title_line,
            text=self.txt.main.install_section_title,
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
        self.lbl_optiscaler_version_line.grid(row=0, column=1, padx=(10, 0), pady=(2, 0), sticky="e")

        mid_bottom = ctk.CTkFrame(bar, fg_color=_SURFACE, corner_radius=0)
        mid_bottom.grid(row=1, column=0, sticky="ew", padx=20, pady=(2, 0))
        mid_bottom.grid_columnconfigure(0, weight=1)

        self.apply_btn = ctk.CTkButton(
            mid_bottom,
            text=self.txt.main.install_button,
            width=104,
            height=87,
            corner_radius=10,
            fg_color=_INSTALL_BUTTON_DISABLED,
            hover_color=_INSTALL_BUTTON_DISABLED,
            text_color=_INSTALL_BUTTON_TEXT,
            border_width=1,
            border_color=_INSTALL_BUTTON_BORDER_DISABLED,
            font=ctk.CTkFont(family=FONT_UI, size=14, weight="bold"),
            state="disabled",
            command=self.apply_optiscaler,
        )
        self.apply_btn.grid(row=0, column=1, padx=(10, 0), pady=(0, 0))

        self.info_text = ctk.CTkTextbox(
            mid_bottom,
            height=87,
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

        self._refresh_optiscaler_archive_info_ui()
        self._set_information_text(self.txt.main.select_game_hint)
        self._update_install_button_state()
        self.root.after(0, self._align_supported_games_count_label)

    def _refresh_optiscaler_archive_info_ui(self):
        # Do not show placeholder version text before sheet load completes.
        if getattr(self, "sheet_loading", False):
            if hasattr(self, "lbl_optiscaler_version_line"):
                self.lbl_optiscaler_version_line.configure(text="")
            return

        entry = self.module_download_links.get("optiscaler", {}) if hasattr(self, "module_download_links") else {}
        archive_name = ""

        if isinstance(entry, dict):
            archive_name = str(entry.get("filename", "") or entry.get("version", "")).strip()
            raw_version = str(entry.get("version", "")).replace("\r", " ").replace("\n", " ").strip()
            version = re.sub(r"\s+", " ", raw_version)
        else:
            version = ""

        archive_display_name = _format_optiscaler_version_display_name(archive_name)
        version_display_name = _format_optiscaler_version_display_name(version)

        if archive_display_name:
            version_text = self.txt.main.version_line_template.format(value=archive_display_name)
        elif version_display_name:
            version_text = self.txt.main.version_line_template.format(value=version_display_name)
        else:
            version_text = self.txt.main.version_line_template.format(value="-")

        if hasattr(self, "lbl_optiscaler_version_line"):
            self.lbl_optiscaler_version_line.configure(text=version_text, text_color="#AEB9C8")

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
    # Status indicator
    # ------------------------------------------------------------------

    def _update_sheet_status(self):
        if self.multi_gpu_blocked:
            self._set_status_badge_state(
                self.txt.main.status_gpu_config,
                _STATUS_INDICATOR_OFFLINE,
            )
            self.root.after(0, self._align_supported_games_count_label)
            return
        if self._gpu_selection_pending:
            self._set_status_badge_state(
                self.txt.main.status_gpu_select,
                _STATUS_INDICATOR_WARNING,
            )
            self.root.after(0, self._align_supported_games_count_label)
            return
        if self.sheet_loading:
            self._set_status_badge_state(
                self.txt.main.status_game_db,
                _STATUS_INDICATOR_LOADING,
                pulse=True,
            )
            self.root.after(0, self._align_supported_games_count_label)
            return
        if self.sheet_status:
            self._set_status_badge_state(
                self.txt.main.status_game_db,
                _STATUS_INDICATOR_ONLINE,
            )
        else:
            self._set_status_badge_state(
                self.txt.main.status_game_db,
                _STATUS_INDICATOR_OFFLINE,
            )
        self.root.after(0, self._align_supported_games_count_label)

    # ------------------------------------------------------------------
    # Information text
    # ------------------------------------------------------------------

    def _set_information_text(self, text=""):
        info_text = (text or "").strip() or self.txt.main.no_information
        text_widget = getattr(self.info_text, "_textbox", self.info_text)
        self._apply_information_text_shift()
        self.info_text.configure(state="normal")
        try:
            text_widget.delete("1.0", "end")
            self._insert_information_with_markup(info_text)
        except Exception as exc:
            logging.warning("Failed to render information markup, falling back to plain text: %s", exc)
            text_widget.delete("1.0", "end")
            fallback_text = strip_markup_text(info_text)
            text_widget.insert("1.0", fallback_text)
        finally:
            self.info_text.configure(state="disabled")

    def _insert_information_with_markup(self, raw_text: str):
        text_widget = getattr(self.info_text, "_textbox", self.info_text)
        render_markup_to_text_widget(
            text_widget,
            raw_text,
            emphasis_tag="info_red_emphasis",
            emphasis_color=_STATUS_INDICATOR_WARNING,
            emphasis_size_offset=1,
            emphasis_weight="bold",
            trim_emphasis=True,
        )

    # ------------------------------------------------------------------
    # Poster card grid
    # ------------------------------------------------------------------

    def _clear_cards(self, keep_selection=False):
        self._render_generation += 1
        self._pending_image_jobs.clear()
        self._inflight_image_futures.clear()
        self._failed_image_jobs.clear()
        for index in list(self._delayed_image_retry_after_ids.keys()):
            self._cancel_delayed_image_retry(index)
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
            self._game_popup_confirmed = False
            self.install_precheck_running = False
            self.install_precheck_ok = False
            self.install_precheck_error = ""
            self.install_precheck_dll_name = ""
            self._set_information_text("")
        self._hovered_card_index = None
        self._update_selected_game_header()
        self._update_install_button_state()

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
            # 열 개수 변화가 없어도 너비 차이가 크면 재정렬한다 (안전장치)
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
        self.root.after_idle(self._align_supported_games_count_label)

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

    def _schedule_games_viewport_update(self, delay_ms: int = 30):
        try:
            if self._games_viewport_after_id is not None:
                self.root.after_cancel(self._games_viewport_after_id)
            self._games_viewport_after_id = self.root.after(max(0, int(delay_ms)), self._run_games_viewport_update)
        except Exception:
            self._games_viewport_after_id = None

    def _run_games_viewport_update(self):
        self._games_viewport_after_id = None
        self._pump_image_jobs()

    def _on_games_scrollbar_command(self, *args):
        canvas = getattr(self.games_scroll, "_parent_canvas", None)
        if canvas is None or not args:
            return

        try:
            canvas.yview(*args)
        except Exception:
            return

        self._schedule_games_viewport_update()

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
        self._schedule_games_viewport_update()

    def _rerender_cards_for_resize(self):
        self._resize_after_id = None
        cols = self._get_dynamic_column_count()
        self._fit_cards_to_visible_width(cols)

    def _ensure_card_image_cache(self, item: dict):
        base_revision = int(item.get("base_revision", 0))
        if item.get("ctk_img_cache_revision") == base_revision and item.get("ctk_img_cache"):
            return

        base_pil = item["base_pil"]
        normal_img = base_pil.convert("RGBA")

        ctk_cache = {
            "normal": ctk.CTkImage(light_image=normal_img, dark_image=normal_img, size=(CARD_W, CARD_H)),
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

        item["card"].configure(border_color=_CARD_BG, fg_color=_CARD_BG, border_width=2)

        if selected or hovered:
            title_overlay.place(x=0, y=CARD_H - 34)
            title_overlay.lift()
        else:
            title_overlay.place_forget()

        self._ensure_card_image_cache(item)
        if item.get("current_image_state") == "normal":
            return

        item["img_label"].configure(image=item["ctk_img_cache"]["normal"])
        item["current_image_state"] = "normal"

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
            text_color=_CARD_TITLE_OVERLAY_TEXT,
            fg_color=_CARD_TITLE_OVERLAY_BG,
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
        filename_cover = game.get("filename_cover", "")
        self._queue_card_image_fetch(
            index,
            img_label,
            game["display"],
            filename_cover,
            cover_url,
        )

        return card

    def _set_card_placeholder(self, index: int, label: ctk.CTkLabel, title: str):
        pil_img = self._default_poster_base.copy().convert("RGBA")
        self.root.after(0, lambda idx=index, l=label, img=pil_img: self._set_card_base_image(idx, l, img))

    def _find_bundled_cover_asset(self, cover_filename: str) -> Optional[Path]:
        normalized = _normalize_cover_filename(cover_filename)
        if not normalized:
            return None

        bundled_name = BUNDLED_COVER_FILENAME_MAP.get(normalized.casefold())
        if not bundled_name:
            return None

        candidate = ASSETS_DIR / bundled_name
        if candidate.exists() and candidate.is_file():
            return candidate
        return None

    def _get_cover_cache_path(self, cover_filename: str) -> Optional[Path]:
        normalized = _normalize_cover_filename(cover_filename)
        if not normalized:
            return None
        return app_update.resolve_safe_child_path(Path(getattr(self, "cover_cache_dir", COVER_CACHE_DIR)), normalized)

    def _build_cover_repo_raw_url(self, cover_filename: str) -> str:
        normalized = _normalize_cover_filename(cover_filename)
        if not normalized or not COVERS_REPO_RAW_BASE_URL:
            return ""
        return f"{COVERS_REPO_RAW_BASE_URL}/{quote(normalized, safe='')}"

    def _poster_cache_key(self, source_type: str, source_value: str, title: str = "") -> str:
        normalized_source = ""
        raw_value = str(source_value or "").strip()
        if source_type == "cover_url" and raw_value:
            try:
                parsed = urlparse(raw_value)
                normalized_source = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}".strip().lower()
            except Exception:
                normalized_source = raw_value.lower()
        else:
            normalized_source = raw_value.casefold()

        if not normalized_source:
            normalized_source = str(title or "").strip().casefold() or "unknown"

        cache_source = f"poster|v{POSTER_CACHE_VERSION}|{TARGET_POSTER_W}x{TARGET_POSTER_H}|{source_type}|{normalized_source}"
        return hashlib.sha256(cache_source.encode("utf-8")).hexdigest()

    def _load_prepared_image_from_path(self, image_path: Path, cache_key: str) -> Optional[Image.Image]:
        cached_image = self._image_cache_get(cache_key) if ENABLE_POSTER_CACHE else None
        if cached_image is not None:
            return cached_image
        if not image_path.exists() or not image_path.is_file():
            return None

        try:
            with Image.open(image_path) as source_img:
                pil_img = _prepare_cover_image(source_img, TARGET_POSTER_W, TARGET_POSTER_H)
            if ENABLE_POSTER_CACHE:
                self._image_cache_put(cache_key, pil_img)
            return pil_img
        except Exception:
            return None

    def _load_prepared_image_from_bytes(self, image_bytes: bytes, cache_key: str, source_label: str) -> Optional[Image.Image]:
        cached_image = self._image_cache_get(cache_key) if ENABLE_POSTER_CACHE else None
        if cached_image is not None:
            return cached_image

        try:
            with Image.open(io.BytesIO(image_bytes)) as source_img:
                pil_img = _prepare_cover_image(source_img, TARGET_POSTER_W, TARGET_POSTER_H)
            if ENABLE_POSTER_CACHE:
                self._image_cache_put(cache_key, pil_img)
            return pil_img
        except Exception:
            return None

    def _download_image_bytes(self, url: str) -> bytes:
        with _temporary_logger_level(("urllib3.connectionpool", "urllib3.util.retry"), logging.ERROR):
            with self._image_session.get(url, timeout=IMAGE_TIMEOUT_SECONDS, stream=True) as response:
                response.raise_for_status()
                return b"".join(response.iter_content(chunk_size=65536))

    def _store_cover_cache_bytes(self, cover_filename: str, image_bytes: bytes) -> Optional[Path]:
        cache_path = self._get_cover_cache_path(cover_filename)
        if cache_path is None:
            return None

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = cache_path.with_name(cache_path.name + ".tmp")
        with temp_path.open("wb") as cache_fp:
            cache_fp.write(image_bytes)
        temp_path.replace(cache_path)
        return cache_path

    def _cancel_delayed_image_retry(self, index: int):
        after_id = self._delayed_image_retry_after_ids.pop(index, None)
        if after_id is None:
            return
        try:
            self.root.after_cancel(after_id)
        except Exception:
            pass

    def _schedule_delayed_image_retry(self, job: dict):
        index = int(job.get("index", -1))
        if index < 0:
            return
        if int(job.get("delayed_retry_count", 0)) >= 1:
            return
        if index in self._delayed_image_retry_after_ids:
            return

        retry_job = dict(job)
        retry_job["delayed_retry_count"] = int(job.get("delayed_retry_count", 0)) + 1

        def _requeue():
            self._delayed_image_retry_after_ids.pop(index, None)
            try:
                if not self.root.winfo_exists():
                    return
            except tk.TclError:
                return

            if retry_job.get("generation") != self._render_generation:
                return

            self._pending_image_jobs[index] = retry_job
            self._pump_image_jobs()

        try:
            after_id = self.root.after(max(0, IMAGE_RETRY_DELAY_MS), _requeue)
        except Exception:
            return
        self._delayed_image_retry_after_ids[index] = after_id

    def _queue_card_image_fetch(self, index: int, label: ctk.CTkLabel, title: str, cover_filename: str, url: str):
        self._cancel_delayed_image_retry(index)
        self._pending_image_jobs[index] = {
            "index": index,
            "label": label,
            "title": title,
            "cover_filename": cover_filename,
            "url": url,
            "generation": self._render_generation,
            "delayed_retry_count": 0,
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
                job.get("cover_filename", ""),
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
                pil_img, _is_default, should_retry = future.result()
                self._apply_loaded_poster(job["index"], job["label"], job["generation"], pil_img)
                if should_retry:
                    self._schedule_delayed_image_retry(job)
            except Exception as exc:
                logging.warning("Poster download failed (will retry): %s", exc)
                # Store for one automatic retry after all jobs are done.
                self._failed_image_jobs[job["index"]] = job

    def _image_queue_tick(self):
        self._image_queue_after_id = None
        self._pump_image_jobs()

    def _load_poster_image_worker(self, title: str, cover_filename: str, url: str) -> tuple[Image.Image, bool, bool]:
        normalized_cover_filename = _normalize_cover_filename(cover_filename)
        repo_failed = False

        if normalized_cover_filename:
            cover_cache_key = self._poster_cache_key("cover_file", normalized_cover_filename, title=title)

            bundled_cover_path = self._find_bundled_cover_asset(normalized_cover_filename)
            if bundled_cover_path is not None:
                pil_img = self._load_prepared_image_from_path(bundled_cover_path, cover_cache_key)
                if pil_img is not None:
                    return pil_img, False, False

            disk_cache_path = self._get_cover_cache_path(normalized_cover_filename)
            if disk_cache_path is not None and disk_cache_path.exists():
                pil_img = self._load_prepared_image_from_path(disk_cache_path, cover_cache_key)
                if pil_img is not None:
                    return pil_img, False, False
                try:
                    disk_cache_path.unlink()
                except OSError:
                    pass

            repo_url = self._build_cover_repo_raw_url(normalized_cover_filename)
            if repo_url:
                try:
                    image_bytes = self._download_image_bytes(repo_url)
                    pil_img = self._load_prepared_image_from_bytes(image_bytes, cover_cache_key, repo_url)
                    if pil_img is None:
                        raise RuntimeError("Downloaded cover image could not be decoded")
                    try:
                        self._store_cover_cache_bytes(normalized_cover_filename, image_bytes)
                    except Exception:
                        pass
                    return pil_img, False, False
                except Exception:
                    repo_failed = True

        cache_key = self._poster_cache_key("cover_url", url, title=title)
        cached_image = self._image_cache_get(cache_key) if ENABLE_POSTER_CACHE else None
        if cached_image is not None:
            return cached_image, False, False

        if not url:
            fallback = self._default_poster_base.copy().convert("RGBA")
            return fallback, True, repo_failed

        try:
            image_bytes = self._download_image_bytes(url)
            pil_img = self._load_prepared_image_from_bytes(image_bytes, cache_key, url)
            if pil_img is None:
                raise RuntimeError("Downloaded cover image could not be decoded")
            return pil_img, False, False
        except Exception:
            fallback = self._default_poster_base.copy().convert("RGBA")
            return fallback, True, True

    def _apply_loaded_poster(self, index: int, label: ctk.CTkLabel, generation: int, pil_img: Image.Image):
        if generation != self._render_generation:
            return
        self._set_card_base_image(index, label, pil_img)

    def _image_cache_get(self, key: str) -> Optional[Image.Image]:
        try:
            pil_img = self._image_cache.get(key)
            if pil_img is None:
                return None
            # Refresh insertion order so frequently viewed posters stay cached longer.
            self._image_cache.pop(key, None)
            self._image_cache[key] = pil_img
            return pil_img
        except Exception:
            logging.exception("Failed to read image cache for key=%s", key)
            return None

    def _image_cache_put(self, key: str, pil_img: Image.Image):
        try:
            self._image_cache.pop(key, None)
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
        self._update_selected_game_header()
        self._refresh_all_card_visuals()

        # Popup confirmation logic
        self._game_popup_confirmed = False
        self.install_precheck_running = True
        self.install_precheck_ok = False
        self.install_precheck_error = ""
        self.install_precheck_dll_name = ""
        self._update_install_button_state()
        if 0 <= index < len(self.found_exe_list):
            game = self.found_exe_list[index]
            self._set_information_text(game.get("information", ""))
            self._run_install_precheck(game)
            popup_msg = pick_sheet_text(game, "popup", self.lang)
            if popup_msg:
                def _on_confirm():
                    self._game_popup_confirmed = True
                    self._update_install_button_state()
                self.root.after_idle(
                    lambda msg=popup_msg, cb=_on_confirm: self._show_game_selection_popup(msg, on_confirm=cb)
                )
            else:
                self._game_popup_confirmed = True
                self._update_install_button_state()
        else:
            self.install_precheck_running = False
            self.install_precheck_ok = False
            self.install_precheck_error = ""
            self.install_precheck_dll_name = ""
            self._update_install_button_state()

    def _run_install_precheck(self, game_data: dict):
        target_path = str(game_data.get("path", "")).strip()
        preferred_dll = str(game_data.get("dll_name", "")).strip()
        logger = get_prefixed_logger(str(game_data.get("game_name", "unknown")).strip() or "unknown")
        try:
            if bool(game_data.get("ultimate_asi_loader")):
                resolved_name = OPTISCALER_ASI_NAME
                logger.info("Install precheck selected Ultimate ASI Loader mode: %s", resolved_name)
            else:
                resolved_name = installer_services.resolve_proxy_dll_name(target_path, preferred_dll, logger=logger)
            self.install_precheck_ok = True
            self.install_precheck_error = ""
            self.install_precheck_dll_name = resolved_name
        except Exception as exc:
            raw_error = translate_default_precheck_error(str(exc), self.lang)
            self.install_precheck_ok = False
            self.install_precheck_error = raw_error
            self.install_precheck_dll_name = ""
            logger.warning("Install precheck failed: %s", raw_error)
        finally:
            self.install_precheck_running = False
            self._update_install_button_state()

    # ------------------------------------------------------------------
    # File dialogs
    # ------------------------------------------------------------------

    def select_game_folder(self):
        if self.multi_gpu_blocked:
            return
        if self.sheet_loading:
            messagebox.showinfo(self.txt.dialogs.game_db_loading_title, self.txt.dialogs.game_db_loading_body)
            return
        if not self.sheet_status:
            messagebox.showerror(
                self.txt.dialogs.game_db_error_title,
                self.txt.dialogs.game_db_error_body,
            )
            return

        self.game_folder = filedialog.askdirectory()
        if not self.game_folder:
            return

        self._begin_scan([self.game_folder], is_auto=False)

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
        self._set_supported_games_value(count)
        self._set_scan_status_message("")
        if count > 0:
            self._set_information_text(self.txt.main.select_game_hint)
        elif is_auto:
            self._enqueue_initial_auto_scan_empty_popup()
        else:
            self._show_scan_result_popup(self.txt.main.manual_scan_no_results)
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

        self._set_supported_games_value(len(self.found_exe_list))

    # ------------------------------------------------------------------
    # Install
    # ------------------------------------------------------------------

    def apply_optiscaler(self):
        if self.multi_gpu_blocked:
            return
        if self.install_in_progress:
            messagebox.showinfo(self.txt.dialogs.installing_title, self.txt.dialogs.installing_body)
            return


        if self.selected_game_index is None:
            messagebox.showwarning(self.txt.common.warning, self.txt.dialogs.select_game_card_body)
            return

        if self.optiscaler_archive_downloading:
            messagebox.showinfo(self.txt.dialogs.preparing_archive_title, self.txt.dialogs.preparing_archive_body)
            return

        if self.install_precheck_running:
            return

        if not self.install_precheck_ok or not self.install_precheck_dll_name:
            detail = self.install_precheck_error or self.txt.dialogs.precheck_incomplete_body
            detail = f"{detail}\n\n{self.txt.dialogs.precheck_retry_mods_body}"
            messagebox.showwarning(self.txt.common.warning, detail)
            return

        if not self.optiscaler_archive_ready or not getattr(self, "opti_source_archive", None):
            detail = self.optiscaler_archive_error or self.txt.dialogs.optiscaler_archive_not_ready
            messagebox.showwarning(self.txt.common.warning, detail)
            return

        if self.selected_game_index < 0 or self.selected_game_index >= len(self.found_exe_list):
            messagebox.showwarning(self.txt.common.warning, self.txt.dialogs.invalid_game_body)
            return

        selected_game = self.found_exe_list[self.selected_game_index]
        fsr4_required = self._should_apply_fsr4_for_game(selected_game)
        if fsr4_required and self.fsr4_archive_downloading:
            messagebox.showinfo(self.txt.dialogs.preparing_download_title, self.txt.dialogs.preparing_download_body)
            return

        if fsr4_required and (not self.fsr4_archive_ready or not getattr(self, "fsr4_source_archive", None)):
            detail = self.fsr4_archive_error or self.txt.dialogs.fsr4_not_ready
            messagebox.showwarning(self.txt.common.warning, detail)
            return

        # Block install if popup not confirmed
        if not getattr(self, "_game_popup_confirmed", True):
            messagebox.showwarning(self.txt.common.notice, self.txt.dialogs.confirm_popup_body)
            return

        game_data = dict(selected_game)
        source_archive = self.opti_source_archive
        resolved_dll_name = self.install_precheck_dll_name
        fsr4_source_archive = self.fsr4_source_archive if fsr4_required else ""

        self.install_in_progress = True
        self.apply_btn.configure(
            state="disabled",
            text=self.txt.main.installing_button,
            fg_color=_INSTALL_BUTTON_DISABLED,
            hover_color=_INSTALL_BUTTON_DISABLED,
            border_color=_INSTALL_BUTTON_BORDER_DISABLED,
        )

        self._task_executor.submit(
            self._apply_optiscaler_worker,
            game_data,
            source_archive,
            resolved_dll_name,
            fsr4_source_archive,
            fsr4_required,
        )

    def _apply_optiscaler_worker(self, game_data, source_archive, resolved_dll_name, fsr4_source_archive, fsr4_required):
        target_path = game_data["path"]
        game_name = str(game_data.get("game_name", "unknown")).strip() or "unknown"
        logger = get_prefixed_logger(game_name)
        try:
            use_ultimate_asi_loader = bool(game_data.get("ultimate_asi_loader"))
            if use_ultimate_asi_loader and game_data.get("reframework_url"):
                raise RuntimeError(
                    "Ultimate ASI Loader and REFramework both require dinput8.dll, and this combination is not supported yet."
                )

            if use_ultimate_asi_loader:
                final_dll_name = resolved_dll_name or OPTISCALER_ASI_NAME
                logger.info("Install mode: Ultimate ASI Loader (%s)", final_dll_name)
            else:
                final_dll_name = installer_services.resolve_proxy_dll_name(
                    target_path,
                    resolved_dll_name or str(game_data.get("dll_name", "")).strip(),
                    logger=logger,
                )
            logger.info("Install started: target=%s", target_path)
            exclude_raw = str(self.module_download_links.get("__exclude_list__", "")).strip()
            exclude_patterns = [token.strip() for token in exclude_raw.split("|") if token.strip()]
            with tempfile.TemporaryDirectory() as tmpdir:
                installer_services.extract_archive(source_archive, tmpdir, logger=logger)
                contents = os.listdir(tmpdir)
                if len(contents) == 1 and os.path.isdir(os.path.join(tmpdir, contents[0])):
                    actual_source = os.path.join(tmpdir, contents[0])
                else:
                    actual_source = tmpdir
                installer_services.backup_existing_optiscaler_dlls(target_path, logger=logger)
                installer_services.remove_legacy_optiscaler_files(target_path, logger=logger)
                installer_services.install_from_source_folder(
                    actual_source,
                    target_path,
                    dll_name=final_dll_name,
                    exclude_patterns=exclude_patterns,
                    logger=logger,
                )
                logger.info(f"Extracted and installed files to {target_path}")

            ini_path = os.path.join(target_path, "OptiScaler.ini")
            if not os.path.exists(ini_path):
                raise FileNotFoundError("OptiScaler.ini not found after installation")

            if use_ultimate_asi_loader:
                install_ultimate_asi_loader(target_path, self.module_download_links, logger=logger)

            merged_ini_settings = dict(game_data.get("ini_settings", {}))
            install_reframework_dinput8(target_path, game_data, logger=logger)
            merged_ini_settings.update(
                install_optipatcher(
                    target_path,
                    game_data,
                    self.module_download_links,
                    OPTIPATCHER_URL,
                    logger=logger,
                )
            )

            ini_utils.apply_ini_settings(ini_path, merged_ini_settings, force_frame_generation=True, logger=logger)
            logger.info(f"Applied ini settings to {ini_path}")

            # Optional in-game ini patching from sheet columns:
            # - only when #ingame_ini is provided
            # - only when that file already exists in target folder
            # - update only keys present in #ingame_setting (no key/file creation)
            ingame_ini_name = str(game_data.get("ingame_ini", "")).strip()
            ingame_settings = dict(game_data.get("ingame_settings", {}) or {})
            if ingame_ini_name and ingame_settings:
                logger.info("#ingame_ini configured: %s", ingame_ini_name)
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
                            ini_utils._ensure_file_writable(ini_file)
                        logger.info("#ingame_ini exists: %s", ingame_ini_path)
                        ini_utils.apply_ini_settings(ingame_ini_path, ingame_settings, force_frame_generation=False, logger=logger)
                        logger.info(f"Applied in-game settings to {ingame_ini_path}")
                    finally:
                        # Restore original read-only state
                        if orig_readonly:
                            ini_utils._set_file_readonly(ini_file)
                else:
                    logger.info("#ingame_ini missing, skipped edits: %s", ingame_ini_path)
            elif ingame_ini_name:
                logger.info("#ingame_ini configured but no #ingame_setting values provided: %s", ingame_ini_name)

            try:
                engine_loc = str(game_data.get("engine_ini_location", "")).strip()
                engine_ini_content = str(game_data.get("engine_ini_type", "")).strip()
                if engine_loc and engine_ini_content:
                    logger.info(f"engine.ini info for install: target={target_path}, engine_ini_location='{engine_loc}'")
                    ini_path = ini_utils._find_or_create_engine_ini(engine_loc, workspace_root=target_path, logger=logger)
                    
                    if ini_path:
                        try:
                            ini_utils._ensure_file_writable(ini_path)
                            section_map = ini_utils._parse_version_text_to_ini_entries(engine_ini_content)
                            
                            if section_map:
                                ini_utils._upsert_ini_entries(ini_path, section_map, logger=logger)
                                logger.info(f"Upserted engine.ini entries to {ini_path}")
                        finally:
                            ini_utils._set_file_readonly(ini_path)
            except Exception:
                logger.exception("Failed while handling engine.ini for %s", target_path)

            install_unreal5_patch(
                target_path,
                game_data,
                self.module_download_links,
                self.gpu_info,
                logger=logger,
            )

            if fsr4_required:
                if not fsr4_source_archive:
                    raise FileNotFoundError("FSR4 is not ready")

                # Overwrite the DLL installed from the OptiScaler archive at the very end.
                with tempfile.TemporaryDirectory() as tmpdir:
                    installer_services.extract_archive(fsr4_source_archive, tmpdir, logger=None)
                    dll_candidates = [path for path in Path(tmpdir).rglob("*.dll") if path.is_file()]
                    if not dll_candidates:
                        raise FileNotFoundError("No DLL found inside FSR4 zip")
                    if len(dll_candidates) > 1:
                        raise RuntimeError("Multiple DLL files found inside FSR4 zip")

                    source_dll = dll_candidates[0]
                    destination_dll = Path(target_path) / source_dll.name
                    try:
                        os.chmod(destination_dll, 0o666)
                    except OSError:
                        pass
                    shutil.copy2(source_dll, destination_dll)
                    logger.info("Installed FSR4 DLL to %s", destination_dll)
            else:
                logger.info("Skipped FSR4 install for current GPU/game selection")

            logger.info("Install completed")
            self.root.after(
                0,
                lambda game=dict(game_data): self._on_install_finished(True, "Install Completed", game),
            )
        except Exception as e:
            logger.exception("Install failed: %s", e)
            self.root.after(
                0,
                lambda err=e, game=dict(game_data): self._on_install_finished(False, str(err), game),
            )

    def _on_install_finished(self, success, message, installed_game=None):
        self.install_in_progress = False
        self._update_install_button_state()

        if success:
            game = installed_game if isinstance(installed_game, dict) else {}
            self.root.after_idle(lambda g=dict(game): self._show_after_install_popup(g))
        else:
            messagebox.showerror(
                self.txt.common.error,
                self.txt.dialogs.install_failed_body_template.format(message=message),
            )


if __name__ == "__main__":
    if "--edit-engine-ini" in sys.argv:
        gpu_info = gpu_service.get_graphics_adapter_info()
        _, selected_sheet_gid = gpu_service.resolve_game_db_target_for_gpu(gpu_info, GPU_VENDOR_DB_GIDS, SHEET_GID)
        logging.info(
            "Running engine.ini edits from Google Sheet (gid=%s, gpu=%s)",
            selected_sheet_gid,
            gpu_info,
        )
        try:
            ini_utils.process_engine_ini_edits(SHEET_ID, gid=selected_sheet_gid)
        except Exception:
            logging.exception("engine.ini edit run failed")
        sys.exit(0)

    root = ctk.CTk()
    app = OptiManagerApp(root)
    root.mainloop()
