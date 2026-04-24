from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
from queue import Empty, SimpleQueue
from tkinter import filedialog, messagebox
from typing import Any

from installer.data import gpu_bundle_loader, message_loader, sheet_loader
from installer.i18n import build_install_information_text, build_install_selection_popup_text
from installer.install import services as installer_services
from installer.system import gpu_service

from . import gpu_notice
from .app_runtime_actions import (
    apply_install_selection_state,
    pump_poster_queue,
    set_folder_select_enabled,
    set_game_folder,
    shutdown_app,
    start_game_db_load_async,
    start_manual_scan_from_folder,
)
from .app_actions_controller import AppActionCallbacks, AppActionsController
from .app_shutdown_controller import AppShutdownCallbacks, AppShutdownController, AppShutdownStep
from .archive_controller import ArchivePreparationCallbacks, ArchivePreparationController
from .card_runtime_actions import (
    add_game_card_incremental,
    append_found_game,
    apply_selected_game_index,
    clear_cards,
    create_and_place_card,
    hide_empty_label,
    reset_scan_results_for_new_scan,
    restore_rendered_selection,
    run_install_precheck,
)
from .card_render_controller import CardRenderCallbacks, CardRenderController
from .game_db_controller import GameDbControllerCallbacks, GameDbLoadController
from .gpu_flow_controller import GpuFlowCallbacks, GpuFlowController
from .install_flow import InstallFlowController, create_install_flow_controller
from .install_runtime_actions import (
    is_game_supported_for_current_gpu,
    update_install_button_state,
)
from .install_selection_controller import InstallSelectionCallbacks, InstallSelectionController
from .notice_controller import AppNoticeController
from .scan_controller import ScanController, ScanControllerCallbacks
from .scan_entry_controller import ScanEntryCallbacks, ScanEntryController
from .scan_feedback import ScanFeedbackCallbacks, ScanFeedbackController
from .ui_shell_actions import (
    set_information_text,
    set_scan_status_message,
    update_sheet_status,
)


@dataclass(frozen=True)
class AppControllerFactoryConfig:
    create_prefixed_logger: Callable[[str], Any]
    gpu_bundle_url: str
    game_master_url: str
    resource_master_url: str
    message_binding_url: str
    message_center_url: str
    gpu_notice_theme: Any
    max_supported_gpu_count: int
    message_popup_theme: Any
    root_height_fallback: int
    root_width_fallback: int
    supported_games_wiki_url: str
    game_ini_profile_url: str = ""
    game_unreal_ini_profile_url: str = ""
    engine_ini_profile_url: str = ""
    game_xml_profile_url: str = ""
    registry_profile_url: str = ""
    game_json_profile_url: str = ""
    gpu_bundle_debug: bool = False


@dataclass(frozen=True)
class AppControllers:
    app_notice: AppNoticeController
    app_actions: AppActionsController
    app_shutdown: AppShutdownController
    archive: ArchivePreparationController
    game_db: GameDbLoadController
    gpu_flow: GpuFlowController
    scan_feedback: ScanFeedbackController
    scan: ScanController
    scan_entry: ScanEntryController
    install_flow: InstallFlowController
    install_selection: InstallSelectionController
    card_render: CardRenderController


def _call_optional_method(target: Any, attr_name: str, method_name: str, *args, default=None, **kwargs):
    method_target = getattr(target, attr_name, None)
    if method_target is None:
        return default
    return getattr(method_target, method_name)(*args, **kwargs)


