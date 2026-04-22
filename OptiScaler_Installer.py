import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
import logging
import sys
import importlib
from pathlib import Path
import re
from typing import Callable, Optional
from installer import app_update
from installer.app.app_actions_controller import AppActionsController
from installer.app.app_shutdown_controller import AppShutdownController
from installer.app.archive_controller import ArchivePreparationController, ArchivePreparationState
from installer.app.card_render_controller import CardRenderController
from installer.app.card_ui import GameCardUiCallbacks, GameCardUiController
from installer.app.card_viewport import CardViewportCallbacks, CardViewportController, CardViewportRuntime
from installer.app.controller_factory import (
    AppControllerFactoryConfig,
    AppControllers,
    bind_app_controllers,
    build_app_controllers,
)
from installer.app.game_db_controller import GameDbLoadController, GameDbLoadResult
from installer.app.gpu_flow_controller import GpuFlowController, GpuFlowState
from installer.app.install_flow import InstallFlowController
from installer.app.install_selection_controller import (
    InstallSelectionController,
    InstallSelectionPrecheckOutcome,
    InstallSelectionUiState,
)
from installer.app.game_support_policy import parse_support_flag
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
from installer.app.startup_runtime import (
    StartupRuntimeCallbacks,
    StartupRuntimeCoordinatorDeps,
    create_startup_runtime_coordinator,
)
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
    UiControllerFactoryDeps,
    bind_ui_controllers,
    build_ui_controllers,
)
from installer.app.ui_shell import AppUiShell, create_ui_shell
from installer.app.ui_presenters import BottomPanelPresenter, HeaderStatusPresenter
from installer.common.poster_loader import PosterImageLoader, PosterLoaderConfig
from installer.i18n import (
    build_install_information_text,
    detect_ui_language,
    get_app_strings,
    is_korean,
    pick_module_message,
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

def _load_generated_build_config() -> dict:
    try:
        module = importlib.import_module("installer._generated_build_config")
        config = getattr(module, "BUILD_CONFIG", {})
        if isinstance(config, dict):
            return dict(config)
    except Exception:
        pass
    return {}


_GENERATED_BUILD_CONFIG = _load_generated_build_config()


def _load_dev_env_file() -> None:
    if getattr(sys, "frozen", False):
        return
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=True)


def _get_runtime_config_value(name: str, default: str = "") -> str:
    if getattr(sys, "frozen", False) and name in _GENERATED_BUILD_CONFIG:
        value = _GENERATED_BUILD_CONFIG.get(name)
        if value is None:
            return default
        return str(value)

    value = os.environ.get(name)
    if value is None:
        return default
    return str(value)


# Application Version
APP_VERSION = "0.4.0"
# Install flow supports up to two detected GPUs. Dual-GPU requires explicit user selection.
MAX_SUPPORTED_GPU_COUNT = 2

# Configure logging deterministically below (avoid calling basicConfig early)

# Source runs may load .env, but frozen builds rely on build-time generated config.
_load_dev_env_file()


def _get_int_env(name: str, default: int = 0) -> int:
    raw = _get_runtime_config_value(name, "")
    if not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        logging.warning("[APP] Invalid integer env %s=%r, using %s", name, raw, default)
        return default


def _get_bool_env(name: str, default: bool = False) -> bool:
    raw = _get_runtime_config_value(name, "")
    text = str(raw).strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    logging.warning("[APP] Invalid boolean env %s=%r, using %s", name, raw, default)
    return default


