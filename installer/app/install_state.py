from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .install_entry import InstallEntryState
from .install_ui_state import InstallButtonStateInputs


GameSupportPredicate = Callable[[Mapping[str, Any]], bool]
Fsr4Predicate = Callable[[Mapping[str, Any]], bool]


def _normalize_bool(value: object) -> bool:
    return bool(value)


def _normalize_text(value: object) -> str:
    return str(value or "")


def _build_selected_game_header_text(selected_game: Mapping[str, Any], lang: str) -> str:
    if str(lang or "").lower() == "ko":
        return _normalize_text(
            selected_game.get("display", "")
            or selected_game.get("game_name_kr", "")
            or selected_game.get("game_name_en", "")
        ).strip()
    return _normalize_text(
        selected_game.get("game_name_en", "")
        or selected_game.get("display", "")
    ).strip()


def _is_fsr4_install_ready(
    *,
    fsr4_required: bool,
    fsr4_archive_ready: object,
    fsr4_archive_downloading: object,
) -> bool:
    if not fsr4_required:
        return True
    return _normalize_bool(fsr4_archive_ready) and not _normalize_bool(fsr4_archive_downloading)


@dataclass(frozen=True)
class SelectedGameSnapshot:
    found_games: tuple[Mapping[str, Any], ...]
    selected_game_index: int | None
    selected_game: Mapping[str, Any] | None
    header_text: str = ""

    @property
    def has_valid_selection(self) -> bool:
        return self.selected_game is not None


def build_selected_game_snapshot(
    found_games: Sequence[Mapping[str, Any]],
    selected_game_index: int | None,
    lang: str,
) -> SelectedGameSnapshot:
    normalized_games = tuple(found_games or ())
    normalized_selected_index = None if selected_game_index is None else int(selected_game_index)
    selected_game = None
    if normalized_selected_index is not None and 0 <= normalized_selected_index < len(normalized_games):
        selected_game = normalized_games[normalized_selected_index]
    if selected_game is None:
        return SelectedGameSnapshot(
            found_games=normalized_games,
            selected_game_index=normalized_selected_index,
            selected_game=None,
        )

    return SelectedGameSnapshot(
        found_games=normalized_games,
        selected_game_index=normalized_selected_index,
        selected_game=selected_game,
        header_text=_build_selected_game_header_text(selected_game, lang),
    )


def build_install_button_state_inputs(
    *,
    selection: SelectedGameSnapshot,
    multi_gpu_blocked: bool,
    gpu_selection_pending: bool,
    sheet_ready: bool,
    sheet_loading: bool,
    install_in_progress: bool,
    app_update_in_progress: bool,
    install_precheck_running: bool,
    install_precheck_ok: bool,
    optiscaler_archive_ready: bool,
    optiscaler_archive_downloading: bool,
    fsr4_archive_ready: bool,
    fsr4_archive_downloading: bool,
    game_popup_confirmed: bool,
    is_game_supported: GameSupportPredicate,
    should_apply_fsr4: Fsr4Predicate,
) -> InstallButtonStateInputs:
    selected_game = selection.selected_game
    has_supported_gpu = _normalize_bool(is_game_supported(selected_game)) if selected_game is not None else True
    fsr4_required = _normalize_bool(should_apply_fsr4(selected_game)) if selected_game is not None else False
    fsr4_ready = _is_fsr4_install_ready(
        fsr4_required=fsr4_required,
        fsr4_archive_ready=fsr4_archive_ready,
        fsr4_archive_downloading=fsr4_archive_downloading,
    )

    return InstallButtonStateInputs(
        multi_gpu_blocked=_normalize_bool(multi_gpu_blocked),
        gpu_selection_pending=_normalize_bool(gpu_selection_pending),
        sheet_ready=_normalize_bool(sheet_ready),
        sheet_loading=_normalize_bool(sheet_loading),
        install_in_progress=_normalize_bool(install_in_progress),
        app_update_in_progress=_normalize_bool(app_update_in_progress),
        has_valid_game=_normalize_bool(selection.has_valid_selection),
        has_supported_gpu=_normalize_bool(has_supported_gpu),
        install_precheck_running=_normalize_bool(install_precheck_running),
        install_precheck_ok=_normalize_bool(install_precheck_ok),
        optiscaler_archive_ready=_normalize_bool(optiscaler_archive_ready),
        optiscaler_archive_downloading=_normalize_bool(optiscaler_archive_downloading),
        fsr4_ready=_normalize_bool(fsr4_ready),
        game_popup_confirmed=_normalize_bool(game_popup_confirmed),
    )


def build_install_entry_state(
    *,
    selection: SelectedGameSnapshot,
    multi_gpu_blocked: bool,
    install_in_progress: bool,
    optiscaler_archive_downloading: bool,
    install_precheck_running: bool,
    install_precheck_ok: bool,
    install_precheck_error: str,
    install_precheck_dll_name: str,
    optiscaler_archive_ready: bool,
    opti_source_archive: str,
    optiscaler_archive_error: str,
    fsr4_archive_downloading: bool,
    fsr4_archive_ready: bool,
    fsr4_source_archive: str,
    fsr4_archive_error: str,
    game_popup_confirmed: bool,
    predownload_in_progress: bool = False,
    ual_cached_archive: str = "",
    optipatcher_cached_archive: str = "",
    specialk_cached_archive: str = "",
    unreal5_cached_archive: str = "",
) -> InstallEntryState:
    return InstallEntryState(
        multi_gpu_blocked=_normalize_bool(multi_gpu_blocked),
        install_in_progress=_normalize_bool(install_in_progress),
        selected_game_index=selection.selected_game_index,
        found_games=selection.found_games,
        optiscaler_archive_downloading=_normalize_bool(optiscaler_archive_downloading),
        install_precheck_running=_normalize_bool(install_precheck_running),
        install_precheck_ok=_normalize_bool(install_precheck_ok),
        install_precheck_error=_normalize_text(install_precheck_error),
        install_precheck_dll_name=_normalize_text(install_precheck_dll_name),
        optiscaler_archive_ready=_normalize_bool(optiscaler_archive_ready),
        opti_source_archive=_normalize_text(opti_source_archive),
        optiscaler_archive_error=_normalize_text(optiscaler_archive_error),
        fsr4_archive_downloading=_normalize_bool(fsr4_archive_downloading),
        fsr4_archive_ready=_normalize_bool(fsr4_archive_ready),
        fsr4_source_archive=_normalize_text(fsr4_source_archive),
        fsr4_archive_error=_normalize_text(fsr4_archive_error),
        game_popup_confirmed=_normalize_bool(game_popup_confirmed),
        predownload_in_progress=_normalize_bool(predownload_in_progress),
        ual_cached_archive=_normalize_text(ual_cached_archive),
        optipatcher_cached_archive=_normalize_text(optipatcher_cached_archive),
        specialk_cached_archive=_normalize_text(specialk_cached_archive),
        unreal5_cached_archive=_normalize_text(unreal5_cached_archive),
    )


__all__ = [
    "SelectedGameSnapshot",
    "build_install_button_state_inputs",
    "build_install_entry_state",
    "build_selected_game_snapshot",
]
