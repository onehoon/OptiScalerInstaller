import os
import shutil
import tempfile
import tkinter as tk
import time
from concurrent.futures import ThreadPoolExecutor
from tkinter import messagebox
import logging
import sys
from pathlib import Path
import re
from typing import Callable, Optional
from installer import app_update
from installer.app.app_actions_controller import AppActionsController
from installer.app.app_shutdown_controller import AppShutdownController
from installer.app.archive_controller import ArchivePreparationController, ArchivePreparationState
from installer.app.card_render_controller import CardRenderController
from installer.app.card_ui import GameCardUiController
from installer.app.card_viewport import CardViewportController, CardViewportRuntime
from installer.app.controller_factory import (
    AppControllerFactoryConfig,
    AppControllers,
    bind_app_controllers,
    build_app_controllers,
)
from installer.app.game_db_controller import GameDbLoadController, GameDbLoadResult
from installer.app.gpu_flow_controller import GpuFlowController, GpuFlowState
from installer.app.install_entry import InstallEntryDecision, InstallEntryState
from installer.app.install_flow import InstallFlowController, create_install_flow_controller
from installer.app.install_selection_controller import (
    InstallSelectionController,
    InstallSelectionPrecheckOutcome,
    InstallSelectionUiState,
)
from installer.app.install_state import build_install_button_state_inputs, build_selected_game_snapshot
from installer.app.install_ui_state import InstallButtonStateInputs, compute_install_button_state
from installer.app.notice_controller import AppNoticeController
from installer.app.poster_queue import PosterQueueController
from installer.app.runtime_state import (
    ArchiveRuntimeState,
    CardUiRuntimeState,
    GpuRuntimeState,
    InstallRuntimeState,
    SheetRuntimeState,
    build_runtime_state_bundle,
    get_runtime_state_attr,
    set_runtime_state_attr,
)
from installer.app.scan_controller import ScanController
from installer.app.scan_entry_controller import ScanEntryController, ScanEntryState
from installer.app.scan_feedback import ScanFeedbackController
from installer.app.startup_flow import StartupFlowCallbacks, StartupFlowController
from installer.app.window_focus import has_startup_foreground_request, request_window_foreground
from installer.app.startup_window import (
    apply_startup_window_layout,
    apply_startup_window_workaround,
    build_startup_window_layout,
    get_ctk_scale,
)
from installer.app.theme import build_app_theme
from installer.app.ui_builder import build_main_ui
from installer.app.ui_controller_factory import (
    UiControllerFactoryConfig,
    bind_ui_controllers,
    build_ui_controllers,
    create_card_ui_controller,
    create_card_viewport_bundle,
)
from installer.app.ui_presenters import BottomPanelPresenter, HeaderStatusPresenter
from installer.common.poster_loader import PosterImageLoader, PosterLoaderConfig
from installer.config import ini_utils
from installer.i18n import (
    detect_ui_language,
    get_app_strings,
    is_korean,
    pick_module_message,
    pick_sheet_text,
)
from installer.install import (
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
APP_VERSION = "0.3.2"
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
IMAGE_TIMEOUT_SECONDS = 10
IMAGE_MAX_RETRIES = 3
IMAGE_MAX_WORKERS = 4
IMAGE_RETRY_DELAY_MS = int(os.environ.get("OPTISCALER_IMAGE_RETRY_DELAY_MS", "1500"))
DEFAULT_POSTER_SCALE = 1.5
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

# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

APP_THEME = build_app_theme(
    APP_STRINGS,
    supported_games_wiki_url=SUPPORTED_GAMES_WIKI_URL,
    grid_width=GRID_W,
    grid_height=GRID_H,
)
UI_CONTROLLER_FACTORY_CONFIG = UiControllerFactoryConfig(
    card_width=CARD_W,
    card_height=CARD_H,
    grid_cols=GRID_COLS,
    grid_rows_visible=GRID_ROWS_VISIBLE,
    card_h_spacing=CARD_H_SPACING,
    card_v_spacing=CARD_V_SPACING,
    card_background=APP_THEME.card_background,
    title_overlay_background=APP_THEME.card_title_overlay_background,
    title_overlay_text_color=APP_THEME.card_title_overlay_text,
    title_font_family=APP_THEME.font_ui,
    title_height=34,
)
APP_CONTROLLER_FACTORY_CONFIG = AppControllerFactoryConfig(
    assets_dir=ASSETS_DIR,
    create_prefixed_logger=get_prefixed_logger,
    default_sheet_gid=SHEET_GID,
    download_links_gid=DOWNLOAD_LINKS_SHEET_GID,
    gpu_notice_theme=APP_THEME.gpu_notice_theme,
    gpu_vendor_db_gids=GPU_VENDOR_DB_GIDS,
    max_supported_gpu_count=MAX_SUPPORTED_GPU_COUNT,
    message_popup_theme=APP_THEME.message_popup_theme,
    optipatcher_url=OPTIPATCHER_URL,
    root_width_fallback=WINDOW_W,
    root_height_fallback=WINDOW_H,
    rtss_theme=APP_THEME.rtss_notice_theme,
    sheet_id=SHEET_ID,
    supported_games_wiki_url=SUPPORTED_GAMES_WIKI_URL,
    use_korean=USE_KOREAN,
)

class OptiManagerApp:
    def __getattr__(self, name: str):
        return get_runtime_state_attr(self, name)

    def __setattr__(self, name: str, value) -> None:
        if set_runtime_state_attr(self, name, value):
            return

        object.__setattr__(self, name, value)

    def __init__(self, root: ctk.CTk):
        self.root = root
        self.lang = APP_LANG
        self.txt = APP_STRINGS
        self._configure_startup_window()
        self._initialize_runtime_state()
        self._initialize_controller_slots()
        self._initialize_infra()
        self._initialize_presenters()
        self._initialize_ui_and_controllers()
        self._start_background_services()
        self._bind_root_events()

    def _configure_startup_window(self) -> None:
        self.root.title(self.txt.main.window_title_template.format(version=APP_VERSION))
        startup_layout = build_startup_window_layout(
            self.root,
            window_width=WINDOW_W,
            window_height=WINDOW_H,
            window_min_width=WINDOW_MIN_W,
            window_min_height=WINDOW_MIN_H,
            card_width=CARD_W,
            card_height=CARD_H,
            default_poster_scale=DEFAULT_POSTER_SCALE,
        )
        self._startup_window_workaround_active = startup_layout.workaround_active
        self._startup_window_width = startup_layout.window_width
        self._startup_window_height = startup_layout.window_height
        apply_startup_window_layout(self.root, startup_layout, logger=logging.getLogger())
        self._poster_target_width = startup_layout.poster_target_width
        self._poster_target_height = startup_layout.poster_target_height
        self._poster_target_scale = startup_layout.poster_target_scale
        logging.info(
            "[APP] Poster target size resolved from widget scale %.2f -> %sx%s",
            self._poster_target_scale,
            self._poster_target_width,
            self._poster_target_height,
        )

    def _initialize_runtime_state(self) -> None:
        self.game_folder = ""
        runtime_state_bundle = build_runtime_state_bundle(
            checking_gpu_text=self.txt.main.checking_gpu,
            default_sheet_gid=SHEET_GID,
        )
        self.archive_state = runtime_state_bundle.archive_state
        self.gpu_state = runtime_state_bundle.gpu_state
        self.sheet_state = runtime_state_bundle.sheet_state
        self.install_state = runtime_state_bundle.install_state
        self.card_ui_state = runtime_state_bundle.card_ui_state
        self.optiscaler_cache_dir = OPTISCALER_CACHE_DIR
        self.optiscaler_cache_dir.mkdir(parents=True, exist_ok=True)
        self.fsr4_cache_dir = FSR4_CACHE_DIR
        self.fsr4_cache_dir.mkdir(parents=True, exist_ok=True)
        self.found_exe_list = []
        self.card_frames: list = []
        self.card_items: list = []
        self._ctk_images: list = []   # keep refs alive
        self._grid_cols_current = GRID_COLS
        self._resize_after_id = None
        self._resize_visual_after_id = None
        self._resize_in_progress = False
        self._last_reflow_width = 0
        self._base_root_width = None
        self._games_scrollregion_after_id = None
        self._games_viewport_after_id = None
        self._overflow_fit_after_id = None

    def _initialize_controller_slots(self) -> None:
        self._app_actions_controller = None
        self._app_notice_controller = None
        self._app_shutdown_controller = None
        self._archive_controller = None
        self._bottom_panel_presenter = None
        self._game_db_controller = None
        self._gpu_flow_controller = None
        self._header_status_presenter = None
        self._app_controllers = None
        self._install_flow_controller = None
        self._card_viewport_controller = None
        self._card_viewport_runtime = None
        self._card_ui_controller = None
        self._card_render_controller = None
        self._install_selection_controller = None
        self._scan_entry_controller = None
        self._scan_feedback_controller = None
        self._scan_controller = None

    def _initialize_infra(self) -> None:
        self._initialize_poster_infra()
        self._initialize_startup_update_infra()

    def _initialize_poster_infra(self) -> None:
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

    def _initialize_startup_update_infra(self) -> None:
        self._startup_flow = StartupFlowController(
            root=self.root,
            callbacks=StartupFlowCallbacks(
                start_archive_prepare=self._start_optiscaler_archive_prepare,
                start_auto_scan=self._start_auto_scan,
                show_rtss_notice=self._show_rtss_notice,
                show_startup_warning_popup=self._show_startup_warning_popup,
            ),
            is_multi_gpu_blocked=self._is_multi_gpu_block_active,
            get_startup_warning_text=lambda: pick_module_message(self.sheet_state.module_download_links, "warning", self.lang),
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

    def _initialize_presenters(self) -> None:
        self._header_status_presenter = HeaderStatusPresenter(
            root=self.root,
            status_text_color=APP_THEME.status_text_color,
            scan_status_text_color=APP_THEME.scan_status_text_color,
            status_indicator_loading_dim_color=APP_THEME.status_indicator_loading_dim_color,
            status_indicator_pulse_ms=APP_THEME.status_indicator_pulse_ms,
            supported_games_wiki_url=SUPPORTED_GAMES_WIKI_URL,
            link_active_color=APP_THEME.link_active_color,
            link_hover_color=APP_THEME.link_hover_color,
            logger=logging.getLogger(),
        )
        self._bottom_panel_presenter = BottomPanelPresenter(
            info_text_offset_px=INFO_TEXT_OFFSET_PX,
            version_name_formatter=_format_optiscaler_version_display_name,
            info_emphasis_color=APP_THEME.status_indicator_warning_color,
            logger=logging.getLogger(),
        )

    def _initialize_ui_and_controllers(self) -> None:
        self.setup_ui()
        ui_controllers = build_ui_controllers(self, UI_CONTROLLER_FACTORY_CONFIG)
        bind_ui_controllers(self, ui_controllers)
        self._sync_card_viewport_runtime_to_app()
        self._app_controllers = build_app_controllers(self, APP_CONTROLLER_FACTORY_CONFIG)
        bind_app_controllers(self, self._app_controllers)

    def _start_background_services(self) -> None:
        if self._gpu_flow_controller is not None:
            self._gpu_flow_controller.start_detection()

    def _bind_root_events(self) -> None:
        self.root.bind("<Configure>", self._on_root_resize)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if self._startup_window_workaround_active:
            self.root.after_idle(self._apply_startup_window_workaround)
            self.root.after(220, self._apply_startup_window_workaround)
        self.root.after(250, self._capture_startup_width)

    def _apply_startup_window_workaround(self):
        apply_startup_window_workaround(
            self.root,
            workaround_active=bool(getattr(self, "_startup_window_workaround_active", False)),
            window_width=int(getattr(self, "_startup_window_width", WINDOW_W) or WINDOW_W),
            window_height=int(getattr(self, "_startup_window_height", WINDOW_H) or WINDOW_H),
            logger=logging.getLogger(),
        )

    def _format_gpu_label_text(self, gpu_info: str) -> str:
        normalized_gpu = str(gpu_info or "").strip() or self.txt.main.unknown_gpu
        return self.txt.main.gpu_label_template.format(gpu=normalized_gpu)

    def _show_game_selection_popup(
        self,
        message_text: str,
        on_confirm: Optional[Callable[[], None]] = None,
    ) -> None:
        controller = self._app_notice_controller
        if controller is None:
            return
        controller.show_selection_popup(message_text, on_confirm=on_confirm)

    def _show_precheck_popup(
        self,
        message_text: str,
        on_close: Optional[Callable[[], None]] = None,
    ) -> None:
        controller = self._app_notice_controller
        if controller is None:
            return
        controller.show_precheck_popup(message_text, on_close=on_close)

    def _is_multi_gpu_block_active(self) -> bool:
        return self.gpu_state.gpu_count > MAX_SUPPORTED_GPU_COUNT

    def _is_game_supported_for_current_gpu(self, game_data: dict) -> bool:
        return gpu_service.matches_gpu_rule(str(game_data.get("supported_gpu", "") or ""), self.gpu_state.gpu_info)

    def _matches_fsr4_skip_rule(self, rule_text: str) -> bool:
        return gpu_service.matches_gpu_rule(FSR4_SKIP_GPU_RULE, rule_text)

    def _should_apply_fsr4_for_game(self, game_data: Optional[dict] = None) -> bool:
        if self._matches_fsr4_skip_rule(self.gpu_state.gpu_info):
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
        controller = self._app_notice_controller
        if controller is None:
            return
        controller.open_supported_games_wiki()

    def _set_scan_status_message(self, text: str = "", text_color: Optional[str] = None):
        presenter = self._header_status_presenter
        if presenter is None:
            return
        resolved_text_color = text_color or APP_THEME.scan_status_text_color
        presenter.set_scan_status_message(
            getattr(self, "lbl_scan_status", None),
            text,
            resolved_text_color,
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
        selection = build_selected_game_snapshot(
            self.found_exe_list,
            self.card_ui_state.selected_game_index,
            getattr(self, "lang", "en"),
        )
        return selection.header_text

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
        controller = self._app_notice_controller
        if controller is None:
            return
        controller.show_after_install_popup(
            msg,
            guide_url=str(game.get("guidepage_after_installation") or ""),
            guide_context=str(game.get("display", "<unknown>") or "<unknown>"),
        )

    def _cancel_after_handle(self, attr_name: str) -> None:
        after_id = getattr(self, attr_name, None)
        if after_id is None:
            return
        self.root.after_cancel(after_id)
        setattr(self, attr_name, None)

    def _call_optional_method(self, attr_name: str, method_name: str, *args, **kwargs) -> None:
        target = getattr(self, attr_name, None)
        if target is None:
            return
        getattr(target, method_name)(*args, **kwargs)

    def _sync_card_viewport_runtime_to_app(self) -> None:
        runtime = getattr(self, "_card_viewport_runtime", None)
        if runtime is None:
            return

        self._grid_cols_current = max(1, int(runtime.grid_cols_current or GRID_COLS))
        self._resize_after_id = runtime.resize_after_id
        self._resize_visual_after_id = runtime.resize_visual_after_id
        self._resize_in_progress = bool(runtime.resize_in_progress)
        self._last_reflow_width = max(0, int(runtime.last_reflow_width or 0))
        self._base_root_width = runtime.base_root_width
        self._games_scrollregion_after_id = runtime.games_scrollregion_after_id
        self._games_viewport_after_id = runtime.games_viewport_after_id
        self._overflow_fit_after_id = runtime.overflow_fit_after_id

    def _get_card_viewport_controller(self) -> Optional[CardViewportController]:
        return getattr(self, "_card_viewport_controller", None)

    def _get_card_ui_controller(self) -> Optional[GameCardUiController]:
        return getattr(self, "_card_ui_controller", None)

    def _get_install_flow_controller(self) -> Optional[InstallFlowController]:
        controller = getattr(self, "_install_flow_controller", None)
        if controller is None and hasattr(self, "root"):
            controller = create_install_flow_controller(
                self,
                optipatcher_url=OPTIPATCHER_URL,
                create_prefixed_logger=get_prefixed_logger,
            )
            self._install_flow_controller = controller
        return controller

    def _set_install_button_busy(self) -> None:
        if not hasattr(self, "apply_btn"):
            return
        self.apply_btn.configure(
            state="disabled",
            text=self.txt.main.installing_button,
            fg_color=APP_THEME.install_button_disabled_color,
            hover_color=APP_THEME.install_button_disabled_color,
            border_color=APP_THEME.install_button_border_disabled_color,
        )

    def _update_install_button_state(self):
        if not hasattr(self, "apply_btn"):
            return

        button_state = compute_install_button_state(self._build_install_button_state_inputs())
        can_install = bool(button_state.enabled)
        button_text = self.txt.main.installing_button if button_state.show_installing else self.txt.main.install_button

        self.apply_btn.configure(
            state="normal" if can_install else "disabled",
            text=button_text,
            fg_color=APP_THEME.install_button_color if can_install else APP_THEME.install_button_disabled_color,
            hover_color=APP_THEME.install_button_hover_color if can_install else APP_THEME.install_button_disabled_color,
            border_color=APP_THEME.install_button_border_color if can_install else APP_THEME.install_button_border_disabled_color,
        )

    def _build_install_button_state_inputs(self) -> InstallButtonStateInputs:
        gpu_state = self.gpu_state
        sheet_state = self.sheet_state
        install_state = self.install_state
        archive_state = self.archive_state
        selection = build_selected_game_snapshot(
            self.found_exe_list,
            self.card_ui_state.selected_game_index,
            getattr(self, "lang", "en"),
        )
        app_update_manager = getattr(self, "_app_update_manager", None)
        return build_install_button_state_inputs(
            selection=selection,
            multi_gpu_blocked=bool(gpu_state.multi_gpu_blocked),
            gpu_selection_pending=bool(gpu_state.gpu_selection_pending),
            sheet_ready=bool(sheet_state.status),
            sheet_loading=bool(sheet_state.loading),
            install_in_progress=bool(install_state.in_progress),
            app_update_in_progress=bool(getattr(app_update_manager, "in_progress", False)),
            install_precheck_running=bool(install_state.precheck_running),
            install_precheck_ok=bool(install_state.precheck_ok),
            optiscaler_archive_ready=bool(archive_state.optiscaler_ready),
            optiscaler_archive_downloading=bool(archive_state.optiscaler_downloading),
            fsr4_archive_ready=bool(archive_state.fsr4_ready),
            fsr4_archive_downloading=bool(archive_state.fsr4_downloading),
            game_popup_confirmed=bool(install_state.popup_confirmed),
            is_game_supported=self._is_game_supported_for_current_gpu,
            should_apply_fsr4=self._should_apply_fsr4_for_game,
        )

    # ------------------------------------------------------------------
    # Async DB load
    # ------------------------------------------------------------------

    def _on_close(self):
        controller = getattr(self, "_app_actions_controller", None)
        if controller is None:
            return
        controller.request_close(bool(self.install_state.in_progress))

    def _shutdown_app(self) -> None:
        controller = getattr(self, "_app_shutdown_controller", None)
        if controller is None:
            return
        controller.shutdown()

    def _start_game_db_load_async(self):
        if self._game_db_controller is None:
            return

        sheet_state = self.sheet_state
        game_db_gid = int(sheet_state.active_gid or SHEET_GID)
        game_db_vendor = str(sheet_state.active_vendor or "default")
        started = self._game_db_controller.start_load(game_db_gid, game_db_vendor)
        if not started:
            return
        logging.info(
            "[APP] Starting Game DB load for vendor=%s gpu=%s",
            game_db_vendor,
            self.gpu_state.gpu_info,
        )

    def _on_game_db_loaded(self, result: GameDbLoadResult) -> None:
        sheet_state = self.sheet_state
        gpu_state = self.gpu_state
        sheet_state.loading = False
        sheet_state.active_gid = int(result.game_db_gid)
        sheet_state.active_vendor = str(result.game_db_vendor or "default")
        sheet_state.game_db = result.game_db if result.ok else {}
        sheet_state.module_download_links = result.module_download_links if result.ok else {}

        sheet_state.status = result.ok
        if result.ok:
            logging.info(
                "[APP] Game DB loaded successfully: vendor=%s, games=%d, module_links=%d",
                sheet_state.active_vendor,
                len(sheet_state.game_db),
                len(sheet_state.module_download_links),
            )
        else:
            logging.error(
                "[APP] Failed to load Game DB for vendor=%s: %s",
                sheet_state.active_vendor,
                result.error,
            )
        if gpu_state.multi_gpu_blocked:
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
        sheet_state = self.sheet_state
        entry = sheet_state.module_download_links.get("optiscaler", {})
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
            logging.info("[APP] Skipping FSR4 preparation for GPU: %s", self.gpu_state.gpu_info)
        entry = self.sheet_state.module_download_links.get("fsr4int8", {})
        state = self._archive_controller.prepare_fsr4(
            entry,
            self.fsr4_cache_dir,
            enabled=enabled,
        )
        self._apply_fsr4_archive_state(state)
        self._update_install_button_state()

    def check_app_update(self) -> bool:
        controller = getattr(self, "_app_actions_controller", None)
        if controller is None:
            return False
        return controller.check_app_update(
            self.sheet_state.module_download_links,
            blocked=bool(self.gpu_state.multi_gpu_blocked),
        )

    def _show_rtss_notice(self) -> None:
        controller = getattr(self, "_app_actions_controller", None)
        if controller is None:
            return
        controller.show_rtss_notice(
            tuple(self.found_exe_list),
            self.card_ui_state.selected_game_index,
            getattr(self, "lang", "en"),
            self.sheet_state.module_download_links,
        )

    def _show_startup_warning_popup(
        self,
        warning_text: str,
        on_close: Optional[Callable[[], None]] = None,
    ) -> None:
        controller = self._app_notice_controller
        if controller is None:
            return
        controller.show_startup_warning_popup(warning_text, on_close=on_close)

    def _is_scan_in_progress(self) -> bool:
        controller = getattr(self, "_scan_controller", None)
        return bool(controller and controller.is_scan_in_progress)

    def _apply_gpu_flow_state(self, state: GpuFlowState) -> None:
        gpu_state = self.gpu_state
        sheet_state = self.sheet_state
        gpu_state.gpu_context = state.gpu_context
        gpu_state.gpu_names = list(state.gpu_names or ())
        gpu_state.gpu_count = max(0, int(state.gpu_count or 0))
        gpu_state.is_multi_gpu = bool(state.is_multi_gpu)
        gpu_state.multi_gpu_blocked = bool(state.multi_gpu_blocked)
        gpu_state.gpu_selection_pending = bool(state.gpu_selection_pending)
        gpu_state.selected_adapter = state.selected_adapter

        if state.game_db_vendor is not None:
            sheet_state.active_vendor = str(state.game_db_vendor or "default")
        if state.game_db_gid is not None:
            sheet_state.active_gid = int(state.game_db_gid or SHEET_GID)

        gpu_state.gpu_info = str(state.gpu_info or self.txt.main.unknown_gpu).strip() or self.txt.main.unknown_gpu
        if hasattr(self, "gpu_lbl") and self.gpu_lbl:
            self.gpu_lbl.configure(text=self._format_gpu_label_text(gpu_state.gpu_info))

    def _handle_unsupported_gpu_block(self, scan_status_message: str, info_text: str) -> None:
        sheet_state = self.sheet_state
        install_state = self.install_state
        card_ui_state = self.card_ui_state
        self._startup_flow.mark_post_sheet_startup_done()
        sheet_state.loading = False
        sheet_state.status = False
        sheet_state.game_db = {}
        sheet_state.module_download_links = {}
        self.found_exe_list = []
        card_ui_state.selected_game_index = None
        install_state.popup_confirmed = False
        install_state.precheck_running = False
        install_state.precheck_ok = False
        install_state.precheck_error = ""
        install_state.precheck_dll_name = ""
        self._apply_install_selection_state(
            InstallSelectionUiState(
                popup_confirmed=False,
                precheck_running=False,
                precheck_ok=False,
            )
        )

        if hasattr(self, "btn_select_folder") and self.btn_select_folder:
            self.btn_select_folder.configure(state="disabled")
        self._set_scan_status_message(scan_status_message, "#FF8A8A")
        self._clear_cards()
        if hasattr(self, "info_text") and self.info_text:
            self._set_information_text(info_text)
        self._update_selected_game_header()
        self._update_sheet_status()
        self._update_install_button_state()

    def _apply_optiscaler_archive_state(self, state: ArchivePreparationState) -> None:
        archive_state = self.archive_state
        archive_state.optiscaler_filename = str(state.filename or "")
        archive_state.optiscaler_ready = bool(state.ready)
        archive_state.optiscaler_downloading = bool(state.downloading)
        archive_state.optiscaler_error = str(state.error_message or "")
        archive_state.opti_source_archive = str(state.archive_path or "")

    def _apply_fsr4_archive_state(self, state: ArchivePreparationState) -> None:
        archive_state = self.archive_state
        archive_state.fsr4_filename = str(state.filename or "")
        archive_state.fsr4_ready = bool(state.ready)
        archive_state.fsr4_downloading = bool(state.downloading)
        archive_state.fsr4_error = str(state.error_message or "")
        archive_state.fsr4_source_archive = str(state.archive_path or "")

    def _on_optiscaler_archive_state_changed(self, state: ArchivePreparationState) -> None:
        self._apply_optiscaler_archive_state(state)
        self._start_fsr4_archive_prepare()
        self._update_install_button_state()

    def _on_fsr4_archive_state_changed(self, state: ArchivePreparationState) -> None:
        self._apply_fsr4_archive_state(state)
        self._update_install_button_state()

    def _create_card_viewport_controller(self) -> None:
        runtime, controller = create_card_viewport_bundle(self, UI_CONTROLLER_FACTORY_CONFIG)
        self._card_viewport_runtime = runtime
        self._card_viewport_controller = controller
        self._sync_card_viewport_runtime_to_app()

    def _create_card_ui_controller(self) -> None:
        if getattr(self, "_card_ui_controller", None) is not None:
            return
        self._card_ui_controller = create_card_ui_controller(self, UI_CONTROLLER_FACTORY_CONFIG)

    def _pump_poster_queue(self) -> None:
        self._poster_queue.pump()

    def _start_auto_scan(self):
        """Kick off a silent auto-scan of known Steam/game directories."""
        if self.gpu_state.multi_gpu_blocked:
            return
        if self._scan_controller is None:
            return
        self._scan_controller.start_auto_scan()

    def _set_game_folder(self, folder_path: str) -> None:
        self.game_folder = str(folder_path or "")

    def _start_manual_scan_from_folder(self, folder_path: str) -> bool:
        if self._scan_controller is None:
            return False
        return self._scan_controller.start_manual_scan(folder_path)

    # ------------------------------------------------------------------
    # UI builder
    # ------------------------------------------------------------------

    def setup_ui(self):
        build_main_ui(self, APP_THEME.main_ui_theme)

    def _refresh_optiscaler_archive_info_ui(self):
        presenter = self._bottom_panel_presenter
        if presenter is None:
            return
        presenter.refresh_optiscaler_archive_info_ui(
            getattr(self, "lbl_optiscaler_version_line", None),
            sheet_loading=bool(self.sheet_state.loading),
            module_download_links=self.sheet_state.module_download_links,
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
        gpu_state = self.gpu_state
        sheet_state = self.sheet_state
        presenter.update_sheet_status(
            label_widget=getattr(self, "status_badge_label", None),
            dot_widget=getattr(self, "status_badge_dot", None),
            multi_gpu_blocked=gpu_state.multi_gpu_blocked,
            gpu_selection_pending=gpu_state.gpu_selection_pending,
            sheet_loading=sheet_state.loading,
            sheet_status=sheet_state.status,
            status_gpu_config_text=self.txt.main.status_gpu_config,
            status_gpu_select_text=self.txt.main.status_gpu_select,
            status_game_db_text=self.txt.main.status_game_db,
            indicator_offline=APP_THEME.status_indicator_offline_color,
            indicator_warning=APP_THEME.status_indicator_warning_color,
            indicator_loading=APP_THEME.status_indicator_loading_color,
            indicator_online=APP_THEME.status_indicator_online_color,
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
        self.card_ui_state.selected_game_index = None
        self._apply_install_selection_state(
            InstallSelectionUiState(
                popup_confirmed=False,
                precheck_running=False,
                precheck_ok=False,
            )
        )
        self._set_information_text("")

    def _apply_selected_game_index(self, index: int) -> None:
        self.card_ui_state.selected_game_index = int(index)
        self._update_selected_game_header()
        controller = self._get_card_ui_controller()
        if controller is not None:
            controller.refresh_all_card_visuals()

    def _apply_install_selection_state(self, state: InstallSelectionUiState) -> None:
        install_state = self.install_state
        install_state.popup_confirmed = bool(state.popup_confirmed)
        install_state.precheck_running = bool(state.precheck_running)
        install_state.precheck_ok = bool(state.precheck_ok)
        install_state.precheck_error = str(state.precheck_error or "")
        install_state.precheck_dll_name = str(state.precheck_dll_name or "")

    def _clear_rendered_cards(self) -> None:
        self._poster_queue.begin_new_render()
        for frame in self.card_frames:
            frame.destroy()
        self.card_frames.clear()
        self.card_items.clear()
        self._ctk_images.clear()  # Release stale PhotoImage refs to prevent accumulation.
        self.card_ui_state.hovered_card_index = None

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

    def _hide_empty_label(self) -> None:
        if hasattr(self, "empty_label") and self.empty_label.winfo_exists():
            self.empty_label.grid_remove()

    def _append_found_game(self, game: dict) -> int:
        index = len(self.found_exe_list)
        self.found_exe_list.append(game)
        return index

    def _create_and_place_card(self, index: int, game: dict, placement) -> None:
        card = self._make_card(index, game)
        card.grid(
            row=placement.row,
            column=placement.column,
            padx=(CARD_H_SPACING // 2, CARD_H_SPACING // 2),
            pady=(CARD_V_SPACING // 2, CARD_V_SPACING // 2),
            sticky="nsew",
        )
        self.card_frames.append(card)

    def _restore_rendered_selection(self, index: int, game: dict) -> None:
        self.card_ui_state.selected_game_index = int(index)
        controller = self._get_card_ui_controller()
        if controller is not None:
            controller.refresh_all_card_visuals()
        self._set_information_text(game.get("information", ""))

    def _get_effective_widget_scale(self) -> float:
        return get_ctk_scale(self.root, 1.0)

    def _get_forced_card_area_width(self) -> int:
        controller = self._get_card_viewport_controller()
        if controller is None:
            return max(1, int(self.root.winfo_width() or 0))
        return controller._get_forced_card_area_width()

    def _apply_forced_games_canvas_width(self) -> int:
        return self._get_forced_card_area_width()

    def _get_dynamic_column_count(self) -> int:
        controller = self._get_card_viewport_controller()
        if controller is None:
            return max(1, int(getattr(self, "_grid_cols_current", GRID_COLS) or GRID_COLS))
        return controller._get_dynamic_column_count()

    def _max_safe_columns_for_width(self, usable_w: int) -> int:
        controller = self._get_card_viewport_controller()
        if controller is None:
            slot_width = max(1, CARD_W + CARD_H_SPACING)
            return max(1, max(1, int(usable_w) - 6) // slot_width)
        return controller._max_safe_columns_for_width(usable_w)

    def _get_card_slot_width(self) -> int:
        controller = self._get_card_viewport_controller()
        if controller is None:
            return max(1, CARD_W + CARD_H_SPACING)
        return controller._get_card_slot_width()

    def _capture_startup_width(self):
        controller = self._get_card_viewport_controller()
        if controller is None:
            return
        return controller.capture_startup_width()

    def _get_games_container_width(self) -> int:
        controller = self._get_card_viewport_controller()
        if controller is None:
            return max(1, int(self.root.winfo_width() or 0))
        return controller._get_games_container_width()

    def _schedule_reflow_for_resize(self):
        controller = self._get_card_viewport_controller()
        if controller is None:
            return
        return controller._schedule_reflow_for_resize()

    def _finish_resize_reflow(self):
        controller = self._get_card_viewport_controller()
        if controller is None:
            return
        return controller._finish_resize_reflow()

    def _end_resize_visual_suppression(self):
        controller = self._get_card_viewport_controller()
        if controller is None:
            return
        return controller._end_resize_visual_suppression()

    def _on_root_resize(self, _event=None):
        controller = self._get_card_viewport_controller()
        if controller is None:
            return
        return controller.on_root_resize(_event)

    def _configure_card_columns(self, cols: int):
        controller = self._get_card_viewport_controller()
        if controller is None:
            normalized_cols = max(1, int(cols))
            max_cols = max(int(getattr(self, "_grid_cols_current", GRID_COLS)), normalized_cols)
            for col in range(max_cols):
                self.games_scroll.grid_columnconfigure(col, weight=0, minsize=0)
            for col in range(normalized_cols):
                self.games_scroll.grid_columnconfigure(col, weight=0, minsize=CARD_W)
            self._grid_cols_current = normalized_cols
            return
        return controller._configure_card_columns(cols)

    def _layout_existing_cards(self, cols: int):
        controller = self._get_card_viewport_controller()
        if controller is None:
            return
        return controller._layout_existing_cards(cols)

    def _cards_overflow_visible_width(self) -> bool:
        controller = self._get_card_viewport_controller()
        if controller is None:
            return False
        return controller._cards_overflow_visible_width()

    def _schedule_games_scrollregion_refresh(self):
        controller = self._get_card_viewport_controller()
        if controller is None:
            return
        return controller._schedule_games_scrollregion_refresh()

    def _refresh_games_scrollregion(self):
        controller = self._get_card_viewport_controller()
        if controller is None:
            return
        return controller._refresh_games_scrollregion()

    def _schedule_overflow_fit_check(self):
        controller = self._get_card_viewport_controller()
        if controller is None:
            return
        return controller._schedule_overflow_fit_check()

    def _run_overflow_fit_check(self):
        controller = self._get_card_viewport_controller()
        if controller is None:
            return
        return controller._run_overflow_fit_check()

    def _fit_cards_to_visible_width(self, preferred_cols: int | None = None):
        controller = self._get_card_viewport_controller()
        if controller is None:
            return
        return controller.fit_cards_to_visible_width(preferred_cols)

    def _on_games_area_resize(self, _event=None):
        controller = self._get_card_viewport_controller()
        if controller is None:
            return
        return controller.on_games_area_resize(_event)

    def _schedule_games_viewport_update(self, delay_ms: int = 30):
        controller = self._get_card_viewport_controller()
        if controller is None:
            return
        return controller._schedule_games_viewport_update(delay_ms)

    def _run_games_viewport_update(self):
        controller = self._get_card_viewport_controller()
        if controller is None:
            return
        return controller._run_games_viewport_update()

    def _on_games_scrollbar_command(self, *args):
        controller = self._get_card_viewport_controller()
        if controller is None:
            return
        return controller.on_games_scrollbar_command(*args)

    def _on_games_scroll(self, _event=None):
        controller = self._get_card_viewport_controller()
        if controller is None:
            return
        return controller.on_games_scroll(_event)

    def _rerender_cards_for_resize(self):
        controller = self._get_card_viewport_controller()
        if controller is None:
            return
        return controller._rerender_cards_for_resize()

    def _render_cards(self, keep_selection=False):
        controller = self._get_card_ui_controller()
        if controller is None:
            return
        return controller.render_cards(keep_selection=bool(keep_selection))

    def _make_card(self, index: int, game: dict) -> ctk.CTkFrame:
        controller = self._get_card_ui_controller()
        if controller is None:
            raise RuntimeError("Game card UI controller is not available")
        return controller.make_card(index, game)

    def _visible_game_indices(self) -> set:
        controller = self._get_card_ui_controller()
        if controller is None:
            return set()
        return controller.visible_game_indices()

    def _apply_loaded_poster(self, index: int, label: ctk.CTkLabel, pil_img: Image.Image):
        controller = self._get_card_ui_controller()
        if controller is None:
            return
        return controller.apply_loaded_poster(index, label, pil_img)

    def _set_selected_game(self, index: int):
        controller = self._install_selection_controller
        if controller is None:
            return
        controller.select_game(index, tuple(self.found_exe_list))

    def _run_install_precheck(self, game_data: dict) -> InstallSelectionPrecheckOutcome:
        controller = self._get_install_flow_controller()
        if controller is None:
            return InstallSelectionPrecheckOutcome(
                ok=False,
                error="Install flow controller is not available",
            )
        return controller.run_install_precheck(game_data)

    # ------------------------------------------------------------------
    # File dialogs
    # ------------------------------------------------------------------

    def _build_scan_entry_state(self) -> ScanEntryState:
        gpu_state = self.gpu_state
        sheet_state = self.sheet_state
        return ScanEntryState(
            multi_gpu_blocked=bool(gpu_state.multi_gpu_blocked),
            sheet_loading=bool(sheet_state.loading),
            sheet_ready=bool(sheet_state.status),
        )

    def select_game_folder(self):
        controller = getattr(self, "_scan_entry_controller", None)
        if controller is None:
            return
        controller.select_game_folder(self._build_scan_entry_state())

    def _add_game_card_incremental(self, game: dict):
        """Append one game to the list and immediately render + queue its cover download."""
        controller = self._card_render_controller
        if controller is None:
            return
        cols = max(1, self._grid_cols_current)
        controller.add_game_card(
            game,
            cols=cols,
            target_cols=self._max_safe_columns_for_width(self._get_forced_card_area_width()),
        )

    # ------------------------------------------------------------------
    # Install
    # ------------------------------------------------------------------

    def _build_install_entry_state(self) -> InstallEntryState:
        controller = self._get_install_flow_controller()
        if controller is None:
            raise RuntimeError("Install flow controller is not available")
        return controller.build_install_entry_state()

    def _show_install_entry_rejection(self, decision: InstallEntryDecision) -> None:
        controller = self._get_install_flow_controller()
        if controller is None:
            return
        return controller.show_install_entry_rejection(decision)

    def apply_optiscaler(self):
        controller = self._get_install_flow_controller()
        if controller is None:
            return
        return controller.apply_selected_install()

    def _apply_optiscaler_worker(self, game_data, source_archive, resolved_dll_name, fsr4_source_archive, fsr4_required):
        controller = self._get_install_flow_controller()
        if controller is None:
            return
        return controller.run_install_worker(
            game_data,
            source_archive,
            resolved_dll_name,
            fsr4_source_archive,
            fsr4_required,
        )

    def _on_install_finished(self, success, message, installed_game=None):
        controller = self._get_install_flow_controller()
        if controller is None:
            return
        return controller.on_install_finished(success, message, installed_game)


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

    request_foreground = has_startup_foreground_request(sys.argv[1:])
    root = ctk.CTk()
    app = OptiManagerApp(root)
    if request_foreground:
        request_window_foreground(root, logger=logging.getLogger("APP"))
    root.mainloop()
