from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InstallButtonStateInputs:
    multi_gpu_blocked: bool
    gpu_selection_pending: bool
    sheet_ready: bool
    sheet_loading: bool
    install_in_progress: bool
    app_update_in_progress: bool
    has_valid_game: bool
    has_supported_gpu: bool
    install_precheck_running: bool
    install_precheck_ok: bool
    optiscaler_archive_ready: bool
    optiscaler_archive_downloading: bool
    fsr4_ready: bool
    game_popup_confirmed: bool


@dataclass(frozen=True)
class InstallButtonState:
    enabled: bool
    show_installing: bool
    reason_code: str = ""


def _iter_install_button_checks(inputs: InstallButtonStateInputs):
    return (
        ("multi_gpu_blocked", not inputs.multi_gpu_blocked),
        ("gpu_selection_pending", not inputs.gpu_selection_pending),
        ("sheet_loading", not inputs.sheet_loading),
        ("sheet_not_ready", inputs.sheet_ready),
        ("install_in_progress", not inputs.install_in_progress),
        ("app_update_in_progress", not inputs.app_update_in_progress),
        ("no_game_selected", inputs.has_valid_game),
        ("install_precheck_running", not inputs.install_precheck_running),
        ("precheck_incomplete", inputs.install_precheck_ok),
        ("optiscaler_archive_downloading", not inputs.optiscaler_archive_downloading),
        ("optiscaler_archive_not_ready", inputs.optiscaler_archive_ready),
        ("fsr4_not_ready", inputs.fsr4_ready),
        ("unsupported_gpu", inputs.has_supported_gpu),
        ("confirm_popup_required", inputs.game_popup_confirmed),
    )


def _resolve_install_button_reason_code(inputs: InstallButtonStateInputs) -> str:
    return next((code for code, passed in _iter_install_button_checks(inputs) if not passed), "")


def compute_install_button_state(inputs: InstallButtonStateInputs) -> InstallButtonState:
    reason_code = _resolve_install_button_reason_code(inputs)
    return InstallButtonState(
        enabled=not bool(reason_code),
        show_installing=bool(inputs.install_in_progress),
        reason_code=reason_code,
    )


__all__ = [
    "InstallButtonState",
    "InstallButtonStateInputs",
    "compute_install_button_state",
]