def _build_thread_safe_ui_scheduler(root: Any, *, logger: Any = None, poll_ms: int = 25) -> Callable[[Callable[[], None]], None]:
    pending_callbacks: SimpleQueue[Callable[[], None]] = SimpleQueue()
    schedule_logger = logger or logging.getLogger()

    def _drain_pending_callbacks() -> None:
        while True:
            try:
                callback = pending_callbacks.get_nowait()
            except Empty:
                break
            try:
                callback()
            except Exception:
                schedule_logger.exception("[APP] Scheduled UI callback failed")

        try:
            if hasattr(root, "winfo_exists") and callable(root.winfo_exists) and not root.winfo_exists():
                return
            root.after(poll_ms, _drain_pending_callbacks)
        except Exception:
            # App may be closing; callbacks are best-effort.
            return

    try:
        root.after(poll_ms, _drain_pending_callbacks)
    except Exception:
        schedule_logger.debug("[APP] Failed to start UI callback scheduler", exc_info=True)

    def _schedule(callback: Callable[[], None]) -> None:
        pending_callbacks.put(callback)

    return _schedule


def build_app_controllers(app: Any, config: AppControllerFactoryConfig) -> AppControllers:
    # `app` is only unpacked at this assembly boundary. Downstream builders
    # should take explicit dependencies so wiring stays searchable and local.
    ui_schedule = _build_thread_safe_ui_scheduler(app.root, logger=logging.getLogger())
    app_notice = _build_app_notice_controller(app, config)
    app_actions = _build_app_actions_controller(app)
    app_shutdown = _build_app_shutdown_controller(app)
    archive = _build_archive_controller(app, ui_schedule)
    game_db = _build_game_db_controller(
        executor=app._task_executor,
        schedule_ui=ui_schedule,
        callbacks=GameDbControllerCallbacks(
            on_load_complete=lambda result: app._startup_runtime_coordinator.on_game_db_loaded(result),
        ),
        config=config,
    )
    gpu_flow = _build_gpu_flow_controller(
        executor=app._task_executor,
        schedule_ui=ui_schedule,
        callbacks=GpuFlowCallbacks(
            apply_state=lambda state: app._startup_runtime_coordinator.apply_gpu_flow_state(state),
            handle_unsupported_gpu=lambda scan_status_message, info_text: app._startup_runtime_coordinator.handle_unsupported_gpu_block(
                scan_status_message,
                info_text,
            ),
            set_scan_status_message=lambda text="", text_color=None: set_scan_status_message(app, text, text_color),
            update_sheet_status=lambda: update_sheet_status(app),
            update_install_button_state=lambda: update_install_button_state(app),
            start_game_db_load=lambda: start_game_db_load_async(app),
        ),
        root=app.root,
        strings=app.txt,
        config=config,
    )
    scan_feedback = _build_scan_feedback_controller(
        root=app.root,
        callbacks=ScanFeedbackCallbacks(
            set_scan_status_message=lambda text="", text_color=None: set_scan_status_message(app, text, text_color),
            set_select_folder_enabled=lambda enabled: set_folder_select_enabled(app, enabled),
            set_information_text=lambda text="": set_information_text(app, text),
            enqueue_startup_popup=app._startup_flow.enqueue_popup,
            run_next_startup_popup=app._startup_flow.run_next_popup,
        ),
        config=config,
        popup_title=app.txt.main.scan_result_title,
        popup_confirm_text=app.txt.common.ok,
        scanning_text=app.txt.main.scanning,
        manual_scan_no_results_text=app.txt.main.manual_scan_no_results,
        auto_scan_no_results_text=app.txt.main.auto_scan_no_results,
        select_game_hint_text=app.txt.main.select_game_hint,
    )
    scan = _build_scan_controller(
        executor=app._scan_executor,
        schedule_ui=ui_schedule,
        callbacks=ScanControllerCallbacks(
            prepare_scan_ui=scan_feedback.prepare_scan_ui,
            reset_scan_results=lambda: reset_scan_results_for_new_scan(app),
            add_game_card=lambda game: add_game_card_incremental(app, game),
            finish_scan_ui=scan_feedback.finish_scan_ui,
            pump_poster_queue=lambda: pump_poster_queue(app),
            show_auto_scan_empty_popup=scan_feedback.enqueue_initial_auto_scan_empty_popup,
            show_manual_scan_empty_popup=scan_feedback.show_manual_scan_empty_popup,
            show_select_game_hint=scan_feedback.show_select_game_hint,
        ),
        get_game_db=lambda: app.sheet_state.game_db,
        get_lang=lambda: app.lang,
        is_game_supported=lambda game_data: is_game_supported_for_current_gpu(app, game_data),
    )
    scan_entry = _build_scan_entry_controller(
        callbacks=ScanEntryCallbacks(
            ask_directory=filedialog.askdirectory,
            set_selected_folder=lambda folder_path: set_game_folder(app, folder_path),
            start_manual_scan=lambda folder_path: start_manual_scan_from_folder(app, folder_path),
        ),
    )
    install_flow = _build_install_flow_controller(app, config)
    install_selection = _build_install_selection_controller(
        schedule=app.root.after_idle,
        callbacks=InstallSelectionCallbacks(
            apply_selected_index=lambda index: apply_selected_game_index(app, index),
            set_information_text=lambda text="": set_information_text(app, text),
            build_information_text=lambda game: build_install_information_text(
                game,
                lang=app.lang,
                stage="install_pre",
                rtss_game_overlay_notice=_resolve_rtss_game_overlay_notice(
                    game=game,
                    install_state=app.install_state,
                    dialogs_strings=app.txt.dialogs,
                ),
            ),
            apply_ui_state=lambda state: apply_install_selection_state(app, state),
            update_install_button_state=lambda: update_install_button_state(app),
            run_precheck=lambda game: run_install_precheck(app, game),
            get_selection_popup_message=lambda game: _build_selection_popup_message(
                game=game,
                lang=app.lang,
                install_state=app.install_state,
                dialogs_strings=app.txt.dialogs,
            ),
            show_selection_popup=app_notice.show_selection_popup,
            show_precheck_popup=app_notice.show_precheck_popup,
        ),
    )
    viewport = app._card_viewport_controller
    card_render = _build_card_render_controller(
        callbacks=CardRenderCallbacks(
            append_found_game=lambda game: append_found_game(app, game),
            clear_cards=lambda keep_selection=False: clear_cards(app, keep_selection),
            hide_empty_label=lambda: hide_empty_label(app),
            configure_card_columns=viewport.configure_card_columns,
            create_and_place_card=lambda index, game, placement: create_and_place_card(app, index, game, placement),
            fit_cards_to_visible_width=viewport.fit_cards_to_visible_width,
            restore_selection=lambda index, game: restore_rendered_selection(app, index, game),
            schedule_scrollregion_refresh=viewport.schedule_games_scrollregion_refresh,
            pump_poster_queue=lambda: pump_poster_queue(app),
        ),
    )
    return AppControllers(
        app_notice=app_notice,
        app_actions=app_actions,
        app_shutdown=app_shutdown,
        archive=archive,
        game_db=game_db,
        gpu_flow=gpu_flow,
        scan_feedback=scan_feedback,
        scan=scan,
        scan_entry=scan_entry,
        install_flow=install_flow,
        install_selection=install_selection,
        card_render=card_render,
    )


