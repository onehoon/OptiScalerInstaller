from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class ScanEntryState:
    multi_gpu_blocked: bool
    sheet_loading: bool
    sheet_ready: bool


@dataclass(frozen=True)
class ScanEntryCallbacks:
    show_info: Callable[[str, str], None]
    show_error: Callable[[str, str], None]
    ask_directory: Callable[[], str]
    set_selected_folder: Callable[[str], None]
    start_manual_scan: Callable[[str], bool]


class ScanEntryController:
    def __init__(
        self,
        *,
        callbacks: ScanEntryCallbacks,
        game_db_loading_title: str,
        game_db_loading_body: str,
        game_db_error_title: str,
        game_db_error_body: str,
    ) -> None:
        self._callbacks = callbacks
        self._game_db_loading_title = str(game_db_loading_title or "")
        self._game_db_loading_body = str(game_db_loading_body or "")
        self._game_db_error_title = str(game_db_error_title or "")
        self._game_db_error_body = str(game_db_error_body or "")

    def select_game_folder(self, state: ScanEntryState) -> bool:
        if state.multi_gpu_blocked:
            return False

        if state.sheet_loading:
            self._callbacks.show_info(self._game_db_loading_title, self._game_db_loading_body)
            return False

        if not state.sheet_ready:
            self._callbacks.show_error(self._game_db_error_title, self._game_db_error_body)
            return False

        selected_folder = str(self._callbacks.ask_directory() or "")
        if not selected_folder:
            return False

        self._callbacks.set_selected_folder(selected_folder)
        return bool(self._callbacks.start_manual_scan(selected_folder))


__all__ = [
    "ScanEntryCallbacks",
    "ScanEntryController",
    "ScanEntryState",
]
