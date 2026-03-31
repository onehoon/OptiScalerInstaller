from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Optional, Sequence, TYPE_CHECKING

import customtkinter as ctk

if TYPE_CHECKING:
    from gpu_service import GpuAdapterChoice


@dataclass(frozen=True)
class GpuNoticeTheme:
    surface_color: str
    accent_color: str
    accent_hover_color: str
    font_ui: str
    body_text_color: str = "#E3EAF3"
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


def get_unsupported_gpu_title(use_korean: bool) -> str:
    return "지원되지 않는 GPU 구성" if use_korean else "Unsupported GPU Configuration"


def get_unsupported_gpu_message(use_korean: bool) -> str:
    if use_korean:
        return "3개 이상의 GPU가 감지되었습니다.\n현재 설치는 지원되지 않습니다."
    return "3 or more GPUs were detected.\nThis installation is not supported."


def _get_dual_gpu_selection_message(use_korean: bool) -> str:
    if use_korean:
        return (
            "듀얼 GPU가 감지되었습니다.\n"
            "OptiScaler를 어느 GPU 기준으로 설치할지 선택해 주세요.\n"
            "선택한 GPU에 맞는 설정으로 설치됩니다.\n"
            "다른 GPU로 실행 시 정상적으로 동작하지 않을 수 있습니다."
        )
    return (
        "Dual GPUs were detected.\n"
        "Select which GPU OptiScaler should be installed for.\n"
        "Installation will use settings for the selected GPU.\n"
        "It may not work correctly if the game is run on the other GPU."
    )