def bind_app_controllers(app: Any, controllers: AppControllers) -> None:
    app._app_notice_controller = controllers.app_notice
    app._app_actions_controller = controllers.app_actions
    app._app_shutdown_controller = controllers.app_shutdown
    app._archive_controller = controllers.archive
    app._game_db_controller = controllers.game_db
    app._gpu_flow_controller = controllers.gpu_flow
    app._scan_feedback_controller = controllers.scan_feedback
    app._scan_controller = controllers.scan
    app._scan_entry_controller = controllers.scan_entry
    app._install_flow_controller = controllers.install_flow
    app._install_selection_controller = controllers.install_selection
    app._card_render_controller = controllers.card_render


def _build_app_notice_controller(app: Any, config: AppControllerFactoryConfig) -> AppNoticeController:
    return AppNoticeController(
        root=app.root,
        popup_theme=config.message_popup_theme,
        schedule_idle=app.root.after_idle,
        installer_notice_title=app.txt.dialogs.installer_notice_title,
        warning_title=app.txt.common.warning,
        notice_title=app.txt.common.notice,
        error_title=app.txt.common.error,
        confirm_text=app.txt.common.ok,
        wiki_url=config.supported_games_wiki_url,
        wiki_not_configured_detail=app.txt.dialogs.wiki_not_configured_detail,
        wiki_open_failed_detail=app.txt.dialogs.wiki_open_failed_detail,
        installation_completed_text=app.txt.dialogs.installation_completed,
        root_width_fallback=config.root_width_fallback,
        root_height_fallback=config.root_height_fallback,
        logger=logging.getLogger(),
    )