# Allow overriding these values via build-time config for frozen builds
# and via environment variables/.env during source development.
SUPPORTED_GAMES_WIKI_URL = _get_runtime_config_value("SUPPORTED_GAMES_WIKI_URL", "").strip()
OPTISCALER_GPU_BUNDLE_URL = _get_runtime_config_value("OPTISCALER_GPU_BUNDLE_URL", "").strip()
OPTISCALER_GPU_BUNDLE_DEBUG = _get_bool_env("OPTISCALER_GPU_BUNDLE_DEBUG", False)
OPTISCALER_GAME_MASTER_URL = _get_runtime_config_value("OPTISCALER_GAME_MASTER_URL", "").strip()
OPTISCALER_RESOURCE_MASTER_URL = _get_runtime_config_value("OPTISCALER_RESOURCE_MASTER_URL", "").strip()
OPTISCALER_MESSAGE_CENTER_URL = _get_runtime_config_value("OPTISCALER_MESSAGE_CENTER_URL", "").strip()
OPTISCALER_MESSAGE_BINDING_URL = _get_runtime_config_value("OPTISCALER_MESSAGE_BINDING_URL", "").strip()
OPTISCALER_GAME_INI_PROFILE_URL = _get_runtime_config_value("OPTISCALER_GAME_INI_PROFILE_URL", "").strip()
OPTISCALER_GAME_UNREAL_INI_PROFILE_URL = _get_runtime_config_value("OPTISCALER_GAME_UNREAL_INI_PROFILE_URL", "").strip()
OPTISCALER_ENGINE_INI_PROFILE_URL = _get_runtime_config_value("OPTISCALER_ENGINE_INI_PROFILE_URL", "").strip()
OPTISCALER_GAME_XML_PROFILE_URL = _get_runtime_config_value("OPTISCALER_GAME_XML_PROFILE_URL", "").strip()
OPTISCALER_REGISTRY_PROFILE_URL = _get_runtime_config_value("OPTISCALER_REGISTRY_PROFILE_URL", "").strip()
OPTISCALER_GAME_JSON_PROFILE_URL = _get_runtime_config_value("OPTISCALER_GAME_JSON_PROFILE_URL", "").strip()


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
            candidates.append(Path(__file__).resolve().parent / "logs")
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

    # Suppress urllib3 internal retry/connection noise; app-level logs cover what matters
    logging.getLogger("urllib3").setLevel(logging.WARNING)

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
GRID_COLS = 5
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
LOCAL_APPDATA_DIR = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
APP_CACHE_DIR = LOCAL_APPDATA_DIR / "OptiScalerInstaller"
OPTISCALER_CACHE_DIR = APP_CACHE_DIR / "cache" / "optiscaler"
FSR4_CACHE_DIR = APP_CACHE_DIR / "cache" / "fsr4"
OPTIPATCHER_CACHE_DIR = APP_CACHE_DIR / "cache" / "optipatcher"
SPECIALK_CACHE_DIR = APP_CACHE_DIR / "cache" / "specialk"
UAL_CACHE_DIR = APP_CACHE_DIR / "cache" / "ultimateasiloader"
UNREAL5_CACHE_DIR = APP_CACHE_DIR / "cache" / "unreal5"
COVER_CACHE_DIR = APP_CACHE_DIR / "cache" / "covers"
COVERS_REPO_RAW_BASE_URL = str(
    _get_runtime_config_value(
        "OPTISCALER_COVERS_RAW_BASE_URL",
        "https://raw.githubusercontent.com/onehoon/OptiScalerInstaller/covers/assets",
    )
    or ""
).strip().rstrip("/")
FSR4_SKIP_GPU_RULE = "*rx 90*"
APP_BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ASSETS_DIR = APP_BASE_DIR / "assets"
DEFAULT_POSTER_CANDIDATES = [
    ASSETS_DIR / "cover" / "default_poster.webp",
    ASSETS_DIR / "cover" / "default_poster.jpg",
    ASSETS_DIR / "cover" / "default_poster.png",
    ASSETS_DIR / "default_poster.webp",
    ASSETS_DIR / "default_poster.jpg",
    ASSETS_DIR / "default_poster.png",
]
IMAGE_TIMEOUT_SECONDS = 10
IMAGE_MAX_RETRIES = 3
IMAGE_MAX_WORKERS = 4
IMAGE_RETRY_DELAY_MS = _get_int_env("OPTISCALER_IMAGE_RETRY_DELAY_MS", 1500)
DEFAULT_POSTER_SCALE = 1.5
INFO_TEXT_OFFSET_PX = 10
POSTER_CACHE_VERSION = 2
_ENABLE_POSTER_CACHE_TEXT = str(
    _get_runtime_config_value("OPTISCALER_ENABLE_POSTER_CACHE", "")
).strip().lower()
if not _ENABLE_POSTER_CACHE_TEXT:
    ENABLE_POSTER_CACHE = _get_bool_env("OPTISCALER_ENABLE_POSTER_CACHE", True)
elif _ENABLE_POSTER_CACHE_TEXT in {"1", "true", "yes", "on", "0", "false", "no", "n", "off"}:
    ENABLE_POSTER_CACHE = _get_bool_env("OPTISCALER_ENABLE_POSTER_CACHE", False)
