import os
import shutil
import tempfile
import tkinter as tk
import time
import math
from concurrent.futures import ThreadPoolExecutor
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
    ArchivePreparationCallbacks,
    ArchivePreparationController,
    ArchivePreparationState,
    BottomPanelPresenter,
    GameDbControllerCallbacks,
    GameDbLoadController,
    GameDbLoadResult,
    GpuFlowCallbacks,
    GpuFlowController,
    GpuFlowState,
    HeaderStatusPresenter,
    gpu_notice,
    message_popup,
    rtss_notice,
    StartupFlowController,
    StartupFlowCallbacks,
)
from installer.app.poster_queue import PosterQueueController
from installer.app.scan_controller import ScanController, ScanControllerCallbacks
from installer.app.ui_builder import MainUiTheme, build_main_ui
from installer.common.poster_loader import PosterImageLoader, PosterLoaderConfig
from installer.config import ini_utils
from installer.data import sheet_loader
from installer.games.handlers import get_game_handler
from installer.i18n import (
    detect_ui_language,
    get_app_strings,
    is_korean,
    pick_module_message,
    pick_sheet_text,
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
    from PIL import Image
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
APP_VERSION = "0.3.1"
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
IMAGE_TIMEOUT_SECONDS = 10
IMAGE_MAX_RETRIES = 3
IMAGE_MAX_WORKERS = 4
IMAGE_RETRY_DELAY_MS = int(os.environ.get("OPTISCALER_IMAGE_RETRY_DELAY_MS", "1500"))
DEFAULT_POSTER_SCALE = 1.5
INFO_TEXT_OFFSET_PX = 10
POSTER_CACHE_VERSION = 2
ENABLE_POSTER_CACHE = os.environ.get("OPTISCALER_ENABLE_POSTER_CACHE", "1").strip().lower() in {"1", "true", "yes", "on"}
IMAGE_CACHE_MAX = int(os.environ.get("OPTISCALER_IMAGE_CACHE_MAX", "100"))


def _get_ctk_scale(window: object | None = None, default: float = 1.0) -> float:
    try:
        if window is not None and hasattr(window, "_get_window_scaling"):
            scale = float(window._get_window_scaling())
            if scale > 0:
                return scale
    except Exception:
        logging.debug("[APP] Failed to read CustomTkinter scaling", exc_info=True)
    return float(default)


def _resolve_startup_poster_target_size(window: object | None = None, default_scale: float = DEFAULT_POSTER_SCALE) -> tuple[int, int, float]:
    scale = _get_ctk_scale(window, default_scale)
    target_width = max(1, int(round(CARD_W * scale)))
    target_height = max(1, int(round(CARD_H * scale)))
    return target_width, target_height, scale


def _format_optiscaler_version_display_name(raw_name: str) -> str:
    name = Path(str(raw_name or "").strip()).name
    if not name:
        return ""

    name = re.sub(r"(?i)\.(zip|7z)$", "", name).strip()
    name = re.sub(r"(?i)^optiscaler", "", name).lstrip()
    name = re.sub(r"^[-_]+", "", name).lstrip()
    return re.sub(r"\s+", " ", name).strip()

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
MAIN_UI_THEME = MainUiTheme(
    panel_color=_PANEL,
    surface_color=_SURFACE,
    title_text_color=_TITLE_TEXT,
    font_heading=FONT_HEADING,
    font_ui=FONT_UI,
    status_indicator_size=_STATUS_INDICATOR_SIZE,
    status_indicator_loading_color=_STATUS_INDICATOR_LOADING,
    status_indicator_y_offset=_STATUS_INDICATOR_Y_OFFSET,
    status_text_color=_STATUS_TEXT,
    content_side_pad=_CONTENT_SIDE_PAD,
    browse_button_color=_BROWSE_BUTTON,
    browse_button_hover_color=_BROWSE_BUTTON_HOVER,
    scan_status_text_color=_SCAN_STATUS_TEXT,
    scan_meta_right_inset=_SCAN_META_RIGHT_INSET,
    supported_games_wiki_url=SUPPORTED_GAMES_WIKI_URL,
    link_active_color=_LINK_ACTIVE,
    meta_right_pad=_META_RIGHT_PAD,
    selected_game_highlight_color=_SELECTED_GAME_HIGHLIGHT,
    grid_width=GRID_W,
    grid_height=GRID_H,
    install_button_disabled_color=_INSTALL_BUTTON_DISABLED,
    install_button_text_color=_INSTALL_BUTTON_TEXT,
    install_button_border_disabled_color=_INSTALL_BUTTON_BORDER_DISABLED,
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
        self._poster_target_width, self._poster_target_height, self._poster_target_scale = _resolve_startup_poster_target_size(
            self.root
        )
        logging.info(
            "[APP] Poster target size resolved from widget scale %.2f -> %sx%s",
            self._poster_target_scale,
            self._poster_target_width,
            self._poster_target_height,
        )

        self.game_folder = ""
        self.opti_source_archive = ""
        self.fsr4_source_archive = ""
        self.optiscaler_cache_dir = OPTISCALER_CACHE_DIR
        self.optiscaler_cache_dir.mkdir(parents=True, exist_ok=True)
        self.fsr4_cache_dir = FSR4_CACHE_DIR
        self.fsr4_cache_dir.mkdir(parents=True, exist_ok=True)
        self.optiscaler_archive_ready = False
        self.optiscaler_archive_downloading = False
        self.optiscaler_archive_error = ""
        self.optiscaler_archive_filename = ""
        self.fsr4_archive_ready = False
        self.fsr4_archive_downloading = False
        self.fsr4_archive_error = ""
        self.fsr4_archive_filename = ""
        self._initial_auto_scan_empty_popup_shown = False
        self.found_exe_list = []
        self.game_db = {}
        self.module_download_links = {}
        self.active_game_db_vendor = "default"
        self.active_game_db_gid = SHEET_GID
        self.gpu_names: list[str] = []
        self.gpu_count = 0
        self.is_multi_gpu = False
        self.multi_gpu_blocked = False
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
        self._archive_controller: Optional[ArchivePreparationController] = None
        self._bottom_panel_presenter: Optional[BottomPanelPresenter] = None
        self._game_db_controller: Optional[GameDbLoadController] = None
        self._gpu_flow_controller: Optional[GpuFlowController] = None
        self._header_status_presenter: Optional[HeaderStatusPresenter] = None
        self._scan_controller: Optional[ScanController] = None
        self._poster_loader = PosterImageLoader(
            PosterLoaderConfig(
                cache_dir=COVER_CACHE_DIR,
                assets_dir=ASSETS_DIR,
                default_poster_candidates=tuple(DEFAULT_POSTER_CANDIDATES),
                target_width=self._poster_target_width,
                target_height=self._poster_target_height,
                repo_raw_base_url=COVERS_REPO_RAW_BASE_URL,
                bundled_cover_filename_map=BUNDLED_COVER_FILENAME_MAP,
                timeout_seconds=IMAGE_TIMEOUT_SECONDS,
                max_retries=IMAGE_MAX_RETRIES,
                cache_version=POSTER_CACHE_VERSION,
                enable_memory_cache=ENABLE_POSTER_CACHE,
                memory_cache_max=IMAGE_CACHE_MAX,
            )
        )
        self._image_executor = ThreadPoolExecutor(max_workers=IMAGE_MAX_WORKERS, thread_name_prefix="cover-loader")
        self._poster_queue = PosterQueueController(
            root=self.root,
            executor=self._image_executor,
            loader=self._poster_loader.load,
            max_workers=IMAGE_MAX_WORKERS,
            retry_delay_ms=IMAGE_RETRY_DELAY_MS,
            get_visible_indices=self._visible_game_indices,
            is_scan_in_progress=self._is_scan_in_progress,
            on_image_ready=self._apply_loaded_poster,
        )
        self._startup_flow = StartupFlowController(
            root=self.root,
            callbacks=StartupFlowCallbacks(
                start_archive_prepare=self._start_optiscaler_archive_prepare,
                start_auto_scan=self._start_auto_scan,
                show_rtss_notice=self._show_rtss_notice,
                show_startup_warning_popup=self._show_startup_warning_popup,
            ),
            is_multi_gpu_blocked=self._is_multi_gpu_block_active,
            get_startup_warning_text=lambda: pick_module_message(self.module_download_links, "warning", self.lang),
            logger=logging.getLogger(),
        )
        self._task_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="general-task")
        self._download_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="archive-download")
        self._app_update_manager = app_update.InstallerUpdateManager(
            self.root,
            current_version=APP_VERSION,
            strings=self.txt,
            on_busy_state_changed=self._update_install_button_state,
            on_update_failed=lambda: self._startup_flow.run_post_sheet_startup(True),
            on_exit_requested=self._on_close,
        )
        self._games_scrollregion_after_id = None
        self._games_viewport_after_id = None
        self._overflow_fit_after_id = None
        self._header_status_presenter = HeaderStatusPresenter(
            root=self.root,
            status_text_color=_STATUS_TEXT,
            scan_status_text_color=_SCAN_STATUS_TEXT,
            status_indicator_loading_dim_color=_STATUS_INDICATOR_LOADING_DIM,
            status_indicator_pulse_ms=_STATUS_INDICATOR_PULSE_MS,
            supported_games_wiki_url=SUPPORTED_GAMES_WIKI_URL,
            link_active_color=_LINK_ACTIVE,
            link_hover_color=_LINK_HOVER,
            logger=logging.getLogger(),
        )
        self._bottom_panel_presenter = BottomPanelPresenter(
            info_text_offset_px=INFO_TEXT_OFFSET_PX,
            version_name_formatter=_format_optiscaler_version_display_name,
            info_emphasis_color=_STATUS_INDICATOR_WARNING,
            logger=logging.getLogger(),
        )
        self.setup_ui()
        self._create_archive_controller()
        self._create_game_db_controller()
        self._create_gpu_flow_controller()
        self._create_scan_controller()
        if self._gpu_flow_controller is not None:
            self._gpu_flow_controller.start_detection()
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

    def _format_gpu_label_text(self, gpu_info: str) -> str:
        normalized_gpu = str(gpu_info or "").strip() or self.txt.main.unknown_gpu
        return self.txt.main.gpu_label_template.format(gpu=normalized_gpu)

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
            root_width_fallback=WINDOW_W,
            root_height_fallback=WINDOW_H,
        )

    def _is_multi_gpu_block_active(self) -> bool:
        return self.gpu_count > MAX_SUPPORTED_GPU_COUNT

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

    def _set_supported_games_wiki_link_hover(self, hovered: bool) -> None:
        presenter = self._header_status_presenter
        if presenter is None:
            return
        presenter.set_supported_games_wiki_link_hover(
            getattr(self, "lbl_supported_games_wiki_link", None),
            hovered,
        )

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
        presenter = self._header_status_presenter
        if presenter is None:
            return
        presenter.set_scan_status_message(
            getattr(self, "lbl_scan_status", None),
            text,
            text_color,
        )

    def _set_status_badge_state(self, label_text: str, indicator_color: str, pulse: bool = False):
        presenter = self._header_status_presenter
        if presenter is None:
            return
        presenter.set_status_badge_state(
            label_widget=getattr(self, "status_badge_label", None),
            dot_widget=getattr(self, "status_badge_dot", None),
            label_text=label_text,
            indicator_color=indicator_color,
            pulse=pulse,
        )

    def _get_selected_game_header_text(self) -> str:
        if self.selected_game_index is None or not (0 <= self.selected_game_index < len(self.found_exe_list)):
            return ""

        game = self.found_exe_list[self.selected_game_index]
        if self.lang == "ko":
            game_name = str(game.get("display", "") or game.get("game_name_kr", "") or game.get("game_name", "")).strip()
        else:
            game_name = str(game.get("game_name", "") or game.get("display", "")).strip()
        return game_name

    def _update_selected_game_header(self):
        presenter = self._header_status_presenter
        if presenter is None:
            return
        presenter.update_selected_game_header(
            getattr(self, "lbl_selected_game_header", None),
            self._get_selected_game_header_text(),
        )

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
            if self._header_status_presenter is not None:
                self._header_status_presenter.shutdown()
        except Exception:
            pass
        try:
            self._poster_queue.shutdown()
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
            self._poster_loader.close()
        except Exception:
            pass
        try:
            self._app_update_manager.shutdown()
        except Exception:
            pass
        self.root.destroy()

    def _start_game_db_load_async(self):
        if self._game_db_controller is None:
            return

        game_db_gid = int(getattr(self, "active_game_db_gid", SHEET_GID) or SHEET_GID)
        game_db_vendor = str(getattr(self, "active_game_db_vendor", "default") or "default")
        started = self._game_db_controller.start_load(game_db_gid, game_db_vendor)
        if not started:
            return
        logging.info(
            "[APP] Starting Game DB load for vendor=%s gpu=%s",
            game_db_vendor,
            self.gpu_info,
        )

    def _on_game_db_loaded(self, result: GameDbLoadResult) -> None:
        self.sheet_loading = False
        self.active_game_db_gid = int(result.game_db_gid)
        self.active_game_db_vendor = str(result.game_db_vendor or "default")
        self.game_db = result.game_db if result.ok else {}
        self.module_download_links = result.module_download_links if result.ok else {}

        self.sheet_status = result.ok
        if result.ok:
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
                result.error,
            )
        if self.multi_gpu_blocked:
            self._update_install_button_state()
            self._update_sheet_status()
            return
        self._refresh_optiscaler_archive_info_ui()
        self._update_install_button_state()
        self._update_sheet_status()
        update_started = self.check_app_update() if result.ok else False
        if not update_started:
            self._startup_flow.run_post_sheet_startup(result.ok)

    def _start_optiscaler_archive_prepare(self):
        if self._archive_controller is None:
            return
        entry = self.module_download_links.get("optiscaler", {}) if hasattr(self, "module_download_links") else {}
        state = self._archive_controller.prepare_optiscaler(entry, self.optiscaler_cache_dir)
        self._apply_optiscaler_archive_state(state)
        if state.downloading:
            self._update_install_button_state()
            return
        self._start_fsr4_archive_prepare()
        self._update_install_button_state()

    def _start_fsr4_archive_prepare(self):
        if self._archive_controller is None:
            return
        enabled = self._should_apply_fsr4_for_game()
        if not enabled:
            logging.info("[APP] Skipping FSR4 preparation for GPU: %s", self.gpu_info)
        entry = self.module_download_links.get("fsr4int8", {}) if hasattr(self, "module_download_links") else {}
        state = self._archive_controller.prepare_fsr4(
            entry,
            self.fsr4_cache_dir,
            enabled=enabled,
        )
        self._apply_fsr4_archive_state(state)
        self._update_install_button_state()

    def check_app_update(self) -> bool:
        return self._app_update_manager.check_for_update(
            self.module_download_links,
            blocked=self.multi_gpu_blocked,
        )

    def _show_rtss_notice(self) -> None:
        logger = None
        if getattr(self, "found_exe_list", None) and self.selected_game_index is not None:
            logger = get_prefixed_logger(self.found_exe_list[self.selected_game_index].get("game_name", "unknown"))

        rtss_notice.check_and_show_rtss_notice(
            root=self.root,
            module_download_links=self.module_download_links,
            use_korean=USE_KOREAN,
            assets_dir=ASSETS_DIR,
            theme=RTSS_NOTICE_THEME,
            logger=logger,
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

    def _is_scan_in_progress(self) -> bool:
        controller = getattr(self, "_scan_controller", None)
        return bool(controller and controller.is_scan_in_progress)

    def _apply_gpu_flow_state(self, state: GpuFlowState) -> None:
        self._gpu_context = state.gpu_context
        self.gpu_names = list(state.gpu_names or ())
        self.gpu_count = max(0, int(state.gpu_count or 0))
        self.is_multi_gpu = bool(state.is_multi_gpu)
        self.multi_gpu_blocked = bool(state.multi_gpu_blocked)
        self._gpu_selection_pending = bool(state.gpu_selection_pending)
        self._selected_gpu_adapter = state.selected_adapter

        if state.game_db_vendor is not None:
            self.active_game_db_vendor = str(state.game_db_vendor or "default")
        if state.game_db_gid is not None:
            self.active_game_db_gid = int(state.game_db_gid or SHEET_GID)

        self.gpu_info = str(state.gpu_info or self.txt.main.unknown_gpu).strip() or self.txt.main.unknown_gpu
        if hasattr(self, "gpu_lbl") and self.gpu_lbl:
            self.gpu_lbl.configure(text=self._format_gpu_label_text(self.gpu_info))

    def _handle_unsupported_gpu_block(self, scan_status_message: str, info_text: str) -> None:
        self._startup_flow.mark_post_sheet_startup_done()
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
        self._set_scan_status_message(scan_status_message, "#FF8A8A")
        self._clear_cards()
        if hasattr(self, "info_text") and self.info_text:
            self._set_information_text(info_text)
        self._update_selected_game_header()
        self._update_sheet_status()
        self._update_install_button_state()

    def _create_gpu_flow_controller(self) -> None:
        self._gpu_flow_controller = GpuFlowController(
            executor=self._task_executor,
            schedule=lambda callback: self.root.after(0, callback),
            callbacks=GpuFlowCallbacks(
                apply_state=self._apply_gpu_flow_state,
                handle_unsupported_gpu=self._handle_unsupported_gpu_block,
                set_scan_status_message=self._set_scan_status_message,
                update_sheet_status=self._update_sheet_status,
                update_install_button_state=self._update_install_button_state,
                start_game_db_load=self._start_game_db_load_async,
            ),
            vendor_db_gids=GPU_VENDOR_DB_GIDS,
            default_gid=SHEET_GID,
            unknown_gpu_text=self.txt.main.unknown_gpu,
            waiting_for_gpu_selection_text=self.txt.main.waiting_for_gpu_selection,
            unsupported_gpu_message=self.txt.gpu.unsupported_message,
            unsupported_gpu_info_text=gpu_notice.get_unsupported_gpu_message(self.txt),
            detect_gpu_context=gpu_service.detect_gpu_context,
            select_dual_gpu_adapter=lambda adapters: gpu_notice.select_dual_gpu_adapter(
                root=self.root,
                adapters=adapters,
                strings=self.txt,
                theme=GPU_NOTICE_THEME,
            ),
            show_unsupported_gpu_notice=lambda: gpu_notice.show_unsupported_gpu_notice(
                self.root,
                self.txt,
                GPU_NOTICE_THEME,
            ),
            max_supported_gpu_count=MAX_SUPPORTED_GPU_COUNT,
            logger=logging.getLogger(),
        )

    def _create_archive_controller(self) -> None:
        self._archive_controller = ArchivePreparationController(
            executor=self._download_executor,
            schedule=lambda callback: self.root.after(0, callback),
            callbacks=ArchivePreparationCallbacks(
                on_optiscaler_state_changed=self._on_optiscaler_archive_state_changed,
                on_fsr4_state_changed=self._on_fsr4_archive_state_changed,
            ),
            download_to_file=installer_services.download_to_file,
            logger=logging.getLogger(),
        )

    def _create_game_db_controller(self) -> None:
        self._game_db_controller = GameDbLoadController(
            executor=self._task_executor,
            schedule=lambda callback: self.root.after(0, callback),
            callbacks=GameDbControllerCallbacks(
                on_load_complete=self._on_game_db_loaded,
            ),
            spreadsheet_id=SHEET_ID,
            download_links_gid=DOWNLOAD_LINKS_SHEET_GID,
            load_game_db=sheet_loader.load_game_db_from_public_sheet,
            load_module_download_links=sheet_loader.load_module_download_links_from_public_sheet,
            logger=logging.getLogger(),
        )

    def _apply_optiscaler_archive_state(self, state: ArchivePreparationState) -> None:
        self.optiscaler_archive_filename = str(state.filename or "")
        self.optiscaler_archive_ready = bool(state.ready)
        self.optiscaler_archive_downloading = bool(state.downloading)
        self.optiscaler_archive_error = str(state.error_message or "")
        self.opti_source_archive = str(state.archive_path or "")

    def _apply_fsr4_archive_state(self, state: ArchivePreparationState) -> None:
        self.fsr4_archive_filename = str(state.filename or "")
        self.fsr4_archive_ready = bool(state.ready)
        self.fsr4_archive_downloading = bool(state.downloading)
        self.fsr4_archive_error = str(state.error_message or "")
        self.fsr4_source_archive = str(state.archive_path or "")

    def _on_optiscaler_archive_state_changed(self, state: ArchivePreparationState) -> None:
        self._apply_optiscaler_archive_state(state)
        self._start_fsr4_archive_prepare()
        self._update_install_button_state()

    def _on_fsr4_archive_state_changed(self, state: ArchivePreparationState) -> None:
        self._apply_fsr4_archive_state(state)
        self._update_install_button_state()

    def _create_scan_controller(self) -> None:
        self._scan_controller = ScanController(
            executor=self._task_executor,
            schedule=lambda callback: self.root.after(0, callback),
            callbacks=ScanControllerCallbacks(
                prepare_scan_ui=self._prepare_scan_ui,
                reset_scan_results=self._reset_scan_results_for_new_scan,
                add_game_card=self._add_game_card_incremental,
                finish_scan_ui=self._finish_scan_ui,
                pump_poster_queue=self._pump_poster_queue,
                show_auto_scan_empty_popup=self._enqueue_initial_auto_scan_empty_popup,
                show_manual_scan_empty_popup=self._show_manual_scan_empty_popup,
                show_select_game_hint=self._show_select_game_hint,
            ),
            get_game_db=lambda: self.game_db,
            get_lang=lambda: self.lang,
            is_game_supported=self._is_game_supported_for_current_gpu,
            logger=logging.getLogger(),
        )

    def _prepare_scan_ui(self) -> None:
        self._set_scan_status_message(self.txt.main.scanning, "#F1F5F9")
        self.btn_select_folder.configure(state="disabled")

    def _finish_scan_ui(self) -> None:
        self.btn_select_folder.configure(state="normal")
        self._set_scan_status_message("")

    def _pump_poster_queue(self) -> None:
        self._poster_queue.pump()

    def _show_manual_scan_empty_popup(self) -> None:
        self._show_scan_result_popup(self.txt.main.manual_scan_no_results)

    def _show_select_game_hint(self) -> None:
        self._set_information_text(self.txt.main.select_game_hint)

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
        self._startup_flow.enqueue_popup(
            "auto_scan_no_results",
            priority=60,
            blocking=False,
            show_callback=lambda done_callback, text=detail: self._show_scan_result_popup(
                text,
                on_close=done_callback,
            ),
        )
        self._startup_flow.run_next_popup()

    def _start_auto_scan(self):
        """Kick off a silent auto-scan of known Steam/game directories."""
        if self.multi_gpu_blocked:
            return
        if self._scan_controller is None:
            return
        self._scan_controller.start_auto_scan()

    # ------------------------------------------------------------------
    # UI builder
    # ------------------------------------------------------------------

    def setup_ui(self):
        build_main_ui(self, MAIN_UI_THEME)

    def _refresh_optiscaler_archive_info_ui(self):
        presenter = self._bottom_panel_presenter
        if presenter is None:
            return
        presenter.refresh_optiscaler_archive_info_ui(
            getattr(self, "lbl_optiscaler_version_line", None),
            sheet_loading=bool(getattr(self, "sheet_loading", False)),
            module_download_links=self.module_download_links if hasattr(self, "module_download_links") else {},
            version_line_template=self.txt.main.version_line_template,
        )

    def _apply_information_text_shift(self):
        presenter = self._bottom_panel_presenter
        if presenter is None:
            return
        presenter.apply_information_text_shift(getattr(self, "info_text", None))

    # ------------------------------------------------------------------
    # Status indicator
    # ------------------------------------------------------------------

    def _update_sheet_status(self):
        presenter = self._header_status_presenter
        if presenter is None:
            return
        presenter.update_sheet_status(
            label_widget=getattr(self, "status_badge_label", None),
            dot_widget=getattr(self, "status_badge_dot", None),
            multi_gpu_blocked=self.multi_gpu_blocked,
            gpu_selection_pending=self._gpu_selection_pending,
            sheet_loading=self.sheet_loading,
            sheet_status=self.sheet_status,
            status_gpu_config_text=self.txt.main.status_gpu_config,
            status_gpu_select_text=self.txt.main.status_gpu_select,
            status_game_db_text=self.txt.main.status_game_db,
            indicator_offline=_STATUS_INDICATOR_OFFLINE,
            indicator_warning=_STATUS_INDICATOR_WARNING,
            indicator_loading=_STATUS_INDICATOR_LOADING,
            indicator_online=_STATUS_INDICATOR_ONLINE,
        )

    # ------------------------------------------------------------------
    # Information text
    # ------------------------------------------------------------------

    def _set_information_text(self, text=""):
        presenter = self._bottom_panel_presenter
        if presenter is None:
            return
        presenter.set_information_text(
            getattr(self, "info_text", None),
            text=text,
            no_information_text=self.txt.main.no_information,
        )

    # ------------------------------------------------------------------
    # Poster card grid
    # ------------------------------------------------------------------

    def _reset_selected_game_state(self) -> None:
        self.selected_game_index = None
        self._game_popup_confirmed = False
        self.install_precheck_running = False
        self.install_precheck_ok = False
        self.install_precheck_error = ""
        self.install_precheck_dll_name = ""
        self._set_information_text("")

    def _clear_rendered_cards(self) -> None:
        self._poster_queue.begin_new_render()
        for frame in self.card_frames:
            frame.destroy()
        self.card_frames.clear()
        self.card_items.clear()
        self._ctk_images.clear()  # Release stale PhotoImage refs to prevent accumulation.
        self._hovered_card_index = None

    def _reset_scan_results_for_new_scan(self) -> None:
        self.found_exe_list = []
        self._clear_cards()
        self._configure_card_columns(self._get_dynamic_column_count())

    def _clear_cards(self, keep_selection=False):
        self._clear_rendered_cards()
        if not keep_selection:
            self._reset_selected_game_state()
        self._update_selected_game_header()
        self._update_install_button_state()

    def _get_effective_widget_scale(self) -> float:
        return _get_ctk_scale(self.root, 1.0)

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
        self._poster_queue.pump()

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
            self._poster_queue.pump()

    def _schedule_games_viewport_update(self, delay_ms: int = 30):
        try:
            if self._games_viewport_after_id is not None:
                self.root.after_cancel(self._games_viewport_after_id)
            self._games_viewport_after_id = self.root.after(max(0, int(delay_ms)), self._run_games_viewport_update)
        except Exception:
            self._games_viewport_after_id = None

    def _run_games_viewport_update(self):
        self._games_viewport_after_id = None
        self._poster_queue.pump()

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

        self._poster_queue.pump()

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
                "base_pil": self._poster_loader.make_placeholder_image(),
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
        self._poster_queue.queue(
            index,
            img_label,
            game["display"],
            filename_cover,
            cover_url,
        )

        return card

    def _set_card_placeholder(self, index: int, label: ctk.CTkLabel, title: str):
        pil_img = self._poster_loader.make_placeholder_image()
        self.root.after(0, lambda idx=index, l=label, img=pil_img: self._set_card_base_image(idx, l, img))

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

    def _apply_loaded_poster(self, index: int, label: ctk.CTkLabel, pil_img: Image.Image):
        self._set_card_base_image(index, label, pil_img)

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
        logger = get_prefixed_logger(str(game_data.get("game_name", "unknown")).strip() or "unknown")
        handler = get_game_handler(game_data)
        try:
            logger.info("Running install precheck with handler: %s", getattr(handler, "handler_key", "default"))
            precheck = handler.run_install_precheck(game_data, self.lang == "ko", logger)
            self.install_precheck_ok = bool(precheck.ok)
            self.install_precheck_error = ""
            self.install_precheck_dll_name = str(precheck.resolved_dll_name or "")
            notice_message = handler.format_precheck_notice(precheck, False)
            if notice_message:
                logger.info("Install precheck notice: %s", notice_message)
            if precheck.ok:
                logger.info("Install precheck resolved DLL name: %s", self.install_precheck_dll_name)
            else:
                self.install_precheck_error = handler.format_precheck_error(precheck, self.lang == "ko")
                logger.warning("Install precheck failed: %s", precheck.raw_error_message)
        except Exception as exc:
            self.install_precheck_ok = False
            self.install_precheck_error = str(exc)
            self.install_precheck_dll_name = ""
            logger.exception("Install precheck failed unexpectedly: %s", exc)
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

        if self._scan_controller is None:
            return
        self._scan_controller.start_manual_scan(self.game_folder)

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
        game_name = str(game_data.get("game_name", "unknown")).strip() or "unknown"
        logger = get_prefixed_logger(game_name)
        try:
            handler = get_game_handler(game_data)
            logger.info("Using game handler: %s", getattr(handler, "handler_key", "default"))
            install_plan = handler.prepare_install_plan(self, game_data, source_archive, resolved_dll_name, logger)
            game_data = dict(install_plan.game_data)
            source_archive = str(install_plan.source_archive or source_archive)
            resolved_dll_name = str(install_plan.resolved_dll_name or resolved_dll_name)

            target_path = game_data["path"]
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

            handler.finalize_install(self, game_data, target_path, logger)
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