def _build_app_actions_controller(app: Any) -> AppActionsController:
    return AppActionsController(
        callbacks=AppActionCallbacks(
            show_close_while_installing_warning=lambda: messagebox.showwarning(
                app.txt.common.warning,
                app.txt.dialogs.close_while_installing_body,
            ),
            perform_shutdown=lambda: shutdown_app(app),
            check_for_update=lambda module_download_links, blocked: app._app_update_manager.check_for_update(
                module_download_links,
                blocked=blocked,
            ),
        ),
    )


def _build_app_shutdown_controller(app: Any) -> AppShutdownController:
    return AppShutdownController(
        callbacks=AppShutdownCallbacks(
            best_effort_steps=(
                AppShutdownStep(
                    "shutdown header status presenter",
                    lambda: _call_optional_method(app, "_header_status_presenter", "shutdown"),
                ),
                AppShutdownStep(
                    "shutdown poster queue",
                    lambda: _call_optional_method(app, "_poster_queue", "shutdown"),
                ),
                AppShutdownStep(
                    "shutdown image executor",
                    lambda: _call_optional_method(
                        app,
                        "_image_executor",
                        "shutdown",
                        wait=False,
                        cancel_futures=True,
                    ),
                ),
                AppShutdownStep(
                    "shutdown task executor",
                    lambda: _call_optional_method(
                        app,
                        "_task_executor",
                        "shutdown",
                        wait=False,
                        cancel_futures=True,
                    ),
                ),
                AppShutdownStep(
                    "shutdown scan executor",
                    lambda: _call_optional_method(
                        app,
                        "_scan_executor",
                        "shutdown",
                        wait=False,
                        cancel_futures=True,
                    ),
                ),
                AppShutdownStep(
                    "shutdown optiscaler prepare executor",
                    lambda: _call_optional_method(
                        app,
                        "_optiscaler_prepare_executor",
                        "shutdown",
                        wait=False,
                        cancel_futures=True,
                    ),
                ),
                AppShutdownStep(
                    "shutdown download executor",
                    lambda: _call_optional_method(
                        app,
                        "_download_executor",
                        "shutdown",
                        wait=False,
                        cancel_futures=True,
                    ),
                ),
                AppShutdownStep(
                    "close poster loader",
                    lambda: _call_optional_method(app, "_poster_loader", "close"),
                ),
                AppShutdownStep(
                    "shutdown app update manager",
                    lambda: _call_optional_method(app, "_app_update_manager", "shutdown"),
                ),
            ),
            destroy_root=app.root.destroy,
        ),
        logger=logging.getLogger(),
    )


def _build_archive_controller(app: Any, schedule_ui: Callable[[Callable[[], None]], None]) -> ArchivePreparationController:
    return ArchivePreparationController(
        executor=app._download_executor,
        optiscaler_executor=getattr(app, "_optiscaler_prepare_executor", None),
        schedule=schedule_ui,
        callbacks=ArchivePreparationCallbacks(
            on_optiscaler_state_changed=lambda state: app._startup_runtime_coordinator.on_optiscaler_archive_state_changed(state),
            on_fsr4_state_changed=lambda state: app._startup_runtime_coordinator.on_fsr4_archive_state_changed(state),
            on_optipatcher_state_changed=lambda state: app._startup_runtime_coordinator.on_optipatcher_archive_state_changed(state),
            on_specialk_state_changed=lambda state: app._startup_runtime_coordinator.on_specialk_archive_state_changed(state),
            on_ual_state_changed=lambda state: app._startup_runtime_coordinator.on_ual_archive_state_changed(state),
            on_unreal5_state_changed=lambda state: app._startup_runtime_coordinator.on_unreal5_archive_state_changed(state),
        ),
        download_to_file=installer_services.download_to_file,
        manifest_root=app.manifest_root,
        logger=logging.getLogger(),
    )


