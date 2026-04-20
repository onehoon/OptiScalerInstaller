from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Optional, Sequence, TYPE_CHECKING

import customtkinter as ctk
from .popup_markup import create_popup_markup_text, estimate_wrapped_text_lines
from .popup_utils import PopupFadeController, close_modal_popup, create_modal_popup, present_modal_popup
from ..i18n import AppStrings

if TYPE_CHECKING:
    from ..system.gpu_service import GpuAdapterChoice


@dataclass(frozen=True)
class GpuNoticeTheme:
    surface_color: str
    accent_color: str
    accent_hover_color: str
    font_ui: str
    body_text_color: str = "#E3EAF3"
    warning_text_color: str = "#FFCB62"
    button_text_color: str = "#000000"


@dataclass(frozen=True)
class GpuVendorButtonTheme:
    fg_color: str
    hover_color: str
    text_color: str


UNSUPPORTED_GPU_POPUP_MIN_W = 420
DUAL_GPU_BUTTON_W = 186
DUAL_GPU_BUTTON_H = 66
DUAL_GPU_BUTTON_GAP = 12
GPU_POPUP_MARGIN = 12

INTEL_BUTTON_THEME = GpuVendorButtonTheme(
    fg_color="#0068B5",
    hover_color="#005A9E",
    text_color="#FFFFFF",
)
AMD_BUTTON_THEME = GpuVendorButtonTheme(
    fg_color="#ED1C24",
    hover_color="#C9141A",
    text_color="#FFFFFF",
)
NVIDIA_BUTTON_THEME = GpuVendorButtonTheme(
    fg_color="#76B900",
    hover_color="#5F9500",
    text_color="#111111",
)

def get_unsupported_gpu_message(strings: AppStrings) -> str:
    return strings.gpu.unsupported_message


def _get_vendor_display_name(vendor: str, strings: AppStrings) -> str:
    normalized = str(vendor or "").strip().lower()
    if normalized == "nvidia":
        return "NVIDIA"
    if normalized == "amd":
        return "AMD"
    if normalized == "intel":
        return "Intel"
    return strings.gpu.vendor_unknown


def _get_vendor_button_theme(vendor: str, theme: GpuNoticeTheme) -> GpuVendorButtonTheme:
    normalized = str(vendor or "").strip().lower()
    if normalized == "intel":
        return INTEL_BUTTON_THEME
    if normalized == "amd":
        return AMD_BUTTON_THEME
    if normalized == "nvidia":
        return NVIDIA_BUTTON_THEME
    return GpuVendorButtonTheme(
        fg_color=theme.accent_color,
        hover_color=theme.accent_hover_color,
        text_color=theme.button_text_color,
    )


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
        logging.debug("Failed to resolve GPU popup font scaling", exc_info=True)

    return -max(1, logical_size)


def _center_gpu_popup_on_root(
    root: ctk.CTk,
    popup: ctk.CTkToplevel,
    target_width_px: Optional[int] = None,
    use_requested_size: bool = False,
) -> None:
    try:
        popup.update_idletasks()

        root_x = root.winfo_x()
        root_y = root.winfo_y()
        root_w = root.winfo_width()
        root_h = root.winfo_height()

        popup_w = popup.winfo_reqwidth() if use_requested_size else popup.winfo_width()
        popup_h = popup.winfo_reqheight() if use_requested_size else popup.winfo_height()

        screen_w = max(1, int(root.winfo_screenwidth() or popup_w))
        screen_h = max(1, int(root.winfo_screenheight() or popup_h))
        margin = GPU_POPUP_MARGIN
        if target_width_px is not None:
            popup_w = max(popup_w, min(int(target_width_px), screen_w - (margin * 2)))
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
        logging.debug("Failed to center GPU popup on root window", exc_info=True)


def _resolve_popup_width(root: ctk.CTk, min_width_px: int) -> int:
    root_w = max(1, int(root.winfo_width() or 512))
    screen_w = max(1, int(root.winfo_screenwidth() or root_w))
    return max(min_width_px, min(root_w, screen_w - (GPU_POPUP_MARGIN * 2)))


