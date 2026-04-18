from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import logging
import os
import re
import subprocess
from typing import Mapping

from ..common.process_utils import subprocess_no_window_kwargs


_ALLOWED_VENDOR_KEYWORDS = (
    "intel", "amd", "nvidia",
    "arc",
    "radeon",
    "geforce", "rtx",
)

_VENDOR_KEYWORD_MAP = {
    "nvidia": ("nvidia", "geforce", "rtx"),
    "amd": ("amd", "radeon"),
    "intel": ("intel", "arc"),
}

_VENDOR_PRIORITY = ("nvidia", "amd", "intel")
_TEST_GPU_ENABLED_ENV = "DUAL_GPU_TEST"
_TEST_GPU_NAMES_ENV = "TEST_GPU_NAMES"


@dataclass(frozen=True)
class GpuAdapterChoice:
    vendor: str
    model_name: str
    display_name: str
    selected_gid: int


@dataclass(frozen=True)
class GpuContext:
    gpu_names: list[str]
    gpu_count: int
    gpu_info: str
    selected_vendor: str
    selected_gid: int = 0
    adapters: tuple[GpuAdapterChoice, ...] = ()
    selected_model_name: str = ""

    @property
    def is_multi_gpu(self) -> bool:
        return self.gpu_count > 1


def _is_truthy_env(name: str) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _get_test_gpu_names_override() -> list[str]:
    if not _is_truthy_env(_TEST_GPU_ENABLED_ENV):
        return []

    raw = str(os.environ.get(_TEST_GPU_NAMES_ENV, "") or "").strip()
    if not raw:
        return []

    normalized = raw.replace("\r", "\n")
    tokens = re.split(r"[|\n]+", normalized)

    gpu_names: list[str] = []
    seen_names = set()
    for token in tokens:
        name = _normalize_text(token)
        if not name:
            continue
        dedupe_key = name.casefold()
        if dedupe_key in seen_names:
            continue
        seen_names.add(dedupe_key)
        gpu_names.append(name)
    return gpu_names


def get_graphics_adapter_snapshot() -> tuple[list[str], int, str]:
    """Return unique GPU names, detected adapter count, and a user-facing summary string."""
    test_gpu_names = _get_test_gpu_names_override()
    if test_gpu_names:
        logging.info(
            "[GPU] Using test GPU override because %s is enabled: %s",
            _TEST_GPU_ENABLED_ENV,
            " | ".join(test_gpu_names),
        )
        return test_gpu_names, len(test_gpu_names), ", ".join(test_gpu_names)

    if os.name != "nt":
        return [], 0, "Unknown (non-Windows OS)"

    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=8,
            **subprocess_no_window_kwargs(),
        )
        if result.returncode != 0:
            return [], 0, "Unknown"

        gpu_names_raw = []
        for line in result.stdout.splitlines():
            name = line.strip()
            if name:
                gpu_names_raw.append(name)

        filtered_names_unique = []
        seen_filtered_names = set()
        for name in gpu_names_raw:
            lowered = name.lower()
            if "mirage driver" in lowered:
                continue
            if any(keyword in lowered for keyword in _ALLOWED_VENDOR_KEYWORDS):
                normalized_name = " ".join(lowered.split())
                if normalized_name not in seen_filtered_names:
                    seen_filtered_names.add(normalized_name)
                    filtered_names_unique.append(name)

        if filtered_names_unique:
            # Only treat distinct GPU names as multi-GPU so duplicate WMI rows do not block installation.
            return filtered_names_unique, len(filtered_names_unique), ", ".join(filtered_names_unique)
    except Exception:
        pass

    return [], 0, "Unknown"


def get_graphics_adapter_info() -> str:
    """Return a user-friendly GPU name string for the current Windows machine."""
    return get_graphics_adapter_snapshot()[2]


def _normalize_text(text: str) -> str:
    return " ".join(str(text or "").split()).strip()


def detect_gpu_vendor(gpu_name: str) -> str:
    lowered = _normalize_text(gpu_name).lower()
    if not lowered:
        return ""

    for vendor in _VENDOR_PRIORITY:
        if any(keyword in lowered for keyword in _VENDOR_KEYWORD_MAP[vendor]):
            return vendor
    return ""


def detect_gpu_vendors(gpu_info: str) -> list[str]:
    lowered = str(gpu_info or "").strip().lower()
    if not lowered:
        return []

    # Prefer discrete GPUs first when multiple adapters are present.
    return [
        vendor
        for vendor in _VENDOR_PRIORITY
        if any(keyword in lowered for keyword in _VENDOR_KEYWORD_MAP[vendor])
    ]


