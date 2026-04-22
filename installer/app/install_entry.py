from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


def _normalize_text(value: object) -> str:
    return str(value or "")


def _reject(code: str, *, detail: str = "") -> "InstallEntryDecision":
    return InstallEntryDecision(
        ok=False,
        code=str(code or ""),
        detail=_normalize_text(detail),
    )


def _resolve_selected_game(state: "InstallEntryState") -> Mapping[str, Any] | None:
    if state.selected_game_index is None:
        return None

    selected_index = int(state.selected_game_index)
    if selected_index < 0 or selected_index >= len(state.found_games):
        return None
    return state.found_games[selected_index]


def _build_success_decision(
    state: "InstallEntryState",
    *,
    selected_game: Mapping[str, Any],
    fsr4_required: bool,
) -> "InstallEntryDecision":
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
        return _reject("multi_gpu_blocked")

    if state.install_in_progress:
        return _reject("install_in_progress")

    if state.predownload_in_progress:
        return _reject("predownload_in_progress")

    if state.selected_game_index is None:
        return _reject("no_game_selected")

    if state.optiscaler_archive_downloading:
        return _reject("optiscaler_archive_downloading")

    if state.install_precheck_running:
        return _reject("install_precheck_running")

    if not state.install_precheck_ok or not state.install_precheck_dll_name:
        return _reject("precheck_incomplete", detail=_normalize_text(state.install_precheck_error))

    if not state.optiscaler_archive_ready or not state.opti_source_archive:
        return _reject("optiscaler_archive_not_ready", detail=_normalize_text(state.optiscaler_archive_error))

    selected_game = _resolve_selected_game(state)
    if selected_game is None:
        return _reject("invalid_game_selection")

    fsr4_required = bool(should_apply_fsr4(selected_game))
    if fsr4_required and state.fsr4_archive_downloading:
        return _reject("fsr4_archive_downloading")

    if fsr4_required and (not state.fsr4_archive_ready or not state.fsr4_source_archive):
        return _reject("fsr4_not_ready", detail=_normalize_text(state.fsr4_archive_error))

    if not state.game_popup_confirmed:
        return _reject("confirm_popup_required")

    return _build_success_decision(
        state,
        selected_game=selected_game,
        fsr4_required=fsr4_required,
    )


__all__ = [
    "InstallEntryDecision",
    "InstallEntryState",
    "validate_install_entry",
]