def show_unsupported_gpu_notice(
    root: ctk.CTk,
    strings: AppStrings,
    theme: GpuNoticeTheme,
) -> None:
    desired_popup_width = _resolve_popup_width(root, UNSUPPORTED_GPU_POPUP_MIN_W)
    message_width = max(280, desired_popup_width - 88)

    popup = create_modal_popup(root, strings.gpu.unsupported_title, theme.surface_color)

    container = ctk.CTkFrame(popup, fg_color="transparent")
    container.pack(fill="both", expand=True, padx=22, pady=(20, 18))

    message_block = create_popup_markup_text(
        container,
        strings.gpu.unsupported_message,
        background_color=theme.surface_color,
        body_text_color=theme.body_text_color,
        font_family=theme.font_ui,
        base_font_size=_resolve_popup_font_size(popup, 13),
        emphasis_color=theme.warning_text_color,
    )
    message_widget = message_block.widget
    zero_char_width = max(7, int(message_block.base_font.measure("0")))
    text_width_chars = max(28, (message_width + max(1, zero_char_width) - 1) // max(1, zero_char_width))
    line_count = estimate_wrapped_text_lines(message_block.plain_text, message_block.base_font, message_width)
    message_widget.configure(width=text_width_chars, height=line_count, state="disabled")
    message_widget.pack(fill="x", pady=(0, 14))

    def _close_popup() -> None:
        close_modal_popup(popup)

    ctk.CTkButton(
        container,
        text=strings.common.ok,
        width=100,
        height=34,
        corner_radius=8,
        fg_color=theme.accent_color,
        hover_color=theme.accent_hover_color,
        text_color=theme.button_text_color,
        font=ctk.CTkFont(family=theme.font_ui, size=12, weight="bold"),
        command=_close_popup,
    ).pack()

    popup.protocol("WM_DELETE_WINDOW", _close_popup)
    present_modal_popup(
        popup,
        initial_layout=lambda: _center_gpu_popup_on_root(
            root,
            popup,
            target_width_px=desired_popup_width,
            use_requested_size=True,
        ),
        after_idle_layout=lambda p=popup: _center_gpu_popup_on_root(
            root,
            p,
            target_width_px=desired_popup_width,
        ),
    )
    popup.wait_window()


def select_dual_gpu_adapter(
    root: ctk.CTk,
    adapters: Sequence["GpuAdapterChoice"],
    strings: AppStrings,
    theme: GpuNoticeTheme,
) -> Optional["GpuAdapterChoice"]:
    adapter_choices = list(adapters[:2])
    if len(adapter_choices) < 2:
        return None

    selected_adapter: Optional["GpuAdapterChoice"] = None
    screen_w = max(1, int(root.winfo_screenwidth() or 512))
    max_message_width = max(320, min(420, screen_w - 140))
    button_row_width = (DUAL_GPU_BUTTON_W * 2) + (DUAL_GPU_BUTTON_GAP * 2)
    message_width = max(320, min(max_message_width, button_row_width))

    popup = create_modal_popup(
        root,
        strings.gpu.dual_selection_title,
        theme.surface_color,
    )

    container = ctk.CTkFrame(popup, fg_color="transparent")
    container.pack(fill="both", expand=True, padx=22, pady=(20, 18))

    message_block = create_popup_markup_text(
        container,
        strings.gpu.dual_selection_message,
        background_color=theme.surface_color,
        body_text_color=theme.body_text_color,
        font_family=theme.font_ui,
        base_font_size=_resolve_popup_font_size(popup, 13),
        emphasis_color=theme.warning_text_color,
    )
    message_widget = message_block.widget
    zero_char_width = max(7, int(message_block.base_font.measure("0")))
    text_width_chars = max(32, (message_width + max(1, zero_char_width) - 1) // max(1, zero_char_width))
    line_count = estimate_wrapped_text_lines(message_block.plain_text, message_block.base_font, message_width)
    message_widget.configure(width=text_width_chars, height=line_count, state="disabled")
    message_widget.pack(fill="x", pady=(0, 16))

    button_row = ctk.CTkFrame(container, fg_color="transparent")
    button_row.pack(anchor="center")

    choice_buttons: list[ctk.CTkButton] = []
    fade_controller = PopupFadeController(popup, debug_name="GPU selection popup")

    def _close_with_selection(adapter: "GpuAdapterChoice") -> None:
        nonlocal selected_adapter
        if selected_adapter is not None or fade_controller.is_closing:
            return

        selected_adapter = adapter
        for btn in choice_buttons:
            try:
                btn.configure(state="disabled")
            except Exception:
                pass
        fade_controller.close()

    for col_idx, adapter in enumerate(adapter_choices):
        button_theme = _get_vendor_button_theme(adapter.vendor, theme)
        vendor_label = _get_vendor_display_name(adapter.vendor, strings)
        model_label = str(adapter.display_name or adapter.model_name or "").strip()
        button_text = vendor_label if not model_label else f"{vendor_label}\n{model_label}"
        btn = ctk.CTkButton(
            button_row,
            text=button_text,
            width=DUAL_GPU_BUTTON_W,
            height=DUAL_GPU_BUTTON_H,
            corner_radius=10,
            fg_color=button_theme.fg_color,
            hover_color=button_theme.hover_color,
            text_color=button_theme.text_color,
            font=ctk.CTkFont(family=theme.font_ui, size=12, weight="bold"),
            command=lambda selected=adapter: _close_with_selection(selected),
        )
        btn.grid(row=0, column=col_idx, padx=(0, DUAL_GPU_BUTTON_GAP) if col_idx == 0 else (DUAL_GPU_BUTTON_GAP, 0))
        choice_buttons.append(btn)

    popup.protocol("WM_DELETE_WINDOW", lambda: None)
    present_modal_popup(
        popup,
        initial_layout=lambda: _center_gpu_popup_on_root(root, popup, use_requested_size=True),
        after_idle_layout=lambda p=popup: _center_gpu_popup_on_root(root, p),
        fade_controller=fade_controller,
    )
    popup.wait_window()
    return selected_adapter
