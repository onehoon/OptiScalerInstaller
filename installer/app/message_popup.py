from __future__ import annotations

from dataclasses import dataclass
import logging
import math
from typing import Callable, Optional

import customtkinter as ctk

from .popup_markup import create_popup_markup_text, estimate_wrapped_text_lines
from .popup_utils import PopupFadeController, create_modal_popup, present_modal_popup


@dataclass(frozen=True)
class MessagePopupTheme:
    surface_color: str
    accent_color: str
    accent_hover_color: str
    font_ui: str
    body_text_color: str = "#E3EAF3"
    emphasis_color: str = "#FFCB62"
    button_text_color: str = "#0B121A"


def _build_width_steps(start: int, stop: int) -> list[int]:
    start = max(1, int(start))
    stop = max(start, int(stop))
    steps = list(range(start, stop + 1, 4))
    if not steps:
        return [start]
    if steps[-1] != stop:
        steps.append(stop)
    return steps


def _resolve_popup_font_size(popup: ctk.CTkToplevel, size: Optional[int]) -> Optional[int]:
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
        logging.debug("Failed to resolve popup font scaling", exc_info=True)

    return -max(1, logical_size)


def _apply_popup_geometry(
    root: ctk.CTk,
    popup: ctk.CTkToplevel,
    *,
    screen_w: int,
    screen_h: int,
    debug_name: str,
    desired_popup_w: Optional[int] = None,
    min_popup_w: int = 200,
    min_popup_h: int = 120,
    use_requested_size: bool = False,
) -> None:
    try:
        popup.update_idletasks()

        if use_requested_size:
            popup_w = max(1, int(popup.winfo_reqwidth()))
            popup_h = max(1, int(popup.winfo_reqheight()))
        else:
            popup_w = max(1, int(popup.winfo_width() or popup.winfo_reqwidth()))
            popup_h = max(1, int(popup.winfo_height() or popup.winfo_reqheight()))

        if desired_popup_w is not None:
            popup_w = max(popup_w, int(desired_popup_w))

        margin = 12
        if popup_w + (margin * 2) > screen_w:
            popup_w = max(min_popup_w, screen_w - (margin * 2))
        if popup_h + (margin * 2) > screen_h:
            popup_h = max(min_popup_h, screen_h - (margin * 2))

        root_x = root.winfo_x()
        root_y = root.winfo_y()
        root_w = root.winfo_width()
        root_h = root.winfo_height()
        x = root_x + (root_w // 2) - (popup_w // 2)
        y = root_y + (root_h // 2) - (popup_h // 2)
        min_x = margin if popup_w + (margin * 2) < screen_w else 0
        min_y = margin if popup_h + (margin * 2) < screen_h else 0
        max_x = max(min_x, screen_w - popup_w - margin)
        max_y = max(min_y, screen_h - popup_h - margin)
        x = max(min_x, min(x, max_x))
        y = max(min_y, min(y, max_y))
        logical_w = max(1, int(round(popup._reverse_window_scaling(popup_w))))
        logical_h = max(1, int(round(popup._reverse_window_scaling(popup_h))))
        popup.geometry(f"{logical_w}x{logical_h}+{x}+{y}")
    except Exception:
        logging.debug("Failed to size %s", debug_name, exc_info=True)


def show_message_popup(
    root: ctk.CTk,
    message_text: str,
    theme: MessagePopupTheme,
    *,
    title: str,
    confirm_text: str,
    on_close: Optional[Callable[[], None]] = None,
    allow_window_close: bool = True,
    scrollable: bool = False,
    debug_name: str = "message popup",
    preferred_text_chars: Optional[int] = None,
    min_text_chars: int = 34,
    max_text_chars: int = 110,
    base_font_size: int = 13,
    emphasis_font_size: Optional[int] = None,
    emphasis_weight: str = "bold",
    root_width_fallback: int = 512,
    root_height_fallback: int = 512,
) -> None:
    text = str(message_text or "").strip()
    if not text:
        if callable(on_close):
            on_close()
        return

    popup = create_modal_popup(root, title, theme.surface_color)

    container = ctk.CTkFrame(popup, fg_color="transparent")
    container.pack(fill="both", expand=True, padx=22, pady=(18, 12))

    message_frame = ctk.CTkFrame(container, fg_color="transparent")
    if scrollable:
        message_frame.pack(fill="x", expand=False)
    else:
        message_frame.pack(fill="both", expand=True)
    if scrollable:
        message_frame.grid_columnconfigure(0, weight=1)
        message_frame.grid_rowconfigure(0, weight=1)

    message_block = create_popup_markup_text(
        message_frame,
        text,
        background_color=theme.surface_color,
        body_text_color=theme.body_text_color,
        font_family=theme.font_ui,
        base_font_size=_resolve_popup_font_size(popup, base_font_size),
        emphasis_color=theme.emphasis_color,
        emphasis_font_size=_resolve_popup_font_size(popup, emphasis_font_size),
        emphasis_weight=emphasis_weight,
    )
    message_widget = message_block.widget
    normal_font = message_block.base_font
    emphasis_font = message_block.emphasis_font
    plain_text = message_block.plain_text

    scrollbar_visible = False
    if scrollable:
        message_widget.grid(row=0, column=0, sticky="nsew")
        scrollbar = ctk.CTkScrollbar(message_frame, orientation="vertical", command=message_widget.yview)
        message_widget.configure(yscrollcommand=scrollbar.set)

        def _set_scrollbar_visible(visible: bool) -> None:
            nonlocal scrollbar_visible
            if visible and not scrollbar_visible:
                scrollbar.grid(row=0, column=1, sticky="ns", padx=(10, 0))
                scrollbar_visible = True
            elif not visible and scrollbar_visible:
                scrollbar.grid_remove()
                scrollbar_visible = False
    else:
        message_widget.pack(anchor="w", fill="x", pady=(0, 6))

        def _set_scrollbar_visible(visible: bool) -> None:
            return None

    screen_w = max(1, int(root.winfo_screenwidth() or root_width_fallback))
    screen_h = max(1, int(root.winfo_screenheight() or root_height_fallback))
    root_w = max(1, int(root.winfo_width() or root_width_fallback))

    avg_char_width = max(7, int(normal_font.measure("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz") / 52))
    zero_char_width = max(7, int(max(normal_font.measure("0"), emphasis_font.measure("0"))))
    max_popup_h = max(240, screen_h - 80)
    desired_popup_w = None
    if scrollable:
        desired_popup_w = min(max(360, root_w), max(360, screen_w - 40))

    capped_max_text_chars = max(
        min_text_chars,
        min(max_text_chars, max(min_text_chars, (screen_w - 140) // max(1, avg_char_width))),
    )
    if preferred_text_chars is None:
        if desired_popup_w is not None:
            target_text_px = max(220, desired_popup_w - 72)
            preferred_width = int(math.ceil(target_text_px / max(1, zero_char_width)))
            preferred_text_chars = max(min_text_chars, min(capped_max_text_chars, preferred_width))
        else:
            preferred_text_chars = min_text_chars
    preferred_text_chars = max(min_text_chars, int(preferred_text_chars))
    capped_max_text_chars = max(preferred_text_chars, capped_max_text_chars)
    width_steps = _build_width_steps(preferred_text_chars, capped_max_text_chars)
    line_height_px = max(normal_font.metrics("linespace"), emphasis_font.metrics("linespace")) + 2

    chosen_width = preferred_text_chars
    resolved_line_count = max(1, plain_text.count("\n") + 1)

    def _layout_non_scrollable_message() -> None:
        nonlocal chosen_width, resolved_line_count
        for width_chars in width_steps:
            message_widget.configure(width=width_chars)
            popup.update_idletasks()
            resolved_line_count = estimate_wrapped_text_lines(
                plain_text,
                normal_font,
                max(32, zero_char_width * width_chars),
            )
            message_widget.configure(height=resolved_line_count)
            popup.update_idletasks()
            chosen_width = width_chars
            if popup.winfo_reqheight() <= max_popup_h:
                break
        message_widget.configure(width=chosen_width, height=resolved_line_count)

    def _layout_scrollable_message() -> None:
        nonlocal chosen_width, resolved_line_count
        _set_scrollbar_visible(False)
        for width_chars in width_steps:
            message_widget.configure(width=width_chars, height=1)
            popup.update_idletasks()
            resolved_line_count = estimate_wrapped_text_lines(
                plain_text,
                normal_font,
                max(32, zero_char_width * width_chars),
            )
            message_widget.configure(height=resolved_line_count)
            popup.update_idletasks()
            chosen_width = width_chars
            if popup.winfo_reqheight() <= max_popup_h:
                break

        popup.update_idletasks()
        if popup.winfo_reqheight() > max_popup_h:
            chrome_height = max(0, popup.winfo_reqheight() - message_widget.winfo_reqheight())
            max_text_height_px = max(96, max_popup_h - chrome_height)
            max_visible_lines = max(4, int(max_text_height_px / max(1, line_height_px)))
            message_widget.configure(height=min(resolved_line_count, max_visible_lines))
            _set_scrollbar_visible(True)
            popup.update_idletasks()

    def _sync_scrollable_message_height() -> None:
        try:
            popup.update_idletasks()
            actual_width_px = max(32, int(message_widget.winfo_width() or (zero_char_width * chosen_width)))
            actual_line_count = estimate_wrapped_text_lines(
                plain_text,
                normal_font,
                actual_width_px,
            )
            target_lines = actual_line_count
            if scrollbar_visible:
                chrome_height = max(0, popup.winfo_reqheight() - message_widget.winfo_reqheight())
                max_text_height_px = max(96, max_popup_h - chrome_height)
                max_visible_lines = max(4, int(max_text_height_px / max(1, line_height_px)))
                target_lines = min(actual_line_count, max_visible_lines)
            if int(message_widget.cget("height")) != target_lines:
                message_widget.configure(height=target_lines)
                popup.update_idletasks()
        except Exception:
            logging.debug("Failed to reflow %s text", debug_name, exc_info=True)

    def _apply_current_popup_geometry(use_requested_size: bool = False) -> None:
        if scrollable:
            _sync_scrollable_message_height()
        _apply_popup_geometry(
            root,
            popup,
            screen_w=screen_w,
            screen_h=screen_h,
            desired_popup_w=desired_popup_w,
            min_popup_w=220 if scrollable else 200,
            min_popup_h=140 if scrollable else 120,
            use_requested_size=(use_requested_size or scrollable),
            debug_name=debug_name,
        )

    def _initial_layout() -> None:
        if scrollable:
            _layout_scrollable_message()
        else:
            _layout_non_scrollable_message()
        _apply_current_popup_geometry(use_requested_size=True)

    message_widget.configure(state="disabled")

    button_row = ctk.CTkFrame(container, fg_color="transparent")
    button_row.pack(fill="x", pady=(10, 0))

    close_button: Optional[ctk.CTkButton] = None
    fade_controller = PopupFadeController(popup, debug_name=debug_name)

    def _after_close() -> None:
        if callable(on_close):
            on_close()

    def _close_popup() -> None:
        if fade_controller.is_closing:
            return
        if close_button is not None:
            try:
                close_button.configure(state="disabled")
            except Exception:
                pass
        fade_controller.close(_after_close)

    close_button = ctk.CTkButton(
        button_row,
        text=confirm_text,
        width=100,
        height=34,
        corner_radius=8,
        fg_color=theme.accent_color,
        hover_color=theme.accent_hover_color,
        text_color=theme.button_text_color,
        font=ctk.CTkFont(family=theme.font_ui, size=12, weight="bold"),
        command=_close_popup,
    )
    close_button.pack()

    popup.protocol("WM_DELETE_WINDOW", _close_popup if allow_window_close else (lambda: None))
    present_modal_popup(
        popup,
        initial_layout=_initial_layout,
        post_show_layout=_apply_current_popup_geometry if scrollable else None,
        after_idle_layout=_apply_current_popup_geometry,
        fade_controller=fade_controller,
    )
    popup.wait_window()