def _require_remote_json_url(name: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} must be configured for remote runtime loading.")
    return normalized


def _build_game_db_controller(
    *,
    executor: Any,
    schedule_ui: Callable[[Callable[[], None]], None],
    callbacks: GameDbControllerCallbacks,
    config: AppControllerFactoryConfig,
) -> GameDbLoadController:
    game_master_url = _require_remote_json_url("game_master_url", config.game_master_url)
    resource_master_url = _require_remote_json_url("resource_master_url", config.resource_master_url)
    message_center_url = _require_remote_json_url("message_center_url", config.message_center_url)
    message_binding_url = _require_remote_json_url("message_binding_url", config.message_binding_url)
    game_ini_profile_url = _require_remote_json_url("game_ini_profile_url", config.game_ini_profile_url)
    game_unreal_ini_profile_url = str(config.game_unreal_ini_profile_url or "").strip()
    engine_ini_profile_url = _require_remote_json_url("engine_ini_profile_url", config.engine_ini_profile_url)
    game_xml_profile_url = _require_remote_json_url("game_xml_profile_url", config.game_xml_profile_url)
    registry_profile_url = _require_remote_json_url("registry_profile_url", config.registry_profile_url)
    game_json_profile_url = str(config.game_json_profile_url or "").strip()
    load_game_db = lambda: sheet_loader.load_game_db_from_remote_json(game_master_url)
    load_module_download_links = lambda: sheet_loader.load_module_download_links_from_remote_json(resource_master_url)
    load_gpu_bundle = lambda base_url, vendor, gpu_model: gpu_bundle_loader.load_supported_game_bundle(
        base_url,
        vendor,
        gpu_model,
        debug=config.gpu_bundle_debug,
        logger=logging.getLogger(),
    )

    return GameDbLoadController(
        executor=executor,
        schedule=schedule_ui,
        callbacks=callbacks,
        load_game_db=load_game_db,
        load_module_download_links=load_module_download_links,
        message_center_url=message_center_url,
        message_binding_url=message_binding_url,
        load_message_center=message_loader.load_message_center,
        load_message_binding=message_loader.load_message_binding,
        build_message_repository=message_loader.build_message_repository,
        materialize_bound_messages=message_loader.materialize_bound_messages_into_game_db,
        gpu_bundle_url=config.gpu_bundle_url,
        load_gpu_bundle=load_gpu_bundle,
        merge_gpu_bundle=gpu_bundle_loader.merge_gpu_bundle_into_game_db,
        game_ini_profile_url=game_ini_profile_url,
        game_unreal_ini_profile_url=game_unreal_ini_profile_url,
        engine_ini_profile_url=engine_ini_profile_url,
        game_xml_profile_url=game_xml_profile_url,
        registry_profile_url=registry_profile_url,
        game_json_profile_url=game_json_profile_url,
        logger=logging.getLogger(),
    )


def _build_gpu_flow_controller(
    *,
    executor: Any,
    schedule_ui: Callable[[Callable[[], None]], None],
    callbacks: GpuFlowCallbacks,
    root: Any,
    strings: Any,
    config: AppControllerFactoryConfig,
) -> GpuFlowController:
    return GpuFlowController(
        executor=executor,
        schedule=schedule_ui,
        callbacks=callbacks,
        unknown_gpu_text=strings.main.unknown_gpu,
        waiting_for_gpu_selection_text=strings.main.waiting_for_gpu_selection,
        unsupported_gpu_message=strings.gpu.unsupported_message,
        unsupported_gpu_info_text=gpu_notice.get_unsupported_gpu_message(strings),
        detect_gpu_context=gpu_service.detect_gpu_context,
        select_dual_gpu_adapter=lambda adapters: gpu_notice.select_dual_gpu_adapter(
            root=root,
            adapters=adapters,
            strings=strings,
            theme=config.gpu_notice_theme,
        ),
        show_unsupported_gpu_notice=lambda: gpu_notice.show_unsupported_gpu_notice(
            root,
            strings,
            config.gpu_notice_theme,
        ),
        max_supported_gpu_count=config.max_supported_gpu_count,
        logger=logging.getLogger(),
    )


