from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any

from installer.data import sheet_loader
from installer.i18n import pick_sheet_text
from installer.install import services as installer_services
from installer.system import gpu_service

from . import gpu_notice
from .app_actions_controller import AppActionCallbacks, AppActionsController
from .app_shutdown_controller import AppShutdownCallbacks, AppShutdownController, AppShutdownStep
from .archive_controller import ArchivePreparationCallbacks, ArchivePreparationController
from .card_render_controller import CardRenderCallbacks, CardRenderController
from .game_db_controller import GameDbControllerCallbacks, GameDbLoadController
from .gpu_flow_controller import GpuFlowCallbacks, GpuFlowController
from .install_flow import InstallFlowController, create_install_flow_controller
from .install_selection_controller import InstallSelectionCallbacks, InstallSelectionController
from .notice_controller import AppNoticeController
from .scan_controller import ScanController, ScanControllerCallbacks
from .scan_entry_controller import ScanEntryCallbacks, ScanEntryController
from .scan_feedback import ScanFeedbackCallbacks, ScanFeedbackController


@dataclass(frozen=True)
class AppControllerFactoryConfig:
    assets_dir: Path
    create_prefixed_logger: Callable[[str], Any]
    default_sheet_gid: int
    download_links_gid: int
    gpu_notice_theme: Any
    gpu_vendor_db_gids: Mapping[str, int]
    max_supported_gpu_count: int
    message_popup_theme: Any
    root_height_fallback: int
    root_width_fallback: int
    optipatcher_url: str
    rtss_theme: Any
    sheet_id: str
    supported_games_wiki_url: str
    use_korean: bool


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


def build_app_controllers(app: Any, config: AppControllerFactoryConfig) -> AppControllers:
    app_notice = _build_app_notice_controller(app, config)
    app_actions = _build_app_actions_controller(app, config)
    app_shutdown = _build_app_shutdown_controller(app)
    archive = _build_archive_controller(app)
    game_db = _build_game_db_controller(app, config)
    gpu_flow = _build_gpu_flow_controller(app, config)
    scan_feedback = _build_scan_feedback_controller(app, config)
    scan = _build_scan_controller(app, scan_feedback)
    scan_entry = _build_scan_entry_controller(app)
    install_flow = _build_install_flow_controller(app, config)
    install_selection = _build_install_selection_controller(app, install_flow)
    card_render = _build_card_render_controller(app, config)
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


def _build_app_actions_controller(app: Any, config: AppControllerFactoryConfig) -> AppActionsController:
    return AppActionsController(
        root=app.root,
        callbacks=AppActionCallbacks(
            show_close_while_installing_warning=lambda: messagebox.showwarning(
                app.txt.common.warning,
                app.txt.dialogs.close_while_installing_body,
            ),
            perform_shutdown=app._shutdown_app,
            check_for_update=lambda module_download_links, blocked: app._app_update_manager.check_for_update(
                module_download_links,
                blocked=blocked,
            ),
            create_prefixed_logger=config.create_prefixed_logger,
        ),
        use_korean=config.use_korean,
        assets_dir=config.assets_dir,
        rtss_theme=config.rtss_theme,
    )


def _build_app_shutdown_controller(app: Any) -> AppShutdownController:
    return AppShutdownController(
        callbacks=AppShutdownCallbacks(
            best_effort_steps=(
                AppShutdownStep(
                    "shutdown header status presenter",
                    lambda: app._call_optional_method("_header_status_presenter", "shutdown"),
                ),
                AppShutdownStep(
                    "shutdown poster queue",
                    lambda: app._call_optional_method("_poster_queue", "shutdown"),
                ),
                AppShutdownStep(
                    "shutdown image executor",
                    lambda: app._call_optional_method(
                        "_image_executor",
                        "shutdown",
                        wait=False,
                        cancel_futures=True,
                    ),
                ),
                AppShutdownStep(
                    "shutdown task executor",
                    lambda: app._call_optional_method(
                        "_task_executor",
                        "shutdown",
                        wait=False,
                        cancel_futures=True,
                    ),
                ),
                AppShutdownStep(
                    "shutdown download executor",
                    lambda: app._call_optional_method(
                        "_download_executor",
                        "shutdown",
                        wait=False,
                        cancel_futures=True,
                    ),
                ),
                AppShutdownStep(
                    "close poster loader",
                    lambda: app._call_optional_method("_poster_loader", "close"),
                ),
                AppShutdownStep(
                    "shutdown app update manager",
                    lambda: app._call_optional_method("_app_update_manager", "shutdown"),
                ),
            ),
            destroy_root=app.root.destroy,
        ),
        logger=logging.getLogger(),
    )


