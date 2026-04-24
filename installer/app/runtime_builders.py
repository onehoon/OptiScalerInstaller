from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any, Callable

from installer import app_update
from installer.i18n import pick_module_message

from .app_runtime_actions import (
    apply_install_selection_state,
    clear_found_games,
    format_gpu_label_text,
    is_scan_in_progress,
    request_close,
    set_folder_select_enabled,
    set_gpu_label_text,
    start_auto_scan,
)
from .card_runtime_actions import (
    apply_loaded_poster,
    configure_card_columns,
    get_dynamic_column_count,
    get_effective_widget_scale,
    render_cards,
    select_game,
    set_card_image_updates_suspended,
    visible_game_indices,
)
from .card_ui import GameCardUiCallbacks
from .card_viewport import CardViewportCallbacks
from .controller_factory import (
    AppControllerFactoryConfig,
    AppControllers,
    bind_app_controllers,
    build_app_controllers,
)
from .install_runtime_actions import (
    is_multi_gpu_block_active,
    should_apply_fsr4_for_game,
    update_install_button_state,
)
from .path_config import AppPathConfig
from .poster_queue import PosterQueueController
from .runtime_state import build_runtime_state_bundle
from .startup_flow import StartupFlowCallbacks, StartupFlowController
from .startup_runtime import (
    StartupRuntimeCallbacks,
    StartupRuntimeCoordinator,
    StartupRuntimeCoordinatorDeps,
    create_startup_runtime_coordinator,
)
from .startup_window import (
    apply_startup_window_layout,
    apply_startup_window_workaround,
    build_startup_window_layout,
)
from .theme import AppThemeBundle
from .ui_builder import build_main_ui
from .ui_controller_factory import (
    UiControllerFactoryConfig,
    UiControllerFactoryDeps,
    UiControllers,
    bind_ui_controllers,
    build_ui_controllers,
)
from .ui_shell_actions import (
    refresh_optiscaler_archive_info_ui,
    set_information_text,
    set_scan_status_message,
    update_selected_game_header,
    update_sheet_status,
)
from .ui_presenters import BottomPanelPresenter, HeaderStatusPresenter
from .ui_shell import AppUiShell, create_ui_shell
from .ui_runtime_config import AppUiRuntimeConfig, format_optiscaler_version_display_name
from ..common.poster_loader import PosterImageLoader, PosterLoaderConfig


VersionNameFormatter = Callable[[str], str]
VisibleIndexProvider = Callable[[], set[int]]
ScanStateProvider = Callable[[], bool]
PosterReadyCallback = Callable[[int, object, Any], None]
StartupWarningTextProvider = Callable[[], str]
StartupWarningPopupCallback = Callable[[str, Callable[[], None] | None], None]


@dataclass(frozen=True)
class PresenterBundle:
    header_status_presenter: HeaderStatusPresenter
    bottom_panel_presenter: BottomPanelPresenter


@dataclass(frozen=True)
class PosterInfraBundle:
    poster_loader: PosterImageLoader
    image_executor: ThreadPoolExecutor
    poster_queue: PosterQueueController


@dataclass(frozen=True)
class StartupUpdateInfraBundle:
    startup_flow: StartupFlowController
    task_executor: ThreadPoolExecutor
    scan_executor: ThreadPoolExecutor
    optiscaler_prepare_executor: ThreadPoolExecutor
    download_executor: ThreadPoolExecutor
    app_update_manager: app_update.InstallerUpdateManager


