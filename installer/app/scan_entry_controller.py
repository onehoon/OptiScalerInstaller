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
    ask_directory: Callable[[], str]
    set_selected_folder: Callable[[str], None]
    start_manual_scan: Callable[[str], bool]


class ScanEntryController:
    def __init__(
        self,
        *,
        callbacks: ScanEntryCallbacks,
    ) -> None:
        self._callbacks = callbacks

    def select_game_folder(self, state: ScanEntryState) -> bool:
        if state.multi_gpu_blocked:
            return False

        if state.sheet_loading:
            return False

        if not state.sheet_ready:
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