def _build_archive_controller(app: Any) -> ArchivePreparationController:
    return ArchivePreparationController(
        executor=app._download_executor,
        schedule=lambda callback: app.root.after(0, callback),
        callbacks=ArchivePreparationCallbacks(
            on_optiscaler_state_changed=app._on_optiscaler_archive_state_changed,
            on_fsr4_state_changed=app._on_fsr4_archive_state_changed,
            on_optipatcher_state_changed=app._on_optipatcher_archive_state_changed,
            on_specialk_state_changed=app._on_specialk_archive_state_changed,
            on_ual_state_changed=app._on_ual_archive_state_changed,
            on_unreal5_state_changed=app._on_unreal5_archive_state_changed,
        ),
        download_to_file=installer_services.download_to_file,
        manifest_root=app.manifest_root,
        logger=logging.getLogger(),
    )


def _build_game_db_controller(app: Any, config: AppControllerFactoryConfig) -> GameDbLoadController:
    return GameDbLoadController(
        executor=app._task_executor,
        schedule=lambda callback: app.root.after(0, callback),
        callbacks=GameDbControllerCallbacks(
            on_load_complete=app._on_game_db_loaded,
        ),
        spreadsheet_id=config.sheet_id,
        download_links_gid=config.download_links_gid,
        load_game_db=sheet_loader.load_game_db_from_public_sheet,
        load_module_download_links=sheet_loader.load_module_download_links_from_public_sheet,
        logger=logging.getLogger(),
    )


def _build_gpu_flow_controller(app: Any, config: AppControllerFactoryConfig) -> GpuFlowController:
    return GpuFlowController(
        executor=app._task_executor,
        schedule=lambda callback: app.root.after(0, callback),
        callbacks=GpuFlowCallbacks(
            apply_state=app._apply_gpu_flow_state,
            handle_unsupported_gpu=app._handle_unsupported_gpu_block,
            set_scan_status_message=app._set_scan_status_message,
            update_sheet_status=app._update_sheet_status,
            update_install_button_state=app._update_install_button_state,
            start_game_db_load=app._start_game_db_load_async,
        ),
        vendor_db_gids=config.gpu_vendor_db_gids,
        default_gid=config.default_sheet_gid,
        unknown_gpu_text=app.txt.main.unknown_gpu,
        waiting_for_gpu_selection_text=app.txt.main.waiting_for_gpu_selection,
        unsupported_gpu_message=app.txt.gpu.unsupported_message,
        unsupported_gpu_info_text=gpu_notice.get_unsupported_gpu_message(app.txt),
        detect_gpu_context=gpu_service.detect_gpu_context,
        select_dual_gpu_adapter=lambda adapters: gpu_notice.select_dual_gpu_adapter(
            root=app.root,
            adapters=adapters,
            strings=app.txt,
            theme=config.gpu_notice_theme,
        ),
        show_unsupported_gpu_notice=lambda: gpu_notice.show_unsupported_gpu_notice(
            app.root,
            app.txt,
            config.gpu_notice_theme,
        ),
        max_supported_gpu_count=config.max_supported_gpu_count,
        logger=logging.getLogger(),
    )


def _build_scan_feedback_controller(app: Any, config: AppControllerFactoryConfig) -> ScanFeedbackController:
    return ScanFeedbackController(
        root=app.root,
        callbacks=ScanFeedbackCallbacks(
            set_scan_status_message=app._set_scan_status_message,
            set_select_folder_enabled=lambda enabled: app.btn_select_folder.configure(
                state="normal" if enabled else "disabled"
            ),
            set_information_text=app._set_information_text,
            enqueue_startup_popup=lambda popup_id, priority, show_callback, blocking=False: app._startup_flow.enqueue_popup(
                popup_id,
                priority=priority,
                show_callback=show_callback,
                blocking=blocking,
            ),
            run_next_startup_popup=app._startup_flow.run_next_popup,
        ),
        popup_theme=config.message_popup_theme,
        popup_title=app.txt.main.scan_result_title,
        popup_confirm_text=app.txt.common.ok,
        scanning_text=app.txt.main.scanning,
        manual_scan_no_results_text=app.txt.main.manual_scan_no_results,
        auto_scan_no_results_text=app.txt.main.auto_scan_no_results,
        select_game_hint_text=app.txt.main.select_game_hint,
        root_width_fallback=config.root_width_fallback,
        root_height_fallback=config.root_height_fallback,
        logger=logging.getLogger(),
    )


