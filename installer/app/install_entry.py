from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


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
    if state.multi_gpu_blocked:
        return InstallEntryDecision(ok=False, code="multi_gpu_blocked")

    if state.install_in_progress:
        return InstallEntryDecision(ok=False, code="install_in_progress")

    if state.predownload_in_progress:
        return InstallEntryDecision(ok=False, code="predownload_in_progress")

    if state.selected_game_index is None:
        return InstallEntryDecision(ok=False, code="no_game_selected")

    if state.optiscaler_archive_downloading:
        return InstallEntryDecision(ok=False, code="optiscaler_archive_downloading")

    if state.install_precheck_running:
        return InstallEntryDecision(ok=False, code="install_precheck_running")

    if not state.install_precheck_ok or not state.install_precheck_dll_name:
        return InstallEntryDecision(
            ok=False,
            code="precheck_incomplete",
            detail=str(state.install_precheck_error or ""),
        )

    if not state.optiscaler_archive_ready or not state.opti_source_archive:
        return InstallEntryDecision(
            ok=False,
            code="optiscaler_archive_not_ready",
            detail=str(state.optiscaler_archive_error or ""),
        )

    selected_index = int(state.selected_game_index)
    if selected_index < 0 or selected_index >= len(state.found_games):
        return InstallEntryDecision(ok=False, code="invalid_game_selection")

    selected_game = state.found_games[selected_index]
    fsr4_required = bool(should_apply_fsr4(selected_game))
    if fsr4_required and state.fsr4_archive_downloading:
        return InstallEntryDecision(ok=False, code="fsr4_archive_downloading")

    if fsr4_required and (not state.fsr4_archive_ready or not state.fsr4_source_archive):
        return InstallEntryDecision(
            ok=False,
            code="fsr4_not_ready",
            detail=str(state.fsr4_archive_error or ""),
        )

    if not state.game_popup_confirmed:
        return InstallEntryDecision(ok=False, code="confirm_popup_required")

    return InstallEntryDecision(
        ok=True,
        selected_game=selected_game,
        source_archive=str(state.opti_source_archive or ""),
        resolved_dll_name=str(state.install_precheck_dll_name or ""),
        fsr4_required=fsr4_required,
        fsr4_source_archive=str(state.fsr4_source_archive or "") if fsr4_required else "",
        ual_cached_archive=str(state.ual_cached_archive or ""),
        optipatcher_cached_archive=str(state.optipatcher_cached_archive or ""),
        specialk_cached_archive=str(state.specialk_cached_archive or ""),
        unreal5_cached_archive=str(state.unreal5_cached_archive or ""),
    )


__all__ = [
    "InstallEntryDecision",
    "InstallEntryState",
    "validate_install_entry",
]
