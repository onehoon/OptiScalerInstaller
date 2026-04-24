from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ..common.flag_parser import parse_bool_token
from ..data.game_db_keys import GPU_BUNDLE_LOADED_KEY, GPU_BUNDLE_SUPPORTED_KEY
from ..system import gpu_service


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _strip_trademark_markers(value: object) -> str:
    text = _normalize_text(value)
    if not text:
        return ""

    # Remove common trademark markers so wildcard rules remain stable.
    text = re.sub(r"\((?:tm|r)\)", "", text, flags=re.IGNORECASE)
    text = text.replace("\u2122", "").replace("\u00AE", "")
    text = text.replace("\u1d40", "").replace("\u1d39", "")
    return _normalize_text(text)


def parse_support_flag(value: object, *, native_xefg_means_false: bool = True) -> bool:
    extra_false_tokens = ("native xefg",) if native_xefg_means_false else ()
    return parse_bool_token(
        value,
        empty_default=False,
        unknown_default=True,
        extra_false_tokens=extra_false_tokens,
    )


def is_game_supported_for_vendor(
    game_data: Mapping[str, Any],
    *,
    vendor: str,
    gpu_info: str,
    native_xefg_means_false: bool = True,
) -> bool:
    if not parse_support_flag(game_data.get("enabled", True), native_xefg_means_false=False):
        return False

    if bool(game_data.get(GPU_BUNDLE_LOADED_KEY, False)):
        return bool(game_data.get(GPU_BUNDLE_SUPPORTED_KEY, False))

    normalized_vendor = str(vendor or "").strip().lower()
    if normalized_vendor in {"intel", "amd", "nvidia"}:
        support_key = f"support_{normalized_vendor}"
        if support_key in game_data:
            return parse_support_flag(
                game_data.get(support_key),
                native_xefg_means_false=native_xefg_means_false,
            )

    rule_text = _strip_trademark_markers(game_data.get("supported_gpu", ""))
    normalized_gpu_info = _strip_trademark_markers(gpu_info)
    return gpu_service.matches_gpu_rule(rule_text, normalized_gpu_info)


__all__ = [
    "is_game_supported_for_vendor",
    "parse_support_flag",
]
