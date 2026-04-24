from __future__ import annotations

import logging
from typing import Callable, Optional

import customtkinter as ctk


def create_modal_popup(root: ctk.CTk, title: str, surface_color: str) -> ctk.CTkToplevel:
    popup = ctk.CTkToplevel(root)
    popup.title(title)
    popup.transient(root)
    popup.grab_set()
    popup.resizable(False, False)
    popup.configure(fg_color=surface_color)
    popup.withdraw()
    return popup


def close_modal_popup(popup: ctk.CTkToplevel) -> None:
    try:
        popup.grab_release()
    except Exception:
        pass
    try:
        popup.destroy()
    except Exception:
        pass


def present_modal_popup(
    popup: ctk.CTkToplevel,
    *,
    initial_layout: Optional[Callable[[], None]] = None,
    post_show_layout: Optional[Callable[[], None]] = None,
    after_idle_layout: Optional[Callable[[], None]] = None,
    fade_controller: Optional["PopupFadeController"] = None,
) -> None:
    if callable(initial_layout):
        initial_layout()
    if fade_controller is not None:
        fade_controller.prepare_for_show()

    popup.deiconify()
    popup.lift()
    try:
        popup.focus_set()
    except Exception:
        pass

    if callable(post_show_layout):
        post_show_layout()
    if callable(after_idle_layout):
        popup.after(0, after_idle_layout)
    if fade_controller is not None:
        fade_controller.start_fade_in()


def resolve_popup_font_size(
    popup: ctk.CTkToplevel,
    size: Optional[int],
    *,
    log_message: str = "Failed to resolve popup font scaling",
) -> Optional[int]:
    if size is None:
        return None

    logical_size = int(size)
    if logical_size < 0:
        return logical_size

    try:
        if hasattr(popup, "_get_window_scaling"):
            scale = float(popup._get_window_scaling())
            if scale > 0:
                return -max(1, int(round(logical_size * scale)))
    except Exception:
        logging.debug(log_message, exc_info=True)

    return -max(1, logical_size)


class PopupFadeController:
    def __init__(
        self,
        popup: ctk.CTkToplevel,
        *,
        debug_name: str = "popup",
        step_in: float = 0.14,
        step_out: float = 0.18,
        interval_in: int = 18,
        interval_out: int = 16,
        fade_in_delay_ms: int = 45,
    ) -> None:
        self.popup = popup
        self.debug_name = debug_name
        self.step_in = step_in
        self.step_out = step_out
        self.interval_in = interval_in
        self.interval_out = interval_out
        self.fade_in_delay_ms = fade_in_delay_ms
        self.fade_supported = False
        self._fade_in_after_id = None
        self._closing = False
        self._on_complete: Optional[Callable[[], None]] = None

    @property
    def is_closing(self) -> bool:
        return self._closing

    def prepare_for_show(self) -> bool:
        try:
            self.popup.attributes("-alpha", 0.0)
            self.fade_supported = True
        except Exception:
            self.fade_supported = False
            logging.debug("Popup alpha fade is not supported for %s", self.debug_name, exc_info=True)
        return self.fade_supported

    def start_fade_in(self, delay_ms: Optional[int] = None) -> None:
        if delay_ms is None:
            delay_ms = self.fade_in_delay_ms
        if not self.fade_supported or self._closing or not self._popup_exists():
            return
        self.cancel_fade_in()
        self._fade_in_after_id = self.popup.after(delay_ms, self._fade_in, 0.0)

    def cancel_fade_in(self) -> None:
        if self._fade_in_after_id is None:
            return
        try:
            self.popup.after_cancel(self._fade_in_after_id)
        except Exception:
            pass
        self._fade_in_after_id = None

    def close(self, on_complete: Optional[Callable[[], None]] = None) -> bool:
        if self._closing:
            return False
        self._closing = True
        self._on_complete = on_complete
        self.cancel_fade_in()
        if self.fade_supported:
            self._fade_out(self._get_popup_alpha())
        else:
            self._finalize_close()
        return True

    def _popup_exists(self) -> bool:
        try:
            return bool(self.popup.winfo_exists())
        except Exception:
            return False

    def _get_popup_alpha(self) -> float:
        try:
            return float(self.popup.attributes("-alpha"))
        except Exception:
            return 1.0

    def _finalize_close(self) -> None:
        on_complete = self._on_complete
        self._on_complete = None
        close_modal_popup(self.popup)
        if callable(on_complete):
            on_complete()

    def _fade_in(self, opacity: float = 0.0) -> None:
        if self._closing or not self._popup_exists():
            return
        next_opacity = min(1.0, opacity + self.step_in)
        try:
            self.popup.attributes("-alpha", next_opacity)
        except Exception:
            self._fade_in_after_id = None
            logging.debug("%s fade-in failed", self.debug_name, exc_info=True)
            try:
                self.popup.attributes("-alpha", 1.0)
            except Exception:
                pass
            return
        if next_opacity < 1.0:
            self._fade_in_after_id = self.popup.after(self.interval_in, self._fade_in, next_opacity)
        else:
            self._fade_in_after_id = None

    def _fade_out(self, opacity: float) -> None:
        if not self._popup_exists():
            self._finalize_close()
            return
        next_opacity = max(0.0, opacity - self.step_out)
        try:
            self.popup.attributes("-alpha", next_opacity)
        except Exception:
            logging.debug("%s fade-out failed", self.debug_name, exc_info=True)
            self._finalize_close()
            return
        if next_opacity > 0.0:
            self.popup.after(self.interval_out, self._fade_out, next_opacity)
        else:
            self._finalize_close()
