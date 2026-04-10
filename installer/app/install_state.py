from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .install_entry import InstallEntryState
from .install_ui_state import InstallButtonStateInputs


GameSupportPredicate = Callable[[Mapping[str, Any]], bool]
Fsr4Predicate = Callable[[Mapping[str, Any]], bool]


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
    if selected_game_index is None:
        return SelectedGameSnapshot(
            found_games=normalized_games,
            selected_game_index=None,
            selected_game=None,
        )

    selected_index = int(selected_game_index)
    if selected_index < 0 or selected_index >= len(normalized_games):
        return SelectedGameSnapshot(
            found_games=normalized_games,
            selected_game_index=selected_index,
            selected_game=None,
        )

    selected_game = normalized_games[selected_index]
    if str(lang or "").lower() == "ko":
        header_text = str(
            selected_game.get("display", "")
            or selected_game.get("game_name_kr", "")
            or selected_game.get("game_name", "")
        ).strip()
    else:
        header_text = str(
            selected_game.get("game_name", "")
            or selected_game.get("display", "")
        ).strip()

    return SelectedGameSnapshot(
        found_games=normalized_games,
        selected_game_index=selected_index,
        selected_game=selected_game,
        header_text=header_text,
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
    has_supported_gpu = bool(is_game_supported(selected_game)) if selected_game is not None else True
    fsr4_required = bool(should_apply_fsr4(selected_game)) if selected_game is not None else False
    fsr4_ready = not fsr4_required or (bool(fsr4_archive_ready) and not bool(fsr4_archive_downloading))

    return InstallButtonStateInputs(
        multi_gpu_blocked=bool(multi_gpu_blocked),
        gpu_selection_pending=bool(gpu_selection_pending),
        sheet_ready=bool(sheet_ready),
        sheet_loading=bool(sheet_loading),
        install_in_progress=bool(install_in_progress),
        app_update_in_progress=bool(app_update_in_progress),
        has_valid_game=bool(selection.has_valid_selection),
        has_supported_gpu=bool(has_supported_gpu),
        install_precheck_running=bool(install_precheck_running),
        install_precheck_ok=bool(install_precheck_ok),
        optiscaler_archive_ready=bool(optiscaler_archive_ready),
        optiscaler_archive_downloading=bool(optiscaler_archive_downloading),
        fsr4_ready=bool(fsr4_ready),
        game_popup_confirmed=bool(game_popup_confirmed),
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
        multi_gpu_blocked=bool(multi_gpu_blocked),
        install_in_progress=bool(install_in_progress),
        selected_game_index=selection.selected_game_index,
        found_games=selection.found_games,
        optiscaler_archive_downloading=bool(optiscaler_archive_downloading),
        install_precheck_running=bool(install_precheck_running),
        install_precheck_ok=bool(install_precheck_ok),
        install_precheck_error=str(install_precheck_error or ""),
        install_precheck_dll_name=str(install_precheck_dll_name or ""),
        optiscaler_archive_ready=bool(optiscaler_archive_ready),
        opti_source_archive=str(opti_source_archive or ""),
        optiscaler_archive_error=str(optiscaler_archive_error or ""),
        fsr4_archive_downloading=bool(fsr4_archive_downloading),
        fsr4_archive_ready=bool(fsr4_archive_ready),
        fsr4_source_archive=str(fsr4_source_archive or ""),
        fsr4_archive_error=str(fsr4_archive_error or ""),
        game_popup_confirmed=bool(game_popup_confirmed),
        predownload_in_progress=bool(predownload_in_progress),
        ual_cached_archive=str(ual_cached_archive or ""),
        optipatcher_cached_archive=str(optipatcher_cached_archive or ""),
        specialk_cached_archive=str(specialk_cached_archive or ""),
        unreal5_cached_archive=str(unreal5_cached_archive or ""),
    )


__all__ = [
    "SelectedGameSnapshot",
    "build_install_button_state_inputs",
    "build_install_entry_state",
    "build_selected_game_snapshot",
]
