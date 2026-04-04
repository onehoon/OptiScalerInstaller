from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
import logging
from typing import Any


SchedulerCallback = Callable[[Callable[[], None]], Any]


@dataclass(frozen=True)
class InstallSelectionUiState:
    popup_confirmed: bool
    precheck_running: bool
    precheck_ok: bool
    precheck_error: str = ""
    precheck_dll_name: str = ""


@dataclass(frozen=True)
class InstallSelectionPrecheckOutcome:
    ok: bool
    error: str = ""
    resolved_dll_name: str = ""
    popup_message: str = ""


@dataclass(frozen=True)
class InstallSelectionCallbacks:
    apply_selected_index: Callable[[int], None]
    set_information_text: Callable[[str], None]
    apply_ui_state: Callable[[InstallSelectionUiState], None]
    update_install_button_state: Callable[[], None]
    run_precheck: Callable[[Mapping[str, Any]], InstallSelectionPrecheckOutcome]
    get_selection_popup_message: Callable[[Mapping[str, Any]], str]
    show_selection_popup: Callable[[str, Callable[[], None]], None]
    show_precheck_popup: Callable[[str], None]


class InstallSelectionController:
    def __init__(
        self,
        *,
        schedule: SchedulerCallback,
        callbacks: InstallSelectionCallbacks,
        logger=None,
    ) -> None:
        self._schedule = schedule
        self._callbacks = callbacks
        self._logger = logger or logging.getLogger()

    def select_game(self, index: int, found_games: Sequence[Mapping[str, Any]]) -> None:
        self._callbacks.apply_selected_index(int(index))
        self._apply_state(
            InstallSelectionUiState(
                popup_confirmed=False,
                precheck_running=True,
                precheck_ok=False,
            )
        )

        if index < 0 or index >= len(found_games):
            self._apply_state(
                InstallSelectionUiState(
                    popup_confirmed=False,
                    precheck_running=False,
                    precheck_ok=False,
                )
            )
            return

        game = found_games[index]
        self._callbacks.set_information_text(str(game.get("information", "") or ""))

        outcome = self._callbacks.run_precheck(game)
        completed_state = InstallSelectionUiState(
            popup_confirmed=False,
            precheck_running=False,
            precheck_ok=bool(outcome.ok),
            precheck_error=str(outcome.error or ""),
            precheck_dll_name=str(outcome.resolved_dll_name or ""),
        )
        self._apply_state(completed_state)

        popup_message = str(outcome.popup_message or "").strip()
        if popup_message:
            self._schedule_callback(
                lambda msg=popup_message: self._callbacks.show_precheck_popup(msg),
                description="install precheck popup",
            )

        if not outcome.ok:
            return

        selection_popup_message = str(self._callbacks.get_selection_popup_message(game) or "").strip()
        if selection_popup_message:
            self._schedule_callback(
                lambda msg=selection_popup_message, state=completed_state: self._callbacks.show_selection_popup(
                    msg,
                    lambda state_snapshot=state: self._confirm_selection(state_snapshot),
                ),
                description="install selection popup",
            )
            return

        self._confirm_selection(completed_state)

    def _confirm_selection(self, state: InstallSelectionUiState) -> None:
        self._apply_state(replace(state, popup_confirmed=True))

    def _apply_state(self, state: InstallSelectionUiState) -> None:
        self._callbacks.apply_ui_state(state)
        self._callbacks.update_install_button_state()

    def _schedule_callback(self, callback: Callable[[], None], *, description: str) -> None:
        try:
            self._schedule(callback)
        except Exception:
            self._logger.exception("Failed to schedule %s", description)


__all__ = [
    "InstallSelectionCallbacks",
    "InstallSelectionController",
    "InstallSelectionPrecheckOutcome",
    "InstallSelectionUiState",
]
