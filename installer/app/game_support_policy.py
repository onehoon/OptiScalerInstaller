from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

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
    if isinstance(value, bool):
        return value

    text = str(value or "").strip().lower()
    if not text:
        return False

    false_tokens = {"0", "false", "no", "n", "off", "null", "none", "na", "n/a", "-"}
    if native_xefg_means_false:
        false_tokens.add("native xefg")
    if text in false_tokens:
        return False

    true_tokens = {"1", "true", "yes", "y", "on"}
    if text in true_tokens:
        return True

    # Keep legacy behavior: any non-empty unsupported token is treated as truthy.
    return True


def is_game_supported_for_vendor(
    game_data: Mapping[str, Any],
    *,
    vendor: str,
    gpu_info: str,
    native_xefg_means_false: bool = True,
) -> bool:
    if not parse_support_flag(game_data.get("enabled", True), native_xefg_means_false=False):
        return False

    if bool(game_data.get("__gpu_bundle_loaded__", False)):
        return bool(game_data.get("__gpu_bundle_supported__", False))

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
