from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
from typing import Any, Mapping, Optional

import customtkinter as ctk
from PIL import Image
from .popup_markup import create_popup_markup_text, estimate_wrapped_text_lines
from .popup_utils import PopupFadeController, create_modal_popup, present_modal_popup
from ..i18n import get_app_strings, lang_from_bool, pick_sheet_text

if os.name == "nt":
    import winreg


@dataclass(frozen=True)
class RtssNoticeTheme:
    surface_color: str
    accent_color: str
    accent_hover_color: str
    font_ui: str
    body_text_color: str = "#E3EAF3"
    warning_text_color: str = "#FFCB62"
    button_text_color: str = "#000000"


@dataclass(frozen=True)
class RtssNoticeDecision:
    should_show: bool
    message_text: str
    install_path: Optional[Path]
    global_path: Optional[Path]
    reflex_value: Optional[str]
    detours_value: Optional[str]


def _log_info_if_logger(logger: Any, message: str, *args) -> None:
    if logger:
        logger.info(message, *args)
    else:
        logging.info(message, *args)


def _log_warning(logger: Any, message: str, *args) -> None:
    if logger:
        logger.warning(message, *args)
    else:
        logging.warning(message, *args)


def _get_rtss_install_path() -> Path:
    if os.name == "nt":
        roots = [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]
        subkeys = [r"SOFTWARE\WOW6432Node\Unwinder\RTSS", r"SOFTWARE\Unwinder\RTSS"]

        for root in roots:
            for subkey in subkeys:
                try:
                    with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ) as key:
                        val, _ = winreg.QueryValueEx(key, "InstallPath")
                        if val:
                            path = Path(val)
                            if path.is_file() and path.name.lower() == "rtss.exe":
                                path = path.parent
                            if path.exists():
                                return path
                except Exception:
                    continue

    return Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "RivaTuner Statistics Server"


def _read_rtss_global_settings(global_path: Path) -> tuple[Optional[str], Optional[str]]:
    ref_val, detours_val = None, None
    lines = global_path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
    for line in lines:
        line = line.strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized_key = key.strip()
        normalized_value = value.strip()
        if normalized_key == "ReflexSetLatencyMarker":
            ref_val = normalized_value
        elif normalized_key == "UseDetours":
            detours_val = normalized_value
    return ref_val, detours_val


def _is_rtss_config_ok(ref_val: Optional[str], detours_val: Optional[str]) -> bool:
    return ref_val == "0" and detours_val == "1"


def _build_rtss_message(module_download_links: Mapping[str, object], use_korean: bool) -> str:
    lang = lang_from_bool(use_korean)
    strings = get_app_strings(lang)
    raw_message = pick_sheet_text(module_download_links, "rtss", lang)
    if raw_message:
        return raw_message

    return strings.rtss.fallback_message


def _evaluate_rtss_notice(
    module_download_links: Mapping[str, object],
    use_korean: bool,
    logger: Any = None,
) -> RtssNoticeDecision:
    install_path = _get_rtss_install_path()
    profiles_dir = install_path / "Profiles"
    global_path = profiles_dir / "Global"

    if not (profiles_dir.exists() and global_path.exists()):
        _log_info_if_logger(logger, "RTSS not installed or Global file missing at %s", global_path)
        return RtssNoticeDecision(
            should_show=False,
            message_text="",
            install_path=install_path,
            global_path=global_path,
            reflex_value=None,
            detours_value=None,
        )

    ref_val, detours_val = _read_rtss_global_settings(global_path)
    _log_info_if_logger(
        logger,
        "[RTSS] Global settings: UseDetours=%s, ReflexSetLatencyMarker=%s",
        detours_val,
        ref_val,
    )

    if _is_rtss_config_ok(ref_val, detours_val):
        _log_info_if_logger(logger, "[RTSS] Settings OK, notice not shown")
        return RtssNoticeDecision(
            should_show=False,
            message_text="",
            install_path=install_path,
            global_path=global_path,
            reflex_value=ref_val,
            detours_value=detours_val,
        )

    _log_info_if_logger(logger, "[RTSS] Settings require notice, popup will be shown")
    return RtssNoticeDecision(
        should_show=True,
        message_text=_build_rtss_message(module_download_links, use_korean),
        install_path=install_path,
        global_path=global_path,
        reflex_value=ref_val,
        detours_value=detours_val,
    )