def _ensure_cache_dir(path: str | Path) -> Path:
    cache_dir = Path(path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def configure_app_startup_window(
    app: Any,
    *,
    app_version: str,
    app_ui_config: AppUiRuntimeConfig,
    logger=None,
) -> None:
    shared_logger = logger or logging.getLogger()
    app.root.title(app.txt.main.window_title_template.format(version=app_version))
    startup_layout = build_startup_window_layout(
        app.root,
        window_width=app_ui_config.window_width,
        window_height=app_ui_config.window_height,
        window_min_width=app_ui_config.window_min_width,
        window_min_height=app_ui_config.window_min_height,
        card_width=app_ui_config.card_width,
        card_height=app_ui_config.card_height,
        default_poster_scale=app_ui_config.default_poster_scale,
    )
    app._startup_window_workaround_active = startup_layout.workaround_active
    app._startup_window_width = startup_layout.window_width
    app._startup_window_height = startup_layout.window_height
    apply_startup_window_layout(app.root, startup_layout, logger=shared_logger)
    app._poster_target_width = startup_layout.poster_target_width
    app._poster_target_height = startup_layout.poster_target_height
    app._poster_target_scale = startup_layout.poster_target_scale


def initialize_app_runtime_state(
    app: Any,
    *,
    app_paths: AppPathConfig,
    checking_gpu_text: str,
) -> None:
    runtime_state_bundle = build_runtime_state_bundle(
        checking_gpu_text=checking_gpu_text,
    )
    app.game_folder = ""
    app.archive_state = runtime_state_bundle.archive_state
    app.gpu_state = runtime_state_bundle.gpu_state
    app.sheet_state = runtime_state_bundle.sheet_state
    app.install_state = runtime_state_bundle.install_state
    app.card_ui_state = runtime_state_bundle.card_ui_state
    app.optiscaler_cache_dir = _ensure_cache_dir(app_paths.optiscaler_cache_dir)
    app.fsr4_cache_dir = _ensure_cache_dir(app_paths.fsr4_cache_dir)
    app.optipatcher_cache_dir = _ensure_cache_dir(app_paths.optipatcher_cache_dir)
    app.specialk_cache_dir = _ensure_cache_dir(app_paths.specialk_cache_dir)
    app.ual_cache_dir = _ensure_cache_dir(app_paths.ual_cache_dir)
    app.unreal5_cache_dir = _ensure_cache_dir(app_paths.unreal5_cache_dir)
    app.manifest_root = app_paths.app_cache_dir
    app.found_exe_list = []
    app.card_frames = []
    app.card_items = []
    app._ctk_images = []


def build_presenter_bundle(
    *,
    root: Any,
    app_theme: AppThemeBundle,
    app_ui_config: AppUiRuntimeConfig,
    supported_games_wiki_url: str,
    version_name_formatter: VersionNameFormatter,
    logger=None,
) -> PresenterBundle:
    shared_logger = logger or logging.getLogger()
    return PresenterBundle(
        header_status_presenter=HeaderStatusPresenter(
            root=root,
            status_text_color=app_theme.status_text_color,
            scan_status_text_color=app_theme.scan_status_text_color,
            status_indicator_loading_dim_color=app_theme.status_indicator_loading_dim_color,
            status_indicator_pulse_ms=app_theme.status_indicator_pulse_ms,
            supported_games_wiki_url=supported_games_wiki_url,
            link_active_color=app_theme.link_active_color,
            link_hover_color=app_theme.link_hover_color,
            logger=shared_logger,
        ),
        bottom_panel_presenter=BottomPanelPresenter(
            info_text_offset_px=app_ui_config.info_text_offset_px,
            version_name_formatter=version_name_formatter,
            info_emphasis_color=app_theme.status_indicator_warning_color,
            logger=shared_logger,
        ),
    )


def build_poster_infra(
    *,
    root: Any,
    app_paths: AppPathConfig,
    app_ui_config: AppUiRuntimeConfig,
    poster_target_width: int,
    poster_target_height: int,
    get_visible_indices: VisibleIndexProvider,
    is_scan_in_progress: ScanStateProvider,
    on_image_ready: PosterReadyCallback,
) -> PosterInfraBundle:
    poster_loader = PosterImageLoader(
        PosterLoaderConfig(
            cache_dir=app_paths.cover_cache_dir,
            assets_dir=app_paths.assets_dir,
            default_poster_candidates=app_paths.default_poster_candidates,
            target_width=poster_target_width,
            target_height=poster_target_height,
            repo_raw_base_url=app_paths.covers_repo_raw_base_url,
            timeout_seconds=app_ui_config.image_timeout_seconds,
            max_retries=app_ui_config.image_max_retries,
            cache_version=app_ui_config.poster_cache_version,
            enable_memory_cache=app_ui_config.enable_poster_cache,
            memory_cache_max=app_ui_config.image_cache_max,
        )
    )
    image_executor = ThreadPoolExecutor(
        max_workers=app_ui_config.image_max_workers,
        thread_name_prefix="cover-loader",
    )
    poster_queue = PosterQueueController(
        root=root,
        executor=image_executor,
        loader=poster_loader.load,
        max_workers=app_ui_config.image_max_workers,
        retry_delay_ms=app_ui_config.image_retry_delay_ms,
        get_visible_indices=get_visible_indices,
        is_scan_in_progress=is_scan_in_progress,
        on_image_ready=on_image_ready,
    )
    return PosterInfraBundle(
        poster_loader=poster_loader,
        image_executor=image_executor,
        poster_queue=poster_queue,
    )


def build_startup_update_infra(
    *,
    root: Any,
    app_version: str,
    strings: Any,
    start_archive_prepare: Callable[[], None],
    start_auto_scan: Callable[[], None],
    show_startup_warning_popup: StartupWarningPopupCallback,
    is_multi_gpu_blocked: Callable[[], bool],
    get_startup_warning_text: StartupWarningTextProvider,
    on_busy_state_changed: Callable[[], None],
    on_exit_requested: Callable[[], None],
    logger=None,
) -> StartupUpdateInfraBundle:
    shared_logger = logger or logging.getLogger()
    startup_flow = StartupFlowController(
        root=root,
        callbacks=StartupFlowCallbacks(
            start_archive_prepare=start_archive_prepare,
            start_auto_scan=start_auto_scan,
            show_startup_warning_popup=show_startup_warning_popup,
        ),
        is_multi_gpu_blocked=is_multi_gpu_blocked,
        get_startup_warning_text=get_startup_warning_text,
        logger=shared_logger,
    )
    return StartupUpdateInfraBundle(
        startup_flow=startup_flow,
        task_executor=ThreadPoolExecutor(max_workers=2, thread_name_prefix="general-task"),
        scan_executor=ThreadPoolExecutor(max_workers=4, thread_name_prefix="scan-worker"),
        optiscaler_prepare_executor=ThreadPoolExecutor(max_workers=1, thread_name_prefix="optiscaler-prepare"),
        download_executor=ThreadPoolExecutor(max_workers=1, thread_name_prefix="archive-download"),
        app_update_manager=app_update.InstallerUpdateManager(
            root,
            current_version=app_version,
            strings=strings,
            on_busy_state_changed=on_busy_state_changed,
            on_update_failed=lambda: startup_flow.run_post_sheet_startup(True),
            on_exit_requested=on_exit_requested,
        ),
    )


def initialize_app_infra(
    app: Any,
    *,
    app_version: str,
    app_paths: AppPathConfig,
    app_ui_config: AppUiRuntimeConfig,
    logger=None,
) -> None:
    shared_logger = logger or logging.getLogger()
    poster_infra = build_poster_infra(
        root=app.root,
        app_paths=app_paths,
        app_ui_config=app_ui_config,
        poster_target_width=app._poster_target_width,
        poster_target_height=app._poster_target_height,
        get_visible_indices=lambda: visible_game_indices(app),
        is_scan_in_progress=lambda: is_scan_in_progress(app),
        on_image_ready=lambda index, label, pil_img: apply_loaded_poster(app, index, label, pil_img),
    )
    app._poster_loader = poster_infra.poster_loader
    app._image_executor = poster_infra.image_executor
    app._poster_queue = poster_infra.poster_queue

    startup_update_infra = build_startup_update_infra(
        root=app.root,
        app_version=app_version,
        strings=app.txt,
        start_archive_prepare=lambda: app._startup_runtime_coordinator.start_optiscaler_archive_prepare(),
        start_auto_scan=lambda: start_auto_scan(app),
        show_startup_warning_popup=lambda warning_text, on_close=None: app._app_notice_controller.show_startup_warning_popup(
            warning_text,
            on_close=on_close,
        ),
        is_multi_gpu_blocked=lambda: is_multi_gpu_block_active(app),
        get_startup_warning_text=lambda: pick_module_message(app.sheet_state.module_download_links, "warning", app.lang),
        on_busy_state_changed=lambda: update_install_button_state(app),
        on_exit_requested=lambda: request_close(app),
        logger=shared_logger,
    )
    app._startup_flow = startup_update_infra.startup_flow
    app._task_executor = startup_update_infra.task_executor
    app._scan_executor = startup_update_infra.scan_executor
    app._optiscaler_prepare_executor = startup_update_infra.optiscaler_prepare_executor
    app._download_executor = startup_update_infra.download_executor
    app._app_update_manager = startup_update_infra.app_update_manager


def initialize_app_presenters(
    app: Any,
    *,
    app_theme: AppThemeBundle,
    app_ui_config: AppUiRuntimeConfig,
    supported_games_wiki_url: str,
    logger=None,
) -> None:
    presenter_bundle = build_presenter_bundle(
        root=app.root,
        app_theme=app_theme,
        app_ui_config=app_ui_config,
        supported_games_wiki_url=supported_games_wiki_url,
        version_name_formatter=format_optiscaler_version_display_name,
        logger=logger or logging.getLogger(),
    )
    app._header_status_presenter = presenter_bundle.header_status_presenter
    app._bottom_panel_presenter = presenter_bundle.bottom_panel_presenter


def build_app_ui_controller_factory_deps(app: Any) -> UiControllerFactoryDeps:
    return UiControllerFactoryDeps(
        root=app.root,
        games_scroll=app.games_scroll,
        poster_loader=app._poster_loader,
        poster_queue=app._poster_queue,
        card_ui_state=app.card_ui_state,
        card_items=app.card_items,
        image_refs=app._ctk_images,
        card_ui_callbacks=GameCardUiCallbacks(
            get_found_games=lambda: tuple(app.found_exe_list),
            get_grid_column_count=lambda: get_dynamic_column_count(app),
            get_dynamic_column_count=lambda: get_dynamic_column_count(app),
            get_card_render_controller=lambda: getattr(app, "_card_render_controller", None),
            select_game=lambda index: select_game(app, index),
            activate_game=lambda index: select_game(app, index),
        ),
        card_viewport_callbacks=CardViewportCallbacks(
            get_card_frames=lambda: tuple(app.card_frames),
            has_found_games=lambda: bool(app.found_exe_list),
            render_cards=lambda keep_selection: render_cards(app, keep_selection=keep_selection),
            get_effective_widget_scale=lambda: get_effective_widget_scale(app),
            set_card_image_updates_suspended=lambda suspended: set_card_image_updates_suspended(app, suspended),
        ),
    )


def initialize_app_ui_controllers(
    app: Any,
    *,
    main_ui_theme: Any,
    ui_controller_factory_config: UiControllerFactoryConfig,
) -> UiControllers:
    build_main_ui(app, main_ui_theme)
    ui_controllers = build_ui_controllers(
        build_app_ui_controller_factory_deps(app),
        ui_controller_factory_config,
    )
    bind_ui_controllers(app, ui_controllers)
    return ui_controllers


def initialize_app_controllers(
    app: Any,
    *,
    app_controller_factory_config: AppControllerFactoryConfig,
) -> AppControllers:
    app_controllers = build_app_controllers(app, app_controller_factory_config)
    bind_app_controllers(app, app_controllers)
    app._app_controllers = app_controllers
    return app_controllers


def initialize_app_ui_startup(
    app: Any,
    *,
    app_theme: AppThemeBundle,
    app_ui_config: AppUiRuntimeConfig,
    ui_controller_factory_config: UiControllerFactoryConfig,
    app_controller_factory_config: AppControllerFactoryConfig,
    logger=None,
) -> None:
    shared_logger = logger or logging.getLogger()
    initialize_app_ui_controllers(
        app,
        main_ui_theme=app_theme.main_ui_theme,
        ui_controller_factory_config=ui_controller_factory_config,
    )
    bind_app_viewport_scroll_events(app, logger=shared_logger)
    initialize_app_controllers(
        app,
        app_controller_factory_config=app_controller_factory_config,
    )
    ensure_app_ui_shell(app, app_theme=app_theme)
    configure_card_columns(app, app_ui_config.grid_cols)
    update_selected_game_header(app)
    ensure_app_startup_runtime_coordinator(app, logger=shared_logger)


def initialize_app_runtime_startup(
    app: Any,
    *,
    app_theme: AppThemeBundle,
    app_ui_config: AppUiRuntimeConfig,
    ui_controller_factory_config: UiControllerFactoryConfig,
    app_controller_factory_config: AppControllerFactoryConfig,
    logger=None,
) -> None:
    shared_logger = logger or logging.getLogger()
    initialize_app_ui_startup(
        app,
        app_theme=app_theme,
        app_ui_config=app_ui_config,
        ui_controller_factory_config=ui_controller_factory_config,
        app_controller_factory_config=app_controller_factory_config,
        logger=shared_logger,
    )
    gpu_flow_controller = getattr(app, "_gpu_flow_controller", None)
    if gpu_flow_controller is not None:
        gpu_flow_controller.start_detection()
    bind_app_root_events(
        app,
        app_ui_config=app_ui_config,
        logger=shared_logger,
    )


def bind_app_viewport_scroll_events(app: Any, *, logger=None) -> None:
    controller = app._card_viewport_controller
    app.games_scroll.bind("<Configure>", controller.on_games_area_resize)
    try:
        canvas = getattr(app.games_scroll, "_parent_canvas", None)
        scrollbar = getattr(app.games_scroll, "_scrollbar", None)
        if canvas is not None:
            canvas.bind("<MouseWheel>", controller.on_games_scroll, add="+")
            canvas.bind("<Button-4>", controller.on_games_scroll, add="+")
            canvas.bind("<Button-5>", controller.on_games_scroll, add="+")
            canvas.bind("<ButtonRelease-1>", controller.on_games_scroll, add="+")
            canvas.bind("<Configure>", controller.on_games_area_resize, add="+")
        if canvas is not None and scrollbar is not None:
            scrollbar.configure(command=controller.on_games_scrollbar_command)
            scrollbar.bind("<Button-1>", controller.on_games_scrollbar_press, add="+")
            scrollbar.bind("<B1-Motion>", controller.on_games_scrollbar_press, add="+")
            scrollbar.bind("<ButtonRelease-1>", controller.on_games_scrollbar_release, add="+")
        app.root.bind("<ButtonRelease-1>", controller.on_games_scrollbar_release, add="+")
    except Exception:
        (logger or logging.getLogger()).exception("Failed to bind viewport scroll events to controller")


def build_app_startup_runtime_coordinator_deps(app: Any, *, logger=None) -> StartupRuntimeCoordinatorDeps:
    return StartupRuntimeCoordinatorDeps(
        archive_state=app.archive_state,
        gpu_state=app.gpu_state,
        sheet_state=app.sheet_state,
        install_state=app.install_state,
        card_ui_state=app.card_ui_state,
        optiscaler_cache_dir=app.optiscaler_cache_dir,
        fsr4_cache_dir=app.fsr4_cache_dir,
        optipatcher_cache_dir=app.optipatcher_cache_dir,
        specialk_cache_dir=app.specialk_cache_dir,
        ual_cache_dir=app.ual_cache_dir,
        unreal5_cache_dir=app.unreal5_cache_dir,
        manifest_root=app.manifest_root,
        callbacks=StartupRuntimeCallbacks(
            format_gpu_label_text=lambda gpu_info: format_gpu_label_text(app, gpu_info),
            set_gpu_label_text=lambda text: set_gpu_label_text(app, text),
            refresh_archive_info_ui=lambda: refresh_optiscaler_archive_info_ui(app),
            update_install_button_state=lambda: update_install_button_state(app),
            update_sheet_status=lambda: update_sheet_status(app),
            run_post_sheet_startup=app._startup_flow.run_post_sheet_startup,
            mark_post_sheet_startup_done=app._startup_flow.mark_post_sheet_startup_done,
            set_scan_status_message=lambda text="", text_color=None: set_scan_status_message(app, text, text_color),
            set_information_text=lambda text="": set_information_text(app, text),
            update_selected_game_header=lambda: update_selected_game_header(app),
            apply_install_selection_state=lambda state: apply_install_selection_state(app, state),
            set_folder_select_enabled=lambda enabled: set_folder_select_enabled(app, enabled),
            check_app_update=lambda: bool(
                app._app_actions_controller.check_app_update(
                    app.sheet_state.module_download_links,
                    blocked=bool(app.gpu_state.multi_gpu_blocked),
                )
            ),
            should_apply_fsr4_for_game=lambda game=None: should_apply_fsr4_for_game(app, game),
            get_archive_controller=lambda: app._archive_controller,
            clear_found_games=lambda: clear_found_games(app),
        ),
        unknown_gpu_text=app.txt.main.unknown_gpu,
        logger=logger or logging.getLogger(),
    )


def apply_app_startup_window_workaround(
    app: Any,
    *,
    app_ui_config: AppUiRuntimeConfig,
    logger=None,
) -> None:
    apply_startup_window_workaround(
        app.root,
        workaround_active=bool(getattr(app, "_startup_window_workaround_active", False)),
        window_width=int(
            getattr(app, "_startup_window_width", app_ui_config.window_width) or app_ui_config.window_width
        ),
        window_height=int(
            getattr(app, "_startup_window_height", app_ui_config.window_height) or app_ui_config.window_height
        ),
        logger=logger or logging.getLogger(),
    )


def ensure_app_ui_shell(app: Any, *, app_theme: AppThemeBundle) -> AppUiShell:
    ui_shell = getattr(app, "_ui_shell", None)
    if ui_shell is not None:
        return ui_shell

    ui_shell = create_ui_shell(
        app,
        scan_status_text_color=app_theme.scan_status_text_color,
        status_indicator_offline_color=app_theme.status_indicator_offline_color,
        status_indicator_warning_color=app_theme.status_indicator_warning_color,
        status_indicator_loading_color=app_theme.status_indicator_loading_color,
        status_indicator_online_color=app_theme.status_indicator_online_color,
    )
    app._ui_shell = ui_shell
    return ui_shell


def bind_app_root_events(
    app: Any,
    *,
    app_ui_config: AppUiRuntimeConfig,
    logger=None,
) -> None:
    app.root.bind("<Configure>", app._card_viewport_controller.on_root_resize)
    app.root.protocol("WM_DELETE_WINDOW", lambda: request_close(app))
    if app._startup_window_workaround_active:
        app.root.after_idle(
            lambda: apply_app_startup_window_workaround(
                app,
                app_ui_config=app_ui_config,
                logger=logger or logging.getLogger(),
            )
        )
        app.root.after(
            220,
            lambda: apply_app_startup_window_workaround(
                app,
                app_ui_config=app_ui_config,
                logger=logger or logging.getLogger(),
            ),
        )
    app.root.after(250, app._card_viewport_controller.capture_startup_width)


def ensure_app_startup_runtime_coordinator(app: Any, *, logger=None) -> StartupRuntimeCoordinator:
    coordinator = getattr(app, "_startup_runtime_coordinator", None)
    if coordinator is not None:
        return coordinator

    coordinator = create_startup_runtime_coordinator(
        build_app_startup_runtime_coordinator_deps(app, logger=logger or logging.getLogger())
    )
    app._startup_runtime_coordinator = coordinator
    return coordinator


__all__ = [
    "PosterInfraBundle",
    "PresenterBundle",
    "StartupUpdateInfraBundle",
    "configure_app_startup_window",
    "apply_app_startup_window_workaround",
    "bind_app_root_events",
    "bind_app_viewport_scroll_events",
    "build_app_startup_runtime_coordinator_deps",
    "build_app_ui_controller_factory_deps",
    "build_poster_infra",
    "build_presenter_bundle",
    "build_startup_update_infra",
    "ensure_app_startup_runtime_coordinator",
    "ensure_app_ui_shell",
    "initialize_app_controllers",
    "initialize_app_infra",
    "initialize_app_presenters",
    "initialize_app_runtime_startup",
    "initialize_app_runtime_state",
    "initialize_app_ui_startup",
    "initialize_app_ui_controllers",
]
