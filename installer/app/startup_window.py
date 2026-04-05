from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from typing import Any, Callable


_SM_CONVERTIBLESLATEMODE = 0x2003


@dataclass(frozen=True)
class StartupWindowLayout:
    screen_width: int
    screen_height: int
    window_width: int
    window_height: int
    workaround_active: bool
    geometry: str
    poster_target_width: int
    poster_target_height: int
    poster_target_scale: float


def _default_get_system_metrics(metric_id: int) -> int:
    import ctypes

    return int(ctypes.windll.user32.GetSystemMetrics(metric_id))


def is_windows_slate_mode(
    *,
    os_name: str | None = None,
    get_system_metrics: Callable[[int], int] | None = None,
) -> bool:
    normalized_os_name = os.name if os_name is None else str(os_name)
    if normalized_os_name != "nt":
        return False

    try:
        metrics_reader = get_system_metrics or _default_get_system_metrics
        return int(metrics_reader(_SM_CONVERTIBLESLATEMODE)) == 0
    except Exception:
        logging.debug("[APP] Failed to read SM_CONVERTIBLESLATEMODE", exc_info=True)
        return False


def build_centered_window_geometry(screen_w: int, screen_h: int, width: int, height: int) -> str:
    x = max(0, (max(1, int(screen_w)) - max(1, int(width))) // 2)
    y = max(0, (max(1, int(screen_h)) - max(1, int(height))) // 2)
    return f"{max(1, int(width))}x{max(1, int(height))}+{x}+{y}"


def should_apply_umpc_window_workaround(
    screen_w: int,
    screen_h: int,
    target_w: int,
    target_h: int,
    *,
    window_height: int,
    slate_mode: bool | None = None,
) -> bool:
    use_slate_mode = is_windows_slate_mode() if slate_mode is None else bool(slate_mode)
    if not use_slate_mode:
        return False

    width_ratio = max(1, int(target_w)) / max(1, int(screen_w))
    height_ratio = max(1, int(target_h)) / max(1, int(screen_h))
    return width_ratio >= 0.90 or height_ratio >= 0.84 or max(1, int(screen_h)) <= int(window_height) + 140


def get_umpc_startup_window_size(
    screen_w: int,
    screen_h: int,
    target_w: int,
    target_h: int,
    *,
    window_min_width: int,
    window_min_height: int,
) -> tuple[int, int]:
    compact_w = min(int(target_w), max(int(window_min_width), int(screen_w) - max(96, int(screen_w) // 10)))
    compact_h = min(int(target_h), max(int(window_min_height), int(screen_h) - max(140, int(screen_h) // 6)))
    return max(int(window_min_width), compact_w), max(int(window_min_height), compact_h)


def get_ctk_scale(window: object | None = None, default: float = 1.0) -> float:
    try:
        if window is not None and hasattr(window, "_get_window_scaling"):
            scale = float(window._get_window_scaling())
            if scale > 0:
                return scale
    except Exception:
        logging.debug("[APP] Failed to read CustomTkinter scaling", exc_info=True)
    return float(default)


def resolve_startup_poster_target_size(
    window: object | None = None,
    *,
    card_width: int,
    card_height: int,
    default_scale: float,
) -> tuple[int, int, float]:
    scale = get_ctk_scale(window, default_scale)
    target_width = max(1, int(round(int(card_width) * scale)))
    target_height = max(1, int(round(int(card_height) * scale)))
    return target_width, target_height, scale


def build_startup_window_layout(
    root: Any,
    *,
    window_width: int,
    window_height: int,
    window_min_width: int,
    window_min_height: int,
    card_width: int,
    card_height: int,
    default_poster_scale: float,
    slate_mode: bool | None = None,
) -> StartupWindowLayout:
    screen_w = max(1, int(root.winfo_screenwidth() or window_width))
    screen_h = max(1, int(root.winfo_screenheight() or window_height))
    target_w = min(int(window_width), max(int(window_min_width), screen_w - 40))
    target_h = min(int(window_height), max(int(window_min_height), screen_h - 80))

    workaround_active = should_apply_umpc_window_workaround(
        screen_w,
        screen_h,
        target_w,
        target_h,
        window_height=window_height,
        slate_mode=slate_mode,
    )
    if workaround_active:
        target_w, target_h = get_umpc_startup_window_size(
            screen_w,
            screen_h,
            target_w,
            target_h,
            window_min_width=window_min_width,
            window_min_height=window_min_height,
        )

    geometry = (
        build_centered_window_geometry(screen_w, screen_h, target_w, target_h)
        if workaround_active
        else f"{target_w}x{target_h}"
    )
    poster_target_width, poster_target_height, poster_target_scale = resolve_startup_poster_target_size(
        root,
        card_width=card_width,
        card_height=card_height,
        default_scale=default_poster_scale,
    )

    return StartupWindowLayout(
        screen_width=screen_w,
        screen_height=screen_h,
        window_width=target_w,
        window_height=target_h,
        workaround_active=workaround_active,
        geometry=geometry,
        poster_target_width=poster_target_width,
        poster_target_height=poster_target_height,
        poster_target_scale=poster_target_scale,
    )


def apply_startup_window_layout(root: Any, layout: StartupWindowLayout, *, logger=None) -> None:
    root.geometry(str(layout.geometry))
    # Intentional: keep the minimum window size equal to the computed startup size.
    # The UI is not intended to be resized smaller than the initial layout.
    root.minsize(int(layout.window_width), int(layout.window_height))
    root.update_idletasks()
    root.state("normal")
    root.overrideredirect(False)
    root.resizable(True, True)

    use_logger = logger or logging.getLogger()
    if layout.workaround_active:
        use_logger.info(
            "[APP] Enabling UMPC startup window workaround (screen=%sx%s, target=%sx%s)",
            layout.screen_width,
            layout.screen_height,
            layout.window_width,
            layout.window_height,
        )


def apply_startup_window_workaround(
    root: Any,
    *,
    workaround_active: bool,
    window_width: int,
    window_height: int,
    logger=None,
) -> None:
    if not workaround_active:
        return

    try:
        screen_w = max(1, int(root.winfo_screenwidth() or window_width))
        screen_h = max(1, int(root.winfo_screenheight() or window_height))
        current_w = max(1, int(root.winfo_width() or window_width))
        current_h = max(1, int(root.winfo_height() or window_height))
        state = str(root.state() or "").strip().lower()
        is_effectively_maximized = state == "zoomed" or current_w >= screen_w - 24 or current_h >= screen_h - 24
        if not is_effectively_maximized:
            return

        root.overrideredirect(False)
        root.state("normal")
        root.deiconify()
        root.geometry(
            build_centered_window_geometry(
                screen_w,
                screen_h,
                window_width,
                window_height,
            )
        )
        root.update_idletasks()
        (logger or logging.getLogger()).info(
            "[APP] Restored startup window from maximized state to %sx%s",
            window_width,
            window_height,
        )
    except Exception:
        logging.debug("[APP] Failed to apply UMPC startup window workaround", exc_info=True)


__all__ = [
    "StartupWindowLayout",
    "apply_startup_window_layout",
    "apply_startup_window_workaround",
    "build_centered_window_geometry",
    "build_startup_window_layout",
    "get_ctk_scale",
    "get_umpc_startup_window_size",
    "is_windows_slate_mode",
    "resolve_startup_poster_target_size",
    "should_apply_umpc_window_workaround",
]
