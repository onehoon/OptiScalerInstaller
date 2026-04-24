from __future__ import annotations

from collections.abc import Callable
import logging
from tkinter import filedialog
from typing import Any

from ..app_runtime_actions import (
    pump_poster_queue,
    set_folder_select_enabled,
    set_game_folder,
    start_manual_scan_from_folder,
)
from ..card_runtime_actions import add_game_card_incremental, reset_scan_results_for_new_scan
from ..install_runtime_actions import is_game_supported_for_current_gpu
from ..scan_controller import ScanController, ScanControllerCallbacks
from ..scan_entry_controller import ScanEntryCallbacks, ScanEntryController
from ..scan_feedback import ScanFeedbackCallbacks, ScanFeedbackController
from ..ui_shell_actions import set_information_text, set_scan_status_message


def build_scan_controllers(
    app: Any,
    config: Any,
    *,
    schedule_ui: Callable[[Callable[[], None]], None],
) -> tuple[ScanFeedbackController, ScanController, ScanEntryController]:
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
        schedule_ui=schedule_ui,
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
    return scan_feedback, scan, scan_entry


def _build_scan_feedback_controller(
    *,
    root: Any,
    callbacks: ScanFeedbackCallbacks,
    config: Any,
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


__all__ = [
    "build_scan_controllers",
]
