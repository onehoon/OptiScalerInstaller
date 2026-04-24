from __future__ import annotations

import logging
from typing import Any

from .install_selection_controller import InstallSelectionUiState
from .scan_entry_controller import ScanEntryState


def format_gpu_label_text(app: Any, gpu_info: str) -> str:
    normalized_gpu = str(gpu_info or "").strip() or app.txt.main.unknown_gpu
    return app.txt.main.gpu_label_template.format(gpu=normalized_gpu)


def set_gpu_label_text(app: Any, text: str) -> None:
    widget = getattr(app, "gpu_lbl", None)
    if widget is None:
        return
    if hasattr(widget, "winfo_exists") and callable(widget.winfo_exists) and not widget.winfo_exists():
        return
    widget.configure(text=str(text or ""))


def set_folder_select_enabled(app: Any, enabled: bool) -> None:
    widget = getattr(app, "btn_select_folder", None)
    if widget is None:
        return
    if hasattr(widget, "winfo_exists") and callable(widget.winfo_exists) and not widget.winfo_exists():
        return
    widget.configure(state="normal" if enabled else "disabled")


def request_close(app: Any) -> None:
    controller = getattr(app, "_app_actions_controller", None)
    if controller is None:
        return
    controller.request_close(bool(app.install_state.in_progress))


def shutdown_app(app: Any) -> None:
    controller = getattr(app, "_app_shutdown_controller", None)
    if controller is None:
        return
    controller.shutdown()


def start_game_db_load_async(app: Any) -> None:
    controller = getattr(app, "_game_db_controller", None)
    if controller is None:
        return

    sheet_state = app.sheet_state
    game_db_vendor = str(sheet_state.active_vendor or "default")
    gpu_model = str(getattr(app.gpu_state, "gpu_info", "") or "").strip()
    started = controller.start_load(game_db_vendor, gpu_model)
    if not started:
        return
    logging.info(
        "[APP] Starting Game DB load for vendor=%s gpu=%s",
        game_db_vendor,
        app.gpu_state.gpu_info,
    )


def is_scan_in_progress(app: Any) -> bool:
    controller = getattr(app, "_scan_controller", None)
    return bool(controller and controller.is_scan_in_progress)


def clear_found_games(app: Any) -> None:
    app.found_exe_list = []


def clear_cards_placeholder(app: Any) -> None:
    del app


def pump_poster_queue(app: Any) -> None:
    app._poster_queue.pump()


def start_auto_scan(app: Any) -> None:
    if app.gpu_state.multi_gpu_blocked:
        return
    if app.install_state.in_progress:
        return
    controller = getattr(app, "_scan_controller", None)
    if controller is None:
        return
    controller.start_auto_scan()


def set_game_folder(app: Any, folder_path: str) -> None:
    app.game_folder = str(folder_path or "")


def start_manual_scan_from_folder(app: Any, folder_path: str) -> bool:
    controller = getattr(app, "_scan_controller", None)
    if controller is None:
        return False
    if app.install_state.in_progress:
        return False
    return controller.start_manual_scan(folder_path)


def apply_install_selection_state(app: Any, state: InstallSelectionUiState) -> None:
    install_state = app.install_state
    install_state.popup_confirmed = bool(state.popup_confirmed)
    install_state.precheck_running = bool(state.precheck_running)
    install_state.precheck_ok = bool(state.precheck_ok)
    install_state.precheck_error = str(state.precheck_error or "")
    install_state.precheck_dll_name = str(state.precheck_dll_name or "")


def build_scan_entry_state(app: Any) -> ScanEntryState:
    gpu_state = app.gpu_state
    sheet_state = app.sheet_state
    return ScanEntryState(
        multi_gpu_blocked=bool(gpu_state.multi_gpu_blocked),
        sheet_loading=bool(sheet_state.loading),
        sheet_ready=bool(sheet_state.status),
    )


def select_game_folder(app: Any) -> None:
    controller = getattr(app, "_scan_entry_controller", None)
    if controller is None:
        return
    if app.install_state.in_progress:
        return
    controller.select_game_folder(build_scan_entry_state(app))


def apply_selected_install(app: Any):
    controller = getattr(app, "_install_flow_controller", None)
    if controller is None:
        return None
    return controller.apply_selected_install()


__all__ = [
    "apply_install_selection_state",
    "apply_selected_install",
    "build_scan_entry_state",
    "clear_cards_placeholder",
    "clear_found_games",
    "format_gpu_label_text",
    "is_scan_in_progress",
    "pump_poster_queue",
    "request_close",
    "select_game_folder",
    "set_folder_select_enabled",
    "set_game_folder",
    "set_gpu_label_text",
    "shutdown_app",
    "start_auto_scan",
    "start_game_db_load_async",
    "start_manual_scan_from_folder",
]
