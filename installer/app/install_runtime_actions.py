from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from installer.system import gpu_service

from .bootstrap_runtime import MAX_SUPPORTED_GPU_COUNT
from .game_support_policy import is_game_supported_for_vendor
from .install_state import (
    build_install_button_state_inputs as build_install_button_state_inputs_bundle,
    build_selected_game_snapshot,
)
from .install_ui_state import InstallButtonStateInputs, compute_install_button_state


def is_multi_gpu_block_active(app: Any) -> bool:
    return app.gpu_state.gpu_count > MAX_SUPPORTED_GPU_COUNT


def is_game_supported_for_current_gpu(app: Any, game_data: Mapping[str, Any]) -> bool:
    return is_game_supported_for_vendor(
        game_data,
        vendor=str(app.sheet_state.active_vendor or ""),
        gpu_info=str(app.gpu_state.gpu_info or ""),
    )


def matches_fsr4_skip_rule(app: Any, target_text: str) -> bool:
    return gpu_service.matches_gpu_rule(app._app_paths.fsr4_skip_gpu_rule, target_text)


def should_apply_fsr4_for_game(app: Any, game_data: Mapping[str, Any] | None = None) -> bool:
    if matches_fsr4_skip_rule(app, app.gpu_state.gpu_info):
        return False

    if isinstance(game_data, Mapping):
        supported_gpu_rule = str(game_data.get("supported_gpu", "") or "").strip()
        if supported_gpu_rule and matches_fsr4_skip_rule(app, supported_gpu_rule):
            return False

    return True


def build_selected_game_snapshot_from_runtime(app: Any):
    return build_selected_game_snapshot(
        app.found_exe_list,
        app.card_ui_state.selected_game_index,
        getattr(app, "lang", "en"),
    )


def build_install_button_state_inputs(app: Any) -> InstallButtonStateInputs:
    gpu_state = app.gpu_state
    sheet_state = app.sheet_state
    install_state = app.install_state
    archive_state = app.archive_state
    selection = build_selected_game_snapshot_from_runtime(app)
    app_update_manager = getattr(app, "_app_update_manager", None)
    return build_install_button_state_inputs_bundle(
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
        is_game_supported=lambda game: is_game_supported_for_current_gpu(app, game),
        should_apply_fsr4=lambda game: should_apply_fsr4_for_game(app, game),
    )


def tick_loading_blink(app: Any) -> None:
    app._loading_blink_job = None
    if not hasattr(app, "apply_btn"):
        return
    button_state = compute_install_button_state(build_install_button_state_inputs(app))
    if button_state.reason_code != "sheet_loading":
        return
    loading_text = app.txt.main.loading_button
    current_text = app.apply_btn.cget("text")
    app.apply_btn.configure(text="" if current_text == loading_text else loading_text)
    app._loading_blink_job = app.root.after(600, lambda: tick_loading_blink(app))


def update_install_button_state(app: Any) -> None:
    if not hasattr(app, "apply_btn"):
        return

    button_state = compute_install_button_state(build_install_button_state_inputs(app))
    can_install = bool(button_state.enabled)
    is_sheet_loading = button_state.reason_code == "sheet_loading"

    blink_job = getattr(app, "_loading_blink_job", None)
    if blink_job is not None:
        app.root.after_cancel(blink_job)
        app._loading_blink_job = None

    if button_state.show_installing:
        button_text = app.txt.main.installing_button
    elif can_install:
        button_text = app.txt.main.install_button
    elif is_sheet_loading:
        button_text = app.txt.main.loading_button
    else:
        button_text = ""

    app.apply_btn.configure(
        state="normal" if can_install else "disabled",
        text=button_text,
        fg_color=app._app_theme.install_button_color if can_install else app._app_theme.install_button_disabled_color,
        hover_color=(
            app._app_theme.install_button_hover_color
            if can_install
            else app._app_theme.install_button_disabled_color
        ),
        border_color=(
            app._app_theme.install_button_border_color
            if can_install
            else app._app_theme.install_button_border_disabled_color
        ),
    )

    if is_sheet_loading:
        app._loading_blink_job = app.root.after(600, lambda: tick_loading_blink(app))


__all__ = [
    "build_selected_game_snapshot_from_runtime",
    "build_install_button_state_inputs",
    "is_game_supported_for_current_gpu",
    "is_multi_gpu_block_active",
    "matches_fsr4_skip_rule",
    "should_apply_fsr4_for_game",
    "tick_loading_blink",
    "update_install_button_state",
]