def _center_rtss_popup_on_root(
    root: ctk.CTk,
    popup: ctk.CTkToplevel,
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
        margin = 12
        x = root_x + (root_w // 2) - (popup_w // 2)
        y = root_y + (root_h // 2) - (popup_h // 2)
        min_x = margin if popup_w + (margin * 2) < screen_w else 0
        min_y = margin if popup_h + (margin * 2) < screen_h else 0
        max_x = max(min_x, screen_w - popup_w - margin)
        max_y = max(min_y, screen_h - popup_h - margin)
        x = max(min_x, min(x, max_x))
        y = max(min_y, min(y, max_y))
        popup.geometry(f"+{x}+{y}")
    except Exception:
        logging.debug("Failed to center RTSS popup on root window", exc_info=True)


def _show_rtss_popup(
    root: ctk.CTk,
    message_text: str,
    assets_dir: Path,
    theme: RtssNoticeTheme,
    use_korean: bool,
) -> None:
    strings = get_app_strings(lang_from_bool(use_korean))
    popup = create_modal_popup(root, strings.rtss.notice_title, theme.surface_color)

    container = ctk.CTkFrame(popup, fg_color="transparent")
    container.pack(fill="both", expand=True, padx=22, pady=(18, 12))

    text = message_text or strings.rtss.no_message
    message_block = create_popup_markup_text(
        container,
        text,
        background_color=theme.surface_color,
        body_text_color=theme.body_text_color,
        font_family=theme.font_ui,
        base_font_size=13,
        emphasis_color=theme.warning_text_color,
        emphasis_font_size=14,
    )
    message_widget = message_block.widget
    wrap_width_px = max(32, int(message_block.base_font.measure("0")) * int(message_widget.cget("width")))
    line_count = max(1, min(16, estimate_wrapped_text_lines(message_block.plain_text, message_block.base_font, wrap_width_px)))
    message_widget.configure(height=line_count)
    message_widget.configure(state="disabled")
    message_widget.pack(anchor="w", fill="x")

    try:
        img_path = Path(assets_dir) / "RTSS.webp"
        if img_path.exists():
            pil_img = Image.open(img_path)
            orig_w, orig_h = pil_img.size
            new_w = int(orig_w * 0.75)
            new_h = int(orig_h * 0.75)
            ctk_image = ctk.CTkImage(light_image=pil_img, size=(new_w, new_h))
            img_label = ctk.CTkLabel(container, image=ctk_image, text="")
            img_label.image = ctk_image
            img_label.pack(pady=(12, 0))
    except Exception:
        logging.debug("Failed to load RTSS notice image", exc_info=True)

    close_button: Optional[ctk.CTkButton] = None
    fade_controller = PopupFadeController(popup, debug_name="RTSS popup")

    def _close_popup():
        if fade_controller.is_closing:
            return
        if close_button is not None:
            try:
                close_button.configure(state="disabled")
            except Exception:
                pass
        fade_controller.close()

    close_button = ctk.CTkButton(
        popup,
        text=strings.common.ok,
        width=100,
        height=34,
        corner_radius=8,
        fg_color=theme.accent_color,
        hover_color=theme.accent_hover_color,
        text_color=theme.button_text_color,
        font=ctk.CTkFont(family=theme.font_ui, size=12, weight="bold"),
        command=_close_popup,
    )
    close_button.pack(pady=(0, 14))

    popup.protocol("WM_DELETE_WINDOW", _close_popup)
    present_modal_popup(
        popup,
        initial_layout=lambda: _center_rtss_popup_on_root(root, popup, use_requested_size=True),
        after_idle_layout=lambda p=popup: _center_rtss_popup_on_root(root, p),
        fade_controller=fade_controller,
    )
    popup.wait_window()


def check_and_show_rtss_notice(
    root: ctk.CTk,
    module_download_links: Mapping[str, object],
    use_korean: bool,
    assets_dir: Path,
    theme: RtssNoticeTheme,
    logger: Any = None,
) -> RtssNoticeDecision:
    try:
        decision = _evaluate_rtss_notice(module_download_links, use_korean, logger=logger)
    except Exception as exc:
        _log_warning(logger, "Error during RTSS popup check: %s", exc)
        return RtssNoticeDecision(
            should_show=False,
            message_text="",
            install_path=None,
            global_path=None,
            reflex_value=None,
            detours_value=None,
        )

    if decision.should_show:
        _show_rtss_popup(root, decision.message_text, assets_dir, theme, use_korean)
    return decision
