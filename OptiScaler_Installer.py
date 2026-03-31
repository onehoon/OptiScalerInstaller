import os
import io
import subprocess
import tempfile
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
import ctypes
import locale
import stat
import gpu_notice
import gpu_service
import installer_services
import ini_utils
import sheet_loader
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

import rtss_notice

 # Application Version
APP_VERSION = "0.2.1"
# Install flow supports up to two detected GPUs. Dual-GPU requires explicit user selection.
MAX_SUPPORTED_GPU_COUNT = 2

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
UI_LANGUAGE_ENV = "FORCE_UI_LANGUAGE"

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

def _subprocess_no_window_kwargs() -> dict:
    if os.name != "nt":
        return {}

    kwargs = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    kwargs["startupinfo"] = startupinfo
    return kwargs


def _get_forced_ui_language() -> Optional[bool]:
    raw = str(os.environ.get(UI_LANGUAGE_ENV, "") or "").strip().lower()
    if raw in {"", "auto"}:
        return None
    if raw in {"ko", "kr", "korean"}:
        return True
    if raw in {"en", "english"}:
        return False

    logging.warning(
        "[APP] Invalid %s=%r, using automatic UI language detection",
        UI_LANGUAGE_ENV,
        raw,
    )
    return None


def _is_korean_ui() -> bool:
    """Return True if the Windows UI language is Korean (ko-KR, LCID 0x0412)."""
    forced = _get_forced_ui_language()
    if forced is not None:
        logging.info("[APP] UI language forced by %s=%s", UI_LANGUAGE_ENV, "KO" if forced else "EN")
        return forced

    try:
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return (lang_id & 0xFF) == 0x12  # Primary language ID for Korean
    except Exception:
        pass
    try:
        lang = locale.getlocale()[0] or ""
        return lang.lower().startswith("ko")
    except Exception:
        pass
    return False


USE_KOREAN: bool = _is_korean_ui()
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
LOCAL_APPDATA_DIR = Path(os.environ.get("LOCALAPPDATA") or Path(tempfile.gettempdir()))
APP_CACHE_DIR = LOCAL_APPDATA_DIR / "OptiScalerInstaller"
OPTISCALER_CACHE_DIR = APP_CACHE_DIR / "cache" / "optiscaler"
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
IMAGE_RETRY_DELAY_MS = int(os.environ.get("OPTISCALER_IMAGE_RETRY_DELAY_MS", "1500"))
HI_DPI_SCALE = 2
TARGET_POSTER_W = CARD_W * HI_DPI_SCALE
TARGET_POSTER_H = CARD_H * HI_DPI_SCALE
INFO_TEXT_OFFSET_PX = 10
POSTER_CACHE_VERSION = 1
ENABLE_POSTER_CACHE = os.environ.get("OPTISCALER_ENABLE_POSTER_CACHE", "1").strip().lower() in {"1", "true", "yes", "on"}
IMAGE_CACHE_MAX = int(os.environ.get("OPTISCALER_IMAGE_CACHE_MAX", "100"))


