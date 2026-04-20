from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import message_popup


@dataclass(frozen=True)
class ScanFeedbackCallbacks:
    set_scan_status_message: Callable[[str, str], None]
    set_select_folder_enabled: Callable[[bool], None]
    set_information_text: Callable[[str], None]
    enqueue_startup_popup: Callable[[str, int, Callable[..., Any], bool], None]
    run_next_startup_popup: Callable[[], None]


class ScanFeedbackController:
    def __init__(
        self,
        *,
        root: Any,
        callbacks: ScanFeedbackCallbacks,
        popup_theme: Any,
        popup_title: str,
        popup_confirm_text: str,
        scanning_text: str,
        manual_scan_no_results_text: str,
        auto_scan_no_results_text: str,
        select_game_hint_text: str,
        root_width_fallback: int,
        root_height_fallback: int,
    ) -> None:
        self._root = root
        self._callbacks = callbacks
        self._popup_theme = popup_theme
        self._popup_title = str(popup_title or "")
        self._popup_confirm_text = str(popup_confirm_text or "")
        self._scanning_text = str(scanning_text or "")
        self._manual_scan_no_results_text = str(manual_scan_no_results_text or "")
        self._auto_scan_no_results_text = str(auto_scan_no_results_text or "")
        self._select_game_hint_text = str(select_game_hint_text or "")
        self._root_width_fallback = int(root_width_fallback)
        self._root_height_fallback = int(root_height_fallback)

        self._initial_auto_scan_empty_popup_shown = False

    def prepare_scan_ui(self) -> None:
        self._callbacks.set_scan_status_message(self._scanning_text, "#F1F5F9")
        self._callbacks.set_select_folder_enabled(False)

    def finish_scan_ui(self) -> None:
        self._callbacks.set_select_folder_enabled(True)
        self._callbacks.set_scan_status_message("", "")

    def show_manual_scan_empty_popup(self) -> None:
        self._show_scan_result_popup(self._manual_scan_no_results_text)

    def show_select_game_hint(self) -> None:
        self._callbacks.set_information_text(self._select_game_hint_text)

    def enqueue_initial_auto_scan_empty_popup(self) -> None:
        if self._initial_auto_scan_empty_popup_shown:
            return
        self._initial_auto_scan_empty_popup_shown = True
        self._callbacks.enqueue_startup_popup(
            "auto_scan_no_results",
            60,
            lambda done_callback, text=self._auto_scan_no_results_text: self._show_scan_result_popup(
                text,
                on_close=done_callback,
            ),
            False,
        )
        self._callbacks.run_next_startup_popup()

    def _show_scan_result_popup(
        self,
        message_text: str,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        message_popup.show_message_popup(
            root=self._root,
            message_text=message_text,
            theme=self._popup_theme,
            title=self._popup_title,
            confirm_text=self._popup_confirm_text,
            on_close=on_close,
            allow_window_close=True,
            scrollable=True,
            debug_name="scan result popup",
            preferred_text_chars=42,
            max_text_chars=72,
            emphasis_font_size=13,
            root_width_fallback=self._root_width_fallback,
            root_height_fallback=self._root_height_fallback,
        )


__all__ = [
    "ScanFeedbackCallbacks",
    "ScanFeedbackController",
]
