from __future__ import annotations

from collections.abc import Callable
import logging
import webbrowser
from typing import Optional

from tkinter import messagebox

from . import message_popup


ScheduleCallback = Callable[[Callable[[], None]], None]
OpenUrlCallback = Callable[[str], bool]
DialogCallback = Callable[[str, str], None]


class AppNoticeController:
    def __init__(
        self,
        *,
        root,
        popup_theme: message_popup.MessagePopupTheme,
        schedule_idle: ScheduleCallback,
        installer_notice_title: str,
        warning_title: str,
        notice_title: str,
        error_title: str,
        confirm_text: str,
        wiki_url: str,
        wiki_not_configured_detail: str,
        wiki_open_failed_detail: str,
        installation_completed_text: str,
        root_width_fallback: int,
        root_height_fallback: int,
        show_info: DialogCallback = messagebox.showinfo,
        show_error: DialogCallback = messagebox.showerror,
        open_url: OpenUrlCallback = webbrowser.open,
        logger=None,
    ) -> None:
        self._root = root
        self._popup_theme = popup_theme
        self._schedule_idle = schedule_idle
        self._installer_notice_title = str(installer_notice_title or "")
        self._warning_title = str(warning_title or "")
        self._notice_title = str(notice_title or "")
        self._error_title = str(error_title or "")
        self._confirm_text = str(confirm_text or "")
        self._wiki_url = str(wiki_url or "")
        self._wiki_not_configured_detail = str(wiki_not_configured_detail or "")
        self._wiki_open_failed_detail = str(wiki_open_failed_detail or "")
        self._installation_completed_text = str(installation_completed_text or "")
        self._root_width_fallback = int(root_width_fallback)
        self._root_height_fallback = int(root_height_fallback)
        self._show_info = show_info
        self._show_error = show_error
        self._open_url = open_url
        self._logger = logger or logging.getLogger()

    def show_selection_popup(
        self,
        message_text: str,
        on_confirm: Optional[Callable[[], None]] = None,
    ) -> None:
        message_popup.show_message_popup(
            root=self._root,
            message_text=message_text,
            theme=self._popup_theme,
            title=self._installer_notice_title,
            confirm_text=self._confirm_text,
            on_close=self._schedule_callback(on_confirm),
            allow_window_close=False,
            scrollable=False,
            debug_name="selection popup",
            max_text_chars=110,
            emphasis_font_size=13,
            root_width_fallback=self._root_width_fallback,
            root_height_fallback=self._root_height_fallback,
        )

    def show_precheck_popup(
        self,
        message_text: str,
        on_close: Optional[Callable[[], None]] = None,
    ) -> None:
        message_popup.show_message_popup(
            root=self._root,
            message_text=message_text,
            theme=self._popup_theme,
            title=self._warning_title,
            confirm_text=self._confirm_text,
            on_close=self._schedule_callback(on_close),
            allow_window_close=True,
            scrollable=False,
            debug_name="precheck popup",
            max_text_chars=110,
            emphasis_font_size=13,
            root_width_fallback=self._root_width_fallback,
            root_height_fallback=self._root_height_fallback,
        )

    def show_startup_warning_popup(
        self,
        warning_text: str,
        on_close: Optional[Callable[[], None]] = None,
    ) -> None:
        message_popup.show_message_popup(
            root=self._root,
            message_text=warning_text,
            theme=self._popup_theme,
            title=self._notice_title,
            confirm_text=self._confirm_text,
            on_close=on_close,
            allow_window_close=True,
            scrollable=False,
            debug_name="startup warning popup",
            max_text_chars=110,
            emphasis_font_size=13,
            root_width_fallback=self._root_width_fallback,
            root_height_fallback=self._root_height_fallback,
        )

    def open_supported_games_wiki(self) -> bool:
        if not self._wiki_url:
            self._show_info(self._notice_title, self._wiki_not_configured_detail)
            return False

        try:
            if not self._open_url(self._wiki_url):
                raise RuntimeError("webbrowser.open returned False")
        except Exception:
            self._logger.exception("Failed to open supported games wiki URL: %s", self._wiki_url)
            self._show_error(self._error_title, self._wiki_open_failed_detail)
            return False

        return True

    def show_after_install_popup(
        self,
        message_text: str,
        *,
        guide_url: str = "",
        guide_context: str = "",
    ) -> None:
        resolved_message = str(message_text or "").strip() or self._installation_completed_text
        normalized_guide_url = str(guide_url or "").strip()

        def _on_confirm_open_guide() -> None:
            try:
                if normalized_guide_url:
                    self._open_url(normalized_guide_url)
                else:
                    self._logger.debug(
                        "No guide URL provided for after-install popup for game: %s",
                        guide_context or "<unknown>",
                    )
            except Exception:
                self._logger.exception("Failed to open guide URL: %s", normalized_guide_url)

        self.show_selection_popup(resolved_message, on_confirm=_on_confirm_open_guide)

    def _schedule_callback(self, callback: Optional[Callable[[], None]]) -> Optional[Callable[[], None]]:
        if not callable(callback):
            return None
        return lambda: self._schedule_idle(callback)


__all__ = ["AppNoticeController"]