def resolve_game_db_target_for_gpu(
    gpu_info: str,
    vendor_db_gids: Mapping[str, int],
    default_gid: int,
) -> tuple[str, int]:
    vendors = detect_gpu_vendors(gpu_info)
    for vendor in vendors:
        gid = int(vendor_db_gids.get(vendor, default_gid) or default_gid)
        if gid:
            return vendor, gid
    return "default", int(default_gid)


def _shorten_gpu_model_name(vendor: str, model_name: str) -> str:
    text = _normalize_text(model_name)
    if not text:
        return ""

    text = re.sub(r"\((?:tm|r)\)", "", text, flags=re.IGNORECASE)
    text = text.replace("\u2122", "").replace("\u00AE", "")
    text = re.sub(r"\bcorporation\b", "", text, flags=re.IGNORECASE)
    text = _normalize_text(text)

    if vendor == "nvidia":
        text = re.sub(r"^nvidia\s+", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^geforce\s+", "", text, flags=re.IGNORECASE)
    elif vendor == "amd":
        text = re.sub(r"^amd\s+", "", text, flags=re.IGNORECASE)
    elif vendor == "intel":
        text = re.sub(r"^intel\s+", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\barc\s+graphics\b", "Arc", text, flags=re.IGNORECASE)

    text = re.sub(r"\bgraphics\b$", "", text, flags=re.IGNORECASE)
    text = _normalize_text(text)
    return text or _normalize_text(model_name)


def build_gpu_adapter_choices(
    gpu_names: list[str],
    vendor_db_gids: Mapping[str, int],
    default_gid: int,
) -> tuple[GpuAdapterChoice, ...]:
    adapters: list[GpuAdapterChoice] = []
    for gpu_name in gpu_names:
        normalized_name = _normalize_text(gpu_name)
        if not normalized_name:
            continue

        vendor = detect_gpu_vendor(normalized_name)
        resolved_gid = int(vendor_db_gids.get(vendor, default_gid) or default_gid) if vendor else int(default_gid)
        adapters.append(
            GpuAdapterChoice(
                vendor=vendor or "default",
                model_name=normalized_name,
                display_name=_shorten_gpu_model_name(vendor, normalized_name),
                selected_gid=resolved_gid,
            )
        )

    return tuple(adapters)


def _select_preferred_adapter(adapters: tuple[GpuAdapterChoice, ...]) -> GpuAdapterChoice | None:
    for vendor in _VENDOR_PRIORITY:
        for adapter in adapters:
            if adapter.vendor == vendor:
                return adapter
    return adapters[0] if adapters else None


def detect_gpu_context(vendor_db_gids: Mapping[str, int], default_gid: int) -> GpuContext:
    gpu_names, gpu_count, gpu_info = get_graphics_adapter_snapshot()
    adapters = build_gpu_adapter_choices(gpu_names, vendor_db_gids, default_gid)

    selected_adapter = _select_preferred_adapter(adapters)
    if selected_adapter:
        selected_vendor = selected_adapter.vendor
        selected_gid = int(selected_adapter.selected_gid or default_gid)
        selected_model_name = selected_adapter.model_name
    else:
        selected_vendor, selected_gid = resolve_game_db_target_for_gpu(gpu_info, vendor_db_gids, default_gid)
        selected_model_name = ""

    return GpuContext(
        gpu_names=list(gpu_names),
        gpu_count=max(0, int(gpu_count or 0)),
        gpu_info=gpu_info,
        selected_vendor=selected_vendor,
        selected_gid=int(selected_gid or default_gid),
        adapters=adapters,
        selected_model_name=selected_model_name,
    )


def _split_gpu_rule_patterns(rule_text: str) -> list[str]:
    text = str(rule_text or "").strip()
    if not text:
        return []

    normalized = text.replace("\r", "\n").replace("\n", "|").replace(";", "|").replace(",", "|")
    return [token.strip().lower() for token in normalized.split("|") if token.strip()]


def matches_gpu_rule(rule_text: str, gpu_text: str) -> bool:
    patterns = _split_gpu_rule_patterns(rule_text)
    if not patterns:
        return False

    if any(pattern in {"all", "true", "yes", "1"} for pattern in patterns):
        return True

    normalized_gpu = str(gpu_text or "").strip().lower()
    if normalized_gpu in {"", "checking gpu...", "unknown"}:
        return False

    for pattern in patterns:
        if pattern in {"null", "none"}:
            continue
        if any(char in pattern for char in "*?[]"):
            if fnmatch.fnmatch(normalized_gpu, pattern):
                return True
        elif pattern in normalized_gpu:
            return True
    return False