def _build_scan_feedback_controller(
    *,
    root: Any,
    callbacks: ScanFeedbackCallbacks,
    config: AppControllerFactoryConfig,
    popup_title: str,
    popup_confirm_text: str,
    scanning_text: str,
    manual_scan_no_results_text: str,
    auto_scan_no_results_text: str,
    select_game_hint_text: str,
) -> ScanFeedbackController:
    return ScanFeedbackController(
        root=root,
        callbacks=callbacks,
        popup_theme=config.message_popup_theme,
        popup_title=popup_title,
        popup_confirm_text=popup_confirm_text,
        scanning_text=scanning_text,
        manual_scan_no_results_text=manual_scan_no_results_text,
        auto_scan_no_results_text=auto_scan_no_results_text,
        select_game_hint_text=select_game_hint_text,
        root_width_fallback=config.root_width_fallback,
        root_height_fallback=config.root_height_fallback,
    )


def _build_scan_controller(
    *,
    executor: Any,
    schedule_ui: Callable[[Callable[[], None]], None],
    callbacks: ScanControllerCallbacks,
    get_game_db: Callable[[], dict[str, dict[str, Any]]],
    get_lang: Callable[[], Any],
    is_game_supported: Callable[[dict[str, Any]], bool],
) -> ScanController:
    return ScanController(
        executor=executor,
        schedule=schedule_ui,
        callbacks=callbacks,
        get_game_db=get_game_db,
        get_lang=get_lang,
        is_game_supported=is_game_supported,
        logger=logging.getLogger(),
    )


def _build_scan_entry_controller(*, callbacks: ScanEntryCallbacks) -> ScanEntryController:
    return ScanEntryController(
        callbacks=callbacks,
    )


def _build_install_flow_controller(app: Any, config: AppControllerFactoryConfig) -> InstallFlowController:
    return create_install_flow_controller(
        app,
        create_prefixed_logger=config.create_prefixed_logger,
    )


def _build_selection_popup_message(
    *,
    game: Mapping[str, Any],
    lang: str,
    install_state: Any,
    dialogs_strings: Any,
) -> str:
    rtss_notice = _resolve_rtss_game_overlay_notice(
        game=game,
        install_state=install_state,
        dialogs_strings=dialogs_strings,
    )

    return build_install_selection_popup_text(
        game,
        lang=lang,
        rtss_game_overlay_notice=rtss_notice,
    )


def _resolve_rtss_game_overlay_notice(
    *,
    game: Mapping[str, Any],
    install_state: Any,
    dialogs_strings: Any,
) -> str:
    if bool((game or {}).get("rtss_overlay")) and bool(getattr(install_state, "rtss_installed", False)) and bool(
        getattr(install_state, "rtss_profiles_global_exists", False)
    ):
        return str(getattr(dialogs_strings, "rtss_game_overlay_notice", "") or "")
    return ""


def _build_install_selection_controller(
    *,
    schedule: Callable[[Callable[[], None]], Any],
    callbacks: InstallSelectionCallbacks,
) -> InstallSelectionController:
    return InstallSelectionController(
        schedule=schedule,
        callbacks=callbacks,
        logger=logging.getLogger(),
    )


def _build_card_render_controller(*, callbacks: CardRenderCallbacks) -> CardRenderController:
    return CardRenderController(
        callbacks=callbacks,
    )


__all__ = [
    "AppControllerFactoryConfig",
    "AppControllers",
    "bind_app_controllers",
    "build_app_controllers",
]