def _parse_version_tuple(verstr: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", str(verstr or "")))


def _get_runtime_launch_path() -> Path:
    try:
        if getattr(sys, "frozen", False) and hasattr(sys, "executable"):
            return Path(sys.executable).resolve()
    except Exception:
        pass
    return Path(__file__).resolve()


def _get_runtime_install_dir() -> Path:
    return _get_runtime_launch_path().parent


def _build_expected_installer_exe_name(version_text: str, fallback_url: str = "") -> str:
    normalized = re.sub(r"\s+", "", str(version_text or ""))
    if normalized.lower().endswith(".exe"):
        return Path(normalized).name
    if normalized.lower().startswith("v"):
        normalized = normalized[1:]
    if normalized:
        return f"OptiScaler_Installer_v{normalized}.exe"

    fallback_name = Path(urlparse(str(fallback_url or "")).path).name
    if fallback_name.lower().endswith(".exe"):
        return fallback_name
    return ""


def _resolve_safe_child_path(base_dir: Path, child_path: str) -> Optional[Path]:
    raw_name = str(child_path or "").replace("\\", "/").strip()
    if not raw_name:
        return None

    try:
        resolved_base = base_dir.resolve(strict=False)
        resolved_child = (resolved_base / Path(raw_name)).resolve(strict=False)
        resolved_child.relative_to(resolved_base)
        return resolved_child
    except Exception:
        return None


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


def _estimate_wrapped_text_lines(text: str, font: tkfont.Font, max_width_px: int) -> int:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    available_width = max(32, int(max_width_px or 0))
    total_lines = 0

    for paragraph in normalized.split("\n"):
        if paragraph == "":
            total_lines += 1
            continue

        remaining = paragraph
        while remaining:
            if font.measure(remaining) <= available_width:
                total_lines += 1
                break

            fit_len = 0
            lo, hi = 1, len(remaining)
            while lo <= hi:
                mid = (lo + hi) // 2
                if font.measure(remaining[:mid]) <= available_width:
                    fit_len = mid
                    lo = mid + 1
                else:
                    hi = mid - 1

            if fit_len <= 0:
                fit_len = 1

            break_at = fit_len
            for idx in range(fit_len - 1, 0, -1):
                if remaining[idx].isspace():
                    break_at = idx
                    break

            if break_at <= 0:
                break_at = fit_len

            total_lines += 1
            remaining = remaining[break_at:].lstrip()

    return max(1, total_lines)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Accent colours
_ACCENT = "#4CC9F0"
_ACCENT_HOVER = "#35B6E0"
_ACCENT_SUCCESS = "#7EE1AA"
_LINK_ACTIVE = "#7DD3FC"
_LINK_HOVER = "#38BDF8"
_CARD_BG = "#181B21"
_SURFACE = "#2A2E35"
_PANEL = "#1E2128"
_ACCENT_DISABLED = "#3A414C"
FONT_HEADING = "Malgun Gothic" if USE_KOREAN else "Segoe UI"
FONT_UI = "Malgun Gothic" if USE_KOREAN else "Segoe UI"
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


class OptiManagerApp:
    def __init__(self, root: ctk.CTk):
        self.root = root
        self.root.title(f"OptiScaler Installer (v{APP_VERSION})")
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
        self.optiscaler_cache_dir = OPTISCALER_CACHE_DIR
        self.optiscaler_cache_dir.mkdir(parents=True, exist_ok=True)
        self.optiscaler_archive_ready = False
        self.optiscaler_archive_downloading = False
        self.optiscaler_archive_error = ""
        self.optiscaler_archive_filename = ""
        self.app_update_in_progress = False
        self._post_sheet_startup_done = False
        self._startup_popup_queue: list[dict[str, object]] = []
        self._startup_popup_active = False
        self._startup_popup_order = 0
        self.found_exe_list = []
        self.game_db = {}
        self.module_download_links = {}
        self._supported_games_popup_shown = False
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
        self.gpu_info = "Checking GPU..."
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
        self._app_update_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="app-update")
        self._pending_image_jobs: dict = {}
        self._inflight_image_futures: dict = {}
        self._failed_image_jobs: dict = {}
        self._delayed_image_retry_after_ids: dict[int, str] = {}
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

    def _show_game_selection_popup(self, message_text: str, on_confirm: callable = None, is_after_popup: bool = False):
        popup = ctk.CTkToplevel(self.root)
        popup.title("Installer Notice")
        popup.transient(self.root)
        popup.grab_set()
        popup.resizable(False, False)
        popup.configure(fg_color=_SURFACE)
        popup.withdraw()

        container = ctk.CTkFrame(popup, fg_color="transparent")
        container.pack(fill="both", padx=22, pady=(18, 8))

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
        text_widget.configure(font=normal_font, padx=0, pady=0, spacing1=0, spacing2=0, spacing3=0)

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

        plain_message_text = re.sub(r"\[\s*(?:RED|END)\s*\]", "", message_text, flags=re.IGNORECASE)

        if "[RED]" in message_text and "[END]" in message_text:
            text_widget.tag_configure("red", foreground="#FF4444")
            insert_with_red(message_text)
        else:
            text_widget.insert("end", message_text)

        text_widget.pack(anchor="w", fill="x", pady=(0, 6))

        preferred_text_chars = 72
        screen_w = max(1, int(self.root.winfo_screenwidth() or WINDOW_W))
        screen_h = max(1, int(self.root.winfo_screenheight() or WINDOW_H))
        avg_char_width = max(7, int(normal_font.measure("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz") / 52))
        zero_char_width = max(7, int(normal_font.measure("0")))
        max_text_chars = max(preferred_text_chars, min(110, max(58, (screen_w - 140) // avg_char_width)))
        max_popup_h = max(240, screen_h - 80)
        width_steps = list(range(preferred_text_chars, max_text_chars + 1, 4))
        if not width_steps:
            width_steps = [preferred_text_chars]
        if width_steps[-1] != max_text_chars:
            width_steps.append(max_text_chars)

        resolved_line_count = max(1, plain_message_text.count("\n") + 1)
        chosen_width = preferred_text_chars
        for width_chars in width_steps:
            text_widget.configure(width=width_chars)
            popup.update_idletasks()
            resolved_line_count = _estimate_wrapped_text_lines(
                plain_message_text,
                normal_font,
                max(32, zero_char_width * width_chars),
            )
            text_widget.configure(height=resolved_line_count)
            popup.update_idletasks()
            chosen_width = width_chars
            if popup.winfo_reqheight() <= max_popup_h:
                break

        text_widget.configure(width=chosen_width, height=resolved_line_count, state="disabled")

        fade_in_step = 0.14
        fade_out_step = 0.18
        fade_interval_ms = 18
        fade_out_interval_ms = 16
        fade_supported = False
        fade_in_after_id = None
        closing_popup = False
        confirm_button: Optional[ctk.CTkButton] = None

        def _popup_exists() -> bool:
            try:
                return bool(popup.winfo_exists())
            except Exception:
                return False

        def _get_popup_alpha() -> float:
            try:
                return float(popup.attributes("-alpha"))
            except Exception:
                return 1.0

        def _finalize_close():
            try:
                popup.grab_release()
            except Exception:
                pass
            try:
                popup.destroy()
            except Exception:
                pass
            if on_confirm:
                self.root.after_idle(on_confirm)

        def _fade_in(opacity: float = 0.0):
            nonlocal fade_in_after_id
            if closing_popup or not _popup_exists():
                return
            next_opacity = min(1.0, opacity + fade_in_step)
            try:
                popup.attributes("-alpha", next_opacity)
            except Exception:
                fade_in_after_id = None
                logging.debug("Selection popup fade-in failed", exc_info=True)
                try:
                    popup.attributes("-alpha", 1.0)
                except Exception:
                    pass
                return
            if next_opacity < 1.0:
                fade_in_after_id = popup.after(fade_interval_ms, _fade_in, next_opacity)
            else:
                fade_in_after_id = None

        def _fade_out(opacity: float):
            if not _popup_exists():
                return
            next_opacity = max(0.0, opacity - fade_out_step)
            try:
                popup.attributes("-alpha", next_opacity)
            except Exception:
                _finalize_close()
                return
            if next_opacity > 0.0:
                popup.after(fade_out_interval_ms, _fade_out, next_opacity)
            else:
                _finalize_close()

        def _confirm():
            nonlocal closing_popup, fade_in_after_id
            if closing_popup:
                return
            closing_popup = True
            if confirm_button is not None:
                try:
                    confirm_button.configure(state="disabled")
                except Exception:
                    pass
            if fade_in_after_id is not None:
                try:
                    popup.after_cancel(fade_in_after_id)
                except Exception:
                    pass
                fade_in_after_id = None
            if fade_supported:
                _fade_out(_get_popup_alpha())
            else:
                _finalize_close()

        confirm_button = ctk.CTkButton(
            container,
            text="확인" if USE_KOREAN else "OK",
            width=100,
            height=34,
            corner_radius=8,
            fg_color=_ACCENT,
            hover_color=_ACCENT_HOVER,
            text_color="#000000",
            font=ctk.CTkFont(family=FONT_UI, size=12, weight="bold"),
            command=_confirm,
        )
        confirm_button.pack(pady=(10, 0))

        def _apply_selection_popup_geometry(use_requested_size: bool = False):
            try:
                popup.update_idletasks()

                if use_requested_size:
                    popup_w = max(1, int(popup.winfo_reqwidth()))
                    popup_h = max(1, int(popup.winfo_reqheight()))
                else:
                    popup_w = max(1, int(popup.winfo_width() or popup.winfo_reqwidth()))
                    popup_h = max(1, int(popup.winfo_height() or popup.winfo_reqheight()))
                screen_w = max(1, int(self.root.winfo_screenwidth() or popup_w))
                screen_h = max(1, int(self.root.winfo_screenheight() or popup_h))
                margin = 12

                if popup_w + (margin * 2) > screen_w:
                    popup_w = max(200, screen_w - (margin * 2))
                if popup_h + (margin * 2) > screen_h:
                    popup_h = max(120, screen_h - (margin * 2))

                root_x = self.root.winfo_x()
                root_y = self.root.winfo_y()
                root_w = self.root.winfo_width()
                root_h = self.root.winfo_height()
                x = root_x + (root_w // 2) - (popup_w // 2)
                y = root_y + (root_h // 2) - (popup_h // 2)
                min_x = margin if popup_w + (margin * 2) < screen_w else 0
                min_y = margin if popup_h + (margin * 2) < screen_h else 0
                max_x = max(min_x, screen_w - popup_w - margin)
                max_y = max(min_y, screen_h - popup_h - margin)
                x = max(min_x, min(x, max_x))
                y = max(min_y, min(y, max_y))
                logical_w = max(1, int(round(popup._reverse_window_scaling(popup_w))))
                logical_h = max(1, int(round(popup._reverse_window_scaling(popup_h))))
                popup.geometry(f"{logical_w}x{logical_h}+{x}+{y}")
            except Exception:
                logging.debug("Failed to size selection popup", exc_info=True)

        popup.protocol("WM_DELETE_WINDOW", lambda: None)  # Block closing without confirm
        _apply_selection_popup_geometry(use_requested_size=True)
        try:
            popup.attributes("-alpha", 0.0)
            fade_supported = True
        except Exception:
            fade_supported = False
            logging.debug("Popup alpha fade is not supported for selection popup", exc_info=True)
        popup.deiconify()
        popup.lift()
        try:
            popup.focus_set()
        except Exception:
            pass
        popup.after(0, _apply_selection_popup_geometry)
        if fade_supported:
            fade_in_after_id = popup.after(45, _fade_in, 0.0)

    def _fetch_gpu_info_async(self):
        try:
            gpu_context = gpu_service.detect_gpu_context(GPU_VENDOR_DB_GIDS, SHEET_GID)
        except Exception:
            logging.exception("Error fetching GPU info")
            gpu_context = gpu_service.GpuContext(
                gpu_names=[],
                gpu_count=0,
                gpu_info="Unknown",
                vendors=[],
                selected_vendor="default",
                selected_gid=SHEET_GID,
                adapters=(),
                selected_model_name="",
                selected_display_name="",
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
        self._supported_games_popup_shown = True
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
        if hasattr(self, "lbl_game_path") and self.lbl_game_path:
            text = "3개 이상의 GPU는 지원되지 않습니다." if USE_KOREAN else "3 or more GPUs are not supported."
            self.lbl_game_path.configure(text=text, text_color="#FF8A8A")
            self.root.after(0, self._align_supported_games_count_label)
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
            self.gpu_info = str(selected_adapter.model_name or gpu_context.selected_model_name or gpu_context.gpu_info or "Unknown")
        else:
            self.active_game_db_vendor = str(gpu_context.selected_vendor or "default")
            self.active_game_db_gid = int(gpu_context.selected_gid or SHEET_GID)
            self.gpu_info = str(gpu_context.selected_model_name or gpu_context.gpu_info or "Unknown")

        if hasattr(self, "gpu_lbl") and self.gpu_lbl:
            self.gpu_lbl.configure(text=f"GPU: {self.gpu_info}")

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
                self.gpu_info = str(gpu_context.gpu_info or "Unknown")
                if hasattr(self, "gpu_lbl") and self.gpu_lbl:
                    self.gpu_lbl.configure(text=f"GPU: {self.gpu_info}")
                self._apply_multi_gpu_block_state()
                return
            if self.gpu_count == 2 and len(gpu_context.adapters or ()) >= 2:
                self._gpu_selection_pending = True
                self._selected_gpu_adapter = None
                self.gpu_info = "GPU 선택 대기 중" if USE_KOREAN else "Waiting for GPU selection"
                if hasattr(self, "gpu_lbl") and self.gpu_lbl:
                    self.gpu_lbl.configure(text=f"GPU: {self.gpu_info}")
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

    def _align_supported_games_count_label(self):
        try:
            if not hasattr(self, "lbl_game_path") or not hasattr(self, "status_badge") or not hasattr(self, "scan_row"):
                return
            if not self.lbl_game_path.winfo_exists() or not self.status_badge.winfo_exists() or not self.scan_row.winfo_exists():
                return

            self.root.update_idletasks()
            label_width = max(self.lbl_game_path.winfo_reqwidth(), self.lbl_game_path.winfo_width())
            if label_width <= 1:
                return

            badge_center_root = self.status_badge.winfo_rootx() + (self.status_badge.winfo_width() / 2.0)
            row_root_x = self.scan_row.winfo_rootx()
            desired_left = int(round(badge_center_root - row_root_x - (label_width / 2.0)))

            button_right = 0
            if hasattr(self, "btn_select_folder") and self.btn_select_folder.winfo_exists():
                button_right = self.btn_select_folder.winfo_x() + self.btn_select_folder.winfo_width() + 18

            row_width = max(1, self.scan_row.winfo_width())
            max_left = max(button_right, row_width - label_width - 20)
            clamped_left = max(button_right, min(desired_left, max_left))
            self.lbl_game_path.place_configure(x=clamped_left, rely=0.5, anchor="w")
        except Exception:
            logging.debug("Failed to align supported-games count label", exc_info=True)

    def _get_selected_game_header_parts(self) -> tuple[str, str]:
        label = "선택된 게임" if USE_KOREAN else "Selected Game"
        if self.selected_game_index is None or not (0 <= self.selected_game_index < len(self.found_exe_list)):
            return "", ""

        game = self.found_exe_list[self.selected_game_index]
        if USE_KOREAN:
            game_name = str(game.get("display", "") or game.get("game_name_kr", "") or game.get("game_name", "")).strip()
        else:
            game_name = str(game.get("game_name", "") or game.get("display", "")).strip()
        if not game_name:
            return "", ""
        return f"{label}: ", game_name

    def _update_selected_game_header(self):
        try:
            label_text, game_name = self._get_selected_game_header_parts()
            if hasattr(self, "lbl_selected_game_header_label") and self.lbl_selected_game_header_label.winfo_exists():
                self.lbl_selected_game_header_label.configure(text=label_text)
            if hasattr(self, "lbl_selected_game_header") and self.lbl_selected_game_header.winfo_exists():
                self.lbl_selected_game_header.configure(text=game_name)
        except Exception:
            logging.debug("Failed to update selected game header", exc_info=True)

    def _show_after_install_popup(self, game: dict):
        is_kr = USE_KOREAN
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
        can_install = (
            not self.multi_gpu_blocked
            and not self._gpu_selection_pending
            and self.sheet_status
            and not self.sheet_loading
            and not self.install_in_progress
            and not self.app_update_in_progress
            and has_valid_game
            and not self.install_precheck_running
            and self.install_precheck_ok
            and self.optiscaler_archive_ready
            and not self.optiscaler_archive_downloading
            and has_supported_gpu
            and getattr(self, "_game_popup_confirmed", False)
        )

        self.apply_btn.configure(
            state="normal" if can_install else "disabled",
            text="Install" if not self.install_in_progress else "Installing...",
            fg_color=_ACCENT if can_install else _ACCENT_DISABLED,
        )

    # ------------------------------------------------------------------
    # Async DB load
    # ------------------------------------------------------------------

    def _on_close(self):
        if self.install_in_progress:
            msg = "설치가 진행 중입니다. 완료 후 종료해 주세요." if USE_KOREAN else "Installation is in progress. Please wait."
            messagebox.showwarning("Warning", msg)
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
        try:
            self._download_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        try:
            self._app_update_executor.shutdown(wait=False, cancel_futures=True)
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
            self._update_install_button_state()
            return

        cache_path = self.optiscaler_cache_dir / filename
        self.opti_source_archive = str(cache_path)
        if cache_path.exists():
            self.optiscaler_archive_ready = True
            self.optiscaler_archive_downloading = False
            self.optiscaler_archive_error = ""
            logging.info("[APP] OptiScaler archive already cached: %s", cache_path)
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

        self._update_install_button_state()

    def _get_installer_update_entry(self) -> dict:
        entry = self.module_download_links.get("latest_installer_dl", {}) if hasattr(self, "module_download_links") else {}
        return entry if isinstance(entry, dict) else {}

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

        warning_key = "__warning_kr__" if USE_KOREAN else "__warning_en__"
        warning_text = str(self.module_download_links.get(warning_key, "")).strip()
        if not self._supported_games_popup_shown:
            self._supported_games_popup_shown = True
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
            self._enqueue_startup_popup(
                "supported_games",
                priority=20,
                blocking=False,
                show_callback=lambda done_callback: self._show_supported_games_popup(on_close=done_callback),
            )
        self._run_next_startup_popup()

    def _start_app_update(self, latest_info: dict) -> bool:
        if self.app_update_in_progress:
            return True

        latest_version = str(latest_info.get("version", "")).strip()
        download_url = str(latest_info.get("url") or latest_info.get("link") or "").strip()
        if not latest_version or not download_url:
            logging.warning(
                "[APP] Skipping installer update: missing latest_installer_dl metadata (version=%r, url=%r)",
                latest_version,
                download_url,
            )
            return False

        source_name = Path(urlparse(download_url).path).name
        source_ext = Path(source_name).suffix.lower()
        if source_ext not in {".zip", ".exe"}:
            logging.warning(
                "[APP] Skipping installer update: unsupported asset type %r from %s",
                source_ext or "<none>",
                download_url,
            )
            return False

        runtime_dir = _get_runtime_install_dir()
        if source_ext == ".exe":
            target_name = _build_expected_installer_exe_name(latest_version, download_url) or source_name
        else:
            target_name = source_name or "OptiScaler_Installer_update.zip"
        download_path = runtime_dir / Path(target_name).name

        self.app_update_in_progress = True
        self._update_install_button_state()
        logging.info("[APP] Starting installer self-update to version %s from %s", latest_version, download_url)
        self._app_update_executor.submit(
            self._app_update_worker,
            latest_version,
            download_url,
            str(download_path),
            str(runtime_dir),
        )
        return True

    def _confirm_app_update(self, latest_version: str) -> bool:
        title = "업데이트 확인" if USE_KOREAN else "Update Available"
        detail = (
            f"최신 버전(v{latest_version})이 있습니다.\n지금 업데이트하시겠습니까?"
            if USE_KOREAN
            else f"A newer version (v{latest_version}) is available.\nDo you want to update now?"
        )
        return bool(messagebox.askyesno(title, detail))

    def _app_update_worker(self, latest_version: str, download_url: str, download_path: str, runtime_dir: str):
        try:
            payload_path = Path(download_path)
            target_dir = Path(runtime_dir)
            installer_services.download_to_file(download_url, str(payload_path), timeout=300)
            launch_path = self._prepare_app_update_payload(payload_path, target_dir, latest_version)
            self.root.after(
                0,
                lambda path=str(launch_path), version=latest_version: self._on_app_update_ready(path, version, None),
            )
        except Exception as exc:
            logging.exception("[APP] Installer self-update failed")
            self.root.after(
                0,
                lambda err=str(exc), version=latest_version: self._on_app_update_ready("", version, err),
            )

    def _prepare_app_update_payload(self, payload_path: Path, target_dir: Path, latest_version: str) -> Path:
        payload_ext = payload_path.suffix.lower()
        expected_exe_name = _build_expected_installer_exe_name(latest_version, str(payload_path))

        if payload_ext == ".exe":
            if expected_exe_name and payload_path.name.lower() != expected_exe_name.lower():
                renamed_target = payload_path.with_name(expected_exe_name)
                if renamed_target.exists():
                    try:
                        renamed_target.unlink()
                    except Exception:
                        logging.debug("Failed to remove existing installer payload before rename: %s", renamed_target, exc_info=True)
                payload_path.replace(renamed_target)
                payload_path = renamed_target
            logging.info("[APP] Downloaded updated installer executable to %s", payload_path)
            return payload_path

        if payload_ext != ".zip":
            raise ValueError(f"Unsupported installer update payload: {payload_path}")

        exe_members: list[str] = []
        with zipfile.ZipFile(payload_path, "r") as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                member_name = str(member.filename).replace("\\", "/").strip()
                if member_name.lower().endswith(".exe"):
                    exe_members.append(member_name)

        try:
            installer_services.extract_archive(str(payload_path), str(target_dir))
            launch_candidates: list[Path] = []
            for member_name in exe_members:
                candidate = _resolve_safe_child_path(target_dir, member_name)
                if candidate and candidate.exists():
                    launch_candidates.append(candidate)

            if expected_exe_name:
                for candidate in launch_candidates:
                    if candidate.name.lower() == expected_exe_name.lower():
                        logging.info("[APP] Prepared updated installer from zip: %s", candidate)
                        return candidate

                direct_expected = target_dir / expected_exe_name
                if direct_expected.exists():
                    logging.info("[APP] Prepared updated installer from zip: %s", direct_expected)
                    return direct_expected

            if len(launch_candidates) == 1:
                logging.info("[APP] Prepared updated installer from zip: %s", launch_candidates[0])
                return launch_candidates[0]

            if not launch_candidates:
                raise FileNotFoundError(f"No installer executable found in update zip: {payload_path}")

            raise RuntimeError(
                "Multiple installer executables were extracted from update zip and no unique target could be selected."
            )
        finally:
            try:
                payload_path.unlink(missing_ok=True)
            except Exception:
                logging.debug("Failed to remove installer update zip after extraction: %s", payload_path, exc_info=True)

    def _launch_updated_installer(self, launch_path: str, latest_version: str):
        target = Path(launch_path)
        if not target.exists():
            raise FileNotFoundError(f"Updated installer not found: {target}")

        logging.info("[APP] Launching updated installer v%s from %s", latest_version, target)
        subprocess.Popen(
            [str(target)],
            cwd=str(target.parent),
            **_subprocess_no_window_kwargs(),
        )

    def _on_app_update_ready(self, launch_path: str, latest_version: str, error_message: Optional[str]):
        self.app_update_in_progress = False
        self._update_install_button_state()

        if error_message:
            logging.error("[APP] Installer update to v%s failed: %s", latest_version, error_message)
            self._run_post_sheet_startup(True)
            return

        try:
            self._launch_updated_installer(launch_path, latest_version)
        except Exception as exc:
            logging.exception("[APP] Failed to launch updated installer")
            logging.error("[APP] Updated installer launch failed for v%s: %s", latest_version, exc)
            self._run_post_sheet_startup(True)
            return

        self.root.after(50, self._on_close)

    def check_app_update(self) -> bool:
        """Check for app update using latest_installer_dl from module_download_links."""
        if self.multi_gpu_blocked:
            return False
        try:
            latest_info = self._get_installer_update_entry()
            if latest_info:
                app_ver = _parse_version_tuple(APP_VERSION)
                sheet_ver = _parse_version_tuple(latest_info.get("version", ""))
                if sheet_ver and app_ver and app_ver < sheet_ver:
                    if not self._confirm_app_update(str(latest_info.get("version", "")).strip()):
                        logging.info("[APP] User declined installer update to v%s", latest_info.get("version", ""))
                        return False
                    return self._start_app_update(latest_info)
        except Exception as e:
            logging.warning("[APP] Version check failed: %s", e)
        return False

    def _show_startup_warning_popup(self, warning_text: str, on_close=None):
        text = str(warning_text or "").strip()
        if not text:
            if callable(on_close):
                on_close()
            return

        popup = ctk.CTkToplevel(self.root)
        popup.title("Notice")
        popup.transient(self.root)
        popup.grab_set()
        popup.resizable(False, False)
        popup.configure(fg_color=_SURFACE)
        popup.withdraw()

        container = ctk.CTkFrame(popup, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=22, pady=(18, 12))

        message_frame = ctk.CTkFrame(container, fg_color="transparent")
        message_frame.pack(fill="both", expand=True)
        message_frame.grid_columnconfigure(0, weight=1)
        message_frame.grid_rowconfigure(0, weight=1)

        # Parse [RED]... [END] and color only the text between
        pattern = re.compile(r"\[\s*RED\s*\](.*?)\[\s*END\s*\]", re.IGNORECASE | re.DOTALL)
        last = 0
        message_text = tk.Text(
            message_frame,
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
        message_text.configure(font=normal_font, padx=0, pady=0, spacing1=0, spacing2=0, spacing3=0)
        message_text.tag_configure("warning_red", foreground="#FF4D4F", font=red_font)
        message_text.grid(row=0, column=0, sticky="nsew")

        scrollbar = ctk.CTkScrollbar(message_frame, orientation="vertical", command=message_text.yview)
        message_text.configure(yscrollcommand=scrollbar.set)
        scrollbar_visible = False

        def _set_scrollbar_visible(visible: bool):
            nonlocal scrollbar_visible
            if visible and not scrollbar_visible:
                scrollbar.grid(row=0, column=1, sticky="ns", padx=(10, 0))
                scrollbar_visible = True
            elif not visible and scrollbar_visible:
                scrollbar.grid_remove()
                scrollbar_visible = False

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

        message_text.configure(state="disabled")

        button_row = ctk.CTkFrame(container, fg_color="transparent")
        button_row.pack(fill="x", pady=(10, 0))

        fade_in_step = 0.14
        fade_out_step = 0.18
        fade_interval_ms = 18
        fade_out_interval_ms = 16
        fade_supported = False
        fade_in_after_id = None
        closing_popup = False
        close_button: Optional[ctk.CTkButton] = None

        def _popup_exists() -> bool:
            try:
                return bool(popup.winfo_exists())
            except Exception:
                return False

        def _get_popup_alpha() -> float:
            try:
                return float(popup.attributes("-alpha"))
            except Exception:
                return 1.0

        def _finalize_close():
            try:
                popup.grab_release()
            except Exception:
                pass
            try:
                popup.destroy()
            except Exception:
                pass
            if callable(on_close):
                on_close()

        def _fade_in(opacity: float = 0.0):
            nonlocal fade_in_after_id
            if closing_popup or not _popup_exists():
                return
            next_opacity = min(1.0, opacity + fade_in_step)
            try:
                popup.attributes("-alpha", next_opacity)
            except Exception:
                fade_in_after_id = None
                logging.debug("Startup warning popup fade-in failed", exc_info=True)
                try:
                    popup.attributes("-alpha", 1.0)
                except Exception:
                    pass
                return
            if next_opacity < 1.0:
                fade_in_after_id = popup.after(fade_interval_ms, _fade_in, next_opacity)
            else:
                fade_in_after_id = None

        def _fade_out(opacity: float):
            if not _popup_exists():
                return
            next_opacity = max(0.0, opacity - fade_out_step)
            try:
                popup.attributes("-alpha", next_opacity)
            except Exception:
                logging.debug("Startup warning popup fade-out failed", exc_info=True)
                _finalize_close()
                return
            if next_opacity > 0.0:
                popup.after(fade_out_interval_ms, _fade_out, next_opacity)
            else:
                _finalize_close()

        def _close_popup():
            nonlocal closing_popup, fade_in_after_id
            if closing_popup:
                return
            closing_popup = True
            if close_button is not None:
                try:
                    close_button.configure(state="disabled")
                except Exception:
                    pass
            if fade_in_after_id is not None:
                try:
                    popup.after_cancel(fade_in_after_id)
                except Exception:
                    pass
                fade_in_after_id = None
            if fade_supported:
                _fade_out(_get_popup_alpha())
            else:
                _finalize_close()

        close_button = ctk.CTkButton(
            button_row,
            text="OK",
            width=100,
            height=34,
            corner_radius=8,
            fg_color=_ACCENT,
            hover_color=_ACCENT_HOVER,
            text_color="#000000",
            font=ctk.CTkFont(family=FONT_UI, size=12, weight="bold"),
            command=_close_popup,
        )
        close_button.pack()

        screen_w = max(1, int(self.root.winfo_screenwidth() or WINDOW_W))
        screen_h = max(1, int(self.root.winfo_screenheight() or WINDOW_H))
        avg_char_width = max(7, int(normal_font.measure("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz") / 52))
        zero_char_width = max(7, int(max(normal_font.measure("0"), red_font.measure("0"))))
        min_text_chars = 34
        max_text_chars = max(min_text_chars, min(110, max(min_text_chars, (screen_w - 140) // avg_char_width)))
        root_w = max(1, int(self.root.winfo_width() or WINDOW_W))
        desired_popup_w = min(max(360, root_w), max(360, screen_w - 40))
        target_text_px = max(220, desired_popup_w - 72)
        preferred_text_chars = max(
            min_text_chars,
            min(max_text_chars, int(math.ceil(target_text_px / max(1, zero_char_width)))),
        )
        max_popup_h = max(240, screen_h - 80)
        line_height_px = max(normal_font.metrics("linespace"), red_font.metrics("linespace")) + 2

        width_steps = list(range(preferred_text_chars, max_text_chars + 1, 4))
        if not width_steps:
            width_steps = [preferred_text_chars]
        if width_steps[-1] != max_text_chars:
            width_steps.append(max_text_chars)

        chosen_width = preferred_text_chars
        resolved_line_count = 1

        def _layout_warning_popup():
            nonlocal chosen_width, resolved_line_count

            _set_scrollbar_visible(False)
            for width_chars in width_steps:
                message_text.configure(width=width_chars, height=1)
                popup.update_idletasks()

                resolved_line_count = _estimate_wrapped_text_lines(
                    full_plain_text,
                    normal_font,
                    max(32, zero_char_width * width_chars),
                )
                message_text.configure(height=resolved_line_count)
                popup.update_idletasks()

                chosen_width = width_chars
                if popup.winfo_reqheight() <= max_popup_h:
                    break

            popup.update_idletasks()
            if popup.winfo_reqheight() > max_popup_h:
                chrome_height = max(0, popup.winfo_reqheight() - message_text.winfo_reqheight())
                max_text_height_px = max(96, max_popup_h - chrome_height)
                max_visible_lines = max(4, int(max_text_height_px / max(1, line_height_px)))
                message_text.configure(height=min(resolved_line_count, max_visible_lines))
                _set_scrollbar_visible(True)
                popup.update_idletasks()

        def _sync_warning_popup_text_height():
            try:
                popup.update_idletasks()
                actual_width_px = max(32, int(message_text.winfo_width() or (zero_char_width * chosen_width)))
                actual_line_count = _estimate_wrapped_text_lines(
                    full_plain_text,
                    normal_font,
                    actual_width_px,
                )
                target_lines = actual_line_count
                if scrollbar_visible:
                    chrome_height = max(0, popup.winfo_reqheight() - message_text.winfo_reqheight())
                    max_text_height_px = max(96, max_popup_h - chrome_height)
                    max_visible_lines = max(4, int(max_text_height_px / max(1, line_height_px)))
                    target_lines = min(actual_line_count, max_visible_lines)
                if int(message_text.cget("height")) != target_lines:
                    message_text.configure(height=target_lines)
                    popup.update_idletasks()
            except Exception:
                logging.debug("Failed to reflow startup warning popup text", exc_info=True)

        def _apply_warning_popup_geometry():
            try:
                popup.update_idletasks()
                _sync_warning_popup_text_height()

                popup_w = max(desired_popup_w, int(popup.winfo_reqwidth()))
                popup_h = max(1, int(popup.winfo_reqheight()))
                margin = 12

                if popup_w + (margin * 2) > screen_w:
                    popup_w = max(220, screen_w - (margin * 2))
                if popup_h + (margin * 2) > screen_h:
                    popup_h = max(140, screen_h - (margin * 2))

                root_x = self.root.winfo_x()
                root_y = self.root.winfo_y()
                root_w = self.root.winfo_width()
                root_h = self.root.winfo_height()
                x = root_x + (root_w // 2) - (popup_w // 2)
                y = root_y + (root_h // 2) - (popup_h // 2)
                min_x = margin if popup_w + (margin * 2) < screen_w else 0
                min_y = margin if popup_h + (margin * 2) < screen_h else 0
                max_x = max(min_x, screen_w - popup_w - margin)
                max_y = max(min_y, screen_h - popup_h - margin)
                x = max(min_x, min(x, max_x))
                y = max(min_y, min(y, max_y))
                logical_w = max(1, int(round(popup._reverse_window_scaling(popup_w))))
                logical_h = max(1, int(round(popup._reverse_window_scaling(popup_h))))
                popup.geometry(f"{logical_w}x{logical_h}+{x}+{y}")
            except Exception:
                logging.debug("Failed to size startup warning popup", exc_info=True)

        popup.protocol("WM_DELETE_WINDOW", _close_popup)
        _layout_warning_popup()
        try:
            popup.attributes("-alpha", 0.0)
            fade_supported = True
        except Exception:
            fade_supported = False
            logging.debug("Popup alpha fade is not supported for startup warning popup", exc_info=True)
        popup.deiconify()
        popup.lift()
        try:
            popup.focus_set()
        except Exception:
            pass
        _apply_warning_popup_geometry()
        popup.after(0, _apply_warning_popup_geometry)
        if fade_supported:
            fade_in_after_id = popup.after(45, _fade_in, 0.0)

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
        if self.multi_gpu_blocked:
            return
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

    def _show_supported_games_popup(self, on_close=None):
        if self.multi_gpu_blocked:
            if callable(on_close):
                on_close()
            return
        names = []
        seen = set()
        for idx, entry in enumerate(self.game_db.values()):
            if not self._is_game_supported_for_current_gpu(entry):
                continue
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
            if callable(on_close):
                on_close()
            return

        screen_w = max(1, int(self.root.winfo_screenwidth() or WINDOW_W))
        screen_h = max(1, int(self.root.winfo_screenheight() or WINDOW_H))
        root_w = max(1, int(self.root.winfo_width() or WINDOW_W))
        desired_popup_w = min(max(360, root_w), max(360, screen_w - 40))
        max_popup_h = max(240, screen_h - 80)

        popup = ctk.CTkToplevel(self.root)
        popup.title("Supported Game List")
        popup.transient(self.root)
        popup.grab_set()
        popup.resizable(False, False)
        popup.configure(fg_color=_SURFACE)
        popup.withdraw()

        container = ctk.CTkFrame(popup, width=0, height=0, fg_color="transparent")
        container.pack(fill="both", padx=18, pady=(16, 14))

        list_font = ctk.CTkFont(family=FONT_UI, size=12)
        metrics_font = tkfont.Font(family=FONT_UI, size=12)
        row_height = max(20, int(metrics_font.metrics("linespace")) + 6)
        desired_list_h = max(72, (len(names) * row_height) + 18)
        popup_chrome_h = 88
        max_list_h = max(120, max_popup_h - popup_chrome_h)
        use_scroll = desired_list_h > max_list_h
        list_width = max(344, min(420, desired_popup_w - 40))

        if use_scroll:
            list_frame = ctk.CTkScrollableFrame(
                container,
                width=list_width,
                height=max_list_h,
                corner_radius=8,
                fg_color="#2A303A",
                border_width=0,
            )
            list_frame.pack(fill="both", expand=True, pady=(0, 12))
        else:
            list_frame = ctk.CTkFrame(
                container,
                width=list_width,
                height=desired_list_h,
                corner_radius=8,
                fg_color="#2A303A",
                border_width=0,
            )
            list_frame.pack(fill="x", pady=(0, 12))
        list_frame.grid_columnconfigure(0, weight=1)

        for i, name in enumerate(names):
            ctk.CTkLabel(
                list_frame,
                text=f"- {name}",
                font=list_font,
                text_color="#E3EAF3",
                anchor="w",
                justify="left",
                height=16,
            ).grid(row=i, column=0, sticky="ew", padx=10, pady=0)

        fade_in_step = 0.14
        fade_out_step = 0.18
        fade_interval_ms = 18
        fade_out_interval_ms = 16
        fade_supported = False
        fade_in_after_id = None
        closing_popup = False
        close_button: Optional[ctk.CTkButton] = None

        def _popup_exists() -> bool:
            try:
                return bool(popup.winfo_exists())
            except Exception:
                return False

        def _get_popup_alpha() -> float:
            try:
                return float(popup.attributes("-alpha"))
            except Exception:
                return 1.0

        def _finalize_close():
            try:
                popup.grab_release()
            except Exception:
                pass
            try:
                popup.destroy()
            except Exception:
                pass
            if callable(on_close):
                on_close()

        def _fade_in(opacity: float = 0.0):
            nonlocal fade_in_after_id
            if closing_popup or not _popup_exists():
                return
            next_opacity = min(1.0, opacity + fade_in_step)
            try:
                popup.attributes("-alpha", next_opacity)
            except Exception:
                fade_in_after_id = None
                logging.debug("Supported-games popup fade-in failed", exc_info=True)
                try:
                    popup.attributes("-alpha", 1.0)
                except Exception:
                    pass
                return
            if next_opacity < 1.0:
                fade_in_after_id = popup.after(fade_interval_ms, _fade_in, next_opacity)
            else:
                fade_in_after_id = None

        def _fade_out(opacity: float):
            if not _popup_exists():
                return
            next_opacity = max(0.0, opacity - fade_out_step)
            try:
                popup.attributes("-alpha", next_opacity)
            except Exception:
                logging.debug("Supported-games popup fade-out failed", exc_info=True)
                _finalize_close()
                return
            if next_opacity > 0.0:
                popup.after(fade_out_interval_ms, _fade_out, next_opacity)
            else:
                _finalize_close()

        def _close_popup():
            nonlocal closing_popup, fade_in_after_id
            if closing_popup:
                return
            closing_popup = True
            if close_button is not None:
                try:
                    close_button.configure(state="disabled")
                except Exception:
                    pass
            if fade_in_after_id is not None:
                try:
                    popup.after_cancel(fade_in_after_id)
                except Exception:
                    pass
                fade_in_after_id = None
            if fade_supported:
                _fade_out(_get_popup_alpha())
            else:
                _finalize_close()

        close_button = ctk.CTkButton(
            container,
            text="OK",
            width=100,
            height=34,
            corner_radius=8,
            fg_color=_ACCENT,
            hover_color=_ACCENT_HOVER,
            text_color="#000000",
            font=ctk.CTkFont(family=FONT_UI, size=12, weight="bold"),
            command=_close_popup,
        )
        close_button.pack()

        popup.update_idletasks()
        if use_scroll:
            list_req_h = max(1, int(list_frame.winfo_reqheight() or max_list_h))
            chrome_h = max(72, int(popup.winfo_reqheight() or 0) - list_req_h)
            target_list_h = max(120, min(max_list_h, max_popup_h - chrome_h))
            list_frame.configure(height=target_list_h)
            popup.update_idletasks()
            current_popup_h = max(1, int(popup.winfo_reqheight() or 0))
            slack_h = max(0, max_popup_h - current_popup_h)
            if slack_h > 0:
                grown_list_h = min(desired_list_h, target_list_h + slack_h)
                if grown_list_h > target_list_h:
                    list_frame.configure(height=grown_list_h)
                    popup.update_idletasks()
                    target_list_h = grown_list_h
            overflow_h = max(0, int(popup.winfo_reqheight() or 0) - max_popup_h)
            if overflow_h > 0:
                list_frame.configure(height=max(120, target_list_h - overflow_h))
                popup.update_idletasks()

        def _apply_supported_games_popup_geometry(use_requested_size: bool = False):
            try:
                popup.update_idletasks()

                if use_requested_size:
                    popup_w = max(1, int(popup.winfo_reqwidth()))
                    popup_h = max(1, int(popup.winfo_reqheight()))
                else:
                    popup_w = max(1, int(popup.winfo_width() or popup.winfo_reqwidth()))
                    popup_h = max(1, int(popup.winfo_height() or popup.winfo_reqheight()))
                screen_w = max(1, int(self.root.winfo_screenwidth() or popup_w))
                screen_h = max(1, int(self.root.winfo_screenheight() or popup_h))
                margin = 12

                root_x = self.root.winfo_x()
                root_y = self.root.winfo_y()
                root_w = self.root.winfo_width()
                root_h = self.root.winfo_height()
                x = root_x + (root_w // 2) - (popup_w // 2)
                y = root_y + (root_h // 2) - (popup_h // 2)
                min_x = margin if popup_w + (margin * 2) < screen_w else 0
                min_y = margin if popup_h + (margin * 2) < screen_h else 0
                max_x = max(min_x, screen_w - popup_w - margin)
                max_y = max(min_y, screen_h - popup_h - margin)
                x = max(min_x, min(x, max_x))
                y = max(min_y, min(y, max_y))
                logical_w = max(1, int(round(popup._reverse_window_scaling(popup_w))))
                logical_h = max(1, int(round(popup._reverse_window_scaling(popup_h))))
                popup.geometry(f"{logical_w}x{logical_h}+{x}+{y}")
            except Exception:
                logging.debug("Failed to size supported-games popup", exc_info=True)

        popup.protocol("WM_DELETE_WINDOW", _close_popup)
        _apply_supported_games_popup_geometry(use_requested_size=True)
        try:
            popup.attributes("-alpha", 0.0)
            fade_supported = True
        except Exception:
            fade_supported = False
            logging.debug("Popup alpha fade is not supported for supported-games popup", exc_info=True)
        popup.deiconify()
        popup.lift()
        try:
            popup.focus_set()
        except Exception:
            pass
        popup.after(0, _apply_supported_games_popup_geometry)
        if fade_supported:
            fade_in_after_id = popup.after(45, _fade_in, 0.0)

    def _center_popup_on_root(self, popup: ctk.CTkToplevel, use_requested_size: bool = False):
        try:
            popup.update_idletasks()

            root_x = self.root.winfo_x()
            root_y = self.root.winfo_y()
            root_w = self.root.winfo_width()
            root_h = self.root.winfo_height()

            popup_w = popup.winfo_reqwidth() if use_requested_size else popup.winfo_width()
            popup_h = popup.winfo_reqheight() if use_requested_size else popup.winfo_height()

            screen_w = max(1, int(self.root.winfo_screenwidth() or popup_w))
            screen_h = max(1, int(self.root.winfo_screenheight() or popup_h))
            margin = 12
            x = root_x + (root_w // 2) - (popup_w // 2)
            y = root_y + (root_h // 2) - (popup_h // 2)
            min_x = margin if popup_w + (margin * 2) < screen_w else 0
            min_y = margin if popup_h + (margin * 2) < screen_h else 0
            max_x = max(min_x, screen_w - popup_w - margin)
            max_y = max(min_y, screen_h - popup_h - margin)
            x = max(min_x, min(x, max_x))
            y = max(min_y, min(y, max_y))
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
            text=f"GPU: {self.gpu_info}",
            font=ctk.CTkFont(family=FONT_UI, size=11),
            text_color="#C5CFDB",
            anchor="w",
        )
        self.gpu_lbl.grid(row=0, column=0, padx=(1, 0), sticky="w")

        # Badge-style status indicator
        self.status_badge = ctk.CTkLabel(
            sub_frame,
            text="  Game DB: Loading  ",
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
        self.scan_row = row

        sec_lbl = ctk.CTkLabel(
            row,
            text="1. Scan Game Folder",
            font=ctk.CTkFont(family=FONT_HEADING, size=12, weight="bold"),
            text_color="#F1F5F9",
        )
        sec_lbl.grid(row=0, column=0, padx=(20, 10), pady=12, sticky="w")

        self.btn_select_folder = ctk.CTkButton(
            row,
            text="Browse...",
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
        self.lbl_game_path.place(x=0, rely=0.5, anchor="w")
        self.root.after(0, self._align_supported_games_count_label)

    # -- Grid area (poster cards) -----------------------------------------

    def _build_grid_area(self):
        wrapper = ctk.CTkFrame(self.root, fg_color=_PANEL, corner_radius=0)
        wrapper.grid(row=2, column=0, sticky="nsew", padx=0, pady=0)
        wrapper.grid_rowconfigure(1, weight=1)
        wrapper.grid_columnconfigure(0, weight=1)

        header_row = ctk.CTkFrame(wrapper, fg_color="transparent", corner_radius=0)
        header_row.grid(row=0, column=0, padx=20, pady=(6, 6), sticky="ew")
        header_row.grid_columnconfigure(1, weight=1)

        sec_lbl = ctk.CTkLabel(
            header_row,
            text="2. Supported Games",
            font=ctk.CTkFont(family=FONT_HEADING, size=12, weight="bold"),
            text_color="#F1F5F9",
        )
        sec_lbl.grid(row=0, column=0, sticky="w")

        selected_header_row = ctk.CTkFrame(header_row, fg_color="transparent", corner_radius=0)
        selected_header_row.grid(row=0, column=1, padx=(8, 5), pady=(1, 0), sticky="e")

        label_text, game_name = self._get_selected_game_header_parts()

        self.lbl_selected_game_header_label = ctk.CTkLabel(
            selected_header_row,
            text=label_text,
            font=ctk.CTkFont(family=FONT_UI, size=12),
            text_color="#AEB9C8",
            anchor="e",
            justify="right",
        )
        self.lbl_selected_game_header_label.grid(row=0, column=0, sticky="e")

        self.lbl_selected_game_header = ctk.CTkLabel(
            selected_header_row,
            text=game_name,
            font=ctk.CTkFont(family=FONT_UI, size=12, weight="bold"),
            text_color=_ACCENT_SUCCESS,
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
            text="3. Install Information",
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
            text="Install",
            width=104,
            height=87,
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
        self._set_information_text("Select a game to view information.")
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

        if archive_name:
            version_text = f"Install Version: {archive_name}"
        elif version:
            version_text = f"Install Version: {version}"
        else:
            version_text = "Install Version: -"

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
    # Status badge
    # ------------------------------------------------------------------

    def _update_sheet_status(self):
        if self.multi_gpu_blocked:
            self.status_badge.configure(
                text="  GPU Config: Unsupported  " if not USE_KOREAN else "  GPU 구성: 미지원  ",
                text_color="#FF8A8A",
                fg_color="#4A2F34",
            )
            self.root.after(0, self._align_supported_games_count_label)
            return
        if self._gpu_selection_pending:
            self.status_badge.configure(
                text="  GPU Selection Required  " if not USE_KOREAN else "  GPU 선택 필요  ",
                text_color="#FFCB62",
                fg_color="#4B4330",
            )
            self.root.after(0, self._align_supported_games_count_label)
            return
        if self.sheet_loading:
            self.status_badge.configure(
                text="  Game DB: Loading  ",
                text_color="#FFCB62",
                fg_color="#4B4330",
            )
            self.root.after(0, self._align_supported_games_count_label)
            return
        if self.sheet_status:
            self.status_badge.configure(
                text="  Game DB: Online  ",
                text_color="#7EE1AA",
                fg_color="#244336",
            )
        else:
            self.status_badge.configure(
                text="  Game DB: Offline  ",
                text_color="#FF8A8A",
                fg_color="#4A2F34",
            )
        self.root.after(0, self._align_supported_games_count_label)

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
        cache_source = f"poster|v{POSTER_CACHE_VERSION}|{TARGET_POSTER_W}x{TARGET_POSTER_H}|{source}"
        return hashlib.sha256(cache_source.encode("utf-8")).hexdigest()

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

    def _queue_card_image_fetch(self, index: int, label: ctk.CTkLabel, title: str, game_name: str, url: str):
        self._cancel_delayed_image_retry(index)
        self._pending_image_jobs[index] = {
            "index": index,
            "label": label,
            "title": title,
            "game_name": game_name,
            "url": url,
            "generation": self._render_generation,
            "cache_key": self._poster_cache_key(title, url),
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

    def _load_poster_image_worker(self, title: str, game_name: str, url: str) -> tuple[Image.Image, bool, bool]:
        local_cover_path = self._find_local_cover_asset(game_name)
        if local_cover_path is not None:
            local_cache_key = f"local::v{POSTER_CACHE_VERSION}::{TARGET_POSTER_W}x{TARGET_POSTER_H}::{str(local_cover_path).lower()}"
            cached_local = self._image_cache_get(local_cache_key) if ENABLE_POSTER_CACHE else None
            if cached_local is not None:
                return cached_local, False, False
            try:
                with Image.open(local_cover_path) as local_img:
                    pil_img = _prepare_cover_image(local_img, TARGET_POSTER_W, TARGET_POSTER_H)
                if ENABLE_POSTER_CACHE:
                    self._image_cache_put(local_cache_key, pil_img)
                return pil_img, False, False
            except Exception as exc:
                logging.warning("Failed to load local cover image from %s: %s", local_cover_path, exc)

        cache_key = self._poster_cache_key(title, url)
        cached_image = self._image_cache_get(cache_key) if ENABLE_POSTER_CACHE else None
        if cached_image is not None:
            return cached_image, False, False

        if not url:
            fallback = self._default_poster_base.copy().convert("RGBA")
            return fallback, True, False

        try:
            with self._image_session.get(url, timeout=IMAGE_TIMEOUT_SECONDS, stream=True) as response:
                response.raise_for_status()
                data = b"".join(response.iter_content(chunk_size=65536))
            with Image.open(io.BytesIO(data)) as downloaded_img:
                pil_img = _prepare_cover_image(downloaded_img, TARGET_POSTER_W, TARGET_POSTER_H)
            if ENABLE_POSTER_CACHE:
                self._image_cache_put(cache_key, pil_img)
            return pil_img, False, False
        except Exception as exc:
            logging.warning("Failed to load cover image from %s: %s", url, exc)
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
            popup_msg = ""
            if USE_KOREAN:
                popup_msg = game.get("popup_kr", "").strip()
            else:
                popup_msg = game.get("popup_en", "").strip()
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
            resolved_name = installer_services.resolve_proxy_dll_name(target_path, preferred_dll, logger=logger)
            self.install_precheck_ok = True
            self.install_precheck_error = ""
            self.install_precheck_dll_name = resolved_name
        except Exception as exc:
            raw_error = str(exc)
            checked_prefix = "Checked: "
            if USE_KOREAN and raw_error.startswith("No available OptiScaler DLL names for installation. "):
                checked_names = raw_error.split(checked_prefix, 1)[1] if checked_prefix in raw_error else ""
                translated = "설치에 사용할 수 있는 OptiScaler DLL 이름이 없습니다."
                if checked_names:
                    translated += f" 확인한 이름: {checked_names}"
                raw_error = translated
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
            seen_paths = set()  # deduplicate by (sheet rule key, normalised_dir)
            match_index = {}
            for entry_key, entry in self.game_db.items():
                required_files = tuple(entry.get("match_files") or [entry_key])
                for token in required_files:
                    match_index.setdefault(token, []).append((entry_key, entry))

            for game_folder in game_folders:
                try:
                    folder_iter = os.walk(game_folder)
                except Exception as walk_err:
                    logging.debug("Cannot walk %s: %s", game_folder, walk_err)
                    continue
                for root_dir, _, files in folder_iter:
                    if not files:
                        continue

                    file_lookup = {}
                    for file in files:
                        key = file.lower()
                        if key not in file_lookup:
                            file_lookup[key] = file

                    candidate_entries = {}
                    for key in file_lookup:
                        for entry_key, entry in match_index.get(key, ()):
                            candidate_entries[entry_key] = entry

                    if not candidate_entries:
                        continue

                    normalized_root = os.path.normcase(root_dir)
                    for entry_key, entry in candidate_entries.items():
                        required_files = entry.get("match_files") or [entry_key]
                        if not all(token in file_lookup for token in required_files):
                            continue

                        dedup_key = (entry_key, normalized_root)
                        if dedup_key in seen_paths:
                            continue
                        seen_paths.add(dedup_key)

                        if not self._is_game_supported_for_current_gpu(entry):
                            continue

                        anchor_key = str(entry.get("match_anchor", "")).strip().lower()
                        matched_file = file_lookup.get(anchor_key)
                        if not matched_file:
                            matched_file = next(
                                (file_lookup[token] for token in required_files if token.endswith(".exe") and token in file_lookup),
                                "",
                            )
                        if not matched_file and required_files:
                            matched_file = file_lookup.get(required_files[0], required_files[0])

                        _kr_display = entry.get("game_name_kr", "") if USE_KOREAN else ""
                        _kr_info = entry.get("information_kr", "") if USE_KOREAN else ""
                        game = {
                            "path": root_dir,
                            "exe": matched_file,
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
                            "unreal5_rule": entry.get("unreal5_rule", ""),
                            "reframework_url": entry.get("reframework_url", ""),
                            "information": _kr_info or entry.get("information", ""),
                            "cover_url": entry.get("cover_url", ""),
                            "supported_gpu": entry.get("supported_gpu", ""),
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
            text=f"Supported Games: {count}",
            text_color="#F1F5F9",
        )
        self.root.after(0, self._align_supported_games_count_label)
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
            text=f"Supported Games: {len(self.found_exe_list)}",
            text_color="#F1F5F9",
        )
        self.root.after(0, self._align_supported_games_count_label)

    # ------------------------------------------------------------------
    # Install
    # ------------------------------------------------------------------

    def apply_optiscaler(self):
        if self.multi_gpu_blocked:
            return
        if self.install_in_progress:
            messagebox.showinfo("Installing", "Installation is already in progress. Please wait.")
            return


        if self.selected_game_index is None:
            messagebox.showwarning("Warning", "Please select a game card to install.")
            return

        if self.optiscaler_archive_downloading:
            messagebox.showinfo("Preparing Archive", "OptiScaler archive download is still in progress. Please wait.")
            return

        if self.install_precheck_running:
            return

        if not self.install_precheck_ok or not self.install_precheck_dll_name:
            detail = self.install_precheck_error or (
                "OptiScaler DLL compatibility check has not completed."
                if not USE_KOREAN
                else "OptiScaler DLL 호환성 확인이 아직 완료되지 않았습니다."
            )
            if USE_KOREAN:
                detail = f"{detail}\n\nReShade, Special K 등 다른 MOD 사용 중이면 확인 후 다시 설치해 주세요."
            else:
                detail = f"{detail}\n\nIf you are using other mods such as ReShade or Special K, please verify them and try the installation again."
            messagebox.showwarning("Warning", detail)
            return

        if not self.optiscaler_archive_ready or not getattr(self, "opti_source_archive", None):
            detail = self.optiscaler_archive_error or "OptiScaler archive is not ready yet."
            messagebox.showwarning("Warning", detail)
            return

        if self.selected_game_index < 0 or self.selected_game_index >= len(self.found_exe_list):
            messagebox.showwarning("Warning", "Please select a valid game item.")
            return

        # Block install if popup not confirmed
        if not getattr(self, "_game_popup_confirmed", True):
            messagebox.showwarning("Notice", "Please confirm the popup before installing.")
            return

        game_data = dict(self.found_exe_list[self.selected_game_index])
        source_archive = self.opti_source_archive
        resolved_dll_name = self.install_precheck_dll_name

        self.install_in_progress = True
        self.apply_btn.configure(state="disabled", text="Installing...", fg_color=_ACCENT_DISABLED)

        self._task_executor.submit(self._apply_optiscaler_worker, game_data, source_archive, resolved_dll_name)

    def _apply_optiscaler_worker(self, game_data, source_archive, resolved_dll_name):
        target_path = game_data["path"]
        game_name = str(game_data.get("game_name", "unknown")).strip() or "unknown"
        logger = get_prefixed_logger(game_name)
        try:
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
                installer_services.backup_existing_optiscaler_proxy_dlls(target_path, logger=logger)
                installer_services.remove_legacy_optiscaler_files(target_path, logger=logger)
                installer_services.install_from_source_folder(
                    actual_source,
                    target_path,
                    dll_name=final_dll_name,
                    exclude_patterns=exclude_patterns,
                    logger=logger,
                )
                logger.info(f"Extracted and installed files to {target_path}")

            module_key = str(game_data.get("module_dl", "")).strip().lower()

            unreal_link_entry = self.module_download_links.get("unreal5")
            unreal_url = ""
            if isinstance(unreal_link_entry, dict) and unreal_link_entry.get("url"):
                unreal_url = unreal_link_entry["url"]

            ini_path = os.path.join(target_path, "OptiScaler.ini")
            if not os.path.exists(ini_path):
                raise FileNotFoundError("OptiScaler.ini not found after installation")

            if game_data.get("reframework_url"):
                installer_services.install_reframework_dinput8_from_url(game_data["reframework_url"], target_path, logger=logger)
                logger.info(f"Installed REFramework dinput8.dll from {game_data['reframework_url']} to {target_path}")

            merged_ini_settings = dict(game_data.get("ini_settings", {}))
            if game_data.get("optipatcher"):
                opti_key = module_key or "optipatcher"
                opti_link_entry = self.module_download_links.get(opti_key) or self.module_download_links.get("optipatcher")
                opti_url = OPTIPATCHER_URL
                if isinstance(opti_link_entry, dict):
                    opti_url = opti_link_entry.get("url", OPTIPATCHER_URL)
                installer_services.install_optipatcher(target_path, url=opti_url, logger=logger)
                merged_ini_settings["LoadAsiPlugins"] = "True"
                logger.info(f"Installed OptiPatcher from {opti_url} to {target_path}")

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

            unreal5_rule = str(game_data.get("unreal5_rule", "") or "").strip()
            if gpu_service.matches_gpu_rule(unreal5_rule, self.gpu_info) and unreal_url:
                unreal_installed = installer_services.install_unreal5_from_url(unreal_url, target_path, logger=logger)
                if unreal_installed:
                    logger.info(f"Installed Unreal5 patch from {unreal_url} to {target_path}")
                else:
                    logger.info("Skipped Unreal5 patch because dxgi.dll is already present in %s", target_path)

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
            messagebox.showerror("Error", f"An error occurred during installation: {message}")


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