def _get_vendor_display_name(vendor: str) -> str:
    normalized = str(vendor or "").strip().lower()
    if normalized == "nvidia":
        return "NVIDIA"
    if normalized == "amd":
        return "AMD"
    if normalized == "intel":
        return "Intel"
    return "Unknown"


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
    use_korean: bool,
    theme: GpuNoticeTheme,
) -> None:
    desired_popup_width = _resolve_popup_width(root, UNSUPPORTED_GPU_POPUP_MIN_W)
    message_width = max(280, desired_popup_width - 88)

    popup = ctk.CTkToplevel(root)
    popup.title(get_unsupported_gpu_title(use_korean))
    popup.transient(root)
    popup.grab_set()
    popup.resizable(False, False)
    popup.configure(fg_color=theme.surface_color)
    popup.withdraw()

    container = ctk.CTkFrame(popup, fg_color="transparent")
    container.pack(fill="both", expand=True, padx=22, pady=(20, 18))

    ctk.CTkLabel(
        container,
        text=get_unsupported_gpu_message(use_korean),
        justify="left",
        anchor="w",
        width=message_width,
        wraplength=message_width,
        text_color=theme.body_text_color,
        font=ctk.CTkFont(family=theme.font_ui, size=13),
    ).pack(fill="x", pady=(0, 14))

    def _close_popup() -> None:
        try:
            popup.grab_release()
        except Exception:
            pass
        popup.destroy()

    ctk.CTkButton(
        container,
        text="확인" if use_korean else "OK",
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
    popup.deiconify()
    popup.lift()
    try:
        popup.focus_set()
    except Exception:
        pass
    _center_gpu_popup_on_root(root, popup, target_width_px=desired_popup_width, use_requested_size=True)
    popup.after(0, lambda p=popup: _center_gpu_popup_on_root(root, p, target_width_px=desired_popup_width))
    popup.wait_window()


def select_dual_gpu_adapter(
    root: ctk.CTk,
    adapters: Sequence["GpuAdapterChoice"],
    use_korean: bool,
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

    popup = ctk.CTkToplevel(root)
    popup.title("GPU Selection" if not use_korean else "GPU 선택")
    popup.transient(root)
    popup.grab_set()
    popup.resizable(False, False)
    popup.configure(fg_color=theme.surface_color)
    popup.withdraw()

    container = ctk.CTkFrame(popup, fg_color="transparent")
    container.pack(fill="both", expand=True, padx=22, pady=(20, 18))

    ctk.CTkLabel(
        container,
        text=_get_dual_gpu_selection_message(use_korean),
        justify="left",
        anchor="w",
        width=message_width,
        wraplength=message_width,
        text_color=theme.body_text_color,
        font=ctk.CTkFont(family=theme.font_ui, size=13),
    ).pack(fill="x", pady=(0, 16))

    button_row = ctk.CTkFrame(container, fg_color="transparent")
    button_row.pack(anchor="center")

    choice_buttons: list[ctk.CTkButton] = []
    fade_in_step = 0.14
    fade_out_step = 0.18
    fade_interval_ms = 18
    fade_out_interval_ms = 16
    fade_supported = False
    fade_in_after_id = None
    closing_popup = False

    def _popup_exists() -> bool:
        try:
            return bool(popup.winfo_exists())
        except Exception:
            return False

    def _get_popup_alpha() -> float:
        try:
            return float(popup.attributes("-alpha"))
        except Exception:
            return 1.0

    def _finalize_close() -> None:
        try:
            popup.grab_release()
        except Exception:
            pass
        try:
            popup.destroy()
        except Exception:
            pass

    def _fade_in(opacity: float = 0.0) -> None:
        nonlocal fade_in_after_id
        if closing_popup or not _popup_exists():
            return
        next_opacity = min(1.0, opacity + fade_in_step)
        try:
            popup.attributes("-alpha", next_opacity)
        except Exception:
            fade_in_after_id = None
            logging.debug("GPU selection popup fade-in failed", exc_info=True)
            try:
                popup.attributes("-alpha", 1.0)
            except Exception:
                pass
            return
        if next_opacity < 1.0:
            fade_in_after_id = popup.after(fade_interval_ms, _fade_in, next_opacity)
        else:
            fade_in_after_id = None

    def _fade_out(opacity: float) -> None:
        if not _popup_exists():
            return
        next_opacity = max(0.0, opacity - fade_out_step)
        try:
            popup.attributes("-alpha", next_opacity)
        except Exception:
            logging.debug("GPU selection popup fade-out failed", exc_info=True)
            _finalize_close()
            return
        if next_opacity > 0.0:
            popup.after(fade_out_interval_ms, _fade_out, next_opacity)
        else:
            _finalize_close()

    def _close_with_selection(adapter: "GpuAdapterChoice") -> None:
        nonlocal selected_adapter, closing_popup, fade_in_after_id
        if selected_adapter is not None or closing_popup:
            return

        selected_adapter = adapter
        closing_popup = True
        for btn in choice_buttons:
            try:
                btn.configure(state="disabled")
            except Exception:
                pass
        if fade_in_after_id is not None:
            try:
                popup.after_cancel(fade_in_after_id)
            except Exception:
                pass
            fade_in_after_id = None
        if fade_supported:
            _fade_out(_get_popup_alpha())
        else:
            _finalize_close()

    for col_idx, adapter in enumerate(adapter_choices):
        button_theme = _get_vendor_button_theme(getattr(adapter, "vendor", ""), theme)
        vendor_label = _get_vendor_display_name(getattr(adapter, "vendor", ""))
        model_label = str(getattr(adapter, "display_name", "") or getattr(adapter, "model_name", "") or "").strip()
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
    _center_gpu_popup_on_root(root, popup, use_requested_size=True)
    try:
        popup.attributes("-alpha", 0.0)
        fade_supported = True
    except Exception:
        fade_supported = False
        logging.debug("Popup alpha fade is not supported for GPU selection popup", exc_info=True)
    popup.deiconify()
    popup.lift()
    try:
        popup.focus_set()
    except Exception:
        pass
    popup.after(0, lambda p=popup: _center_gpu_popup_on_root(root, p))
    if fade_supported:
        fade_in_after_id = popup.after(45, _fade_in, 0.0)
    popup.wait_window()
    return selected_adapter
