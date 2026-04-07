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
        margin = 12
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
        logging.debug("Failed to center RTSS popup on root window", exc_info=True)


def _resolve_popup_font_size(popup: ctk.CTkToplevel, size: int) -> int:
    logical_size = max(1, int(size))
    try:
        if hasattr(popup, "_get_window_scaling"):
            scale = float(popup._get_window_scaling())
            if scale > 0:
                return -max(1, int(round(logical_size * scale)))
    except Exception:
        logging.debug("Failed to resolve RTSS popup font scaling", exc_info=True)
    return -logical_size


def _resolve_popup_width(root: ctk.CTk, min_width_px: int) -> int:
    root_w = max(1, int(root.winfo_width() or 512))
    screen_w = max(1, int(root.winfo_screenwidth() or root_w))
    return max(min_width_px, min(root_w, screen_w - 24))


def _show_rtss_popup(
    root: ctk.CTk,
    message_text: str,
    assets_dir: Path,
    theme: RtssNoticeTheme,
    use_korean: bool,
) -> None:
    strings = get_app_strings(lang_from_bool(use_korean))
    desired_popup_width = _resolve_popup_width(root, 420)
    message_width = max(280, desired_popup_width - 72)
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
        base_font_size=_resolve_popup_font_size(popup, 13),
        emphasis_color=theme.warning_text_color,
        emphasis_font_size=_resolve_popup_font_size(popup, 14),
    )
    message_widget = message_block.widget
    zero_char_width = max(
        7,
        int(max(message_block.base_font.measure("0"), message_block.emphasis_font.measure("0"))),
    )
    text_width_chars = max(28, (message_width + max(1, zero_char_width) - 1) // max(1, zero_char_width))
    line_count = estimate_wrapped_text_lines(message_block.plain_text, message_block.base_font, message_width)
    message_widget.configure(width=text_width_chars, height=line_count)
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
        initial_layout=lambda: _center_rtss_popup_on_root(
            root,
            popup,
            target_width_px=desired_popup_width,
            use_requested_size=True,
        ),
        after_idle_layout=lambda p=popup: _center_rtss_popup_on_root(
            root,
            p,
            target_width_px=desired_popup_width,
        ),
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


def _write_rtss_global_settings(global_path: Path, logger: Any = None) -> None:
    TARGET_KEYS = {
        "ReflexSetLatencyMarker": "0",
        "UseDetours": "1",
    }

    raw_bytes = global_path.read_bytes()

    # Detect BOM
    has_bom = raw_bytes.startswith(b"\xef\xbb\xbf")
    content_bytes = raw_bytes[3:] if has_bom else raw_bytes

    # Detect line ending
    if b"\r\n" in content_bytes:
        line_ending = "\r\n"
    elif b"\r" in content_bytes:
        line_ending = "\r"
    else:
        line_ending = "\n"

    text = content_bytes.decode("utf-8", errors="ignore")
    lines = text.splitlines()

    new_lines = []
    applied_keys: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if "=" in stripped:
            key, _ = stripped.split("=", 1)
            key = key.strip()
            if key in TARGET_KEYS:
                new_lines.append(f"{key}={TARGET_KEYS[key]}")
                applied_keys.add(key)
                continue
        new_lines.append(line)

    # Append any keys that were not present in the file
    for key, value in TARGET_KEYS.items():
        if key not in applied_keys:
            new_lines.append(f"{key}={value}")
            _log_info_if_logger(logger, "[RTSS] Key not found in Global file, appending: %s=%s", key, value)

    new_text = line_ending.join(new_lines)
    # Preserve trailing newline if original had one
    if text.endswith("\n") or text.endswith("\r"):
        new_text += line_ending

    encoded = new_text.encode("utf-8")
    if has_bom:
        encoded = b"\xef\xbb\xbf" + encoded

    global_path.write_bytes(encoded)
    _log_info_if_logger(logger, "[RTSS] Global file updated: %s", global_path)


def apply_rtss_global_settings_if_needed(logger: Any = None) -> None:
    """Called after a successful install. Silently fixes RTSS Global settings if needed.
    No popup is shown. Read-only state of the file is preserved."""
    try:
        install_path = _get_rtss_install_path()
        global_path = install_path / "Profiles" / "Global"

        if not global_path.exists():
            _log_info_if_logger(logger, "[RTSS] Global file not found, skipping fix: %s", global_path)
            return

        ref_val, detours_val = _read_rtss_global_settings(global_path)
        _log_info_if_logger(
            logger,
            "[RTSS] Pre-fix values: ReflexSetLatencyMarker=%s, UseDetours=%s",
            ref_val,
            detours_val,
        )

        if _is_rtss_config_ok(ref_val, detours_val):
            _log_info_if_logger(logger, "[RTSS] Settings already OK, no changes needed")
            return

        import stat as _stat
        orig_stat = global_path.stat()
        orig_readonly = not (orig_stat.st_mode & _stat.S_IWRITE)

        try:
            if orig_readonly:
                global_path.chmod(orig_stat.st_mode | _stat.S_IWRITE)
                _log_info_if_logger(logger, "[RTSS] Temporarily removed read-only from Global file")

            _write_rtss_global_settings(global_path, logger=logger)
        finally:
            if orig_readonly:
                global_path.chmod(orig_stat.st_mode & ~_stat.S_IWRITE)
                _log_info_if_logger(logger, "[RTSS] Restored read-only on Global file")

    except Exception as exc:
        _log_warning(logger, "[RTSS] Failed to apply Global settings fix: %s", exc)
