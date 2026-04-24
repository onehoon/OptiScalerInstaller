from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .install_rejection_codes import (
    INSTALL_REJECT_CONFIRM_POPUP_REQUIRED,
    INSTALL_REJECT_FSR4_ARCHIVE_DOWNLOADING,
    INSTALL_REJECT_FSR4_NOT_READY,
    INSTALL_REJECT_INSTALL_IN_PROGRESS,
    INSTALL_REJECT_INSTALL_PRECHECK_RUNNING,
    INSTALL_REJECT_INVALID_GAME_SELECTION,
    INSTALL_REJECT_MULTI_GPU_BLOCKED,
    INSTALL_REJECT_NO_GAME_SELECTED,
    INSTALL_REJECT_OPTISCALER_ARCHIVE_DOWNLOADING,
    INSTALL_REJECT_OPTISCALER_ARCHIVE_NOT_READY,
    INSTALL_REJECT_PRECHECK_INCOMPLETE,
    INSTALL_REJECT_PREDOWNLOAD_IN_PROGRESS,
)


def _normalize_text(value: object) -> str:
    return str(value or "")


def _reject(code: str, *, detail: str = "") -> "InstallEntryDecision":
    return InstallEntryDecision(
        ok=False,
        code=str(code or ""),
        detail=_normalize_text(detail),
    )


@dataclass(frozen=True)
class InstallEntryState:
    multi_gpu_blocked: bool
    install_in_progress: bool
    selected_game_index: int | None
    found_games: tuple[Mapping[str, Any], ...]
    optiscaler_archive_downloading: bool
    install_precheck_running: bool
    install_precheck_ok: bool
    install_precheck_error: str
    install_precheck_dll_name: str
    optiscaler_archive_ready: bool
    opti_source_archive: str
    optiscaler_archive_error: str
    fsr4_archive_downloading: bool
    fsr4_archive_ready: bool
    fsr4_source_archive: str
    fsr4_archive_error: str
    game_popup_confirmed: bool
    predownload_in_progress: bool = False
    ual_cached_archive: str = ""
    optipatcher_cached_archive: str = ""
    specialk_cached_archive: str = ""
    unreal5_cached_archive: str = ""


@dataclass(frozen=True)
class InstallEntryDecision:
    ok: bool
    code: str = ""
    detail: str = ""
    selected_game: Mapping[str, Any] | None = None
    source_archive: str = ""
    resolved_dll_name: str = ""
    fsr4_required: bool = False
    fsr4_source_archive: str = ""
    ual_cached_archive: str = ""
    optipatcher_cached_archive: str = ""
    specialk_cached_archive: str = ""
    unreal5_cached_archive: str = ""


def validate_install_entry(
    state: InstallEntryState,
    should_apply_fsr4: Callable[[Mapping[str, Any]], bool],
) -> InstallEntryDecision:
    # Worker gate: validate only the install-start invariants that still matter
    # after the button has been pressed. UI-only loading/update states stay in
    # compute_install_button_state().
    if state.multi_gpu_blocked:
        return _reject(INSTALL_REJECT_MULTI_GPU_BLOCKED)

    if state.install_in_progress:
        return _reject(INSTALL_REJECT_INSTALL_IN_PROGRESS)

    if state.predownload_in_progress:
        return _reject(INSTALL_REJECT_PREDOWNLOAD_IN_PROGRESS)

    if state.selected_game_index is None:
        return _reject(INSTALL_REJECT_NO_GAME_SELECTED)

    if state.optiscaler_archive_downloading:
        return _reject(INSTALL_REJECT_OPTISCALER_ARCHIVE_DOWNLOADING)

    if state.install_precheck_running:
        return _reject(INSTALL_REJECT_INSTALL_PRECHECK_RUNNING)

    if not state.install_precheck_ok or not state.install_precheck_dll_name:
        return _reject(INSTALL_REJECT_PRECHECK_INCOMPLETE, detail=_normalize_text(state.install_precheck_error))

    if not state.optiscaler_archive_ready or not state.opti_source_archive:
        return _reject(INSTALL_REJECT_OPTISCALER_ARCHIVE_NOT_READY, detail=_normalize_text(state.optiscaler_archive_error))

    selected_index = state.selected_game_index
    selected_game = None
    if selected_index is not None:
        selected_index = int(selected_index)
        if 0 <= selected_index < len(state.found_games):
            selected_game = state.found_games[selected_index]
    if selected_game is None:
        return _reject(INSTALL_REJECT_INVALID_GAME_SELECTION)

    fsr4_required = bool(should_apply_fsr4(selected_game))
    if fsr4_required and state.fsr4_archive_downloading:
        return _reject(INSTALL_REJECT_FSR4_ARCHIVE_DOWNLOADING)

    if fsr4_required and (not state.fsr4_archive_ready or not state.fsr4_source_archive):
        return _reject(INSTALL_REJECT_FSR4_NOT_READY, detail=_normalize_text(state.fsr4_archive_error))

    if not state.game_popup_confirmed:
        return _reject(INSTALL_REJECT_CONFIRM_POPUP_REQUIRED)

    return InstallEntryDecision(
        ok=True,
        selected_game=selected_game,
        source_archive=_normalize_text(state.opti_source_archive),
        resolved_dll_name=_normalize_text(state.install_precheck_dll_name),
        fsr4_required=bool(fsr4_required),
        fsr4_source_archive=_normalize_text(state.fsr4_source_archive) if fsr4_required else "",
        ual_cached_archive=_normalize_text(state.ual_cached_archive),
        optipatcher_cached_archive=_normalize_text(state.optipatcher_cached_archive),
        specialk_cached_archive=_normalize_text(state.specialk_cached_archive),
        unreal5_cached_archive=_normalize_text(state.unreal5_cached_archive),
    )


__all__ = [
    "InstallEntryDecision",
    "InstallEntryState",
    "validate_install_entry",
]
