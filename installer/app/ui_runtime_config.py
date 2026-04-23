from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Callable


RuntimeConfigGetter = Callable[[str, str], str]
BoolEnvGetter = Callable[[str, bool], bool]
IntEnvGetter = Callable[[str, int], int]


@dataclass(frozen=True)
class AppUiRuntimeConfig:
    card_width: int
    card_height: int
    grid_cols: int
    grid_rows_visible: int
    card_h_spacing: int
    card_v_spacing: int
    grid_side_padding: int
    grid_width: int
    grid_height: int
    window_width: int
    window_height: int
    window_min_width: int
    window_min_height: int
    image_timeout_seconds: int
    image_max_retries: int
    image_max_workers: int
    image_retry_delay_ms: int
    default_poster_scale: float
    info_text_offset_px: int
    poster_cache_version: int
    enable_poster_cache: bool
    image_cache_max: int


def build_app_ui_runtime_config(
    *,
    get_runtime_config_value: RuntimeConfigGetter,
    get_bool_env: BoolEnvGetter,
    get_int_env: IntEnvGetter,
) -> AppUiRuntimeConfig:
    card_width = 120
    card_height = 180
    grid_cols = 5
    grid_rows_visible = 2
    card_h_spacing = 2
    card_v_spacing = 2
    grid_side_padding = 12
    grid_width = (card_width * grid_cols) + (card_h_spacing * grid_cols) + (grid_side_padding * 2)
    grid_height = card_height * grid_rows_visible
    window_width = grid_width
    window_height = 710
    window_min_width = 360
    window_min_height = 420
    image_timeout_seconds = 10
    image_max_retries = 3
    image_max_workers = 4
    image_retry_delay_ms = get_int_env("OPTISCALER_IMAGE_RETRY_DELAY_MS", 1500)
    default_poster_scale = 1.5
    info_text_offset_px = 10
    poster_cache_version = 2

    enable_poster_cache_text = str(
        get_runtime_config_value("OPTISCALER_ENABLE_POSTER_CACHE", "")
    ).strip().lower()
    if not enable_poster_cache_text:
        enable_poster_cache = get_bool_env("OPTISCALER_ENABLE_POSTER_CACHE", True)
    elif enable_poster_cache_text in {"1", "true", "yes", "on", "0", "false", "no", "n", "off"}:
        enable_poster_cache = get_bool_env("OPTISCALER_ENABLE_POSTER_CACHE", False)
    else:
        enable_poster_cache = False

    return AppUiRuntimeConfig(
        card_width=card_width,
        card_height=card_height,
        grid_cols=grid_cols,
        grid_rows_visible=grid_rows_visible,
        card_h_spacing=card_h_spacing,
        card_v_spacing=card_v_spacing,
        grid_side_padding=grid_side_padding,
        grid_width=grid_width,
        grid_height=grid_height,
        window_width=window_width,
        window_height=window_height,
        window_min_width=window_min_width,
        window_min_height=window_min_height,
        image_timeout_seconds=image_timeout_seconds,
        image_max_retries=image_max_retries,
        image_max_workers=image_max_workers,
        image_retry_delay_ms=image_retry_delay_ms,
        default_poster_scale=default_poster_scale,
        info_text_offset_px=info_text_offset_px,
        poster_cache_version=poster_cache_version,
        enable_poster_cache=enable_poster_cache,
        image_cache_max=get_int_env("OPTISCALER_IMAGE_CACHE_MAX", 100),
    )


def format_optiscaler_version_display_name(raw_name: str) -> str:
    name = Path(str(raw_name or "").strip()).name
    if not name:
        return ""

    name = re.sub(r"(?i)\.(zip|7z)$", "", name).strip()
    name = re.sub(r"(?i)^optiscaler", "", name).lstrip()
    name = re.sub(r"^[-_]+", "", name).lstrip()
    return re.sub(r"\s+", " ", name).strip()


__all__ = [
    "AppUiRuntimeConfig",
    "build_app_ui_runtime_config",
    "format_optiscaler_version_display_name",
]
