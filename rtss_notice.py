from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re
import tkinter as tk
import tkinter.font as tkfont
from typing import Any, Mapping, Optional

import customtkinter as ctk
from PIL import Image

if os.name == "nt":
    import winreg


@dataclass(frozen=True)
class RtssNoticeTheme:
    surface_color: str
    accent_color: str
    accent_hover_color: str
    font_ui: str
    body_text_color: str = "#E3EAF3"
    warning_text_color: str = "#FF4D4F"
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
    key = "rtss_kr" if use_korean else "rtss_en"
    raw_message = str(module_download_links.get(key, "") or "").strip()
    if raw_message:
        return raw_message

    if use_korean:
        return (
            "RTSS 설정을 확인해주세요.\n\n"
            "[Global]\n"
            "UseDetours=1\n"
            "ReflexSetLatencyMarker=0\n\n"
            "위 설정이 적용되어 있는지 확인해 주세요."
        )
    return (
        "RTSS Configuration Check:\n\n"
        "Please ensure the following settings in your Global profile:\n"
        "UseDetours=1\n"
        "ReflexSetLatencyMarker=0"
    )


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
) -> None:
    popup = ctk.CTkToplevel(root)
    popup.title("RTSS Notice")
    popup.transient(root)
    popup.grab_set()
    popup.resizable(False, False)
    popup.configure(fg_color=theme.surface_color)
    popup.withdraw()

    container = ctk.CTkFrame(popup, fg_color="transparent")
    container.pack(fill="both", expand=True, padx=22, pady=(18, 12))

    text = message_text or "(No message)"
    pattern = re.compile(r"\[\s*RED\s*\](.*?)\[\s*END\s*\]", re.IGNORECASE | re.DOTALL)
    last = 0
    message_widget = tk.Text(
        container,
        wrap="word",
        relief="flat",
        borderwidth=0,
        highlightthickness=0,
        bg=theme.surface_color,
        fg=theme.body_text_color,
        width=58,
    )
    normal_font = tkfont.Font(family=theme.font_ui, size=13)
    red_font = tkfont.Font(family=theme.font_ui, size=14, weight="bold")
    message_widget.configure(font=normal_font)
    message_widget.tag_configure("warning_red", foreground=theme.warning_text_color, font=red_font)

    full_plain_text = ""
    for match in pattern.finditer(text):
        if match.start() > last:
            normal = text[last:match.start()]
            message_widget.insert("end", normal)
            full_plain_text += normal
        red_text = match.group(1)
        if red_text:
            message_widget.insert("end", red_text, ("warning_red",))
            full_plain_text += red_text
        last = match.end()
    if last < len(text):
        tail = text[last:]
        message_widget.insert("end", tail)
        full_plain_text += tail

    line_count = max(1, min(16, full_plain_text.count("\n") + 1))
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

    fade_in_step = 0.14
    fade_out_step = 0.18
    fade_interval_ms = 18
    fade_out_interval_ms = 16
    fade_supported = False
    fade_in_after_id = None
    closing_popup = False
    close_button: Optional[ctk.CTkButton] = None

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
            logging.debug("RTSS popup fade-in failed", exc_info=True)
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
            logging.debug("RTSS popup fade-out failed", exc_info=True)
            _finalize_close()
            return
        if next_opacity > 0.0:
            popup.after(fade_out_interval_ms, _fade_out, next_opacity)
        else:
            _finalize_close()

    def _close_popup():
        nonlocal closing_popup, fade_in_after_id
        if closing_popup:
            return
        closing_popup = True
        if close_button is not None:
            try:
                close_button.configure(state="disabled")
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

    close_button = ctk.CTkButton(
        popup,
        text="OK",
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
    _center_rtss_popup_on_root(root, popup, use_requested_size=True)
    try:
        popup.attributes("-alpha", 0.0)
        fade_supported = True
    except Exception:
        fade_supported = False
        logging.debug("Popup alpha fade is not supported for RTSS popup", exc_info=True)
    popup.deiconify()
    popup.lift()
    try:
        popup.focus_set()
    except Exception:
        pass
    popup.after(0, lambda p=popup: _center_rtss_popup_on_root(root, p))
    if fade_supported:
        fade_in_after_id = popup.after(45, _fade_in, 0.0)
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
        _show_rtss_popup(root, decision.message_text, assets_dir, theme)
    return decision