else:
    ENABLE_POSTER_CACHE = False
IMAGE_CACHE_MAX = _get_int_env("OPTISCALER_IMAGE_CACHE_MAX", 100)


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
    create_prefixed_logger=get_prefixed_logger,
    gpu_bundle_url=OPTISCALER_GPU_BUNDLE_URL,
    gpu_bundle_debug=OPTISCALER_GPU_BUNDLE_DEBUG,
    game_master_url=OPTISCALER_GAME_MASTER_URL,
    resource_master_url=OPTISCALER_RESOURCE_MASTER_URL,
    message_binding_url=OPTISCALER_MESSAGE_BINDING_URL,
    message_center_url=OPTISCALER_MESSAGE_CENTER_URL,
    gpu_notice_theme=APP_THEME.gpu_notice_theme,
    max_supported_gpu_count=MAX_SUPPORTED_GPU_COUNT,
    message_popup_theme=APP_THEME.message_popup_theme,
    root_width_fallback=WINDOW_W,
    root_height_fallback=WINDOW_H,
    supported_games_wiki_url=SUPPORTED_GAMES_WIKI_URL,
    game_ini_profile_url=OPTISCALER_GAME_INI_PROFILE_URL,
    game_unreal_ini_profile_url=OPTISCALER_GAME_UNREAL_INI_PROFILE_URL,
    engine_ini_profile_url=OPTISCALER_ENGINE_INI_PROFILE_URL,
    game_xml_profile_url=OPTISCALER_GAME_XML_PROFILE_URL,
    registry_profile_url=OPTISCALER_REGISTRY_PROFILE_URL,
    game_json_profile_url=OPTISCALER_GAME_JSON_PROFILE_URL,
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

    def _ensure_cache_dir(self, path: Path) -> Path:
        cache_dir = Path(path)
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    def _initialize_runtime_state(self) -> None:
        self.game_folder = ""
        runtime_state_bundle = build_runtime_state_bundle(
            checking_gpu_text=self.txt.main.checking_gpu,
        )
        self.archive_state = runtime_state_bundle.archive_state
        self.gpu_state = runtime_state_bundle.gpu_state
        self.sheet_state = runtime_state_bundle.sheet_state
        self.install_state = runtime_state_bundle.install_state
        self.card_ui_state = runtime_state_bundle.card_ui_state
        self.optiscaler_cache_dir = self._ensure_cache_dir(OPTISCALER_CACHE_DIR)
        self.fsr4_cache_dir = self._ensure_cache_dir(FSR4_CACHE_DIR)
        self.optipatcher_cache_dir = self._ensure_cache_dir(OPTIPATCHER_CACHE_DIR)
        self.specialk_cache_dir = self._ensure_cache_dir(SPECIALK_CACHE_DIR)
        self.ual_cache_dir = self._ensure_cache_dir(UAL_CACHE_DIR)
        self.unreal5_cache_dir = self._ensure_cache_dir(UNREAL5_CACHE_DIR)
        self.manifest_root = APP_CACHE_DIR
        self.found_exe_list = []
        self.card_frames: list = []
        self.card_items: list = []
        self._ctk_images: list = []   # keep refs alive

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
                show_startup_warning_popup=self._show_startup_warning_popup,
            ),
            is_multi_gpu_blocked=self._is_multi_gpu_block_active,
            get_startup_warning_text=lambda: pick_module_message(self.sheet_state.module_download_links, "warning", self.lang),
            logger=logging.getLogger(),
        )
        self._task_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="general-task")
        self._scan_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="scan-worker")
        self._optiscaler_prepare_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="optiscaler-prepare")
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
        build_main_ui(self, APP_THEME.main_ui_theme)
        ui_controllers = build_ui_controllers(self._build_ui_controller_factory_deps(), UI_CONTROLLER_FACTORY_CONFIG)
        bind_ui_controllers(self, ui_controllers)
        self._bind_viewport_scroll_events()
        self._app_controllers = build_app_controllers(self, APP_CONTROLLER_FACTORY_CONFIG)
        bind_app_controllers(self, self._app_controllers)
        self._create_ui_shell()
        self._configure_card_columns(GRID_COLS)
        self._update_selected_game_header()
        self._create_startup_runtime_coordinator()

    def _build_ui_controller_factory_deps(self) -> UiControllerFactoryDeps:
        return UiControllerFactoryDeps(
            root=self.root,
            games_scroll=self.games_scroll,
            poster_loader=self._poster_loader,
            poster_queue=self._poster_queue,
            card_ui_state=self.card_ui_state,
            card_items=self.card_items,
            image_refs=self._ctk_images,
            card_ui_callbacks=GameCardUiCallbacks(
                get_found_games=lambda: tuple(self.found_exe_list),
                get_grid_column_count=self._get_dynamic_column_count,
                get_dynamic_column_count=self._get_dynamic_column_count,
                get_card_render_controller=lambda: getattr(self, "_card_render_controller", None),
                select_game=self._set_selected_game,
                activate_game=self._set_selected_game,
            ),
            card_viewport_callbacks=CardViewportCallbacks(
                get_card_frames=lambda: tuple(self.card_frames),
                has_found_games=lambda: bool(self.found_exe_list),
                render_cards=lambda keep_selection: self._render_cards(keep_selection=keep_selection),
                get_effective_widget_scale=self._get_effective_widget_scale,
                set_card_image_updates_suspended=self._set_card_image_updates_suspended,
            ),
        )

    def _bind_viewport_scroll_events(self) -> None:
        c = self._card_viewport_controller
        self.games_scroll.bind("<Configure>", c.on_games_area_resize)
        try:
            canvas = getattr(self.games_scroll, "_parent_canvas", None)
            scrollbar = getattr(self.games_scroll, "_scrollbar", None)
            if canvas is not None:
                canvas.bind("<MouseWheel>", c.on_games_scroll, add="+")
                canvas.bind("<Button-4>", c.on_games_scroll, add="+")
                canvas.bind("<Button-5>", c.on_games_scroll, add="+")
                canvas.bind("<ButtonRelease-1>", c.on_games_scroll, add="+")
                canvas.bind("<Configure>", c.on_games_area_resize, add="+")
            if canvas is not None and scrollbar is not None:
                scrollbar.configure(command=c.on_games_scrollbar_command)
                scrollbar.bind("<Button-1>", c.on_games_scrollbar_press, add="+")
                scrollbar.bind("<B1-Motion>", c.on_games_scrollbar_press, add="+")
                scrollbar.bind("<ButtonRelease-1>", c.on_games_scrollbar_release, add="+")
            self.root.bind("<ButtonRelease-1>", c.on_games_scrollbar_release, add="+")
        except Exception:
            logging.exception("Failed to bind viewport scroll events to controller")

    def _start_background_services(self) -> None:
        if getattr(self, "_gpu_flow_controller", None) is not None:
            self._gpu_flow_controller.start_detection()

    def _bind_root_events(self) -> None:
        self.root.bind("<Configure>", self._card_viewport_controller.on_root_resize)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if self._startup_window_workaround_active:
            self.root.after_idle(self._apply_startup_window_workaround)
            self.root.after(220, self._apply_startup_window_workaround)
        self.root.after(250, self._card_viewport_controller.capture_startup_width)

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

    def _set_gpu_label_text(self, text: str) -> None:
        widget = getattr(self, "gpu_lbl", None)
        if widget is None:
            return
        if hasattr(widget, "winfo_exists") and callable(widget.winfo_exists) and not widget.winfo_exists():
            return
        widget.configure(text=str(text or ""))

    def _set_folder_select_enabled(self, enabled: bool) -> None:
        widget = getattr(self, "btn_select_folder", None)
        if widget is None:
            return
        if hasattr(widget, "winfo_exists") and callable(widget.winfo_exists) and not widget.winfo_exists():
            return
        widget.configure(state="normal" if enabled else "disabled")

    def _show_game_selection_popup(
        self,
        message_text: str,
        on_confirm: Optional[Callable[[], None]] = None,
    ) -> None:
        shell = self._get_ui_shell()
        if shell is None:
            return
        shell.show_game_selection_popup(message_text, on_confirm=on_confirm)

    def _show_precheck_popup(
        self,
        message_text: str,
        on_close: Optional[Callable[[], None]] = None,
    ) -> None:
        shell = self._get_ui_shell()
        if shell is None:
            return
        shell.show_precheck_popup(message_text, on_close=on_close)

    def _is_multi_gpu_block_active(self) -> bool:
        return self.gpu_state.gpu_count > MAX_SUPPORTED_GPU_COUNT

    def _is_vendor_allowed_by_game_flags(self, game_data: dict) -> bool:
        vendor = str(self.sheet_state.active_vendor or "").strip().lower()
        if vendor not in {"intel", "amd", "nvidia"}:
            return True

        support_key = f"support_{vendor}"
        if support_key not in game_data:
            return True

        return parse_support_flag(game_data.get(support_key), native_xefg_means_false=True)

    def _is_game_supported_for_current_gpu(self, game_data: dict) -> bool:
        if not self._is_vendor_allowed_by_game_flags(game_data):
            return False
        if bool(game_data.get("__gpu_bundle_loaded__", False)):
            return bool(game_data.get("__gpu_bundle_supported__", False))
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
        shell = self._get_ui_shell()
        if shell is None:
            return
        shell.set_supported_games_wiki_link_hover(hovered)

    def _open_supported_games_wiki(self, _event=None) -> None:
        shell = self._get_ui_shell()
        if shell is None:
            return
        shell.open_supported_games_wiki()

    def _set_scan_status_message(self, text: str = "", text_color: Optional[str] = None):
        shell = self._get_ui_shell()
        if shell is None:
            return
        shell.set_scan_status_message(text, text_color)

    def _set_status_badge_state(self, label_text: str, indicator_color: str, pulse: bool = False):
        shell = self._get_ui_shell()
        if shell is None:
            return
        shell.set_status_badge_state(label_text=label_text, indicator_color=indicator_color, pulse=pulse)

    def _get_selected_game_header_text(self) -> str:
        shell = self._get_ui_shell()
        if shell is None:
            return ""
        return shell.get_selected_game_header_text()

    def _update_selected_game_header(self):
        shell = self._get_ui_shell()
        if shell is None:
            return
        shell.update_selected_game_header()

    def _show_after_install_popup(self, game: dict):
        shell = self._get_ui_shell()
        if shell is None:
            return
        shell.show_after_install_popup(game)

    def _call_optional_method(self, attr_name: str, method_name: str, *args, **kwargs) -> None:
        target = getattr(self, attr_name, None)
        if target is None:
            return
        getattr(target, method_name)(*args, **kwargs)

    def _get_ui_shell(self) -> Optional[AppUiShell]:
        return getattr(self, "_ui_shell", None)

    def _get_install_flow_controller(self) -> Optional[InstallFlowController]:
        return getattr(self, "_install_flow_controller", None)

    def _update_install_button_state(self):
        if not hasattr(self, "apply_btn"):
            return

        button_state = compute_install_button_state(self._build_install_button_state_inputs())
        can_install = bool(button_state.enabled)
        is_sheet_loading = button_state.reason_code == "sheet_loading"

        # Cancel any ongoing loading blink
        blink_job = getattr(self, "_loading_blink_job", None)
        if blink_job is not None:
            self.root.after_cancel(blink_job)
            self._loading_blink_job = None

        if button_state.show_installing:
            button_text = self.txt.main.installing_button
        elif can_install:
            button_text = self.txt.main.install_button
        elif is_sheet_loading:
            button_text = self.txt.main.loading_button
        else:
            button_text = ""

        self.apply_btn.configure(
            state="normal" if can_install else "disabled",
            text=button_text,
            fg_color=APP_THEME.install_button_color if can_install else APP_THEME.install_button_disabled_color,
            hover_color=APP_THEME.install_button_hover_color if can_install else APP_THEME.install_button_disabled_color,
            border_color=APP_THEME.install_button_border_color if can_install else APP_THEME.install_button_border_disabled_color,
        )

        # Start blinking if still loading
        if is_sheet_loading:
            self._loading_blink_job = self.root.after(600, self._tick_loading_blink)

    def _tick_loading_blink(self):
        """Toggle install button text to create a blinking loading effect."""
        self._loading_blink_job = None
        if not hasattr(self, "apply_btn"):
            return
        button_state = compute_install_button_state(self._build_install_button_state_inputs())
        if button_state.reason_code != "sheet_loading":
            return
        loading_text = self.txt.main.loading_button
        current_text = self.apply_btn.cget("text")
        self.apply_btn.configure(text="" if current_text == loading_text else loading_text)
        self._loading_blink_job = self.root.after(600, self._tick_loading_blink)

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
        if getattr(self, "_game_db_controller", None) is None:
            return

        sheet_state = self.sheet_state
        game_db_vendor = str(sheet_state.active_vendor or "default")
        gpu_model = str(getattr(self.gpu_state, "gpu_info", "") or "").strip()
        started = self._game_db_controller.start_load(game_db_vendor, gpu_model)
        if not started:
            return
        logging.info(
            "[APP] Starting Game DB load for vendor=%s gpu=%s",
            game_db_vendor,
            self.gpu_state.gpu_info,
        )

    def _on_game_db_loaded(self, result: GameDbLoadResult) -> None:
        coordinator = getattr(self, "_startup_runtime_coordinator", None)
        if coordinator is None:
            return
        return coordinator.on_game_db_loaded(result)

    def _start_optiscaler_archive_prepare(self):
        coordinator = getattr(self, "_startup_runtime_coordinator", None)
        if coordinator is None:
            return
        return coordinator.start_optiscaler_archive_prepare()

    def check_app_update(self) -> bool:
        controller = getattr(self, "_app_actions_controller", None)
        if controller is None:
            return False
        return controller.check_app_update(
            self.sheet_state.module_download_links,
            blocked=bool(self.gpu_state.multi_gpu_blocked),
        )

    def _show_startup_warning_popup(
        self,
        warning_text: str,
        on_close: Optional[Callable[[], None]] = None,
    ) -> None:
        controller = getattr(self, "_app_notice_controller", None)
        if controller is None:
            return
        controller.show_startup_warning_popup(warning_text, on_close=on_close)

    def _is_scan_in_progress(self) -> bool:
        controller = getattr(self, "_scan_controller", None)
        return bool(controller and controller.is_scan_in_progress)

    def _apply_gpu_flow_state(self, state: GpuFlowState) -> None:
        coordinator = getattr(self, "_startup_runtime_coordinator", None)
        if coordinator is None:
            return
        return coordinator.apply_gpu_flow_state(state)

    def _handle_unsupported_gpu_block(self, scan_status_message: str, info_text: str) -> None:
        coordinator = getattr(self, "_startup_runtime_coordinator", None)
        if coordinator is None:
            return
        return coordinator.handle_unsupported_gpu_block(scan_status_message, info_text)

    def _on_optiscaler_archive_state_changed(self, state: ArchivePreparationState) -> None:
        coordinator = getattr(self, "_startup_runtime_coordinator", None)
        if coordinator is None:
            return
        return coordinator.on_optiscaler_archive_state_changed(state)

    def _on_fsr4_archive_state_changed(self, state: ArchivePreparationState) -> None:
        coordinator = getattr(self, "_startup_runtime_coordinator", None)
        if coordinator is None:
            return
        return coordinator.on_fsr4_archive_state_changed(state)

    def _on_optipatcher_archive_state_changed(self, state: ArchivePreparationState) -> None:
        coordinator = getattr(self, "_startup_runtime_coordinator", None)
        if coordinator is None:
            return
        return coordinator.on_optipatcher_archive_state_changed(state)

    def _on_specialk_archive_state_changed(self, state: ArchivePreparationState) -> None:
        coordinator = getattr(self, "_startup_runtime_coordinator", None)
        if coordinator is None:
            return
        return coordinator.on_specialk_archive_state_changed(state)

    def _on_ual_archive_state_changed(self, state: ArchivePreparationState) -> None:
        coordinator = getattr(self, "_startup_runtime_coordinator", None)
        if coordinator is None:
            return
        return coordinator.on_ual_archive_state_changed(state)

    def _on_unreal5_archive_state_changed(self, state: ArchivePreparationState) -> None:
        coordinator = getattr(self, "_startup_runtime_coordinator", None)
        if coordinator is None:
            return
        return coordinator.on_unreal5_archive_state_changed(state)

    def _clear_found_games(self) -> None:
        self.found_exe_list = []

    def _build_startup_runtime_coordinator_deps(self) -> StartupRuntimeCoordinatorDeps:
        return StartupRuntimeCoordinatorDeps(
            archive_state=self.archive_state,
            gpu_state=self.gpu_state,
            sheet_state=self.sheet_state,
            install_state=self.install_state,
            card_ui_state=self.card_ui_state,
            optiscaler_cache_dir=self.optiscaler_cache_dir,
            fsr4_cache_dir=self.fsr4_cache_dir,
            optipatcher_cache_dir=self.optipatcher_cache_dir,
            specialk_cache_dir=self.specialk_cache_dir,
            ual_cache_dir=self.ual_cache_dir,
            unreal5_cache_dir=self.unreal5_cache_dir,
            manifest_root=self.manifest_root,
            callbacks=StartupRuntimeCallbacks(
                format_gpu_label_text=self._format_gpu_label_text,
                set_gpu_label_text=self._set_gpu_label_text,
                refresh_archive_info_ui=self._refresh_optiscaler_archive_info_ui,
                update_install_button_state=self._update_install_button_state,
                update_sheet_status=self._update_sheet_status,
                run_post_sheet_startup=self._startup_flow.run_post_sheet_startup,
                mark_post_sheet_startup_done=self._startup_flow.mark_post_sheet_startup_done,
                set_scan_status_message=self._set_scan_status_message,
                clear_cards=self._clear_cards,
                set_information_text=self._set_information_text,
                update_selected_game_header=self._update_selected_game_header,
                apply_install_selection_state=self._apply_install_selection_state,
                set_folder_select_enabled=self._set_folder_select_enabled,
                check_app_update=self.check_app_update,
                should_apply_fsr4_for_game=self._should_apply_fsr4_for_game,
                get_archive_controller=lambda: getattr(self, "_archive_controller", None),
                clear_found_games=self._clear_found_games,
            ),
            unknown_gpu_text=self.txt.main.unknown_gpu,
            logger=logging.getLogger(),
        )

    def _create_startup_runtime_coordinator(self) -> None:
        if getattr(self, "_startup_runtime_coordinator", None) is not None:
            return
        self._startup_runtime_coordinator = create_startup_runtime_coordinator(
            self._build_startup_runtime_coordinator_deps()
        )

    def _create_ui_shell(self) -> None:
        if getattr(self, "_ui_shell", None) is not None:
            return
        self._ui_shell = create_ui_shell(
            self,
            scan_status_text_color=APP_THEME.scan_status_text_color,
            status_indicator_offline_color=APP_THEME.status_indicator_offline_color,
            status_indicator_warning_color=APP_THEME.status_indicator_warning_color,
            status_indicator_loading_color=APP_THEME.status_indicator_loading_color,
            status_indicator_online_color=APP_THEME.status_indicator_online_color,
        )

    def _pump_poster_queue(self) -> None:
        self._poster_queue.pump()

    def _start_auto_scan(self):
        """Kick off a silent auto-scan of known launcher/game directories."""
        if self.gpu_state.multi_gpu_blocked:
            return
        if self.install_state.in_progress:
            return
        if getattr(self, "_scan_controller", None) is None:
            return
        self._scan_controller.start_auto_scan()

    def _set_game_folder(self, folder_path: str) -> None:
        self.game_folder = str(folder_path or "")

    def _start_manual_scan_from_folder(self, folder_path: str) -> bool:
        if getattr(self, "_scan_controller", None) is None:
            return False
        if self.install_state.in_progress:
            return False
        return self._scan_controller.start_manual_scan(folder_path)

    # ------------------------------------------------------------------
    # UI builder
    # ------------------------------------------------------------------

    def _refresh_optiscaler_archive_info_ui(self):
        shell = self._get_ui_shell()
        if shell is None:
            return
        shell.refresh_optiscaler_archive_info_ui(
            sheet_loading=bool(self.sheet_state.loading),
            module_download_links=self.sheet_state.module_download_links,
        )

    def _apply_information_text_shift(self):
        shell = self._get_ui_shell()
        if shell is None:
            return
        shell.apply_information_text_shift()

    # ------------------------------------------------------------------
    # Status indicator
    # ------------------------------------------------------------------

    def _update_sheet_status(self):
        shell = self._get_ui_shell()
        if shell is None:
            return
        gpu_state = self.gpu_state
        sheet_state = self.sheet_state
        shell.update_sheet_status(
            multi_gpu_blocked=gpu_state.multi_gpu_blocked,
            gpu_selection_pending=gpu_state.gpu_selection_pending,
            sheet_loading=sheet_state.loading,
            sheet_status=sheet_state.status,
        )

    # ------------------------------------------------------------------
    # Information text
    # ------------------------------------------------------------------

    def _set_information_text(self, text=""):
        shell = self._get_ui_shell()
        if shell is None:
            return
        shell.set_information_text(text=text)

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
        controller = getattr(self, "_card_ui_controller", None)
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
        controller = getattr(self, "_card_ui_controller", None)
        if controller is not None:
            controller.refresh_all_card_visuals()
        self._set_information_text(
            build_install_information_text(
                game,
                lang=self.lang,
                stage="install_pre",
            )
        )

    def _get_effective_widget_scale(self) -> float:
        return get_ctk_scale(self.root, 1.0)

    def _get_forced_card_area_width(self) -> int:
        return self._card_viewport_controller._get_forced_card_area_width()

    def _get_dynamic_column_count(self) -> int:
        return self._card_viewport_controller._get_dynamic_column_count()

    def _max_safe_columns_for_width(self, usable_w: int) -> int:
        return self._card_viewport_controller._max_safe_columns_for_width(usable_w)

    def _configure_card_columns(self, cols: int):
        return self._card_viewport_controller.configure_card_columns(cols)

    def _refresh_games_scrollregion(self):
        return self._card_viewport_controller._refresh_games_scrollregion()

    def _render_cards(self, keep_selection=False):
        controller = getattr(self, "_card_ui_controller", None)
        if controller is None:
            return
        return controller.render_cards(keep_selection=bool(keep_selection))

    def _make_card(self, index: int, game: dict) -> ctk.CTkFrame:
        controller = getattr(self, "_card_ui_controller", None)
        if controller is None:
            raise RuntimeError("Game card UI controller is not available")
        return controller.make_card(index, game)

    def _visible_game_indices(self) -> set:
        controller = getattr(self, "_card_ui_controller", None)
        if controller is None:
            return set()
        return controller.visible_game_indices()

    def _apply_loaded_poster(self, index: int, label: ctk.CTkLabel, pil_img: Image.Image):
        controller = getattr(self, "_card_ui_controller", None)
        if controller is None:
            return
        return controller.set_card_base_image(index, label, pil_img)

    def _set_card_image_updates_suspended(self, suspended: bool) -> None:
        controller = getattr(self, "_card_ui_controller", None)
        if controller is None:
            return
        controller.set_card_image_updates_suspended(bool(suspended))

    def _set_selected_game(self, index: int):
        controller = getattr(self, "_install_selection_controller", None)
        if controller is None:
            return
        if self.install_state.in_progress:
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
        if self.install_state.in_progress:
            return
        controller.select_game_folder(self._build_scan_entry_state())

    def _add_game_card_incremental(self, game: dict):
        """Append one game to the list and immediately render + queue its cover download."""
        controller = getattr(self, "_card_render_controller", None)
        if controller is None:
            return
        cols = max(1, self._get_dynamic_column_count())
        controller.add_game_card(
            game,
            cols=cols,
            target_cols=self._max_safe_columns_for_width(self._get_forced_card_area_width()),
        )

    # ------------------------------------------------------------------
    # Install
    # ------------------------------------------------------------------

    def apply_optiscaler(self):
        controller = self._get_install_flow_controller()
        if controller is None:
            return
        return controller.apply_selected_install()

    def _apply_optiscaler_worker(
        self,
        game_data,
        source_archive,
        resolved_dll_name,
        fsr4_source_archive,
        fsr4_required,
        ual_cached_archive="",
        optipatcher_cached_archive="",
        specialk_cached_archive="",
        unreal5_cached_archive="",
    ):
        controller = self._get_install_flow_controller()
        if controller is None:
            return
        return controller.run_install_worker(
            game_data,
            source_archive,
            resolved_dll_name,
            fsr4_source_archive,
            fsr4_required,
            ual_cached_archive=ual_cached_archive,
            optipatcher_cached_archive=optipatcher_cached_archive,
            specialk_cached_archive=specialk_cached_archive,
            unreal5_cached_archive=unreal5_cached_archive,
        )

    def _on_install_finished(self, success, message, installed_game=None):
        controller = self._get_install_flow_controller()
        if controller is None:
            return
        return controller.on_install_finished(success, message, installed_game)


if __name__ == "__main__":
    if "--edit-engine-ini" in sys.argv:
        logging.warning("--edit-engine-ini no longer supports Google Sheet source and has been disabled.")
        sys.exit(0)

    request_foreground = has_startup_foreground_request(sys.argv[1:])
    root = ctk.CTk()
    app = OptiManagerApp(root)
    if request_foreground:
        request_window_foreground(root, logger=logging.getLogger("APP"))
    root.mainloop()