def _build_scan_controller(app: Any, scan_feedback: ScanFeedbackController) -> ScanController:
    return ScanController(
        executor=app._task_executor,
        schedule=lambda callback: app.root.after(0, callback),
        callbacks=ScanControllerCallbacks(
            prepare_scan_ui=scan_feedback.prepare_scan_ui,
            reset_scan_results=app._reset_scan_results_for_new_scan,
            add_game_card=app._add_game_card_incremental,
            finish_scan_ui=scan_feedback.finish_scan_ui,
            pump_poster_queue=app._pump_poster_queue,
            show_auto_scan_empty_popup=scan_feedback.enqueue_initial_auto_scan_empty_popup,
            show_manual_scan_empty_popup=scan_feedback.show_manual_scan_empty_popup,
            show_select_game_hint=scan_feedback.show_select_game_hint,
        ),
        get_game_db=lambda: app.sheet_state.game_db,
        get_lang=lambda: app.lang,
        is_game_supported=app._is_game_supported_for_current_gpu,
        logger=logging.getLogger(),
    )


def _build_scan_entry_controller(app: Any) -> ScanEntryController:
    return ScanEntryController(
        callbacks=ScanEntryCallbacks(
            show_info=messagebox.showinfo,
            show_error=messagebox.showerror,
            ask_directory=filedialog.askdirectory,
            set_selected_folder=app._set_game_folder,
            start_manual_scan=app._start_manual_scan_from_folder,
        ),
        game_db_loading_title=app.txt.dialogs.game_db_loading_title,
        game_db_loading_body=app.txt.dialogs.game_db_loading_body,
        game_db_error_title=app.txt.dialogs.game_db_error_title,
        game_db_error_body=app.txt.dialogs.game_db_error_body,
    )


def _build_install_flow_controller(app: Any, config: AppControllerFactoryConfig) -> InstallFlowController:
    return create_install_flow_controller(
        app,
        optipatcher_url=config.optipatcher_url,
        create_prefixed_logger=config.create_prefixed_logger,
    )


def _build_install_selection_controller(app: Any, install_flow: InstallFlowController) -> InstallSelectionController:
    return InstallSelectionController(
        schedule=lambda callback: app.root.after_idle(callback),
        callbacks=InstallSelectionCallbacks(
            apply_selected_index=app._apply_selected_game_index,
            set_information_text=app._set_information_text,
            apply_ui_state=app._apply_install_selection_state,
            update_install_button_state=app._update_install_button_state,
            run_precheck=install_flow.run_install_precheck,
            get_selection_popup_message=lambda game: pick_sheet_text(game, "popup", app.lang),
            show_selection_popup=app._show_game_selection_popup,
            show_precheck_popup=app._show_precheck_popup,
        ),
        logger=logging.getLogger(),
    )


def _build_card_render_controller(app: Any, config: AppControllerFactoryConfig) -> CardRenderController:
    viewport = app._card_viewport_controller
    return CardRenderController(
        callbacks=CardRenderCallbacks(
            append_found_game=app._append_found_game,
            clear_cards=app._clear_cards,
            hide_empty_label=app._hide_empty_label,
            configure_card_columns=viewport.configure_card_columns,
            create_and_place_card=app._create_and_place_card,
            fit_cards_to_visible_width=viewport.fit_cards_to_visible_width,
            restore_selection=app._restore_rendered_selection,
            schedule_scrollregion_refresh=viewport.schedule_games_scrollregion_refresh,
            pump_poster_queue=app._pump_poster_queue,
        )
    )


__all__ = [
    "AppControllerFactoryConfig",
    "AppControllers",
    "bind_app_controllers",
    "build_app_controllers",
]
